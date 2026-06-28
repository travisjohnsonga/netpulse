"""
Version + update-check endpoints (no auth).

GET /api/version/        local git info (version / commit / branch / built_at)
GET /api/version/check/  compares the local commit against GitHub's latest;
                         result cached 1h in the default cache (Valkey).

The check never surfaces an error to the user: GitHub unreachable, rate-limited,
disabled, or a private repo without a token all return update_available=false.
"""
from __future__ import annotations

import logging

from django.conf import settings
from django.core.cache import cache
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

logger = logging.getLogger(__name__)

_CACHE_KEY = "version_check_result"
_CACHE_TTL = 3600  # 1 hour — don't hammer the GitHub API


_APP_TAG_PREFIX = "app-v"


def _semver_tuple(s: str) -> tuple:
    """Parse a version string to a comparable (major, minor, patch) tuple.
    Tolerates an ``app-v`` prefix and a git-describe / pre-release suffix
    (``0.5.0-3-gabc123`` → (0,5,0)); unparseable parts → 0."""
    s = (s or "").strip()
    if s.startswith(_APP_TAG_PREFIX):
        s = s[len(_APP_TAG_PREFIX):]
    s = s.lstrip("v").split("-", 1)[0].split("+", 1)[0]  # drop pre-release / build
    parts = (s.split(".") + ["0", "0", "0"])[:3]
    out = []
    for p in parts:
        try:
            out.append(int(p))
        except ValueError:
            out.append(0)
    return tuple(out)


@api_view(["GET"])
@permission_classes([AllowAny])
def version(request):
    return Response({
        "version": settings.VERSION,
        "commit": settings.GIT_COMMIT,
        "branch": settings.GITHUB_BRANCH,
        "built_at": settings.BUILT_AT,
    })


@api_view(["GET"])
@permission_classes([AllowAny])
def version_check(request):
    base = {
        "current_version": settings.VERSION,
        "current_commit": settings.GIT_COMMIT,
        "latest_version": None,
        "update_available": False,
        "commits_behind": 0,  # retained for the badge's response shape (tag-based now)
        "release_notes_url":
            f"https://github.com/{settings.GITHUB_REPO}/releases",
    }
    if not settings.VERSION_CHECK_ENABLED:
        return Response({**base, "checked": False})

    remote = cache.get(_CACHE_KEY)
    if remote is None:
        remote = _check_github()
        if remote is not None:
            cache.set(_CACHE_KEY, remote, _CACHE_TTL)
    # remote is None only on a transient failure — return base (no badge, no error).
    return Response({**base, **(remote or {})})


def _github_headers() -> dict:
    headers = {"Accept": "application/vnd.github+json"}
    if settings.GITHUB_TOKEN:  # only needed for a private repo
        headers["Authorization"] = f"token {settings.GITHUB_TOKEN}"
    return headers


def _check_github() -> dict | None:
    """Compare the running app version to the latest **app-v\\*** tag on GitHub
    (Option C semver — not a commit count). dict on success, None on transient
    failure. The app and agent version independently, so we only look at app-v*
    tags (never the agent's v* tags)."""
    import requests

    repo = settings.GITHUB_REPO
    try:
        r = requests.get(
            f"https://api.github.com/repos/{repo}/tags?per_page=100",
            headers=_github_headers(), timeout=5)
    except Exception as exc:
        logger.warning("version check failed: %s", exc)
        return None
    if r.status_code == 404:
        logger.info("version check: %s not accessible (private repo, no token)", repo)
        return {"error": "private_repo_no_token"}
    if r.status_code != 200:
        logger.warning("version check: GitHub returned %s", r.status_code)
        return None

    app_tags = [str(t.get("name", "")) for t in (r.json() or [])
                if str(t.get("name", "")).startswith(_APP_TAG_PREFIX)]
    if not app_tags:
        return {"latest_version": None, "update_available": False, "commits_behind": 0}
    latest_tag = max(app_tags, key=_semver_tuple)
    return {
        "latest_version": latest_tag[len(_APP_TAG_PREFIX):],
        "update_available": _semver_tuple(latest_tag) > _semver_tuple(settings.VERSION),
        "commits_behind": 0,
        "release_notes_url": f"https://github.com/{repo}/releases/tag/{latest_tag}",
    }
