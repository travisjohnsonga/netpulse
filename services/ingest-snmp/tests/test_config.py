"""OpenBao token resolution (env var → keys-file fallback)."""
import json

from ingest.config import _resolve_openbao_token


def test_explicit_token_wins(monkeypatch):
    monkeypatch.setenv("OPENBAO_TOKEN", "s.explicit")
    assert _resolve_openbao_token() == "s.explicit"


def test_falls_back_to_keys_file(monkeypatch, tmp_path):
    keys = tmp_path / ".init_keys"
    keys.write_text(json.dumps({"root_token": "s.rootfromfile", "unseal_key": "x"}))
    monkeypatch.setenv("OPENBAO_TOKEN", "")          # blank → read the keys file
    monkeypatch.setenv("OPENBAO_KEYS_FILE", str(keys))
    assert _resolve_openbao_token() == "s.rootfromfile"


def test_empty_when_neither_available(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENBAO_TOKEN", "")
    monkeypatch.setenv("OPENBAO_KEYS_FILE", str(tmp_path / "does-not-exist"))
    assert _resolve_openbao_token() == ""
