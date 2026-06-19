"""
Best-effort destination reachability checks for the backup test-connection
endpoint. These are intentionally lightweight (a TCP connect or a presence check)
and must NEVER leak secrets or raw exception text — callers wrap them and return
a generic message on failure.
"""
from __future__ import annotations

import os
import socket

from .models import BackupConfig


def _tcp_ok(host: str, port: int, timeout: float = 4.0) -> bool:
    with socket.create_connection((host, int(port)), timeout=timeout):
        return True


def test_destination(dest: str, cfg: BackupConfig) -> tuple[bool, str]:
    """Return (ok, detail) for the configured destination. Never raises for the
    expected 'unreachable' cases — only genuinely unexpected errors propagate to
    the caller's generic handler."""
    if dest == BackupConfig.Destination.LOCAL:
        path = cfg.local_path
        if os.path.isdir(path) and os.access(path, os.W_OK):
            return True, f"Local path {path} is writable."
        # The directory may not exist yet but its parent could be creatable.
        parent = os.path.dirname(path.rstrip("/")) or "/"
        if os.path.isdir(parent) and os.access(parent, os.W_OK):
            return True, f"Local path {path} can be created."
        return False, f"Local path {path} is not writable."

    if dest == BackupConfig.Destination.SCP:
        if not cfg.scp_host:
            return False, "No SCP host configured."
        try:
            _tcp_ok(cfg.scp_host, cfg.scp_port)
            return True, f"Reached {cfg.scp_host}:{cfg.scp_port}."
        except OSError:
            return False, f"Could not connect to {cfg.scp_host}:{cfg.scp_port}."

    if dest == BackupConfig.Destination.GIT:
        if not cfg.git_repo_url:
            return False, "No Git repository URL configured."
        # Parse host[:port] from an scp-style or URL git remote for a TCP probe.
        url = cfg.git_repo_url
        host, port = _git_host_port(url)
        if not host:
            return True, "Git remote configured (cannot probe this URL form)."
        try:
            _tcp_ok(host, port)
            return True, f"Reached {host}:{port}."
        except OSError:
            return False, f"Could not connect to {host}:{port}."

    if dest == BackupConfig.Destination.S3:
        if not cfg.s3_bucket:
            return False, "No S3 bucket configured."
        endpoint = cfg.s3_endpoint
        if endpoint:
            host, port = _url_host_port(endpoint)
            try:
                _tcp_ok(host, port)
                return True, f"Reached S3 endpoint {host}:{port}."
            except OSError:
                return False, f"Could not connect to S3 endpoint {host}:{port}."
        return True, "S3 bucket configured (AWS endpoint, not probed)."

    return False, "Unknown destination."


def _url_host_port(url: str) -> tuple[str, int]:
    from urllib.parse import urlparse
    if "://" not in url:
        url = "https://" + url
    p = urlparse(url)
    port = p.port or (443 if p.scheme == "https" else 80)
    return p.hostname or "", port


def _git_host_port(url: str) -> tuple[str, int]:
    # https://host/... or ssh://host:port/... or git@host:path
    if url.startswith(("http://", "https://", "ssh://", "git://")):
        return _url_host_port(url)
    if "@" in url and ":" in url:  # scp-style git@host:path
        hostpart = url.split("@", 1)[1].split(":", 1)[0]
        return hostpart, 22
    return "", 0
