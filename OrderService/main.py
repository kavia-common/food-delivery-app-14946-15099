import os
import uuid
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any

import httpx
from fastapi import FastAPI, HTTPException, Path, Body, Query, status, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# Configuration from environment with sensible localhost defaults
HTTP_TIMEOUT_SECONDS: float = float(os.getenv("HTTP_TIMEOUT_SECONDS", "10"))

PAYMENT_SERVICE_URL = os.getenv("PAYMENT_SERVICE_URL", "http://localhost:8104")
NOTIFICATION_SERVICE_URL = os.getenv("NOTIFICATION_SERVICE_URL", "http://localhost:8106")
LOCATION_SERVICE_URL = os.getenv("LOCATION_SERVICE_URL", "http://localhost:8107")
PROMOTION_SERVICE_URL = os.getenv("PROMOTION_SERVICE_URL", "http://localhost:8108")

# Optional internal token for service-to-service auth
INTERNAL_SERVICE_TOKEN: Optional[str] = os.getenv("INTERNAL_SERVICE_TOKEN")

# App metadata and tags for OpenAPI
app = FastAPI(
    title="Order Service API",
    description="Handles cart, order placement, status tracking, and order history.",
    version="1.0.0",
    openapi_tags=[
        {"name": "Cart", "description": "Cart management"},
        {"name": "Orders", "description": "Order creation and tracking"},
        {"name": "Internal", "description": "Service docs and info"},
    ],
)

