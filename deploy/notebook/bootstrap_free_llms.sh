#!/usr/bin/env sh
# Bootstrap Notebook AI with ShopAIKey + Groq/OpenRouter/Ollama fallbacks.
# Idempotent — safe to re-run on every compose up.
# ShopAIKey is registered as openai_compatible because Open Notebook supports
# a single custom OpenAI-compatible endpoint.
set -eu

API="${NOTEBOOK_BOOTSTRAP_URL:-http://notebook-gateway:80}"
API_FALLBACK="${NOTEBOOK_BOOTSTRAP_URL_FALLBACK:-http://notebook-ai:8502}"
GROQ_CHAT="${NOTEBOOK_GROQ_CHAT_MODEL:-openai/gpt-oss-20b}"
GROQ_CHAT_ALT="${NOTEBOOK_GROQ_CHAT_MODEL_ALT:-openai/gpt-oss-120b}"
QWEN_CHAT="${NOTEBOOK_QWEN_CHAT_MODEL:-qwen2.5:3b}"
QWEN_CHAT_FAST="${NOTEBOOK_QWEN_CHAT_MODEL_FAST:-qwen2.5:1.5b}"
EMBED_MODEL="${NOTEBOOK_EMBED_MODEL:-nomic-embed-text}"
FORCE="${NOTEBOOK_BOOTSTRAP_FORCE:-0}"
# Paid gateway is primary; free pools remain bounded fallbacks.
CHAT_PROVIDER="${NOTEBOOK_DEFAULT_CHAT_PROVIDER:-shopaikey}"
SHOP_KEY="${NOTEBOOK_SHOPAIKEY_API_KEY:-}"
SHOP_FAST="${NOTEBOOK_SHOPAIKEY_MODEL_FAST:-qwen3-235b-a22b}"
SHOP_DEEP="${NOTEBOOK_SHOPAIKEY_MODEL_DEEP:-qwen3-next-80b-a3b-instruct}"
SHOP_FALLBACK="${NOTEBOOK_SHOPAIKEY_MODEL_FALLBACK:-gpt-5-mini}"
SHOP_PROVIDER="openai_compatible"
OR_PRIMARY="${NOTEBOOK_OPENROUTER_CHAT_MODEL:-openrouter/free}"
OR_FALLBACKS="${NOTEBOOK_OPENROUTER_FALLBACK_MODELS:-meta-llama/llama-3.3-70b-instruct:free,qwen/qwen3-235b-a22b:free,google/gemma-3-27b-it:free,deepseek/deepseek-r1-0528:free}"
# Cerebras public models (Llama retired): gpt-oss-120b production 131k ctx.
CB_PRIMARY="${NOTEBOOK_CEREBRAS_CHAT_MODEL:-gpt-oss-120b}"
CB_FALLBACKS="${NOTEBOOK_CEREBRAS_FALLBACK_MODELS:-gemma-4-31b,zai-glm-4.7}"
# Open Notebook provider id for Cerebras OpenAI-compatible endpoint.
CB_PROVIDER="openai_compatible"

log() { echo "[notebook-bootstrap] $*"; }

wait_api() {
  base="$1"
  i=0
  while [ "$i" -lt 60 ]; do
    if curl -fsS "$base/api/config" >/dev/null 2>&1; then
      echo "$base"
      return 0
    fi
    i=$((i + 1))
    sleep 3
  done
  return 1
}

