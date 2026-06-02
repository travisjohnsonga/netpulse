"""
Lightweight ASN.1 SMI MIB parser + OID resolver.

Works on raw vendor/community MIB source files (`.my` / `.mib` / `.txt`) — no
pre-compilation needed — which is what people actually contribute. It extracts
object definitions (``name TYPE ... ::= { parent num }``) and resolves each to a
full numeric OID by walking the parent chain, anchored at the well-known SMI
roots. Good enough to (a) count a MIB's objects and (b) resolve an OID to a
human-readable name; it is not a full ASN.1 compiler.
"""
from __future__ import annotations

import re

# Well-known SMI roots so vendor MIBs anchored at e.g. ``enterprises`` resolve
# even when the base SMI MIBs aren't present.
ROOTS = {
    "ccitt": "0", "zeroDotZero": "0.0",
    "iso": "1", "org": "1.3", "dod": "1.3.6", "internet": "1.3.6.1",
    "directory": "1.3.6.1.1", "mgmt": "1.3.6.1.2", "mib-2": "1.3.6.1.2.1",
    "transmission": "1.3.6.1.2.1.10", "experimental": "1.3.6.1.3",
    "private": "1.3.6.1.4", "enterprises": "1.3.6.1.4.1",
    "security": "1.3.6.1.5", "snmpV2": "1.3.6.1.6",
    "snmpDomains": "1.3.6.1.6.1", "snmpProxys": "1.3.6.1.6.2",
    "snmpModules": "1.3.6.1.6.3",
}

# Definition types we count as "objects".
_TYPES = (
    "OBJECT-TYPE", "OBJECT IDENTIFIER", "MODULE-IDENTITY", "OBJECT-IDENTITY",
    "NOTIFICATION-TYPE",
)
_TYPE_ALT = "|".join(re.escape(t) for t in _TYPES)
# name  TYPE  ... ::= { clause }
_DEF_RE = re.compile(
    r"(?P<name>[A-Za-z][\w-]*)\s+(?:" + _TYPE_ALT + r")\b.*?::=\s*\{(?P<clause>[^}]+)\}",
    re.DOTALL,
)
_MODULE_RE = re.compile(r"(?P<name>[A-Za-z0-9][\w-]*)\s+DEFINITIONS\b[^:]*::=\s*BEGIN")
_NAME_NUM = re.compile(r"^([A-Za-z][\w-]*)\((\d+)\)$")
_BARE_NAME = re.compile(r"^[A-Za-z][\w-]*$")
_BARE_NUM = re.compile(r"^\d+$")


def _strip_comments(text: str) -> str:
    # ASN.1 comments run from "--" to the next "--" or end of line.
    return re.sub(r"--.*?(--|\n)", " ", text)


def module_name(text: str, fallback: str) -> str:
    m = _MODULE_RE.search(text)
    return m.group("name") if m else fallback


def parse_definitions(text: str) -> dict[str, list[str]]:
    """Return {symbol_name: [clause tokens]} for every object definition."""
    clean = _strip_comments(text)
    defs: dict[str, list[str]] = {}
    for m in _DEF_RE.finditer(clean):
        tokens = m.group("clause").split()
        if tokens:
            defs[m.group("name")] = tokens
    return defs


class OIDResolver:
    """Resolves symbol names / clauses to numeric OIDs across all parsed MIBs."""

    def __init__(self, defs: dict[str, list[str]]):
        self._defs = defs
        self._cache: dict[str, str | None] = {}
        self._resolving: set[str] = set()

    def resolve_symbol(self, name: str) -> str | None:
        if name in ROOTS:
            return ROOTS[name]
        if name in self._cache:
            return self._cache[name]
        if name in self._resolving or name not in self._defs:
            return None  # cycle, or undefined symbol
        self._resolving.add(name)
        oid = self._resolve_clause(self._defs[name])
        self._resolving.discard(name)
        self._cache[name] = oid
        return oid

    def _resolve_clause(self, tokens: list[str]) -> str | None:
        parts: list[str] = []
        for i, tok in enumerate(tokens):
            m = _NAME_NUM.match(tok)
            if m:
                if i == 0:
                    base = self.resolve_symbol(m.group(1))
                    parts.extend((base or m.group(2)).split("."))
                else:
                    parts.append(m.group(2))
            elif _BARE_NUM.match(tok):
                parts.append(tok)
            elif _BARE_NAME.match(tok) and i == 0:
                base = self.resolve_symbol(tok)
                if not base:
                    return None
                parts.extend(base.split("."))
            else:
                return None
        return ".".join(parts) if parts else None

    def name_to_oid(self) -> dict[str, str]:
        out = {}
        for name in self._defs:
            oid = self.resolve_symbol(name)
            if oid:
                out[name] = oid
        return out
