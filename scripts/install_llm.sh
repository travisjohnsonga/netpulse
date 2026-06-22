#!/usr/bin/env bash
#
# Install the recommended local LLM for spane's ChatOps NLP backend.
#   - Runtime : Ollama (ollama/ollama) — single self-hosted HTTP server on :11434
#   - Model   : qwen2.5:3b by default — Apache-2.0, ~2 GB, CPU-friendly, strong at
#               the constrained "map this command to one of N intents → JSON" task.
#               Alternatives: llama3.2:3b (Meta license), phi3:mini (MIT).
#
# The container attaches to spane's existing Docker network (discovered from the
# running `api` container) with the alias `ollama`, so the api reaches it at
# http://ollama:11434 with no compose edit. Model weights live in a named volume,
# so they survive restarts. The model is pinned resident (OLLAMA_KEEP_ALIVE=-1) so
# the first query after an idle period doesn't eat a cold-start reload — important
# because ChatOps fails NLP closed after ~5 s.
#
# Re-runnable: existing container is reused; a missing model is pulled; the model
# is re-warmed every run.
#
# Env overrides:
#   LLM_MODEL=qwen2.5:3b      which Ollama model to pull/serve
#   LLM_GPU=auto|on|off       GPU passthrough (auto = use it only if detected)
#   OLLAMA_IMAGE=ollama/ollama
#   OLLAMA_CONTAINER=spane-ollama
#   OLLAMA_VOLUME=spane-ollama-models
set -u

LLM_MODEL="${LLM_MODEL:-qwen2.5:3b}"
LLM_GPU="${LLM_GPU:-auto}"
OLLAMA_IMAGE="${OLLAMA_IMAGE:-ollama/ollama}"
OLLAMA_CONTAINER="${OLLAMA_CONTAINER:-spane-ollama}"
OLLAMA_VOLUME="${OLLAMA_VOLUME:-spane-ollama-models}"
OLLAMA_PORT="11434"

cd "$(dirname "$0")/.." || exit 1

die() { echo "❌ $*" >&2; exit 1; }

# ── Prerequisites ─────────────────────────────────────────────────────────────
command -v docker >/dev/null 2>&1 || die "docker not found — install Docker first."
docker info >/dev/null 2>&1 || die "cannot talk to the Docker daemon (permissions? is it running?)"

echo "Installing recommended local LLM for ChatOps NLP ..."
echo "  model:     $LLM_MODEL"
echo "  container: $OLLAMA_CONTAINER"

# ── Discover spane's Docker network (so api can resolve http://ollama:11434) ──
API_CID="$(docker ps -q --filter 'label=com.docker.compose.service=api' | head -1)"
if [ -n "$API_CID" ]; then
  NET="$(docker inspect "$API_CID" \
        --format '{{range $k,$_ := .NetworkSettings.Networks}}{{$k}} {{end}}' \
        2>/dev/null | awk '{print $1}')"
fi
if [ -z "${NET:-}" ]; then
  # Fallback: first network whose name contains spane or netpulse.
  NET="$(docker network ls --format '{{.Name}}' \
        | grep -Ei 'spane|netpulse' | head -1)"
fi
[ -n "${NET:-}" ] || die "could not find the spane Docker network — is the stack running? (start it, then re-run)"
echo "  network:   $NET (api reaches the model at http://ollama:$OLLAMA_PORT)"

# ── GPU decision (default CPU; opt-in/auto) ──────────────────────────────────
GPU_ARGS=()
case "$LLM_GPU" in
  on)   GPU_ARGS=(--gpus all) ;;
  off)  : ;;
  auto)
    if command -v nvidia-smi >/dev/null 2>&1 \
       && docker info 2>/dev/null | grep -qi 'nvidia'; then
      GPU_ARGS=(--gpus all); echo "  gpu:       detected — enabling passthrough"
    else
      echo "  gpu:       none detected — running CPU-only (fine for a 3B model)"
    fi ;;
  *) echo "  WARNING: unknown LLM_GPU='$LLM_GPU' — treating as off" ;;
esac

# ── Provision the Ollama container (idempotent) ──────────────────────────────
if docker ps -a --format '{{.Names}}' | grep -qx "$OLLAMA_CONTAINER"; then
  if docker ps --format '{{.Names}}' | grep -qx "$OLLAMA_CONTAINER"; then
    echo "  $OLLAMA_CONTAINER already running — reusing."
  else
    echo "  starting existing $OLLAMA_CONTAINER ..."
    docker start "$OLLAMA_CONTAINER" >/dev/null || die "failed to start $OLLAMA_CONTAINER"
  fi
  # Ensure it's on the spane network (no-op if already attached).
  docker network connect --alias ollama "$NET" "$OLLAMA_CONTAINER" 2>/dev/null || true
else
  echo "  creating $OLLAMA_CONTAINER ..."
  docker volume create "$OLLAMA_VOLUME" >/dev/null
  # Port published to localhost only — operator testing without exposing the LLM.
  docker run -d \
    --name "$OLLAMA_CONTAINER" \
    --restart unless-stopped \
    --network "$NET" --network-alias ollama \
    -v "$OLLAMA_VOLUME:/root/.ollama" \
    -e OLLAMA_KEEP_ALIVE=-1 \
    -p "127.0.0.1:$OLLAMA_PORT:$OLLAMA_PORT" \
    "${GPU_ARGS[@]}" \
    "$OLLAMA_IMAGE" >/dev/null || die "failed to start $OLLAMA_CONTAINER"
fi

# ── Wait for the server, pull the model, warm it ─────────────────────────────
echo -n "  waiting for Ollama to come up "
for _ in $(seq 1 30); do
  if docker exec "$OLLAMA_CONTAINER" ollama list >/dev/null 2>&1; then break; fi
  echo -n "."; sleep 1
done
echo ""

if docker exec "$OLLAMA_CONTAINER" ollama list 2>/dev/null | awk 'NR>1{print $1}' \
     | grep -qx "$LLM_MODEL"; then
  echo "  model $LLM_MODEL already present."
else
  echo "  pulling $LLM_MODEL (first run downloads ~2 GB) ..."
  docker exec "$OLLAMA_CONTAINER" ollama pull "$LLM_MODEL" \
    || die "model pull failed for $LLM_MODEL"
fi

echo -n "  warming the model into memory ... "
if docker exec "$OLLAMA_CONTAINER" ollama run "$LLM_MODEL" "reply with: ok" \
     >/dev/null 2>&1; then
  echo "ready."
else
  echo "WARNING: warm-up call failed (model is installed; it'll load on first query)."
fi

# ── Summary + how to point spane at it ───────────────────────────────────────
echo ""
echo "=== Local LLM ready ==="
echo "  endpoint : http://ollama:$OLLAMA_PORT   (from the api container)"
echo "  model    : $LLM_MODEL"
echo "  volume   : $OLLAMA_VOLUME (weights persist across restarts)"
echo ""
echo "Point spane's ChatOps NLP at it (admin) — via the ChatOps config API:"
echo "  curl -X PUT https://<spane-host>/api/chatops/config/ \\"
echo "    -H 'Authorization: Bearer <spane-admin-jwt>' \\"
echo "    -H 'Content-Type: application/json' \\"
echo "    -d '{\"nlp_provider\":\"local\",\"nlp_endpoint\":\"http://ollama:$OLLAMA_PORT\",\"nlp_model\":\"$LLM_MODEL\"}'"
echo ""
echo "  (or set the same three fields in Django admin → ChatOps config)"
echo "  Tip: for Teams, keep the NLP timeout ~2–3 s so a fallback stays inside Teams' 5 s window."