ensure_model() {
  base="$1"
  name="$2"
  provider="$3"
  type="$4"
  exists=$(curl -fsS "$base/api/models" | python3 -c "
import json,sys
name, provider, mtype = sys.argv[1:4]
for m in json.load(sys.stdin):
    if m.get('name')==name and m.get('provider')==provider and m.get('type')==mtype:
        print('1'); break
else:
    print('0')
" "$name" "$provider" "$type")
  if [ "$exists" = "1" ]; then
    log "exists $provider/$name ($type)"
    return 0
  fi
  code=$(curl -sS -o /tmp/nb_model.json -w '%{http_code}' -X POST "$base/api/models" \
    -H 'Content-Type: application/json' \
    -d "{\"name\":\"$name\",\"provider\":\"$provider\",\"type\":\"$type\"}" || true)
  if [ "$code" = "200" ] || [ "$code" = "201" ]; then
    log "created $provider/$name ($type)"
    return 0
  fi
  if grep -qi 'already exists' /tmp/nb_model.json 2>/dev/null; then
    log "exists $provider/$name ($type)"
    return 0
  fi
  log "WARN create $provider/$name failed HTTP $code: $(head -c 200 /tmp/nb_model.json 2>/dev/null || true)"
  return 1
}

model_id() {
  base="$1"
  name="$2"
  provider="$3"
  type="$4"
  curl -fsS "$base/api/models" | python3 -c "
import json,sys
name, provider, mtype = sys.argv[1:4]
for m in json.load(sys.stdin):
    if m.get('name')==name and m.get('provider')==provider and m.get('type')==mtype:
        print(m.get('id') or '')
        break
" "$name" "$provider" "$type" 2>/dev/null || true
}

set_defaults() {
  base="$1"
  chat_id="$2"
  embed_id="$3"
  tools_id="$4"
  transform_id="${5:-$tools_id}"
  body=$(CHAT_ID="$chat_id" EMBED_ID="$embed_id" TOOLS_ID="$tools_id" TRANSFORM_ID="$transform_id" python3 - <<'PY'
import json, os
print(json.dumps({
  "default_chat_model": os.environ["CHAT_ID"] or None,
  "default_embedding_model": os.environ["EMBED_ID"] or None,
  "default_tools_model": os.environ["TOOLS_ID"] or None,
  "default_transformation_model": os.environ["TRANSFORM_ID"] or None,
  "large_context_model": os.environ["CHAT_ID"] or None,
}))
PY
)
  code=$(curl -sS -o /tmp/nb_defaults.json -w '%{http_code}' -X PUT "$base/api/models/defaults" \
    -H 'Content-Type: application/json' \
    -d "$body" || true)
  if [ "$code" = "200" ]; then
    log "defaults set chat=$chat_id embed=$embed_id tools=$tools_id transform=$transform_id"
  else
    log "WARN defaults HTTP $code: $(head -c 200 /tmp/nb_defaults.json 2>/dev/null || true)"
  fi
}

BASE="$(wait_api "$API" || wait_api "$API_FALLBACK" || true)"
if [ -z "${BASE:-}" ]; then
  log "ERROR: Notebook API not reachable"
  exit 1
fi
log "using API $BASE"

ensure_model "$BASE" "$QWEN_CHAT" "ollama" "language" || true
ensure_model "$BASE" "$QWEN_CHAT_FAST" "ollama" "language" || true
ensure_model "$BASE" "$EMBED_MODEL" "ollama" "embedding" || true

# ShopAIKey owns Open Notebook's single openai_compatible endpoint.
if [ -n "$SHOP_KEY" ]; then
  ensure_model "$BASE" "$SHOP_FAST" "$SHOP_PROVIDER" "language" || true
  ensure_model "$BASE" "$SHOP_DEEP" "$SHOP_PROVIDER" "language" || true
  ensure_model "$BASE" "$SHOP_FALLBACK" "$SHOP_PROVIDER" "language" || true
else
  log "NOTEBOOK_SHOPAIKEY_API_KEY unset — skip ShopAIKey model registration"
fi

# Keep Cerebras registration only on installations without ShopAIKey.
if [ -z "$SHOP_KEY" ] && { [ -n "${CEREBRAS_API_KEY:-}" ] || [ -n "${OPENAI_COMPATIBLE_API_KEY:-}" ]; }; then
  ensure_model "$BASE" "$CB_PRIMARY" "$CB_PROVIDER" "language" || true
  OLD_IFS=$IFS
  IFS=,
  for mid in $CB_FALLBACKS; do
    mid=$(echo "$mid" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
    [ -n "$mid" ] || continue
    ensure_model "$BASE" "$mid" "$CB_PROVIDER" "language" || true
  done
  IFS=$OLD_IFS
else
  log "CEREBRAS_API_KEY unset — skip Cerebras model registration"
fi

# OpenRouter free stack — second cloud tier before Ollama/Groq.
if [ -n "${OPENROUTER_API_KEY:-}" ]; then
  ensure_model "$BASE" "$OR_PRIMARY" "openrouter" "language" || true
  OLD_IFS=$IFS
  IFS=,
  for mid in $OR_FALLBACKS; do
    mid=$(echo "$mid" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
    [ -n "$mid" ] || continue
    ensure_model "$BASE" "$mid" "openrouter" "language" || true
  done
  IFS=$OLD_IFS
else
  log "OPENROUTER_API_KEY unset — skip OpenRouter model registration"
fi

# Groq registered as optional UI choice only — keeps OSINT quota free
ensure_model "$BASE" "$GROQ_CHAT" "groq" "language" || true
ensure_model "$BASE" "$GROQ_CHAT_ALT" "groq" "language" || true

QWEN_ID="$(model_id "$BASE" "$QWEN_CHAT" "ollama" "language")"
QWEN_FAST_ID="$(model_id "$BASE" "$QWEN_CHAT_FAST" "ollama" "language")"
SHOP_FAST_ID="$(model_id "$BASE" "$SHOP_FAST" "$SHOP_PROVIDER" "language")"
SHOP_DEEP_ID="$(model_id "$BASE" "$SHOP_DEEP" "$SHOP_PROVIDER" "language")"
SHOP_FALLBACK_ID="$(model_id "$BASE" "$SHOP_FALLBACK" "$SHOP_PROVIDER" "language")"
CB_ID="$(model_id "$BASE" "$CB_PRIMARY" "$CB_PROVIDER" "language")"
OR_ID="$(model_id "$BASE" "$OR_PRIMARY" "openrouter" "language")"
GROQ_ID="$(model_id "$BASE" "$GROQ_CHAT" "groq" "language")"

CHAT_ID=""
TOOLS_ID=""
case "$CHAT_PROVIDER" in
  shopaikey)
    CHAT_ID="${SHOP_FAST_ID:-}"
    [ -z "$CHAT_ID" ] && CHAT_ID="${GROQ_ID:-}"
    [ -z "$CHAT_ID" ] && CHAT_ID="${OR_ID:-}"
    [ -z "$CHAT_ID" ] && CHAT_ID="${QWEN_FAST_ID:-}"
    TOOLS_ID="${SHOP_DEEP_ID:-}"
    [ -z "$TOOLS_ID" ] && TOOLS_ID="${SHOP_FALLBACK_ID:-}"
    [ -z "$TOOLS_ID" ] && TOOLS_ID="${CHAT_ID:-}"
    ;;
  cerebras|openai_compatible)
    # Cerebras first; OpenRouter → Ollama → Groq fallbacks.
    CHAT_ID="${CB_ID:-}"
    [ -z "$CHAT_ID" ] && CHAT_ID="${OR_ID:-}"
    [ -z "$CHAT_ID" ] && CHAT_ID="${QWEN_FAST_ID:-}"
    [ -z "$CHAT_ID" ] && CHAT_ID="${QWEN_ID:-}"
    [ -z "$CHAT_ID" ] && CHAT_ID="${GROQ_ID:-}"
    TOOLS_ID="${CB_ID:-}"
    [ -z "$TOOLS_ID" ] && TOOLS_ID="${OR_ID:-}"
    [ -z "$TOOLS_ID" ] && TOOLS_ID="${QWEN_FAST_ID:-}"
    [ -z "$TOOLS_ID" ] && TOOLS_ID="${QWEN_ID:-}"
    ;;
  openrouter)
    # Free cloud first; Ollama/Groq only if OpenRouter model missing.
    CHAT_ID="${OR_ID:-}"
    [ -z "$CHAT_ID" ] && CHAT_ID="${CB_ID:-}"
    [ -z "$CHAT_ID" ] && CHAT_ID="${QWEN_FAST_ID:-}"
    [ -z "$CHAT_ID" ] && CHAT_ID="${QWEN_ID:-}"
    [ -z "$CHAT_ID" ] && CHAT_ID="${GROQ_ID:-}"
    TOOLS_ID="${OR_ID:-}"
    [ -z "$TOOLS_ID" ] && TOOLS_ID="${CB_ID:-}"
    [ -z "$TOOLS_ID" ] && TOOLS_ID="${QWEN_FAST_ID:-}"
    [ -z "$TOOLS_ID" ] && TOOLS_ID="${QWEN_ID:-}"
    ;;
  groq)
    CHAT_ID="${GROQ_ID:-}"
    [ -z "$CHAT_ID" ] && CHAT_ID="${CB_ID:-}"
    [ -z "$CHAT_ID" ] && CHAT_ID="${OR_ID:-}"
    [ -z "$CHAT_ID" ] && CHAT_ID="${QWEN_FAST_ID:-}"
    [ -z "$CHAT_ID" ] && CHAT_ID="${QWEN_ID:-}"
    ;;
  *)
    # ollama: local chat; cloud fallback Cerebras → OpenRouter → Groq
    CHAT_ID="${QWEN_FAST_ID:-}"
    [ -z "$CHAT_ID" ] && CHAT_ID="${QWEN_ID:-}"
    [ -z "$CHAT_ID" ] && CHAT_ID="${CB_ID:-}"
    [ -z "$CHAT_ID" ] && CHAT_ID="${OR_ID:-}"
    [ -z "$CHAT_ID" ] && CHAT_ID="${GROQ_ID:-}"
    TOOLS_ID="${QWEN_FAST_ID:-}"
    [ -z "$TOOLS_ID" ] && TOOLS_ID="${QWEN_ID:-}"
    ;;
