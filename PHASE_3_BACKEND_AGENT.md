# Phase 3 — Backend, ingestion, vector search, and basic agent

Deliver the core product functionality on the Compose stack. After this phase, raggo satisfies the acceptance criteria in `AGENTS.md` end-to-end on a developer laptop.

## Scope

### Backend modules

Implement the modules already enumerated in `AGENTS.md`:

```
backend/app/
  main.py
  config.py
  database.py
  models.py
  schemas.py
  seed.py
  ingestion.py
  vector_search.py
  agent.py
  model_clients.py
  safe_sql_tools.py
```

Plus the domain abstraction introduced in this phase:

```
backend/app/domains/
  __init__.py            # registry
  flight_ops/
    __init__.py
    schema.py            # SQLAlchemy models for the domain
    seed.py              # seed-data generator
    prompts.py           # system + tool prompts
    sql_tools.py         # safe SQL tool definitions
    fixtures/            # contract-test fixtures
```

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
- Vector search returns expected hits for canned queries in `fixtures/`.
- Agent answers a fixture question with grounded evidence and emits a valid trace.

The flight-ops pack is the first implementation of this contract.

## Deliverables

- All modules above implemented with pytest unit tests.
- End-to-end tests covering ingest → search → agent query.
- Frontend components with vitest unit tests and at least one Playwright E2E test for the agent chat happy path.
- `docs/agent.md` describing intent classification, tool selection, and the trace schema.
- `docs/domain-packs.md` describing the contract.

## Out of scope

- Helm chart and Kubernetes install — Phase 4.
- Multiple domain packs — Phase 7 (only flight-ops here).
- Advanced agent features (memory, multi-step planning, human approval gates) — explicitly deferred per `AGENTS.md`.

## Exit criteria

- All `AGENTS.md` acceptance criteria pass on a fresh `docker compose up --build`.
- Contract test suite passes for the flight-ops domain pack.
- `/query` returns answer, evidence, and trace for at least the example questions documented in `README.md`.
- No raw SQL string interpolation exists in the codebase (verified by a lint rule or test).
- Coverage thresholds defined in Phase 2 are met by the new code.
