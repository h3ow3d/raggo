# Phase 7 — Domain pack hardening, interface refactor, and a third pack

The repo already ships two domain packs (`flights`, `support_tickets`). Phase 7's job is to (a) harden the contract those packs share, (b) land the one-time interface refactor that the second pack already exposes as needed, and (c) prove the abstraction by adding a **third** pack under the cleaned-up interface.

## Scope

### Interface refactor (lands first)

- Audit `backend/app/core/domain.py` and the existing pack contracts (`flights`, `support_tickets`) for assumptions that leaked from the original `flights` implementation.
- Rewrite the pack interface so both existing packs implement it cleanly (typical leaks to look for: `flights`-specific column names in shared agent code, prompts hard-coded to flight terminology, intent-rule shapes that only fit one schema).
- This refactor lands **before** the third pack is added. Do not stack new-pack work on an unstable interface.
- Update `docs/domain-packs.md` with the final interface.

### Third domain pack

- Add a third pack under `backend/app/domains/<name>/` using the same on-disk layout already used by `flights` and `support_tickets` (`__init__.py`, `init.sql`, `models.py`, `prompts.py`, `seed.py`, `sql_tools.py`, `intent_rules.py`).
- Candidate domains: maritime-ops, industrial-maintenance, rail-operations — pick one and document the choice in `docs/domain-packs.md`.
- The pack ships its own:
  - SQLAlchemy models and `init.sql` schema bootstrap
  - seed data generator producing realistic medium-sized data
  - prompts (system + tool descriptions)
  - safe SQL tools, parameterised and limited
  - intent rules
- Contract-test fixtures for the new pack land alongside the existing pack fixtures under `backend/tests/contract/fixtures/<pack>/`.

### Domain selection at install

- A single deployment runs **one** domain pack at a time (two domain packs do not co-exist in one DB; that is an explicit non-goal).
- **Compose:** the existing `RAGGO_DOMAIN` env var continues to drive selection. An optional `docker-compose.<domain>.yml` overlay can carry any pack-specific config (e.g. a different seed-count default).
- **Helm:** `values-domain-<domain>.yaml` overlay sets `domain.name` and any pack-specific values. The chart's `values.schema.json` is updated to enumerate the supported domain names (`flights`, `support_tickets`, plus the new pack).
- The backend reads `RAGGO_DOMAIN` at boot and resolves the pack via the registry; an unknown or missing domain fails fast with a clear error.

### Contract tests

- The contract suite from Phase 3 runs against **every** bundled domain pack (now three) on every PR. A regression in any pack blocks merge.
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

- All three domain packs (`flights`, `support_tickets`, and the new pack) pass the contract test suite.
- `RAGGO_DOMAIN=<new> docker compose up --build` (optionally with a `docker-compose.<new>.yml` overlay) brings up a working stack for the new domain.
- `helm install raggo ./helm/raggo -f helm/raggo/values-domain-<new>.yaml` brings up a working stack on kind.
- The domain interface refactor is documented in the changelog and reflected in `docs/domain-packs.md`. Both pre-existing packs were updated as part of the refactor commit; no pack carries pre-refactor shims.
- An attempt to install with an unknown `domain.name` fails at chart-render time (schema) and at backend boot (defensive check).
- README clearly states the one-domain-per-deployment rule and the supported domain list.
