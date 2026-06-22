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
# A hostname-ish capture: letters/digits/_ plus the chars that show up in device
# names and addresses (dot, dash, colon, slash). Shared across patterns; a
# trailing-punctuation strip after matching cleans up any stray "." / ":" caught
# at the end. Re-used per pattern (one `name` group per compiled regex, so no
# duplicate-group-name error).
_NAME = r"(?P<name>[\w.\-:/]+)"

# Ordered most-specific → most-general; the FIRST match wins. active_alerts and
# the site_* rows precede device_status so "alerts" / "show me alerts" / "site X"
# are not swallowed as a device name. Several intents have more than one phrasing
# row (kept separate so each carries a single `name` group). The six intent keys
# are unchanged — this only broadens recall (no new intents, no resolver change).
_INTENTS: list[tuple[str, re.Pattern]] = [
    # help — exact phrases only.
    ("help", re.compile(r"^(?:help|commands|what can you do|\?)$", re.I)),

    # active_alerts — any mention of alerts, plus "what's firing" / "anything alerting".
    ("active_alerts", re.compile(
        r"\b(?:any|active|firing|open|current)?\s*alerts?\b"
        r"|\bwhat'?s firing\b"
        r"|\banything alerting\b", re.I)),

    # cve_query — "cves on/for/affecting NAME" and a "vulnerabilities on NAME" variant.
    ("cve_query", re.compile(r"\bcves?\b.*?\b(?:affect(?:ing)?|on|for)\s+" + _NAME, re.I)),
    ("cve_query", re.compile(r"\bvulnerab\w*\b.*?\bon\s+" + _NAME, re.I)),

    # eol_query — eol / end-of-life / end-of-support / lifecycle … NAME. The name
    # is the final token (anchored with \s…$ so it isn't reduced to the last
    # character by the greedy gap, and connector words like "for"/"of" are skipped).
    ("eol_query", re.compile(
        r"\b(?:eol|end[\s.\-]?of[\s.\-]?life|end[\s.\-]?of[\s.\-]?support|lifecycle)\b.*\s"
        + _NAME + r"$", re.I)),

    # site_status — must precede device_status so "site X" isn't read as a device.
    ("site_status", re.compile(r"\b(?:status of|how'?s|how is)\s+site\s+" + _NAME, re.I)),
    ("site_status", re.compile(r"\bsite\s+" + _NAME + r"\s+status\b", re.I)),

    # device_list — fleet queries. BEFORE device_status so "down devices",
    # "all devices", and "devices at site X" win over the singular device_status
    # patterns. `filter` (down/unreachable/offline → down; all/list → all) and
    # `site` are captured per pattern and read by the resolver.
    ("device_list", re.compile(r"\b(?P<filter>down|unreachable|offline)\s+devices?\b", re.I)),
    ("device_list", re.compile(
        r"\bwhich\s+devices?\s+are\s+(?P<filter>down|unreachable|offline)\b", re.I)),
    ("device_list", re.compile(
        r"\bany\s+(?P<filter>down|unreachable|offline)\s+devices?\b", re.I)),
    ("device_list", re.compile(r"\bdevices?\s+at\s+site\s+(?P<site>[\w.\-:/]+)\b", re.I)),
    ("device_list", re.compile(r"\bhealth\s+of\s+all\s+devices?\b", re.I)),
    ("device_list", re.compile(r"\b(?P<filter>all|list)\s+devices?\b", re.I)),

    # device_status — most general; checked last.
    ("device_status", re.compile(
        r"\b(?:status of|how'?s|how is|check|show(?:\s+me)?)\s+" + _NAME, re.I)),
    ("device_status", re.compile(
        r"\bis\s+" + _NAME + r"\s+(?:up|down|reachable|online|offline)\b", re.I)),
    ("device_status", re.compile(r"^" + _NAME + r"\s+status$", re.I)),
]

# Trailing punctuation a captured name should never keep (NAME may grab a stray
# "." or ":" at the very end, e.g. "edge-rtr-2.").
_NAME_TRIM = ".,;:!?"

# Words that are never a device name. If a pattern captures one of these as
# `name` (e.g. the greedy device_status verbs grabbing "the" from "show me the
# health of all devices"), reject the match and fall through to the next pattern
# — and ultimately to "unknown" so classify() consults the NLP fallback, rather
# than resolving to a bogus device named "the"/"all".
_NAME_STOPWORDS = frozenset({
    "the", "that", "this", "these", "those", "a", "an", "all", "any", "some",
    "my", "our", "your", "it", "them", "device", "devices", "host", "hosts",
})


def _parse_intent(text: str) -> tuple[str, dict]:
    # Normalize: strip + collapse internal whitespace to single spaces, then drop
    # trailing ? . ! — but keep a lone "?" so it can match `help`. Case is kept
    # (lookups are case-insensitive downstream; we don't lowercase the name).
    cleaned = " ".join(text.split())
    trimmed = cleaned.rstrip("?.!")
    cleaned = trimmed if trimmed else cleaned

    for intent, pattern in _INTENTS:
        m = pattern.search(cleaned)
        if not m:
            continue
        params = m.groupdict()
        name = params.get("name")
        if name:
            name = name.rstrip(_NAME_TRIM) or name
            # A stopword captured as the device name is never a real match —
            # skip this pattern and keep looking (→ unknown → NLP if nothing else).
            if name.lower() in _NAME_STOPWORDS:
                continue
            params["name"] = name
        return intent, params
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
