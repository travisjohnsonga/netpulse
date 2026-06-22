"""
Pluggable NLP fallback for ChatOps — Phase 3.

The regex parser (``apps.core.chatops._parse_intent``) stays the always-on
default. Only when it returns ``unknown`` does the webhook handler consult
``resolve_nlp(text)``, which classifies the message into the SAME closed intent
set using a configured backend:

- ``none``  (default) — returns ``None``; the parser stays regex-only.
- ``local`` — POSTs a constrained prompt to an Ollama-style endpoint
  (``/api/generate``) and parses a strict ``{"intent","params"}`` JSON reply.
- ``api``   — same constrained prompt to an Anthropic-style messages endpoint;
  the API key is read from OpenBao (``spane/chatops/nlp`` key ``api_key``) and is
  never stored in the DB or logged.

Hard guarantees (a slow/broken NLP backend must never hang or 500 a webhook):
- Every network call has a ≤5s timeout and is wrapped so any error → ``None``.
- The model output is parsed STRICTLY: anything that isn't a JSON object naming a
  known intent yields ``None`` (fail closed → caller falls through to help).
- The resolved intent is returned to the caller, which still runs it through
  ``enforce_policy`` exactly as a regex-parsed intent would — no policy bypass.
"""
from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

NLP_TIMEOUT_S = 5
# Default Anthropic-style endpoint for the ``api`` backend when none configured.
_DEFAULT_API_ENDPOINT = "https://api.anthropic.com/v1/messages"
_DEFAULT_API_MODEL = "claude-haiku-4-5-20251001"
# OpenBao location of the ``api`` backend key (reuses the chatops vault helpers).
NLP_VAULT_KEY = "nlp"

# The model is constrained to emit exactly one of these intents (or "unknown").
# Kept in sync with resolve.KNOWN_INTENTS.
from .resolve import KNOWN_INTENTS  # noqa: E402

_PROMPT = (
    "You are an intent classifier for a network-monitoring assistant. "
    "Classify the user's message into exactly one intent and extract its target.\n"
    "Allowed intents: device_status, site_status, active_alerts, cve_query, "
    "eol_query, help.\n"
    "Rules:\n"
    "- device_status/cve_query/eol_query take a device name in params.name.\n"
    "- site_status takes a site name in params.name.\n"
    "- active_alerts and help take no params.\n"
    "- If nothing fits, use intent \"unknown\".\n"
    "Respond with ONLY a single-line JSON object, no prose, no code fences, "
    "of the form {\"intent\": \"...\", \"params\": {\"name\": \"...\"}}.\n"
    "User message: "
)


def _strip_fences(text: str) -> str:
    """Remove ``` / ```json code fences a model may wrap its JSON in."""
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[-1] if "\n" in t else t[3:]
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    return t.strip()


def _parse_model_json(raw: str):
    """Strictly parse a model reply into (intent, params) or return None.

    Anything that isn't a JSON object naming a KNOWN intent → None (fail closed).
    """
    try:
        obj = json.loads(_strip_fences(raw))
    except (ValueError, TypeError):
        return None
    if not isinstance(obj, dict):
        return None
    intent = obj.get("intent")
    if intent not in KNOWN_INTENTS:
        return None
    params = obj.get("params")
    if not isinstance(params, dict):
        params = {}
    # Only carry through the single string param we understand.
    clean = {}
    name = params.get("name")
    if isinstance(name, str) and name.strip():
        clean["name"] = name.strip()
    return intent, clean


# ── backends ──────────────────────────────────────────────────────────────────

def _backend_local(text: str, config):
    """Ollama-style /api/generate. Returns (intent, params) or None."""
    endpoint = (config.effective_nlp_endpoint() or "").rstrip("/")
    if not endpoint:
        return None
    if not endpoint.endswith("/api/generate"):
        endpoint = f"{endpoint}/api/generate"
    # SSRF guard: http/https only + no cloud-metadata target (private/on-prem
    # endpoints like http://ollama:11434 stay allowed). Fail closed on rejection.
    from apps.core.net_safety import UnsafeURLError, validate_outbound_url
    try:
        validate_outbound_url(endpoint)
    except UnsafeURLError as exc:
        logger.warning("chatops NLP local endpoint rejected: %s", exc)
        return None
    import requests
    resp = requests.post(
        endpoint,
        json={
            "model": config.effective_nlp_model() or "llama3",
            "prompt": _PROMPT + text,
            "stream": False,
            "format": "json",
            "options": {"temperature": 0},
        },
        timeout=NLP_TIMEOUT_S,
    )
    resp.raise_for_status()
    data = resp.json()
    # /api/generate returns {"response": "<model text>"}; /api/chat nests under message.
    raw = data.get("response")
    if raw is None:
        raw = (data.get("message") or {}).get("content")
    return _parse_model_json(raw or "")


def _backend_api(text: str, config):
    """Anthropic-style messages endpoint; key from OpenBao. (intent, params)|None."""
    from .models import get_chatops_secret
    api_key = get_chatops_secret(NLP_VAULT_KEY, "api_key")
    if not api_key:
        logger.warning("chatops NLP api backend: no key configured in OpenBao")
        return None
    endpoint = (config.effective_nlp_endpoint() or "").strip() or _DEFAULT_API_ENDPOINT
    model = config.effective_nlp_model() or _DEFAULT_API_MODEL
    # SSRF guard (same policy as the local backend): http/https + no metadata.
    from apps.core.net_safety import UnsafeURLError, validate_outbound_url
    try:
        validate_outbound_url(endpoint)
    except UnsafeURLError as exc:
        logger.warning("chatops NLP api endpoint rejected: %s", exc)
        return None
    import requests
    resp = requests.post(
        endpoint,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": model,
            "max_tokens": 100,
            "messages": [{"role": "user", "content": _PROMPT + text}],
        },
        timeout=NLP_TIMEOUT_S,
    )
    resp.raise_for_status()
    data = resp.json()
    # Anthropic messages: {"content":[{"type":"text","text":"..."}]}.
    raw = ""
    for block in (data.get("content") or []):
        if isinstance(block, dict) and block.get("type") == "text":
            raw = block.get("text", "")
            break
    return _parse_model_json(raw)


_BACKENDS = {"local": _backend_local, "api": _backend_api}


def resolve_nlp(text: str):
    """Classify ``text`` via the configured backend. Returns (intent, params) or None.

    Always fails closed: provider ``none``, an empty message, a missing config, a
    timeout, a transport error, or any non-conforming model output all return
    ``None`` so the caller falls through to help text. Never raises.
    """
    text = (text or "").strip()
    if not text:
        return None
    from .models import ChatOpsConfig
    try:
        config = ChatOpsConfig.load()
        backend = _BACKENDS.get(config.effective_nlp_provider())
        if backend is None:  # "none" or unrecognised
            return None
        return backend(text, config)
    except Exception as exc:  # noqa: BLE001 — NLP must never hang/break a webhook
        logger.warning("chatops NLP fallback failed: %s", exc)
        return None
