#!/usr/bin/env bash
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="$DIR/docker-compose.yml"
CADDYFILE="$DIR/Caddyfile"

if grep -q "127.0.0.1:8011" "$CADDYFILE"; then
  ACTIVE_SLOT="blue"; ACTIVE_PORT="8011"
  IDLE_SLOT="green";  IDLE_PORT="8012"
  IDLE_PROFILE="--profile green"
else
  ACTIVE_SLOT="green"; ACTIVE_PORT="8012"
  IDLE_SLOT="blue";    IDLE_PORT="8011"
  IDLE_PROFILE=""
fi

echo "Active slot: $ACTIVE_SLOT (:$ACTIVE_PORT) -> Deploying idle slot: $IDLE_SLOT (:$IDLE_PORT)"

docker compose -f "$COMPOSE_FILE" $IDLE_PROFILE up -d --build "letsvibe-$IDLE_SLOT"

echo "Waiting for letsvibe-$IDLE_SLOT (/api/health)..."
for _ in $(seq 1 30); do
  if curl -sf "http://127.0.0.1:$IDLE_PORT/api/health" >/dev/null; then
    echo "letsvibe-$IDLE_SLOT is healthy."
    break
  fi
  sleep 1
done

if ! curl -sf "http://127.0.0.1:$IDLE_PORT/api/health" >/dev/null; then
  echo "ERROR: letsvibe-$IDLE_SLOT failed health check; keeping $ACTIVE_SLOT active." >&2
  exit 1
fi

LISTEN_LINE="$(head -n 1 "$CADDYFILE")"
cat > "$CADDYFILE" <<EOF
$LISTEN_LINE
    reverse_proxy 127.0.0.1:$IDLE_PORT
}
EOF

docker exec letsvibe-proxy caddy reload --config /etc/caddy/Caddyfile
echo "Switched proxy to $IDLE_SLOT (:$IDLE_PORT)."

docker compose -f "$COMPOSE_FILE" stop "letsvibe-$ACTIVE_SLOT" || true
echo "Stopped old slot: letsvibe-$ACTIVE_SLOT."
