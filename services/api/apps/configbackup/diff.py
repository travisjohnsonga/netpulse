"""Structured config diff used by the Configuration Compare UI.

Produces a unified-diff data structure (hunks of context/add/remove lines with
line numbers) plus summary counts, so the frontend can render a real diff with
highlighted +/- lines rather than just "X added, Y removed".
"""

import difflib


def generate_diff(old_config: str, new_config: str, context: int = 3) -> dict:
    """Return a structured unified diff between two config strings.

    Shape::

        {
          "summary": {"added": int, "removed": int, "changed": int},
          "hunks": [
            {
              "old_start": int, "old_count": int,
              "new_start": int, "new_count": int,
              "lines": [
                {"type": "context|add|remove", "content": str, "line_no": int},
                ...
              ],
            },
            ...
          ],
        }

    `added`/`removed` count every +/- line (a replaced line counts as one of
    each); `changed` counts the number of replace blocks (edited regions).
    """
    old_lines = (old_config or "").splitlines()
    new_lines = (new_config or "").splitlines()

    matcher = difflib.SequenceMatcher(None, old_lines, new_lines)

    # Summary across the full opcode list (not just grouped/changed regions).
    added = removed = changed = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "insert":
            added += j2 - j1
        elif tag == "delete":
            removed += i2 - i1
        elif tag == "replace":
            removed += i2 - i1
            added += j2 - j1
            changed += 1

    hunks = []
    for group in matcher.get_grouped_opcodes(context):
        hunk = {
            "old_start": group[0][1] + 1,
            "old_count": group[-1][2] - group[0][1],
            "new_start": group[0][3] + 1,
            "new_count": group[-1][4] - group[0][3],
            "lines": [],
        }
        for tag, i1, i2, j1, j2 in group:
            if tag == "equal":
                for n, line in enumerate(old_lines[i1:i2]):
                    hunk["lines"].append({"type": "context", "content": line, "line_no": i1 + n + 1})
            if tag in ("replace", "delete"):
                for n, line in enumerate(old_lines[i1:i2]):
                    hunk["lines"].append({"type": "remove", "content": line, "line_no": i1 + n + 1})
            if tag in ("replace", "insert"):
                for n, line in enumerate(new_lines[j1:j2]):
                    hunk["lines"].append({"type": "add", "content": line, "line_no": j1 + n + 1})
        hunks.append(hunk)

    return {
        "summary": {"added": added, "removed": removed, "changed": changed},
        "hunks": hunks,
    }