# CORS for local dev
app.add_middleware(
    CORSMiddleware,
    allow_origins=[os.getenv("CORS_ALLOW_ORIGINS", "*")],
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory storage
CART: List[Dict[str, Any]] = []
ORDERS: Dict[str, Dict[str, Any]] = {}

def _service_headers(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """
    Build headers to call downstream services. Adds INTERNAL_SERVICE_TOKEN if present.
    """
    headers: Dict[str, str] = {}
    if INTERNAL_SERVICE_TOKEN:
        headers["X-Internal-Token"] = INTERNAL_SERVICE_TOKEN
    if extra:
        headers.update(extra)
    return headers

# Pydantic models aligning with openapi/order.yaml


class CartItem(BaseModel):
    id: Optional[str] = Field(None, description="Cart item ID")
    menuItemId: str = Field(..., description="Menu item identifier")
    hotelId: Optional[str] = Field(None, description="Hotel/restaurant identifier")
    quantity: int = Field(..., ge=1, description="Quantity ordered")
    notes: Optional[str] = Field(None, description="Notes for the item")
    selectedOptions: Optional[List[Dict[str, Optional[str]]]] = Field(
        default=None, description="Selected options"
    )
    unitPrice: Optional[float] = Field(None, description="Unit price")
    totalPrice: Optional[float] = Field(None, description="Total price")


class Address(BaseModel):
    id: Optional[str] = None
    label: Optional[str] = None
    line1: Optional[str] = None
    line2: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    postalCode: Optional[str] = None
    country: Optional[str] = None


class OrderTotals(BaseModel):
    subtotal: Optional[float] = 0.0
    discount: Optional[float] = 0.0
    deliveryFee: Optional[float] = 0.0
    tax: Optional[float] = 0.0
    grandTotal: Optional[float] = 0.0
    currency: Optional[str] = "USD"


class Order(BaseModel):
    id: str
    userId: str
    hotelId: Optional[str] = None
    status: str
    items: List[CartItem]
    deliveryAddress: Optional[Address] = None
    paymentId: Optional[str] = None
    totals: OrderTotals
    createdAt: datetime
    updatedAt: Optional[datetime] = None


class OrderCreateRequest(BaseModel):
    hotelId: str
    items: List[CartItem]
    deliveryAddressId: str
    promoCode: Optional[str] = None
    notes: Optional[str] = None


class PatchStatusBody(BaseModel):
    status: str = Field(..., description="Only 'cancelled' is allowed by user", pattern="^(cancelled)$")


# Utility functions


def _iso_now() -> datetime:
    return datetime.now(timezone.utc)


def _compute_totals(items: List[CartItem], promo_code: Optional[str]) -> OrderTotals:
    # Basic subtotal calculation using provided unitPrice/totalPrice if present
    subtotal = 0.0
    for it in items:
        if it.totalPrice is not None:
            subtotal += float(it.totalPrice)
        elif it.unitPrice is not None and it.quantity:
            subtotal += float(it.unitPrice) * int(it.quantity)
        else:
            # fallback nominal price
            subtotal += 10.0 * int(it.quantity)

    discount = 0.0
    # Try applying promotion service (env-based URL); endpoint per PromotionService: /promotions/validate
    if promo_code:
        try:
            with httpx.Client(timeout=HTTP_TIMEOUT_SECONDS) as client:
                resp = client.post(
                    f"{PROMOTION_SERVICE_URL.rstrip('/')}/promotions/validate",
                    json={"code": promo_code, "orderId": "temp"},
                    headers=_service_headers(),
                )
                if resp.status_code == 200:
                    data = resp.json()
                    discount = float(data.get("discount", 0.0))
        except httpx.TimeoutException:
            # ignore promo failures in MVP
            pass
        except httpx.HTTPError:
            pass

    delivery_fee = 3.99
    tax = round(0.07 * max(subtotal - discount, 0.0), 2)
    grand_total = max(subtotal - discount, 0.0) + delivery_fee + tax

    return OrderTotals(
        subtotal=round(subtotal, 2),
        discount=round(discount, 2),
        deliveryFee=round(delivery_fee, 2),
        tax=round(tax, 2),
        grandTotal=round(grand_total, 2),
        currency="USD",
    )


def _send_notification(user_id: str, message: str, kind: str = "order_event") -> None:
    """
    Notify NotificationService. Uses /notifications with NotificationCreateRequest shape.
    """
    try:
        with httpx.Client(timeout=HTTP_TIMEOUT_SECONDS) as client:
            client.post(
                f"{NOTIFICATION_SERVICE_URL.rstrip('/')}/notifications",
                json={
                    "userId": user_id,
                    "type": "order_update",
                    "title": kind,
                    "body": message,
                    "data": {"message": message, "kind": kind},
                    "channels": ["in_app"],
                },
                headers=_service_headers(),
            )
    except httpx.HTTPError:
        # Swallow errors for MVP
        pass


def _create_payment_intent(amount: float, currency: str, user_id: str) -> Optional[str]:
    """
    Create a payment intent via PaymentService /payments/intent.
    """
    try:
        with httpx.Client(timeout=HTTP_TIMEOUT_SECONDS) as client:
            resp = client.post(
                f"{PAYMENT_SERVICE_URL.rstrip('/')}/payments/intent",
                json={"amount": amount, "currency": currency, "orderId": f"tmp_{uuid.uuid4().hex}", "method": "card"},
                headers=_service_headers(),
            )
            if resp.status_code in (200, 201):
                data = resp.json()
                return str(data.get("id") or data.get("paymentId"))
    except httpx.HTTPError:
        pass
    return None


def _init_order_tracking(order_id: str) -> None:
    """
    Inform LocationService with a placeholder update so tracking endpoint has context.
    For MVP, we do nothing if service is unavailable.
    """
    try:
        with httpx.Client(timeout=HTTP_TIMEOUT_SECONDS) as client:
            # LocationService MVP uses /location/updates and /location/track/{orderId}
            client.post(
                f"{LOCATION_SERVICE_URL.rstrip('/')}/location/updates",
                json={
                    "orderId": order_id,
                    "courierId": "tbd",
                    "position": {"lat": 0.0, "lng": 0.0},
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
                headers=_service_headers(),
            )
    except httpx.HTTPError:
        pass


# Routes

# PUBLIC_INTERFACE
@app.get(
    "/cart",
    tags=["Cart"],
    summary="Get current cart",
    response_model=List[CartItem],
)
def get_cart() -> List[CartItem]:
    """Return all items in current in-memory cart."""
    return [CartItem(**item) for item in CART]


# PUBLIC_INTERFACE
@app.post(
    "/cart",
    tags=["Cart"],
    summary="Replace current cart",
    response_model=List[CartItem],
)
def replace_cart(items: List[CartItem] = Body(..., description="New cart items")) -> List[CartItem]:
    """Replace the entire cart with provided items."""
    global CART
    CART = []
    for it in items:
        obj = it.model_dump()
        if not obj.get("id"):
            obj["id"] = str(uuid.uuid4())
        CART.append(obj)
    return [CartItem(**item) for item in CART]


# PUBLIC_INTERFACE
@app.post(
    "/cart/items",
    tags=["Cart"],
    status_code=status.HTTP_201_CREATED,
    summary="Add item to cart",
    response_model=CartItem,
)
def add_cart_item(item: CartItem) -> CartItem:
    """Add an item to the in-memory cart."""
    obj = item.model_dump()
    if not obj.get("id"):
        obj["id"] = str(uuid.uuid4())
    CART.append(obj)
    return CartItem(**obj)


# PUBLIC_INTERFACE
@app.delete(
    "/cart/items/{itemId}",
    tags=["Cart"],
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove item from cart",
)
def remove_cart_item(itemId: str = Path(..., description="Cart item ID")) -> None:
    """Remove an item from the in-memory cart by ID."""
    global CART
    CART = [i for i in CART if i.get("id") != itemId]
    return None


# PUBLIC_INTERFACE
@app.post(
    "/orders",
    tags=["Orders"],
    status_code=status.HTTP_201_CREATED,
    summary="Create order from current cart",
    response_model=Order,
)
def create_order(req: OrderCreateRequest, request: Request) -> Order:
    """
    Create an order from provided items, compute totals, initiate payment intent,
    send a notification, and initialize order tracking.
    """
    if not req.items:
        # fallback to CART if not provided
        if not CART:
            raise HTTPException(status_code=400, detail="Cart is empty")
        req_items = [CartItem(**i) for i in CART]
    else:
        req_items = req.items

    # Simulate user id (in real world from auth token)
    user_id = "demo-user-1"

    totals = _compute_totals(req_items, req.promoCode)
    payment_id = _create_payment_intent(totals.grandTotal or 0.0, totals.currency or "USD", user_id)

    order_id = str(uuid.uuid4())
    now = _iso_now()

    order_dict: Dict[str, Any] = {
        "id": order_id,
        "userId": user_id,
        "hotelId": req.hotelId,
        "status": "created",
        "items": [it.model_dump() for it in req_items],
        "deliveryAddress": {"id": req.deliveryAddressId},
        "paymentId": payment_id,
        "totals": totals.model_dump(),
        "createdAt": now,
        "updatedAt": now,
    }
    ORDERS[order_id] = order_dict

    # Clear cart on successful order create
    CART.clear()

    # Send notification
    _send_notification(user_id, f"Order {order_id} created", "order_created")
    # Initialize order tracking on LocationService
    _init_order_tracking(order_id)

    return Order(**order_dict)


# PUBLIC_INTERFACE
@app.get(
    "/orders",
    tags=["Orders"],
    summary="List user orders",
    response_model=List[Order],
)
def list_orders(
    status_filter: Optional[str] = Query(
        default=None,
        alias="status",
        description="Filter by status",
    )
) -> List[Order]:
    """List all orders for the demo user, optionally filtered by status."""
    user_id = "demo-user-1"
    orders = [Order(**v) for v in ORDERS.values() if v.get("userId") == user_id]
    if status_filter:
        orders = [o for o in orders if o.status == status_filter]
    return orders


# PUBLIC_INTERFACE
@app.get(
    "/orders/{orderId}",
    tags=["Orders"],
    summary="Get order by id",
    response_model=Order,
)
def get_order(orderId: str = Path(..., description="Order ID")) -> Order:
    """Get a specific order by ID."""
    order = ORDERS.get(orderId)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return Order(**order)


# PUBLIC_INTERFACE
@app.patch(
    "/orders/{orderId}",
    tags=["Orders"],
    summary="Cancel order by user (if allowed)",
    response_model=Order,
)
def patch_order_status(
    orderId: str = Path(..., description="Order ID"),
    body: PatchStatusBody = Body(..., description="Status body; only 'cancelled' supported"),
) -> Order:
    """
    Allow user to cancel an order (limited to allowed states).
    Publishes the status to NotificationService and records changes.
    """
    order = ORDERS.get(orderId)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    current_status = order.get("status")
    if current_status in {"delivered", "cancelled", "refunded"}:
        raise HTTPException(status_code=400, detail=f"Cannot cancel order in '{current_status}' state")

    if body.status != "cancelled":
        raise HTTPException(status_code=400, detail="Only cancelling is supported")

    # Update order status
    order["status"] = "cancelled"
    order["updatedAt"] = _iso_now()
    ORDERS[orderId] = order

    # Notify
    _send_notification(order.get("userId", "demo-user-1"), f"Order {orderId} cancelled", "order_cancelled")

    return Order(**order)


# PUBLIC_INTERFACE
@app.get(
    "/docs/websocket-notes",
    tags=["Internal"],
    summary="WebSocket usage notes",
)
def websocket_notes():
    """
    This service doesn't expose WebSockets directly, but real-time updates are
    expected via NotificationService. Clients should subscribe to notification
    channels provided by that service to receive order status updates.
    """
    return {
        "message": "Order status updates are delivered through the NotificationService.",
        "subscribe": "Use NotificationService channels to receive real-time updates for order events.",
    }


# Healthcheck
# PUBLIC_INTERFACE
@app.get("/health", tags=["Internal"], summary="Healthcheck")
def health():
    """Basic healthcheck endpoint."""
    return {"status": "ok"}