esac
[ -z "$TOOLS_ID" ] && TOOLS_ID="${CB_ID:-}"
[ -z "$TOOLS_ID" ] && TOOLS_ID="${QWEN_FAST_ID:-}"
[ -z "$TOOLS_ID" ] && TOOLS_ID="${QWEN_ID:-}"
[ -z "$TOOLS_ID" ] && TOOLS_ID="${OR_ID:-}"
[ -z "$TOOLS_ID" ] && TOOLS_ID="${CHAT_ID:-}"

# Transformation default: paid deep model → Groq/OpenRouter → local.
TRANSFORM_ID="${SHOP_DEEP_ID:-}"
[ -z "$TRANSFORM_ID" ] && TRANSFORM_ID="${SHOP_FALLBACK_ID:-}"
[ -z "$TRANSFORM_ID" ] && TRANSFORM_ID="${CB_ID:-}"
[ -z "$TRANSFORM_ID" ] && TRANSFORM_ID="${OR_ID:-}"
[ -z "$TRANSFORM_ID" ] && TRANSFORM_ID="${GROQ_ID:-}"
[ -z "$TRANSFORM_ID" ] && TRANSFORM_ID="${QWEN_FAST_ID:-}"
[ -z "$TRANSFORM_ID" ] && TRANSFORM_ID="${QWEN_ID:-}"
[ -z "$TRANSFORM_ID" ] && TRANSFORM_ID="${TOOLS_ID:-}"
[ -z "$TRANSFORM_ID" ] && TRANSFORM_ID="${CHAT_ID:-}"
EMBED_ID="$(model_id "$BASE" "$EMBED_MODEL" "ollama" "embedding")"

