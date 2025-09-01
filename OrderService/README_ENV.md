# Environment configuration

Copy .env.example to .env and set values as needed.

- PAYMENT_SERVICE_URL: URL of PaymentService (default http://localhost:8104)
- NOTIFICATION_SERVICE_URL: URL of NotificationService (default http://localhost:8106)
- LOCATION_SERVICE_URL: URL of LocationService (default http://localhost:8107)
- PROMOTION_SERVICE_URL: URL of PromotionService (default http://localhost:8108)
- HTTP_TIMEOUT_SECONDS: Request timeout for downstream calls (default 10)
- INTERNAL_SERVICE_TOKEN: Optional header `X-Internal-Token` for service-to-service auth
