# Supply Chain & CI Security

This page describes what runs in CI to keep the codebase and its dependencies
honest. It is deliberately specific about what is **enforced in CI** versus what
is a manual or on-demand gate, because the difference matters for a reviewer.

## Workflows in the repository

Three workflows live in `.github/workflows/`:

### `api-tests.yml` — the test gate

The enforced correctness gate. Runs the full backend test suite:

- **Triggers:** push to `main` and pull requests, path-filtered to
  `services/api/**` (and the workflow file itself).
- **Steps:** set up Python 3.13, install the api `requirements.txt` plus the
  system packages the image needs (libpq, nmap, iputils, …), then
  `python -m pytest -q` against the in-memory SQLite test settings.

This is the gate that must be green before merge.

### `security-checks.yml` — exception-exposure guard (CWE-209)

- **Triggers:** push to `main` and pull requests, path-filtered to
  `services/api/apps/**/views.py` and the guard script.
- **Step:** runs `scripts/check_exception_exposure.py`.

`check_exception_exposure.py` is an AST-based guard: it flags any
`Response(...)` / `JsonResponse(...)` / `HttpResponse(...)` returned from inside
an `except ... as <var>:` handler that references the exception variable, unless
it is funneled through an approved sanitizer (`safe_detail`,
`internal_error_response`, `log_internal_error`). This prevents leaking internal
exception text to API clients. The same check runs as a pre-commit hook
(`.pre-commit-config.yaml`) and is asserted in the test suite
(`tests/test_security.py`).

### `build-agent.yml`

Builds the Go monitoring-agent binaries. It carries least-privilege
`permissions` and is not a security-scanning job.

## Dependency updates (Dependabot)

`.github/dependabot.yml` configures **npm** updates for the frontend
(`/services/frontend`) on a **weekly** schedule, ignoring Vite major bumps.

## What is NOT enforced in CI

These were verified absent at the time of writing. Some are referenced elsewhere
in the project (CLAUDE.md, the pre-release checklist) as having been used
ad hoc, but there is no committed CI configuration for them:

- **No CodeQL workflow file** exists in `.github/workflows/`. CodeQL alerts have
  historically been triaged for this repo, which is consistent with GitHub's
  "default setup" (configured in repo settings, not as an in-repo workflow) —
  but that cannot be verified from the source tree, so it is not claimed here as
  a repo-defined control.
- **Dependabot does not cover Python (`pip`) or GitHub Actions** — only npm.
- **No `bandit`, `pip-audit`, `safety`, `trivy`, `docker scout`, or
  `gitleaks`/`trufflehog` step** runs in CI. These are listed in the
  pre-production security-audit checklist (see the "Pre-Release / Production
  Checklist" in `CLAUDE.md`) as tools to run as a release gate, not as
  continuous CI jobs.

Closing these gaps — at minimum CodeQL-as-code and pip/actions Dependabot
ecosystems — is reasonable future hardening.
