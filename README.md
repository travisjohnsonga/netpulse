# NetPulse 🔮

> A push-first, open source network intelligence platform built for modern infrastructure.

[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.13-green.svg)](https://python.org)
[![Django](https://img.shields.io/badge/Django-6.0-green.svg)](https://djangoproject.com)
[![Docker](https://img.shields.io/badge/Docker-Compose-blue.svg)](https://docker.com)
[![Status](https://img.shields.io/badge/Status-Early%20Development-orange.svg)]()

A push-first network intelligence platform handling gRPC/gNMI streaming telemetry,
config compliance, CVE intelligence, lifecycle management, log anomaly detection,
and unified risk scoring — all open source, all containerized.

See full documentation in [docs/](docs/) as the project develops.

## Quickstart

```bash
cp .env.example .env        # review values; change all default credentials
docker compose up -d
```

### Secrets (OpenBao)

OpenBao runs with **persistent file storage** and is initialised + unsealed
automatically on first start — no manual `operator init`/`unseal` needed:

- On first `docker compose up`, the `api` service initialises OpenBao
  (1 unseal share) and writes the unseal key + root token to
  `/openbao/data/.init_keys` (mode `600`) on the `openbao-data` volume.
- On every later start, the `api` auto-unseals from that stored key, so
  secrets (device credentials, git tokens, feed API keys) **persist across
  restarts**.

> ⚠️ `/openbao/data/.init_keys` holds your unseal key and root token. It lives
> on the Docker volume and is git-ignored — never commit it, and back it up
> securely (losing it means you cannot unseal OpenBao after a restart).

`OPENBAO_TOKEN` should normally be left **blank** in `.env`; services read the
root token from the keys file. Set it only to use an externally-managed token.

Upgrading from an older dev-mode deployment? The `openbao-init` one-shot service
fixes volume ownership automatically; if you hit a permission error, recreate
the `openbao-data` volume.
