# Supply Chain & CI Security

This page describes what runs in CI to keep the codebase and its dependencies
honest. It is deliberately specific about what is **enforced in CI** versus what
is report-only or a manual/on-demand gate, because the difference matters for a
reviewer.

## Workflows in the repository

Three workflows live in `.github/workflows/`, plus CodeQL via GitHub default
setup (below).

### `api-tests.yml` — the test gate

The enforced correctness gate. Runs the full backend test suite:

- **Triggers:** push to `main` and pull requests, path-filtered to
  `services/api/**` (and the workflow file itself).
- **Steps:** set up Python 3.13, install the api `requirements.txt` plus the
  system packages the image needs (libpq, nmap, iputils, …), then
  `python -m pytest -q` against the in-memory SQLite test settings.

This is the gate that must be green before merge.

### `security-checks.yml` — exception-exposure guard + Python security scan

Two jobs, path-filtered to `services/api/**`, the guard script, the bandit
baseline, and the workflow file (push to `main` and pull requests):

**`exception-exposure` (CWE-209).** Runs `scripts/check_exception_exposure.py`,
an AST-based guard that flags any `Response(...)` / `JsonResponse(...)` /
`HttpResponse(...)` returned from inside an `except ... as <var>:` handler that
references the exception variable, unless it is funneled through an approved
sanitizer (`safe_detail`, `internal_error_response`, `log_internal_error`). This
prevents leaking internal exception text to API clients. The same check runs as a
pre-commit hook (`.pre-commit-config.yaml`) and is asserted in the test suite
(`tests/test_security.py`).

**`python-security-scan`.** Pinned tool versions (`pip-audit==2.9.0`,
`bandit==1.8.6`):

- **`pip-audit` — BLOCKING.** Audits `services/api/requirements.txt` for known
  CVEs. The backend dependency set is clean today, so a newly-disclosed CVE in a
  pinned dependency fails the build and gets triaged rather than ignored.
- **`bandit` — REPORT-ONLY (baselined).** Static security lint over
  `services/api/apps`, run against a committed baseline
  (`.bandit-baseline.json`). The existing findings (6 High / 5 Medium / 51 Low
  at the time the job was added — predominantly `verify=False` in the internal
  `run_health_checks` probe, the SSRF-guarded NetBox `urlopen`, and cert-parse
  `try/except`) are grandfathered by the baseline, so the step passes today and
  surfaces only **new** findings. It is `continue-on-error` (non-blocking) until
  the baseline is triaged; dropping that flag turns it into a hard ratchet on
  regressions.

### `build-agent.yml`

Builds the Go monitoring-agent binaries. It carries least-privilege
`permissions` and is not a security-scanning job.

### CodeQL (GitHub default setup)

CodeQL static analysis runs via GitHub **default setup** — configured in the
repository's **Settings → Code security**, not as an in-repo workflow file. It
analyzes **`actions`, `go`, `javascript-typescript`, and `python`** (verifiable
via the `code-scanning/default-setup` API) on pushes, pull requests, and a weekly
schedule; results surface as the `CodeQL` / `Analyze (<language>)` checks and in
the repository's code-scanning alerts.

An in-repo *advanced* CodeQL workflow was considered but intentionally **not**
adopted: advanced setup is mutually exclusive with default setup (GitHub won't
process advanced results while default setup is enabled), so committing one would
require disabling default setup first and then maintaining the workflow by hand.
Default setup gives the same four-language coverage with no config to maintain.

## Dependency updates (Dependabot)

`.github/dependabot.yml` runs weekly across three ecosystems, with minor/patch
updates grouped per ecosystem and nothing auto-merged:

- **npm** — `services/frontend` (Vite major bumps ignored — they require a
  coordinated plugin + lockfile bump).
- **pip** — the Django backend (`services/api`) and every ingest/stream service
  with a `requirements.txt` (`ingest-snmp`, `ingest-grpc`, `ingest-syslog`,
  `ingest-flow`, `ingest-otlp`, `ingest-api-poller`, `stream-processor`).
- **github-actions** — the action versions pinned in `.github/workflows/`.

## What is NOT (yet) enforced in CI

- **`bandit` is report-only**, not blocking — the existing findings in
  `services/api/apps` need triage (justify with `# nosec` or fix) before the
  baseline can become a hard gate.
- **No `safety`, `trivy`, `docker scout`, or `gitleaks`/`trufflehog` step** runs
  in CI. These remain release-gate tools in the pre-production security-audit
  checklist (see the "Pre-Release / Production Checklist" in `CLAUDE.md`), not
  continuous CI jobs.
- **Go modules are not yet covered by Dependabot** — `agent/go.mod` could be
  added as a `gomod` ecosystem in a follow-up.
