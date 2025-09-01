```bash
#!/usr/bin/env bash
set -euo pipefail

# Detect privilege level
if [ "$(id -u)" -eq 0 ]; then
    SUDO=""
else
    SUDO="sudo"
fi

# Set workspace path
WORKSPACE="/home/kavia/workspace/code-generation/food-delivery-app-14946-15099/OrderService"
cd "$WORKSPACE"

# === COMMAND: INSTALL ===
# Node.js, npm, yarn, create-react-app, typescript, eslint, jest already preinstalled.
echo 'export NODE_ENV=development' | $SUDO tee /etc/profile.d/orderservice_node_env.sh > /dev/null

# === COMMAND: SCAFFOLD ===
# Create React app if not already present using default template
if [ ! -f "$WORKSPACE/package.json" ]; then
    npx --yes create-react-app "$WORKSPACE" --use-npm --quiet
fi

# === COMMAND: DEPS ===
cd "$WORKSPACE"
npm install --quiet
# If TypeScript project, ensure typescript dependency present
if [ -f "tsconfig.json" ] || grep -q '"typescript"' package.json 2>/dev/null; then
    npm list typescript --depth=0 >/dev/null 2>&1 || npm install --save-exact --quiet typescript
fi

# === COMMAND: BUILD ===
npm run build --quiet

# === COMMAND: TEST ===
npm test --ci --watchAll=false --silent || exit 1

# === COMMAND: START ===
npm start --quiet > /dev/null 2>&1 & echo $! > "$WORKSPACE/react.pid"

# === COMMAND: VALIDATE ===
sleep 7
curl -sf http://localhost:3000/ > /dev/null || { echo "React app failed runtime validation on port 3000" >&2; $SUDO bash -c '[ -f "$WORKSPACE/react.pid" ] && kill $(cat "$WORKSPACE/react.pid") 2>/dev/null && rm -f "$WORKSPACE/react.pid"'; exit 2; }
if [ -f "$WORKSPACE/react.pid" ]; then kill "$(cat "$WORKSPACE/react.pid")" 2>/dev/null || true; rm -f "$WORKSPACE/react.pid"; fi

# === COMMAND: STOP ===
if [ -f "$WORKSPACE/react.pid" ]; then
    kill "$(cat "$WORKSPACE/react.pid")" 2>/dev/null || true
    rm -f "$WORKSPACE/react.pid"
fi
```