"""
MIB directory index.

Scans the MIB tree (``MIBS_DIR``, default ``/app/mibs``) — standard / vendor /
community / custom — parses each file, and exposes:
  - list_mibs(): per-file metadata (name, path, objects, deletable)
  - resolve_oid(): numeric OID → human-readable name (longest-prefix match)
  - save_upload() / delete_mib() for the custom/ directory

Results are cached; mutations (upload/delete) and reload() clear the cache.
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from django.conf import settings

from .parser import OIDResolver, module_name, parse_definitions

logger = logging.getLogger(__name__)

MIB_EXTENSIONS = (".my", ".mib", ".txt")
_CACHE: dict | None = None


def mibs_dir() -> Path:
    return Path(getattr(settings, "MIBS_DIR", os.environ.get("MIBS_DIR", "/app/mibs")))


# A MIB filename is a single path segment of safe characters — no separators, no
# traversal, no leading dot. We allow-list the charset rather than blocklisting
# "..", so encoded/obscured traversal can't slip through.
_SAFE_MIB_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def safe_mib_path(base_dir, user_input: str) -> str:
    """
    Resolve a user-supplied MIB filename inside ``base_dir``, REJECTING (not
    silently stripping) anything that isn't a plain in-directory filename.

    Defence in depth: (1) the name must match an allow-listed charset and not be
    "."/".."; (2) the fully-resolved path must stay inside the resolved base.
    Raises ValueError otherwise; returns the resolved absolute path.
    """
    name = (user_input or "").strip()
    if name in (".", "..") or not _SAFE_MIB_NAME.match(name):
        raise ValueError(f"unsafe MIB filename: {user_input!r}")
    base = Path(base_dir).resolve()
    candidate = (base / name).resolve()
    # candidate must be base itself's child (base must be a parent of candidate).
    if candidate == base or base not in candidate.parents:
        raise ValueError(f"path traversal detected: {user_input!r}")
    return str(candidate)


def _category(path: Path, root: Path) -> str:
    """Relative directory of a MIB file, e.g. 'vendor/cisco' or 'custom'."""
    rel = path.parent.relative_to(root)
    return str(rel) if str(rel) != "." else ""


def _scan() -> dict:
    root = mibs_dir()
    files: list[dict] = []
    all_defs: dict[str, list[str]] = {}
    if not root.is_dir():
        return {"files": [], "resolver": OIDResolver({}), "name_to_oid": {}}

    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in MIB_EXTENSIONS:
            continue
        try:
            text = path.read_text(errors="replace")
        except OSError as exc:
            logger.warning("could not read MIB %s: %s", path, exc)
            continue
        defs = parse_definitions(text)
        all_defs.update(defs)
        category = _category(path, root)
        files.append({
            "name": module_name(text, path.stem),
            "file": path.name,
            "path": category,
            "objects": len(defs),
            "loaded": True,
            "deletable": category == "custom",
        })

    resolver = OIDResolver(all_defs)
    return {"files": files, "resolver": resolver, "name_to_oid": resolver.name_to_oid()}


def _index() -> dict:
    global _CACHE
    if _CACHE is None:
        _CACHE = _scan()
    return _CACHE


def reload() -> None:
    global _CACHE
    _CACHE = None


def list_mibs() -> list[dict]:
    return list(_index()["files"])


def resolve_oid(oid: str) -> dict:
    """
    Resolve a numeric OID to "<name>[.<suffix>]" via longest-prefix match.
    Returns {"oid", "name", "resolved": bool}.
    """
    norm = oid.strip().lstrip(".")
    parts = norm.split(".")
    name_to_oid = _index()["name_to_oid"]
    oid_to_name = {v: k for k, v in name_to_oid.items()}
    for cut in range(len(parts), 0, -1):
        prefix = ".".join(parts[:cut])
        if prefix in oid_to_name:
            base = oid_to_name[prefix]
            suffix = parts[cut:]
            name = base + ("." + ".".join(suffix) if suffix else "")
            return {"oid": norm, "name": name, "resolved": True}
    return {"oid": norm, "name": None, "resolved": False}


def validate_text(text: str) -> dict:
    """Parse a MIB's text without saving. {ok, module, objects, warnings}."""
    defs = parse_definitions(text)
    warnings = []
    if not defs:
        warnings.append("no object definitions found — is this a valid MIB?")
    resolver = OIDResolver(defs)
    unresolved = [n for n in defs if resolver.resolve_symbol(n) is None]
    if unresolved:
        warnings.append(
            f"{len(unresolved)} symbol(s) could not be resolved to an OID "
            f"(missing IMPORTS/parent MIB?): {', '.join(unresolved[:5])}"
            + ("…" if len(unresolved) > 5 else ""))
    return {"ok": bool(defs), "module": module_name(text, ""),
            "objects": len(defs), "warnings": warnings}


def save_upload(filename: str, text: str) -> dict:
    """Validate + save an uploaded MIB into custom/. Returns the validation dict."""
    name = (filename or "").strip()
    if not name.lower().endswith(MIB_EXTENSIONS):
        return {"ok": False, "error": "unsupported extension (use .my/.mib/.txt)"}
    custom = mibs_dir() / "custom"
    custom.mkdir(parents=True, exist_ok=True)
    # Reject (do NOT silently strip) traversal / unsafe filenames.
    try:
        dest = safe_mib_path(custom, name)
    except ValueError:
        return {"ok": False, "error": "invalid MIB filename"}
    result = validate_text(text)
    if not result["ok"]:
        return {"ok": False, "error": "no MIB object definitions found",
                "warnings": result["warnings"]}
    Path(dest).write_text(text)
    reload()
    return {"success": True, "objects_loaded": result["objects"],
            "module": result["module"], "warnings": result["warnings"]}


def delete_mib(name: str) -> bool:
    """Delete a custom MIB by file name or module name. Custom-only; True if removed."""
    custom = mibs_dir() / "custom"
    if not custom.is_dir():
        return False
    target = os.path.basename(name)
    for path in custom.iterdir():
        if not path.is_file() or path.suffix.lower() not in MIB_EXTENSIONS:
            continue
        if path.name == target or path.stem == target or module_name(
                path.read_text(errors="replace"), path.stem) == name:
            path.unlink()
            reload()
            return True
    return False
