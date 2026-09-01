#!/usr/bin/env bash
# Update the shared librenms-mcp container from the image published by CI.
# Run as root on the VM (rootful podman):
#
#   ./update.sh            # pull the image, recreate if it changed
#   ./update.sh --force    # recreate even if the image is unchanged
#
# Order of operations:
#   1. pull the image from ghcr.io (built by the fork's CI on every merge
#      to main);
#   2. skip early if the running container already uses that image (unless
#      --force) — the RUNNING container is left alone, so a pull failure
#      never takes the service down;
#   3. recreate the container (rm -f + run) with the deployment invariants
#      (rootful, --network=host, --user 1001:1001, :z volume, --env-file,
#      unless-stopped);
#   4. verify: wait for the HTTP endpoint to answer (401 without a token
#      is the expected healthy answer), then show the last log lines.
#
# The tokens file is NOT touched: it lives on the bind-mounted volume,
# outside the container, and survives the recreation.
set -euo pipefail

# --- deployment invariants (keep in sync with the README) ------------------
IMAGE=${LIBRENMS_MCP_IMAGE:-ghcr.io/gbarazer/librenms-mcp:latest}
CONTAINER=librenms-mcp
ENV_FILE=${LIBRENMS_MCP_ENV_FILE:-/home/mcp/.config/librenms-mcp/env}
DATA_DIR=${LIBRENMS_MCP_DATA_DIR:-/home/mcp/.local/share/librenms-mcp}
RUN_USER=1001:1001

FORCE=0
for arg in "$@"; do
  case "$arg" in
    --force)   FORCE=1 ;;
    -h|--help) sed -n '2,21p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg (see --help)" >&2; exit 2 ;;
  esac
done

log() { printf '\n=== %s\n' "$*"; }

command -v podman >/dev/null || { echo "podman not found" >&2; exit 1; }
[ -f "$ENV_FILE" ] || { echo "env-file missing: $ENV_FILE" >&2; exit 1; }
[ -d "$DATA_DIR" ] || { echo "data dir missing: $DATA_DIR" >&2; exit 1; }

PORT=$(sed -n 's/^MCP_HTTP_PORT=//p' "$ENV_FILE" | tail -1)
PORT=${PORT:-8000}

log "Pulling $IMAGE (running container untouched)"
podman pull "$IMAGE"
new_id=$(podman image inspect "$IMAGE" --format '{{.Id}}')

current_id=$(podman inspect "$CONTAINER" --format '{{.Image}}' 2>/dev/null || true)
if [ "$FORCE" = 0 ] && [ -n "$current_id" ] && [ "$current_id" = "$new_id" ]; then
  echo "Already up to date: $CONTAINER runs image ${new_id:0:12}. Use --force to recreate."
  exit 0
fi

log "Recreating $CONTAINER on image ${new_id:0:12}"
podman rm -f "$CONTAINER" 2>/dev/null || true
podman run -d --name "$CONTAINER" --restart=unless-stopped \
  --network=host \
  --user "$RUN_USER" \
  --env-file "$ENV_FILE" \
  -v "$DATA_DIR:/data:z" \
  "$IMAGE"

log "Waiting for the endpoint on :$PORT (401 without a token = healthy)"
ok=0
for _ in $(seq 1 30); do
  code=$(curl -s -o /dev/null -w '%{http_code}' -m 2 \
    -X POST -H 'Content-Type: application/json' \
    -H 'Accept: application/json, text/event-stream' \
    -d '{"jsonrpc":"2.0","id":1,"method":"ping"}' \
    "http://127.0.0.1:$PORT/mcp" || true)
  if [ "$code" != "000" ] && [ -n "$code" ]; then ok=1; break; fi
  sleep 1
done

log "Last log lines"
podman logs --tail 5 "$CONTAINER" || true

log "Result"
if [ "$ok" = 1 ]; then
  echo "OK — $CONTAINER answers on :$PORT (HTTP $code) with image ${new_id:0:12}."
  podman image prune -f >/dev/null || true
else
  echo "FAILED — no HTTP answer on :$PORT after 30s; check 'podman logs $CONTAINER'." >&2
  exit 1
fi
