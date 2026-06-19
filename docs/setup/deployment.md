# Deployment & Setup

Operational detail trimmed out of `CLAUDE.md`. NAT specifics live in
[`nat.md`](./nat.md).

## First-run

```bash
cp .env.example .env                    # fill in all change-me values
cp docker-compose.override.yml.example docker-compose.override.yml
./scripts/setup.sh                      # interactive — writes .env (never commits)
docker compose up -d
```

Copy `.env.example` to `.env` and fill in every `change-me` value before running
any service.

### Admin credentials (dev)
- The Django admin password is set during `scripts/setup.sh` (min 12 chars,
  complexity-checked).
- The configured value lives in `.env` (`DJANGO_SUPERUSER_PASSWORD`) after setup —
  never commit real passwords. Do NOT document actual passwords anywhere in the repo.

### External integration credentials (in `.env`)
NVD API key (CVE feed) · Cisco PSIRT client ID/secret (Cisco advisories) ·
SMTP / Slack / PagerDuty (alerting).

## scripts/setup.sh (implemented)

Interactive first-run script, run once after clone, before `docker compose up`.
Idempotent — re-run to update specific values. Colorized output.

Prompts for: platform hostname/domain (SSL SANs), `COLLECTOR_IP`, timezone;
Django admin user/password; PostgreSQL / OpenBao root+unseal / NATS / InfluxDB /
OpenSearch / Valkey passwords (auto-generate option via `openssl rand -base64 32`);
optional NVD key, Cisco PSIRT, SMTP. Reads `.env.example` as template, writes
`.env`. Security checks: warns on default passwords / running as root / ports
80-443 in use; checks Docker + Compose installed, ≥4GB RAM, ≥20GB disk. Offers to
`docker compose pull` + `up -d` and prints access URLs.

After `.env` is written it can pull images and start the stack, then prints:
`spane is available at http://{COLLECTOR_IP}` and API docs at `:8000/api/docs/`.

### First login

The seeded admin starts with the fixed default password **`spane1!`** and is
flagged `must_change_password` — the UI **forces a password change on first
login** before anything else is accessible. setup.sh prints (and saves to
`~/netpulse-credentials.txt`, mode 0600) the URL / username / password.

### Host IP for collectors (`NETPULSE_HOST_IP`)

Inside a container, IP auto-detection returns the *container* IP (172.18.x), not
the host IP that devices must send telemetry to. setup.sh therefore detects the
real host IP on the host (before the stack starts) and writes it to
`NETPULSE_HOST_IP` in `.env`; the local collector and generated device configs
use it. Override it in `.env` if auto-detection is wrong. (`register_local_collector`
also self-heals a stored 172.16.0.0/12 collector IP.)

## Development workflow

Application code is **baked into the image** (`COPY . .`, no source bind mount), so
**backend changes require an image rebuild before they run.** Editing a file on the
host does not update the running service.

All api-context services get their own image. After a backend change:

```bash
./netpulse.sh rebuild-api        # rebuild api images + recreate with --no-deps
./netpulse.sh rebuild-frontend
```

`rebuild-api` recreates these with `--no-deps` (infra untouched): api websocket
config-manager scheduler alert-engine cve-engine lifecycle-engine security-engine
stream-processor check-engine reachability-monitor.

Migrations run automatically on api startup (entrypoint `migrate --noinput`).

### Tests
Run in-container against in-memory SQLite (no external DB). `manage.py test`
delegates to pytest (`config/test_runner.py`).

```bash
docker compose exec api python -m pytest -q
docker compose exec api python -m pytest tests/test_checks.py -q
```

## OpenBao initialization (one-time)

```bash
docker compose exec openbao bao operator init     # save keys + root token securely
docker compose exec openbao bao operator unseal   # run 3× with different unseal keys
```

## systemd boot service

spane runs as a systemd service (`/etc/systemd/system/netpulse.service`,
`Requires=docker.service`, `WorkingDirectory=/home/<user>/netpulse`):

```bash
sudo systemctl {start,stop,restart,status,enable,disable} netpulse
```

After reboot, services start automatically via `docker compose up -d`. Run
`./scripts/setup.sh` again only if OpenBao was wiped (factory reset / volume delete).

## Production watchdog

Install the watchdog to automatically recover from service failures:

```bash
./netpulse.sh install-watchdog
```

The watchdog (`scripts/watchdog.sh`) runs every 5 minutes via cron and:

- starts/restarts any stopped or `unhealthy` critical container
  (api, postgres, openbao, valkey, nats, influxdb, opensearch);
- restarts the api if `GET /api/health/` isn't `ok`, and if it still fails,
  checks OpenBao's seal status (via the container — OpenBao has no host port)
  and unseals it with `init_openbao`;
- preemptively restarts the api if its open file-descriptor count runs away
  (a backstop to gunicorn's `--max-requests` worker recycling).

It logs to `/var/log/spane-watchdog.log` (rotated daily, 14 days). `setup.sh`
offers to install it on first run.

```bash
./netpulse.sh watchdog-status        # cron state + recent log
tail -f /var/log/spane-watchdog.log  # live log
./netpulse.sh remove-watchdog        # uninstall
```

**Resilience defaults baked into the stack:** every app service sets
`ulimits.nofile=65536` (via the `x-app-ulimits` compose anchor) so a low host
limit can't starve it of file descriptors, and the api's gunicorn workers
recycle after ~1000 requests (`--max-requests`/`--max-requests-jitter`) to cap
slow leaks.

## Data persistence

- **Development**: named Docker volumes (current).
- **Production**: bind mounts to `${DATA_DIR:-/opt/netpulse/data}/` —
  `postgres`, `influxdb`, `opensearch`, `valkey`, `nats`, `openbao` subdirs. Add
  `DATA_DIR=/opt/netpulse/data` to `.env`.
- Keep all databases IN Docker — not external. Bind mounts give full data
  accessibility without complexity.
- **Backup**: `docker compose stop` → `tar DATA_DIR` → `docker compose start`.
  OpenBao data is most critical — back it up separately. Document restore in
  `docs/deployment/backup-restore.md`.

## Monorepo + multiple compose files (decided 2026-06-03)

One repo (`travisjohnsonga/netpulse`). `docker-compose.yml` = full stack (default);
`docker-compose.collector.yml` = future collector. `setup.sh` will ask deployment
role. Shared ingest service images, no sync issues.