NEED=$(curl -fsS "$BASE/api/models/defaults" | FORCE="$FORCE" CHAT_ID="$CHAT_ID" TRANSFORM_ID="$TRANSFORM_ID" python3 -c '
import json, os, sys
force = os.environ.get("FORCE", "0") == "1"
want = (os.environ.get("CHAT_ID") or "").strip()
want_tf = (os.environ.get("TRANSFORM_ID") or "").strip()
d = json.load(sys.stdin)
chat = (d.get("default_chat_model") or "").strip()
embed = (d.get("default_embedding_model") or "").strip()
tf = (d.get("default_transformation_model") or "").strip()
# Also re-apply when preferred chat/transform model differs
mismatch = bool(want and chat and chat != want)
tf_mismatch = bool(want_tf and tf and tf != want_tf)
print("1" if (force or not chat or not embed or not tf or mismatch or tf_mismatch) else "0")
')

if [ "$NEED" = "1" ] && [ -n "$CHAT_ID" ] && [ -n "$EMBED_ID" ]; then
  set_defaults "$BASE" "$CHAT_ID" "$EMBED_ID" "$TOOLS_ID" "$TRANSFORM_ID"
elif [ -n "$CHAT_ID" ] && [ -n "$EMBED_ID" ]; then
  log "defaults already set — skip (NOTEBOOK_BOOTSTRAP_FORCE=1 to overwrite)"
else
  log "WARN missing chat/embed model ids — configure in UI Models"
fi

# Vietnamese hành chính–quân sự transform presets + default instructions
if [ -f /seed_transformations.py ]; then
  NOTEBOOK_API="$BASE" python3 /seed_transformations.py || log "WARN seed_transformations failed"
elif [ -f "$(dirname "$0")/seed_transformations.py" ]; then
  NOTEBOOK_API="$BASE" python3 "$(dirname "$0")/seed_transformations.py" || log "WARN seed_transformations failed"
else
  log "WARN seed_transformations.py not found — skip preset seed"
fi

curl -fsS -X POST "$BASE/api/models/auto-assign" >/dev/null 2>&1 || true
log "done — chat=$CHAT_PROVIDER ($CHAT_ID) shop_fast=$SHOP_FAST_ID shop_deep=$SHOP_DEEP_ID openrouter=$OR_ID transform=$TRANSFORM_ID tools=$TOOLS_ID embed=nomic"
curl -fsS "$BASE/api/models/defaults" || true
echo
