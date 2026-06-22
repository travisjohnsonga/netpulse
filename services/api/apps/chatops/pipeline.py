"""
Shared ChatOps natural-language classification: regex parse → optional NLP
fallback.

This is the SAME classify step run before ``enforce_policy`` + ``resolve`` by
both ChatOps surfaces:

- the inbound platform webhooks (``apps.core.chatops``), and
- the authenticated in-UI query endpoint (``apps.chatops.views.ChatOpsQueryView``).

Kept here so both share one source of truth — the parse/resolve logic is never
duplicated. The regex parser is the always-on default; the optional NLP fallback
(``apps.chatops.nlp.resolve_nlp``) is consulted only when the regex yields
``unknown``. The chosen intent is still run through ``enforce_policy`` by every
caller (no policy bypass).
"""
from __future__ import annotations

import re

from .nlp import resolve_nlp

# ── intent patterns ───────────────────────────────────────────────────────────
_INTENTS: list[tuple[str, re.Pattern]] = [
    ("site_status",    re.compile(r"status\s+of\s+site\s+(?P<name>\S+)", re.I)),
    ("device_status",  re.compile(r"status\s+of\s+(?P<name>\S+)", re.I)),
    ("active_alerts",  re.compile(r"(any\s+)?alerts?(\s+right\s+now)?", re.I)),
    ("cve_query",      re.compile(r"cve.*(affect|on)\s+(?P<name>\S+)", re.I)),
    ("eol_query",      re.compile(r"(eol|end.of.life|lifecycle).*(?P<name>\S+)", re.I)),
    ("help",           re.compile(r"^help$", re.I)),
]


def _parse_intent(text: str) -> tuple[str, dict]:
    cleaned = text.strip()
    for intent, pattern in _INTENTS:
        m = pattern.search(cleaned)
        if m:
            return intent, m.groupdict()
    return "unknown", {}


def classify(text: str) -> tuple[str, dict]:
    """Regex parse first (always-on default); only on ``unknown`` consult the
    optional NLP fallback. A known NLP result is used; anything else stays
    ``unknown`` so the resolver returns help. The chosen intent is returned to the
    caller, which still runs it through ``enforce_policy`` (no policy bypass)."""
    intent, params = _parse_intent(text)
    if intent == "unknown":
        nlp = resolve_nlp(text)
        if nlp:
            return nlp
    return intent, params
