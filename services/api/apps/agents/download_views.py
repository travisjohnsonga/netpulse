"""Public endpoints serving the agent install script and prebuilt binaries.

These live at the top level (/agent/...) rather than under /api/ so the install
one-liner stays short:

    curl -fsSL https://<server>/agent/install | sudo bash -s -- --server ... --token ...

The install script is served as plain text; binaries are served as attachments
from settings.AGENT_DIR (scripts/install.sh + dist/<platform>), which CI
populates. When a local binary is absent (e.g. a stack that hasn't fetched the
CI artifacts), download_binary redirects to the public GitHub Release instead,
so installs work out of the box without `gh` CLI, auth, or update.sh fetching
binaries. No auth is required — the script and binaries are not secrets, and
enrollment itself is gated by a one-time token.
"""
import os

from django.conf import settings
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import redirect

# Public GitHub Release that build-agent.yml keeps current (tag `agent-latest`).
GITHUB_RELEASE_BASE = (
    "https://github.com/travisjohnsonga/netpulse/releases/download/agent-latest"
)

# /agent/download/<platform> → (asset filename, download name).
# The asset filename doubles as the name in AGENT_DIR/dist and the GitHub
# Release asset, so it drives both the local serve and the redirect fallback.
_BINARIES = {
    "linux-amd64":   ("netpulse-agent-linux-amd64",       "netpulse-agent"),
    "linux-arm64":   ("netpulse-agent-linux-arm64",       "netpulse-agent"),
    "windows-amd64": ("netpulse-agent-windows-amd64.exe", "netpulse-agent.exe"),
}


def install_script(request):
    """Serve agent/scripts/install.sh as plain text for the curl|bash one-liner."""
    path = os.path.join(settings.AGENT_DIR, "scripts", "install.sh")
    try:
        with open(path, "rb") as fh:
            body = fh.read()
    except OSError as exc:
        raise Http404("install script not available") from exc
    return HttpResponse(body, content_type="text/plain; charset=utf-8")


def install_script_ps1(request):
    """Serve agent/scripts/install.ps1 as plain text for the Windows installer.

    The Windows enrollment one-liner fetches this with `curl.exe` and runs it via
    PowerShell. Parallel to install_script; served as text/plain so curl saves the
    real script (not a download prompt)."""
    path = os.path.join(settings.AGENT_DIR, "scripts", "install.ps1")
    try:
        with open(path, "rb") as fh:
            body = fh.read()
    except OSError as exc:
        raise Http404("install script not available") from exc
    return HttpResponse(body, content_type="text/plain; charset=utf-8")


def download_binary(request, platform):
    """Serve a prebuilt agent binary from AGENT_DIR/dist for the given platform."""
    entry = _BINARIES.get(platform)
    if entry is None:
        raise Http404("unknown platform")
    filename, download_name = entry
    path = os.path.join(settings.AGENT_DIR, "dist", filename)
    if os.path.exists(path):
        # Local binary present (dev / air-gapped / CI-populated) — serve it.
        return FileResponse(
            open(path, "rb"),
            as_attachment=True,
            filename=download_name,
            content_type="application/octet-stream",
        )
    # No local binary — redirect to the public GitHub Release asset.
    return redirect(f"{GITHUB_RELEASE_BASE}/{filename}")
