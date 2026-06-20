#!/usr/bin/env python3
"""Pre-commit / CI guard: no raw exception detail in API responses (CWE-209).

AST-based: flags any ``return Response(...)`` / ``JsonResponse(...)`` /
``HttpResponse(...)`` inside an ``except ... as <var>:`` handler that references
the exception variable *unless* it is funnelled through an approved sanitizer
(``safe_detail`` / ``internal_error_response`` / ``log_internal_error``), which
log server-side and return only a static message.

Usage:
  check_exception_exposure.py [files...]   # pre-commit passes changed files
  check_exception_exposure.py              # scan services/api/apps/**/views.py

Exits 1 if violations are found.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

SANITIZERS = {"safe_detail", "internal_error_response", "log_internal_error"}
RESPONSE_SINKS = {"Response", "JsonResponse", "HttpResponse"}


def _called_name(call: ast.Call) -> str | None:
    f = call.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return None


def _exc_escapes(arg_nodes, var: str) -> bool:
    found = False

    class V(ast.NodeVisitor):
        def visit_Call(self, node):  # noqa: N802
            if _called_name(node) in SANITIZERS:
                return  # sanitized subtree — do not descend
            self.generic_visit(node)

        def visit_Name(self, node):  # noqa: N802
            nonlocal found
            if node.id == var:
                found = True

    visitor = V()
    for n in arg_nodes:
        visitor.visit(n)
    return found


def violations_in(path: Path) -> list[int]:
    try:
        tree = ast.parse(path.read_text(), filename=str(path))
    except SyntaxError:
        return []
    out: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler) or not node.name:
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Return) and isinstance(sub.value, ast.Call) \
                    and _called_name(sub.value) in RESPONSE_SINKS:
                args = list(sub.value.args) + [kw.value for kw in sub.value.keywords]
                if _exc_escapes(args, node.name):
                    out.append(sub.lineno)
    return out


def _targets(argv: list[str]) -> list[Path]:
    if argv:
        return [Path(a) for a in argv if a.endswith("views.py")]
    root = Path(__file__).resolve().parent.parent / "services" / "api" / "apps"
    return [p for p in root.rglob("views.py")
            if not {"migrations", "__pycache__"} & set(p.parts)]


def main(argv: list[str]) -> int:
    found = []
    for path in _targets(argv):
        if not path.exists():
            continue
        for lineno in violations_in(path):
            found.append(f"{path}:{lineno}")
    if found:
        print("ERROR: exception details exposed in API responses (CWE-209):")
        for v in found:
            print(f"  {v}")
        print("\nFix: log the exception (exc_info=True) and return a generic "
              "message — route it through safe_detail()/internal_error_response().")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
