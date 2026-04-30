# Phase 7 — Domain pack expansion

Validate the domain abstraction introduced in Phase 3 by adding a second domain pack alongside flight-ops, and keep both passing the same contract on every PR.

## Scope

### Second domain pack

- Add a second pack under `backend/app/domains/<name>/` (candidate domains: maritime-ops, industrial-maintenance, rail-operations — pick one and document the choice in `docs/domain-packs.md`).
- The pack ships its own:
  - SQLAlchemy schema additions (or an alternative DB schema selected at install time)
  - seed data generator producing realistic medium-sized data
  - prompts (system + tool descriptions)
  - safe SQL tools, parameterised and limited
  - contract-test fixtures

### Domain selection at install

- A single deployment runs **one** domain pack at a time (two domain packs do not co-exist in one DB; that is an explicit non-goal).
- **Compose:** `docker-compose.<domain>.yml` overlay sets the `RAGGO_DOMAIN` env var and any domain-specific config.
- **Helm:** `values-domain-<domain>.yaml` overlay sets `domain.name` and any pack-specific values. The chart's `values.schema.json` is updated to enumerate supported domain names.
- The backend reads `RAGGO_DOMAIN` at boot and resolves the pack via the registry; an unknown or missing domain fails fast with a clear error.

### Refactor expectations

- It is expected (and acceptable) that the domain interface introduced in Phase 3 needs **one** refactor during this phase. Land that refactor explicitly, not by accident:
  - Identify the abstractions that leaked flight-ops assumptions.
  - Rewrite the interface so both packs implement it cleanly.
  - Do not add a third pack until the refactor lands.
- Update `docs/domain-packs.md` with the final interface.

### Contract tests

- The contract suite from Phase 3 runs against **every** bundled domain pack on every PR. A regression in either pack blocks merge.
- Add cross-pack tests that assert the registry behaves correctly (correct pack loaded, unknown pack rejected, switching domains in tests is hermetic).

### Frontend

- Domain-aware labels and dashboard widgets driven by metadata exposed by the backend (`/domain/info`).
- No domain-specific UI code in the core frontend; per-domain copy and field labels come from a manifest the pack ships.

### Documentation

- `docs/authoring-a-domain-pack.md`: end-to-end walkthrough (schema, seed, prompts, SQL tools, fixtures, manifest, registering the pack, CI implications).
- README updated with the list of supported domains and how to choose one at install.

## Out of scope

- Multi-tenant or multi-domain single-deployment installs (intentionally not supported).
- A marketplace / dynamic plug-in loading mechanism — packs are statically registered in the codebase.
- Operational maturity (deployment registry, support policy) — Phase 8.

## Exit criteria

- Two domain packs (flight-ops + the new pack) both pass the contract test suite.
- `docker compose -f docker-compose.yml -f docker-compose.<new>.yml up --build` brings up a working stack for the new domain.
- `helm install raggo ./helm/raggo -f helm/raggo/values-domain-<new>.yaml` brings up a working stack on kind.
- The domain interface refactor is documented in the changelog and reflected in `docs/domain-packs.md`.
- An attempt to install with an unknown `domain.name` fails at chart-render time (schema) and at backend boot (defensive check).
- README clearly states the one-domain-per-deployment rule.
