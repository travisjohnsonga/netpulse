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


def _current_count() -> int:
    try:
        return int(settings.VERSION.rsplit(".", 1)[-1])
    except (ValueError, AttributeError):
        return 0


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
        "latest_commit": None,
        "latest_version": None,
        "update_available": False,
        "commits_behind": 0,
        "release_notes_url":
            f"https://github.com/{settings.GITHUB_REPO}/commits/{settings.GITHUB_BRANCH}",
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
    """Latest-commit comparison. dict on success, None on transient failure."""
    import requests

    repo, branch = settings.GITHUB_REPO, settings.GITHUB_BRANCH
    try:
        r = requests.get(
            f"https://api.github.com/repos/{repo}/commits/{branch}",
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

    latest = (r.json().get("sha") or "")[:7]
    current = settings.GIT_COMMIT
    if not latest or current == "unknown" or latest == current:
        return {"latest_commit": latest or None, "update_available": False, "commits_behind": 0}

    behind = _commits_behind(repo, current, branch)
    return {
        "latest_commit": latest,
        "update_available": True,
        "commits_behind": behind,
        "latest_version": f"1.0.{_current_count() + behind}" if behind else None,
    }


def _commits_behind(repo: str, current: str, branch: str) -> int:
    """How many commits `branch` is ahead of the local commit (0 on failure)."""
    import requests
    try:
        r = requests.get(
            f"https://api.github.com/repos/{repo}/compare/{current}...{branch}",
            headers=_github_headers(), timeout=5)
        if r.status_code == 200:
            return int(r.json().get("ahead_by", 0))
    except Exception as exc:
        logger.debug("compare failed: %s", exc)
    return 0
