"""
Per-platform rendering of a :class:`apps.chatops.resolve.IntentResult` into each
chat platform's native rich shape, always with a plain-text fallback field for
clients that don't render cards.

Rich markup rules (the markdown-regression contract):
- Slack uses ``mrkdwn`` blocks where ``*bold*`` is correct, so asterisks are fine
  there.
- Teams / Google Chat / Discord / Mattermost get only the markdown-neutral
  ``IntentResult.plain()`` text plus structured card fields — NO Slack-style
  ``*`` markup, which those clients would show as literal asterisks.
"""
from __future__ import annotations

# severity → colour for cards that take one.
_HEX = {"critical": "#FF0000", "high": "#FF6600", "medium": "#FFAA00",
        "low": "#0099FF", "info": "#2EB67D"}
_DISCORD_SEVERITIES = {"critical", "high", "medium", "low", "info"}


def _fields(result):
    return [(str(label), str(value)) for label, value in result.fields]


# ── Slack (Block Kit) ─────────────────────────────────────────────────────────

def format_slack(result) -> dict:
    blocks = [{
        "type": "header",
        "text": {"type": "plain_text", "text": result.title[:150] or "spane", "emoji": True},
    }]
    fields = _fields(result)
    if fields:
        # Block Kit allows ≤10 fields per section.
        blocks.append({
            "type": "section",
            "fields": [{"type": "mrkdwn", "text": f"*{label}*\n{value}"}
                       for label, value in fields[:10]],
        })
    if result.lines:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "\n".join(result.lines)},
        })
    return {"text": result.plain(), "blocks": blocks}


# ── Microsoft Teams (Adaptive Card) ───────────────────────────────────────────

def format_teams(result) -> dict:
    body = [{
        "type": "TextBlock", "text": result.title, "weight": "Bolder",
        "size": "Medium", "wrap": True,
    }]
    fields = _fields(result)
    if fields:
        body.append({
            "type": "FactSet",
            "facts": [{"title": label, "value": value} for label, value in fields],
        })
    if result.lines:
        body.append({"type": "TextBlock", "text": "\n".join(result.lines), "wrap": True})
    return {
        "type": "message",
        "text": result.plain(),  # plain fallback
        "attachments": [{
            "contentType": "application/vnd.microsoft.card.adaptive",
            "content": {
                "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                "type": "AdaptiveCard",
                "version": "1.4",
                "body": body,
            },
        }],
    }


# ── Google Chat (cardsV2) ─────────────────────────────────────────────────────

def format_gchat(result) -> dict:
    sections = []
    fields = _fields(result)
    if fields:
        sections.append({"widgets": [
            {"decoratedText": {"topLabel": label, "text": value}}
            for label, value in fields
        ]})
    if result.lines:
        sections.append({"widgets": [{"textParagraph": {"text": "\n".join(result.lines)}}]})
    if not sections:
        sections.append({"widgets": [{"textParagraph": {"text": result.plain()}}]})
    return {
        "text": result.plain(),  # plain fallback
        "cardsV2": [{
            "cardId": "spane-chatops",
            "card": {"header": {"title": result.title or "spane"}, "sections": sections},
        }],
    }


# ── Discord (embed) ───────────────────────────────────────────────────────────

def format_discord(result) -> dict:
    from apps.alerting.channels import discord_embed
    severity = result.severity if result.severity in _DISCORD_SEVERITIES else "info"
    fields = [{"name": label, "value": value, "inline": True}
              for label, value in _fields(result)]
    payload = discord_embed(
        title=result.title or "spane",
        description="\n".join(result.lines),
        severity=severity,
        fields=fields,
    )
    payload["content"] = result.plain()  # plain fallback
    return payload


# ── Mattermost (markdown attachment) ──────────────────────────────────────────

def format_mattermost(result) -> dict:
    attachment = {
        "color": _HEX.get(result.severity, "#808080"),
        "title": result.title,
        "text": "\n".join(result.lines),
        "fields": [{"title": label, "value": value, "short": True}
                   for label, value in _fields(result)],
    }
    return {"text": result.plain(), "attachments": [attachment]}  # plain fallback


_FORMATTERS = {
    "slack": format_slack,
    "teams": format_teams,
    "gchat": format_gchat,
    "discord": format_discord,
    "mattermost": format_mattermost,
}


def format_for(platform: str, result) -> dict:
    """Render ``result`` for ``platform``; falls back to a bare text body."""
    fn = _FORMATTERS.get(platform)
    if fn is None:
        return {"text": result.plain()}
    return fn(result)


# ── denial responses (Phase 2 enforcement messages) ───────────────────────────

def deny_response(platform: str, message: str) -> dict:
    """Per-platform shape for a policy denial — plain text in the native field."""
    if platform == "teams":
        return {"type": "message", "text": message}
    if platform == "discord":
        return {"content": message}
    # slack / gchat / mattermost / default
    return {"text": message}
