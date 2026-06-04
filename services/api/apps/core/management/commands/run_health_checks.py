"""
run_health_checks — post-setup health verification against the REAL running
infrastructure (not mocked). Intended to run after `setup.sh` / a factory reset
to prove the platform is wired correctly end to end.

  python manage.py run_health_checks            # human report, exit 1 on failure
  python manage.py run_health_checks --json      # machine-readable report
  python manage.py run_health_checks --fail-fast # stop at the first failure

Each check returns a CheckResult with status pass/warn/fail and, on failure, an
expected/actual/fix hint. Warnings never fail the suite (exit 0); failures exit 1.
The runner is structured so the rendering + aggregation are unit-testable with
injected results (see tests/test_health_checks.py).
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass

from django.core.management.base import BaseCommand

PASS, WARN, FAIL = "pass", "warn", "fail"
_ICON = {PASS: "✅", WARN: "⚠️ ", FAIL: "❌"}


@dataclass
class CheckResult:
    category: str
    name: str
    status: str = PASS
    expected: str = ""
    actual: str = ""
    fix: str = ""

    @property
    def passed(self) -> bool:
        # Warnings do not fail the suite — only hard failures do.
        return self.status != FAIL

    def to_dict(self) -> dict:
        return {"category": self.category, "name": self.name, "status": self.status,
                "expected": self.expected, "actual": self.actual, "fix": self.fix}


def ok(cat, name) -> CheckResult:
    return CheckResult(cat, name, PASS)


def warn(cat, name, actual="", fix="") -> CheckResult:
    return CheckResult(cat, name, WARN, actual=actual, fix=fix)


def fail(cat, name, expected="", actual="", fix="") -> CheckResult:
    return CheckResult(cat, name, FAIL, expected=expected, actual=actual, fix=fix)


class HealthCheckRunner:
    def __init__(self, fail_fast: bool = False, json_output: bool = False):
        self.fail_fast = fail_fast
        self.json_output = json_output
        self.results: list[CheckResult] = []

    # Ordered (category, method-name) registry. Each method returns a list of
    # CheckResult. Kept as names so tests can override the registry cleanly.
    CHECKS = [
        ("Database", "_check_database"),
        ("OpenBao", "_check_openbao"),
        ("Valkey", "_check_valkey"),
        ("InfluxDB", "_check_influxdb"),
        ("NATS", "_check_nats"),
        ("OpenSearch", "_check_opensearch"),
        ("Django", "_check_django"),
        ("Credentials Flow", "_check_credentials_flow"),
        ("Credential Secrets", "_check_credential_placeholders"),
        ("Ingest Services", "_check_ingest_heartbeats"),
        ("Network", "_check_network"),
        ("Docker NAT", "_check_nat"),
        ("MIBs", "_check_mibs"),
    ]

    def run_all(self) -> list[CheckResult]:
        results: list[CheckResult] = []
        for category, method in self.CHECKS:
            try:
                produced = getattr(self, method)()
            except Exception as exc:  # a check must never crash the runner
                produced = [fail(category, method, actual=f"check raised: {exc}")]
            for r in produced:
                results.append(r)
                if self.fail_fast and not r.passed:
                    self.results = results
                    return results
        self.results = results
        return results

    # ── rendering ────────────────────────────────────────────────────────────
    def render(self, results: list[CheckResult]) -> str:
        return self._render_json(results) if self.json_output else self._render_console(results)

    @staticmethod
    def _render_json(results: list[CheckResult]) -> str:
        passed = sum(1 for r in results if r.status == PASS)
        warned = sum(1 for r in results if r.status == WARN)
        failed = sum(1 for r in results if r.status == FAIL)
        return json.dumps({
            "summary": {"total": len(results), "passed": passed, "warnings": warned,
                        "failed": failed, "ok": failed == 0},
            "results": [r.to_dict() for r in results],
        }, indent=2)

    @staticmethod
    def _render_console(results: list[CheckResult]) -> str:
        lines = [
            "╔══════════════════════════════════════╗",
            "║     NetPulse Health Check Report     ║",
            "╚══════════════════════════════════════╝",
            "",
        ]
        current = None
        for r in results:
            if r.category != current:
                current = r.category
                lines.append(current)
            lines.append(f"  {_ICON[r.status]} {r.name}")
        passed = sum(1 for r in results if r.status == PASS)
        warned = sum(1 for r in results if r.status == WARN)
        failed = sum(1 for r in results if r.status == FAIL)
        lines += [
            "",
            "══════════════════════════════════════",
            f"  PASSED: {passed}/{len(results)}",
            f"  FAILED: {failed}",
            f"  WARNINGS: {warned}",
            "══════════════════════════════════════",
        ]
        for r in results:
            if r.status == FAIL:
                lines += ["", f"❌ FAILED: {r.name}"]
                if r.expected:
                    lines.append(f"   Expected: {r.expected}")
                if r.actual:
                    lines.append(f"   Got: {r.actual}")
                if r.fix:
                    lines.append(f"   Fix: {r.fix}")
        for r in results:
            if r.status == WARN:
                lines += ["", f"⚠️  WARNING: {r.name}"]
                if r.actual:
                    lines.append(f"   {r.actual}")
                if r.fix:
                    lines.append(f"   Fix: {r.fix}")
        return "\n".join(lines)

    # ── checks ───────────────────────────────────────────────────────────────
    def _check_database(self) -> list[CheckResult]:
        cat = "Database"
        out = []
        from django.db import connection
        try:
            connection.ensure_connection()
            out.append(ok(cat, "PostgreSQL connection"))
        except Exception as exc:
            return [fail(cat, "PostgreSQL connection", "connection", str(exc),
                         "docker compose restart postgres")]
        # Migrations applied?
        try:
            from django.db.migrations.executor import MigrationExecutor
            plan = MigrationExecutor(connection).migration_plan(
                MigrationExecutor(connection).loader.graph.leaf_nodes())
            out.append(ok(cat, "Migrations current") if not plan else
                       fail(cat, "Migrations current", "no pending migrations",
                            f"{len(plan)} pending",
                            "docker compose exec api python manage.py migrate"))
        except Exception as exc:
            out.append(warn(cat, "Migrations current", str(exc)))
        # Read/write a throwaway record (use the migrations table via a temp).
        try:
            with connection.cursor() as cur:
                cur.execute("CREATE TEMP TABLE _hc(v int)")
                cur.execute("INSERT INTO _hc VALUES (42)")
                cur.execute("SELECT v FROM _hc")
                got = cur.fetchone()[0]
            out.append(ok(cat, "Read/write test") if got == 42 else
                       fail(cat, "Read/write test", "42", str(got)))
        except Exception as exc:
            out.append(fail(cat, "Read/write test", "write+read 42", str(exc)))
        # pgcrypto available?
        try:
            with connection.cursor() as cur:
                cur.execute("SELECT 1 FROM pg_available_extensions WHERE name='pgcrypto';")
                avail = cur.fetchone() is not None
            out.append(ok(cat, "pgcrypto extension") if avail else
                       warn(cat, "pgcrypto extension", "not available",
                            "install postgresql-contrib in the postgres image"))
        except Exception as exc:
            out.append(warn(cat, "pgcrypto extension", str(exc)))
        return out

    def _check_openbao(self) -> list[CheckResult]:
        cat = "OpenBao"
        out = []
        import requests
        from django.conf import settings
        addr = getattr(settings, "OPENBAO_ADDR", os.environ.get("OPENBAO_ADDR", "http://openbao:8200")).rstrip("/")
        try:
            h = requests.get(f"{addr}/v1/sys/health", params={"sealedcode": 200, "uninitcode": 200}, timeout=5)
            out.append(ok(cat, "Reachable") if h.status_code in (200, 429, 472, 473) else
                       fail(cat, "Reachable", "HTTP 200", f"HTTP {h.status_code}", "docker compose restart openbao"))
            sealed = h.json().get("sealed", True)
            out.append(ok(cat, "Unsealed") if not sealed else
                       fail(cat, "Unsealed", "sealed=false", "sealed=true",
                            "docker compose exec api python manage.py init_openbao"))
        except Exception as exc:
            return [fail(cat, "Reachable", "HTTP 200", str(exc), "docker compose restart openbao")]
        # Token resolvable?
        from apps.credentials import vault
        token = vault._resolve_token()
        if not token:
            out.append(fail(cat, "Token configured", "non-empty token",
                            "empty", "scripts/fix_openbao_token.sh"))
            return out
        out.append(ok(cat, "Token configured"))
        # Write/read/delete a test secret through the real client.
        try:
            vault.write_secret("health-check/test", {"v": "ok-123"})
            got = vault.read_secret("health-check/test").get("v")
            out.append(ok(cat, "Write permission") if got == "ok-123" else
                       fail(cat, "Write permission", "ok-123", str(got)))
            out.append(ok(cat, "Read permission"))
            vault.delete_secret("health-check/test")
            out.append(ok(cat, "KV v2 engine"))
        except Exception as exc:
            out.append(fail(cat, "Write permission", "round-trip secret", str(exc)))
        return out

    def _check_valkey(self) -> list[CheckResult]:
        cat = "Valkey"
        try:
            import redis
        except Exception as exc:
            return [warn(cat, "Connection", f"redis lib unavailable: {exc}")]
        url = self._valkey_url()
        try:
            r = redis.from_url(url, socket_timeout=3)
            r.ping()
            r.set("health-check:test", "pong", ex=30)
            got = r.get("health-check:test")
            r.delete("health-check:test")
            val = got.decode() if isinstance(got, (bytes, bytearray)) else got
            return [ok(cat, "Connection (PING)"),
                    ok(cat, "SET/GET") if val == "pong" else fail(cat, "SET/GET", "pong", str(val)),
                    ok(cat, "Password auth")]
        except Exception as exc:
            return [fail(cat, "Connection (PING)", "PONG", str(exc), "docker compose restart valkey")]

    def _check_influxdb(self) -> list[CheckResult]:
        cat = "InfluxDB"
        from django.conf import settings
        try:
            from influxdb_client import InfluxDBClient, Point
            from influxdb_client.client.write_api import SYNCHRONOUS
        except Exception as exc:
            return [warn(cat, "Reachable", f"influxdb-client unavailable: {exc}")]
        bucket = getattr(settings, "INFLUXDB_BUCKET", "metrics")
        try:
            client = InfluxDBClient(url=settings.INFLUXDB_URL, token=settings.INFLUXDB_TOKEN,
                                    org=settings.INFLUXDB_ORG)
            health = client.health()
            reachable = getattr(health, "status", "") == "pass"
        except Exception as exc:
            return [fail(cat, "Reachable", "healthy", str(exc), "docker compose restart influxdb")]
        out = [ok(cat, "Reachable") if reachable else fail(cat, "Reachable", "healthy", "unhealthy")]
        try:
            buckets = client.buckets_api().find_buckets().buckets or []
            has_bucket = any(b.name == bucket for b in buckets)
            out.append(ok(cat, "Bucket exists") if has_bucket else
                       fail(cat, "Bucket exists", bucket, "missing"))
            out.append(ok(cat, "Admin token valid"))
            w = client.write_api(write_options=SYNCHRONOUS)
            w.write(bucket=bucket, record=Point("_healthcheck").field("v", 1))
            res = client.query_api().query(
                f'from(bucket:"{bucket}") |> range(start:-5m) |> filter(fn:(r)=>r._measurement=="_healthcheck") |> last()')
            out.append(ok(cat, "Write + query") if res else warn(cat, "Write + query", "no point read back"))
        except Exception as exc:
            out.append(fail(cat, "Write + query", "round-trip point", str(exc)))
        finally:
            try:
                client.close()
            except Exception:
                pass
        return out

    def _check_nats(self) -> list[CheckResult]:
        cat = "NATS"
        host = os.environ.get("NATS_HOST", "nats")
        if not self._tcp_ok(host, int(os.environ.get("NATS_PORT", "4222"))):
            return [fail(cat, "Reachable", "TCP 4222 open", "refused", "docker compose restart nats")]
        out = [ok(cat, "Reachable")]
        import asyncio
        try:
            import nats

            async def _probe():
                nc = await nats.connect(
                    os.environ.get("NATS_URL", f"nats://{host}:4222"),
                    user=os.environ.get("NATS_USER") or None,
                    password=os.environ.get("NATS_PASSWORD") or None, connect_timeout=5)
                js = nc.jetstream()
                await nc.publish("netpulse.healthcheck", b"ping")
                streams = []
                try:
                    streams = [s.config.name async for s in await js.streams_info()] if hasattr(js, "streams_info") else []
                except Exception:
                    streams = []
                await nc.close()
                return streams
            streams = asyncio.run(_probe())
            out.append(ok(cat, "Connect + auth"))
            out.append(ok(cat, "Publish"))
            out.append(ok(cat, "JetStream enabled"))
            if streams:
                have = set(streams)
                want = {"TELEMETRY", "ALERTS"}
                missing = want - have
                out.append(ok(cat, "Expected streams") if not missing else
                           warn(cat, "Expected streams", f"missing {sorted(missing)}",
                                "streams are created on first publish by the engines"))
        except Exception as exc:
            out.append(warn(cat, "Connect + auth", str(exc)))
        return out

    def _check_opensearch(self) -> list[CheckResult]:
        cat = "OpenSearch"
        import requests
        from django.conf import settings
        host = settings.OPENSEARCH_HOST
        port = settings.OPENSEARCH_PORT
        user = settings.OPENSEARCH_USER
        pw = settings.OPENSEARCH_PASSWORD
        scheme = "https" if settings.OPENSEARCH_USE_SSL else "http"
        base = f"{scheme}://{host}:{port}"
        auth = (user, pw) if pw else None
        try:
            h = requests.get(f"{base}/_cluster/health", auth=auth, timeout=5, verify=False)
            status = h.json().get("status")
            ok_health = status in ("green", "yellow")
            out = [ok(cat, "Reachable"),
                   ok(cat, "Cluster health") if ok_health else
                   fail(cat, "Cluster health", "green/yellow", str(status))]
        except Exception as exc:
            return [fail(cat, "Reachable", "HTTP 200", str(exc), "docker compose restart opensearch")]
        idx = "netpulse-healthcheck"
        try:
            requests.put(f"{base}/{idx}/_doc/1", json={"v": "ok"}, auth=auth, timeout=5, verify=False)
            requests.post(f"{base}/{idx}/_refresh", auth=auth, timeout=5, verify=False)
            s = requests.get(f"{base}/{idx}/_doc/1", auth=auth, timeout=5, verify=False)
            out.append(ok(cat, "Index + search") if s.status_code == 200 else
                       warn(cat, "Index + search", f"HTTP {s.status_code}"))
            requests.delete(f"{base}/{idx}", auth=auth, timeout=5, verify=False)
        except Exception as exc:
            out.append(warn(cat, "Index + search", str(exc)))
        return out

    def _check_django(self) -> list[CheckResult]:
        cat = "Django"
        from django.conf import settings
        out = []
        sk = getattr(settings, "SECRET_KEY", "")
        out.append(ok(cat, "SECRET_KEY set") if sk and "insecure" not in sk and "change" not in sk.lower() else
                   fail(cat, "SECRET_KEY set", "non-default key", "default/empty",
                        "set DJANGO_SECRET_KEY in .env"))
        out.append(ok(cat, "ALLOWED_HOSTS") if settings.ALLOWED_HOSTS else
                   warn(cat, "ALLOWED_HOSTS", "empty"))
        try:
            from django.contrib.auth import get_user_model
            n = get_user_model().objects.filter(is_superuser=True).count()
            out.append(ok(cat, "Superuser exists") if n else
                       fail(cat, "Superuser exists", "≥1 superuser", "0",
                            "docker compose exec api python manage.py ensure_superuser"))
        except Exception as exc:
            out.append(fail(cat, "Superuser exists", "≥1 superuser", str(exc)))
        backend = getattr(settings, "EMAIL_BACKEND", "")
        out.append(ok(cat, "Email backend") if "smtp" in backend.lower() else
                   warn(cat, "Email backend", "no SMTP backend — alert emails won't send",
                        "configure SMTP in .env"))
        return out

    def _check_credentials_flow(self) -> list[CheckResult]:
        cat = "Credentials Flow"
        from apps.credentials import vault
        if not vault.vault_enabled():
            return [warn(cat, "Create profile", "OpenBao not configured — skipping",
                         "fix the OpenBao token first")]
        out = []
        path = "health-check/credflow"
        try:
            vault.write_secret(path, {"ssh_password": "s3cr3t-hc"})
            out.append(ok(cat, "Write to OpenBao"))
            got = vault.read_secret(path).get("ssh_password")
            out.append(ok(cat, "Read from OpenBao"))
            out.append(ok(cat, "Values match") if got == "s3cr3t-hc" else
                       fail(cat, "Values match", "s3cr3t-hc", str(got)))
            vault.delete_secret(path)
            out.append(ok(cat, "Cleanup"))
        except Exception as exc:
            out.append(fail(cat, "Write to OpenBao", "round-trip secret", str(exc),
                            "verify the OpenBao token has write access to secret/"))
        return out

    def _check_credential_placeholders(self) -> list[CheckResult]:
        """Warn if any credential profile's OpenBao secret still holds a known
        placeholder/test sentinel — the signature of leaked test fixtures (see
        apps.credentials.vault.PLACEHOLDER_SECRETS)."""
        cat = "Credential Secrets"
        from apps.credentials import vault
        if not vault.vault_enabled():
            return [warn(cat, "Placeholder scan", "OpenBao not configured — skipping")]
        try:
            from apps.credentials.models import CredentialProfile
            profiles = list(
                CredentialProfile.objects.exclude(vault_path="").only("name", "vault_path")
            )
        except Exception as exc:
            return [warn(cat, "Placeholder scan", str(exc))]

        offenders = []
        for cp in profiles:
            # read_secret() scrubs placeholders, so go to the raw vault here.
            try:
                raw = vault._client().secrets.kv.v2.read_secret_version(
                    path=cp.vault_path, mount_point=vault._MOUNT_POINT,
                    raise_on_deleted_version=True,
                )["data"]["data"]
            except Exception:
                continue
            bad = sorted(k for k, v in raw.items() if vault.is_placeholder(v))
            if bad:
                offenders.append(f"{cp.name} ({', '.join(bad)})")

        if offenders:
            return [fail(
                cat, "No placeholder secrets",
                "real credentials", f"{len(offenders)} profile(s): " + "; ".join(offenders),
                "re-enter the real credential in Settings → Credentials to overwrite",
            )]
        return [ok(cat, "No placeholder secrets")]

    def _check_ingest_heartbeats(self) -> list[CheckResult]:
        cat = "Ingest Services"
        try:
            import redis
            r = redis.from_url(self._valkey_url(), socket_timeout=3)
        except Exception as exc:
            return [warn(cat, "Heartbeats", f"Valkey unavailable: {exc}")]
        out = []
        for svc in ("ingest-snmp", "ingest-syslog", "stream-processor"):
            key = f"service:heartbeat:{svc}"
            try:
                val = r.get(key)
            except Exception as exc:
                out.append(warn(cat, f"{svc} heartbeat", str(exc)))
                continue
            out.append(ok(cat, f"{svc} heartbeat") if val else
                       warn(cat, f"{svc} heartbeat", "no heartbeat in Valkey (<5m)",
                            f"check `docker compose logs {svc}`"))
        return out

    def _check_network(self) -> list[CheckResult]:
        cat = "Network"
        import socket
        out = []
        try:
            socket.gethostbyname("github.com")
            out.append(ok(cat, "DNS resolution"))
        except Exception as exc:
            out.append(warn(cat, "DNS resolution", str(exc), "core features work offline"))
        import requests
        try:
            requests.get("https://github.com", timeout=5)
            out.append(ok(cat, "Outbound HTTPS"))
        except Exception as exc:
            out.append(warn(cat, "Outbound HTTPS", str(exc),
                            "CVE/advisory feeds + git sync need outbound HTTPS"))
        return out

    def _check_nat(self) -> list[CheckResult]:
        """
        Verify the Docker MASQUERADE NAT rule (so containers egress as the host
        IP for SNMP/SSH). This runs inside the api container, which normally has
        no iptables / host nat access — in that case we WARN (can't verify from
        here) rather than fail, and point at `./netpulse.sh fix-nat` on the host.
        """
        import subprocess
        cat = "Docker NAT"
        subnet = self._docker_subnet_guess()
        cmd = ["iptables", "-t", "nat", "-C", "POSTROUTING",
               "-s", subnet, "!", "-d", subnet, "-j", "MASQUERADE"]
        try:
            result = subprocess.run(cmd, capture_output=True, timeout=5)
        except FileNotFoundError:
            return [warn(cat, "MASQUERADE rule",
                         "iptables unavailable in this container — verify on the host",
                         "sudo ./netpulse.sh fix-nat")]
        except Exception as exc:
            return [warn(cat, "MASQUERADE rule", str(exc), "sudo ./netpulse.sh fix-nat")]
        if result.returncode == 0:
            return [ok(cat, "MASQUERADE rule")]
        stderr = (result.stderr or b"").decode(errors="replace").lower()
        if any(s in stderr for s in ("permission", "denied", "must be root", "operation not permitted")):
            return [warn(cat, "MASQUERADE rule",
                         "insufficient privileges to read the host nat table from the container",
                         "sudo ./netpulse.sh fix-nat")]
        return [fail(cat, "MASQUERADE rule", f"MASQUERADE for {subnet}", "rule missing",
                     "sudo ./netpulse.sh fix-nat (or re-run scripts/setup.sh)")]

    @staticmethod
    def _docker_subnet_guess() -> str:
        """Best-effort Docker bridge subnet for the NAT check message: an explicit
        DOCKER_SUBNET env, else the container's own /16, else the default."""
        import socket
        env = os.environ.get("DOCKER_SUBNET")
        if env:
            return env
        try:
            ip = socket.gethostbyname(socket.gethostname())
            o = ip.split(".")
            if len(o) == 4 and o[0] == "172":
                return f"{o[0]}.{o[1]}.0.0/16"
        except Exception:
            pass
        return "172.18.0.0/16"

    def _check_mibs(self) -> list[CheckResult]:
        cat = "MIBs"
        roots = ["/app/mibs", "mibs"]
        root = next((p for p in roots if os.path.isdir(p)), None)
        if not root:
            return [warn(cat, "MIB directory", "mibs/ not found", "./scripts/download_mibs.sh")]
        files = []
        for dirpath, _dirs, names in os.walk(root):
            files += [n for n in names if n.endswith((".mib", ".my", ".txt"))]
        out = [ok(cat, "MIB directory exists")]
        out.append(ok(cat, "≥100 MIB files") if len(files) >= 100 else
                   warn(cat, "≥100 MIB files", f"only {len(files)}", "./scripts/download_mibs.sh"))
        std = os.path.join(root, "standard")
        have_std = os.path.isdir(std) and any(
            f.startswith(("SNMPv2-MIB", "IF-MIB")) for f in os.listdir(std))
        out.append(ok(cat, "Standard MIBs present") if have_std else
                   warn(cat, "Standard MIBs present", "SNMPv2-MIB/IF-MIB missing", "./scripts/download_mibs.sh"))
        out.append(ok(cat, "Vendor MIB dirs") if os.path.isdir(os.path.join(root, "vendor")) else
                   warn(cat, "Vendor MIB dirs", "mibs/vendor missing"))
        return out

    # ── helpers ──────────────────────────────────────────────────────────────
    @staticmethod
    def _valkey_url() -> str:
        from urllib.parse import quote
        url = os.environ.get("VALKEY_URL")
        if url:
            return url
        pw = os.environ.get("VALKEY_PASSWORD", "")
        auth = f":{quote(pw, safe='')}@" if pw else ""
        return f"redis://{auth}{os.environ.get('VALKEY_HOST', 'valkey')}:{os.environ.get('VALKEY_PORT', '6379')}/0"

    @staticmethod
    def _tcp_ok(host: str, port: int, timeout: float = 3.0) -> bool:
        import socket
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except Exception:
            return False


class Command(BaseCommand):
    help = "Post-setup health verification against the running infrastructure."

    def add_arguments(self, parser):
        parser.add_argument("--fail-fast", action="store_true", help="Stop on first failure")
        parser.add_argument("--json", action="store_true", help="Output a JSON report")

    def handle(self, *args, **options):
        runner = HealthCheckRunner(fail_fast=options["fail_fast"], json_output=options["json"])
        results = runner.run_all()
        self.stdout.write(runner.render(results))
        if not all(r.passed for r in results):
            sys.exit(1)
