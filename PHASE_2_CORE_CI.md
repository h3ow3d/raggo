# Phase 2 — Core CI (per-PR quality gates)

Lock in the cheap, fast quality gates so every subsequent phase ships behind a green check. The goal is for a PR to fail in under ten minutes if it breaks lint, types, tests, Compose validity, or commits a secret.

## Scope

- **`ci.yml`** workflow runs on every pull request and on push to the default branch.
- **Backend job:**
  - `ruff check` and `ruff format --check`.
  - `mypy` with the project config.
  - `pytest` with coverage; fail if coverage on changed files drops below the threshold defined in `pyproject.toml`.
  - Tests run against a Postgres + pgvector service container.
- **Frontend job:**
  - `eslint`.
  - `tsc --noEmit`.
  - `vitest run` with coverage.
  - `npm audit --omit=dev` (advisory; does not fail the build in this phase).
- **Compose validation job:**
  - `docker compose -f docker-compose.yml config`.
  - `docker compose -f docker-compose.yml -f docker-compose.gpu.yml config`.
- **Shell job:** `shellcheck` over all `*.sh` files.
- **Secrets job:** `gitleaks detect --redact` on the PR diff. Native GitHub secret scanning remains enabled in addition.
- **Commit hygiene job:** Conventional Commits linter (e.g. `commitlint`) on the PR title and on each commit message.
- **CodeQL** (Python + JS/TS) runs on a weekly schedule. Not gating yet; promoted to PR-gating in Phase 6.
- **Branch protection on the default branch:**
  - All `ci.yml` jobs must pass.
  - Linear history (squash-merge only).
  - Signed commits required.
  - At least one approving review.
  - `CODEOWNERS` review required for changes under `helm/`, `bundle/`, `.github/workflows/`, and `backend/app/domains/` (the last one preps for Phase 3/7).
- **Caching:** `actions/setup-python`, `actions/setup-node` with built-in caching; `pip` and `npm` caches keyed on lockfiles.
- **Concurrency:** `concurrency: pr-${{ github.ref }}` with `cancel-in-progress: true` for PR runs.

## Deliverables

- `.github/workflows/ci.yml`.
- `.github/workflows/codeql.yml` (scheduled).
- `.github/CODEOWNERS`.
- `commitlint.config.js` (or equivalent).
- Documented branch-protection settings in `docs/branch-protection.md` (so settings can be restored if lost).

## Out of scope

- Vulnerability gating on container images — Phase 6.
- Helm chart CI — Phase 4 introduces it as a separate workflow.
- Release / publishing logic — Phase 6.

## Exit criteria

- A PR that introduces a lint error, a type error, a failing test, an invalid Compose file, a shell-check error, a leaked secret, or a non-Conventional-Commits message **fails** the corresponding job.
- Median PR check duration is under 10 minutes on a representative change.
- Default branch protection is enabled with the rules above and is documented.
- A scheduled CodeQL run has completed successfully at least once and any findings are triaged.
