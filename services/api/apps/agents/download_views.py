"""Public endpoints serving the agent install script and prebuilt binaries.

These live at the top level (/agent/...) rather than under /api/ so the install
one-liner stays short:

    curl -fsSL https://<server>/agent/install | sudo bash -s -- --server ... --token ...

The install script is served as plain text; binaries are served as attachments
from settings.AGENT_DIR (scripts/install.sh + dist/<platform>), which CI
populates. No auth is required — the script and binaries are not secrets, and
enrollment itself is gated by a one-time token.
"""
import os

from django.conf import settings
from django.http import FileResponse, Http404, HttpResponse

# /agent/download/<platform> → (filename in AGENT_DIR/dist, download name).
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


def download_binary(request, platform):
    """Serve a prebuilt agent binary from AGENT_DIR/dist for the given platform."""
    entry = _BINARIES.get(platform)
    if entry is None:
        raise Http404("unknown platform")
    filename, download_name = entry
    path = os.path.join(settings.AGENT_DIR, "dist", filename)
    if not os.path.exists(path):
        raise Http404("binary not available")
    return FileResponse(
        open(path, "rb"),
        as_attachment=True,
        filename=download_name,
        content_type="application/octet-stream",
    )
