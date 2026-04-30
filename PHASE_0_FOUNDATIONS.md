# Phase 0 — Foundations

Establish the repository scaffolding, baseline tooling, and spec alignment that every later phase depends on. No production-grade functionality lands here; the goal is a healthy empty stack and a green local quality bar.

## Scope

- Repository directory layout for backend, frontend, models, db, helm chart, air-gap bundle, workflows, and docs.
- Base `docker-compose.yml` matching the architecture and security boundaries in `PROJECT_SPEC.md`:
  - Five services: `backend`, `frontend`, `postgres`, `embedding-model`, `generation-model`.
  - Internal `model_net` (Docker `internal: true`), `database_net`, `frontend_net`.
  - No host port exposure for `postgres`, `embedding-model`, `generation-model`.
- Existing `docker-compose.gpu.yml` retained and validated.
- Postgres + pgvector image with the existing `db/init.sql` (extension enablement) plus the per-domain `backend/app/domains/<pack>/init.sql` bootstrap that the backend already runs at startup. **No migration framework (Alembic, Flyway, etc.) is introduced in this phase** — schema management stays init.sql-driven. A decision to adopt a migration tool would be a follow-up tracked separately and reflected back into the spec before any phase doc references it.
- Seed-data generators per domain pack (already present for `flights` and `support_tickets`) producing a realistic medium-sized corpus.
- FastAPI backend skeleton with `/health` endpoint, config module, database connection module.
- Vite + React + TypeScript frontend skeleton with a placeholder dashboard route.
- Pre-commit hooks: `ruff` (lint+format), `mypy`, `eslint`, `prettier`, `shellcheck`, `gitleaks`. All hooks must pass on a clean checkout.
- Spec alignment: this phase commits the changes already made to `PROJECT_SPEC.md` (Section 21) and `AGENTS.md` removing the "no Kubernetes" line.

## Deliverables

- Directory tree present with placeholder `README.md` per top-level directory where useful.
- `docker-compose.yml` that passes `docker compose config`.
- Postgres container starts and applies `db/init.sql` (pgvector extension) via the standard `docker-entrypoint-initdb.d` mechanism. The backend then applies the active domain pack's `init.sql` and runs its seed generator on first boot. Re-runs are idempotent.
- Backend `/health` returns 200 from inside the network.
- Frontend serves a placeholder page on the documented host port.
- `.pre-commit-config.yaml` and matching tool configs (`pyproject.toml`, `.eslintrc.*`, `.prettierrc`, `.shellcheckrc`, `.gitleaks.toml`).
- `CODEOWNERS` skeleton.

## Out of scope

- Ingestion, embeddings, vector search, agent logic — these arrive in Phase 3.
- GHCR publishing, CI workflows, Helm chart — Phases 1, 2, 4.
- Domain pack abstraction — introduced in Phase 3 and expanded in Phase 7.

## Exit criteria

- `docker compose up --build` brings the full stack up; all containers report healthy.
- Postgres is reachable only from `backend` (verified by attempting connection from the host and from `frontend` and seeing it fail).
- Model services have no published host ports and no outbound internet at runtime (verified by `docker inspect` and a network test).
- `pre-commit run --all-files` exits 0.
- `PROJECT_SPEC.md` Section 21 and the updated `AGENTS.md` are committed; no contradictions remain in the spec.
- The repository builds from a fresh clone with no manual steps beyond `cp .env.example .env` and `docker compose up --build`.
