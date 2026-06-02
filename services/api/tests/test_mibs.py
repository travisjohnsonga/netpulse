"""MIB parser, index, and management API."""
import pytest

from apps.mibs import index
from apps.mibs.parser import OIDResolver, parse_definitions

pytestmark = pytest.mark.django_db

SAMPLE_MIB = """
TEST-MIB DEFINITIONS ::= BEGIN
IMPORTS enterprises FROM SNMPv2-SMI;
testMib      OBJECT IDENTIFIER ::= { enterprises 99999 }
testObjects  OBJECT IDENTIFIER ::= { testMib 1 }
testTemp OBJECT-TYPE
    SYNTAX      Integer32
    MAX-ACCESS  read-only
    STATUS      current
    DESCRIPTION "A temperature sensor."
    ::= { testObjects 3 }
END
"""


@pytest.fixture
def mib_tree(tmp_path, settings):
    """A MIBS_DIR with a vendor MIB and a custom MIB; index cache reset."""
    (tmp_path / "vendor" / "community").mkdir(parents=True)
    (tmp_path / "custom").mkdir(parents=True)
    (tmp_path / "vendor" / "community" / "TEST-MIB.my").write_text(SAMPLE_MIB)
    settings.MIBS_DIR = str(tmp_path)
    index.reload()
    yield tmp_path
    index.reload()


# ── parser ──────────────────────────────────────────────────────────────────

class TestParser:
    def test_parse_counts_objects(self):
        defs = parse_definitions(SAMPLE_MIB)
        assert set(defs) == {"testMib", "testObjects", "testTemp"}

    def test_resolves_oid_from_enterprises_root(self):
        defs = parse_definitions(SAMPLE_MIB)
        r = OIDResolver(defs)
        assert r.resolve_symbol("testMib") == "1.3.6.1.4.1.99999"
        assert r.resolve_symbol("testTemp") == "1.3.6.1.4.1.99999.1.3"

    def test_unknown_symbol_unresolved(self):
        r = OIDResolver(parse_definitions(SAMPLE_MIB))
        assert r.resolve_symbol("nope") is None


# ── index ───────────────────────────────────────────────────────────────────

class TestIndex:
    def test_list_mibs(self, mib_tree):
        mibs = index.list_mibs()
        assert len(mibs) == 1
        m = mibs[0]
        assert m["name"] == "TEST-MIB" and m["path"] == "vendor/community"
        assert m["objects"] == 3 and m["deletable"] is False

    def test_resolve_oid_exact_and_suffix(self, mib_tree):
        assert index.resolve_oid("1.3.6.1.4.1.99999.1.3")["name"] == "testTemp"
        assert index.resolve_oid("1.3.6.1.4.1.99999.1.3.5")["name"] == "testTemp.5"
        assert index.resolve_oid("1.2.3.4.5")["resolved"] is False

    def test_save_and_delete_custom(self, mib_tree):
        res = index.save_upload("MY-MIB.my", SAMPLE_MIB.replace("TEST-MIB", "MY-MIB"))
        assert res["success"] and res["objects_loaded"] == 3
        assert any(m["path"] == "custom" for m in index.list_mibs())
        assert index.delete_mib("MY-MIB") is True
        assert not any(m["path"] == "custom" for m in index.list_mibs())

    def test_delete_non_custom_returns_false(self, mib_tree):
        assert index.delete_mib("TEST-MIB") is False  # vendor MIB, not deletable

    def test_validate_warns_on_unresolved(self):
        bad = "X-MIB DEFINITIONS ::= BEGIN\nfoo OBJECT IDENTIFIER ::= { missingParent 1 }\nEND"
        out = index.validate_text(bad)
        assert out["ok"] and out["objects"] == 1 and out["warnings"]


# ── API ─────────────────────────────────────────────────────────────────────

class TestMibApi:
    def test_list_requires_auth(self, api_client):
        assert api_client.get("/api/mibs/").status_code == 401

    def test_list(self, auth_client, mib_tree):
        body = auth_client.get("/api/mibs/").json()
        assert body["mibs"][0]["name"] == "TEST-MIB"

    def test_resolve_endpoint(self, auth_client, mib_tree):
        body = auth_client.get("/api/mibs/resolve/1.3.6.1.4.1.99999.1.3/").json()
        assert body["name"] == "testTemp" and body["resolved"] is True

    def test_upload_then_delete(self, auth_client, mib_tree):
        from io import BytesIO
        f = BytesIO(SAMPLE_MIB.replace("TEST-MIB", "UP-MIB").encode())
        f.name = "UP-MIB.my"
        resp = auth_client.post("/api/mibs/upload/", {"file": f}, format="multipart")
        assert resp.status_code == 201 and resp.json()["objects_loaded"] == 3
        assert auth_client.delete("/api/mibs/UP-MIB/").status_code == 204

    def test_delete_vendor_mib_rejected(self, auth_client, mib_tree):
        assert auth_client.delete("/api/mibs/TEST-MIB/").status_code == 404
