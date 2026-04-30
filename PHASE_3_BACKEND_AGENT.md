# Phase 3 — Backend, ingestion, vector search, and basic agent

Deliver the core product functionality on the Compose stack. After this phase, raggo satisfies the acceptance criteria in `AGENTS.md` end-to-end on a developer laptop.

## Scope

### Backend modules

The backend layout in this repo, which Phase 3 work builds on (do **not** create parallel entrypoints under `backend/app/` at the top level — shared logic lives under `backend/app/core/`):

```
backend/app/
  __init__.py
  main.py                  # FastAPI entrypoint
  core/
    __init__.py
    config.py              # settings / env
    database.py            # SQLAlchemy engine + session
    domain.py              # domain-pack registry + loader
    ingestion.py           # chunk + embed + upsert
    vector_search.py       # pgvector similarity search
    model_clients.py       # embedding-model + generation-model HTTP clients
    safe_sql.py            # parameterised, allowlisted SQL tool runner
    schemas.py             # cross-domain Pydantic schemas
    agent/                 # basic RAG agent (intent → tools → grounded answer)
```

Domain packs live alongside under `backend/app/domains/<pack>/`. The current convention (already implemented for `flights` and `support_tickets`) is:

```
backend/app/domains/<pack>/
  __init__.py              # pack registration
  init.sql                 # schema bootstrap applied on first boot
  models.py                # SQLAlchemy models for this pack
  seed.py                  # seed-data generator
  prompts.py               # system + tool prompts
  sql_tools.py             # parameterised, limit-bounded SQL tools
  intent_rules.py          # intent classification rules used by the agent
```

Phase 3 keeps this structure as-is. Any rename or reshape of the pack layout (e.g. introducing `schema.py` or a `fixtures/` subdirectory) is an explicit refactor that must land in Phase 7 alongside the interface refactor described there, not silently here.

### Ingestion pipeline

- Chunk flight logs and incident records with deterministic, configurable chunking.
- Embed via the `embedding-model` container over the internal `model_net`.
- Upsert into pgvector with metadata (flight id, timestamp, source table, chunk index).
- Idempotent re-ingestion: re-running ingestion does not duplicate rows.

### Vector search

- `/search` endpoint accepting query text plus optional filters (date range, flight id, severity).
- Returns top-k results with similarity scores and source metadata.
- Basic reranking by score with a configurable `k` and a hard server-side cap.

### Safe SQL tools

Implement the allowlisted set called out in `AGENTS.md`:

- `get_delayed_flights`
- `get_incidents_by_severity`
- `get_flights_by_airport`
- `get_logs_by_flight`
- `get_top_delay_airports`

All queries parameterised. All queries have hard `LIMIT`s. No string-built SQL anywhere in the codebase.

### Basic RAG agent

`/query` endpoint:

1. Receives a natural-language question.
2. Classifies intent (structured vs semantic vs hybrid).
3. Picks safe SQL tools, vector search, or both.
4. Retrieves evidence.
5. Builds a grounded prompt and calls the local generation model.
6. Returns `{ answer, evidence, agent_trace }` where `agent_trace` includes selected strategy, tools used, vector queries used, retrieved IDs.

The agent must never invent data. Final answers cite only retrieved evidence.

### Frontend

- Dashboard with live stats from the backend (counts, recent incidents, top delays).
- Add-flight-log form posting to the backend.
- Vector search page.
- Agent chat page rendering answer, evidence list, and an expandable agent trace.

### Domain pack contract tests

A test suite (`backend/tests/contract/`) that every domain pack must pass:

- Seed loads cleanly into a fresh DB.
- Ingestion produces a non-zero number of embeddings.
- Vector search returns expected hits for canned queries shipped with the pack (fixtures committed under `backend/tests/contract/fixtures/<pack>/` rather than inside the pack, to keep packs free of test-only files).
- Agent answers a fixture question with grounded evidence and emits a valid trace.

The `flights` pack (already in-repo) is the reference implementation that backfills this contract; the `support_tickets` pack must be brought up to the same contract as part of Phase 3 wrap-up.

## Deliverables

- All modules above implemented with pytest unit tests.
- End-to-end tests covering ingest → search → agent query.
- Frontend components with vitest unit tests and at least one Playwright E2E test for the agent chat happy path.
- `docs/agent.md` describing intent classification, tool selection, and the trace schema.
- `docs/domain-packs.md` describing the contract.

## Out of scope

- Helm chart and Kubernetes install — Phase 4.
- Adding **new** domain packs beyond the two already in-repo (`flights`, `support_tickets`) — Phase 7.
- Advanced agent features (memory, multi-step planning, human approval gates) — explicitly deferred per `AGENTS.md`.

## Exit criteria

- All `AGENTS.md` acceptance criteria pass on a fresh `docker compose up --build`.
- Contract test suite passes for both the `flights` and `support_tickets` domain packs.
- `/query` returns answer, evidence, and trace for at least the example questions documented in `README.md`.
- No raw SQL string interpolation exists in the codebase (verified by a lint rule or test).
- Coverage thresholds defined in Phase 2 are met by the new code.
