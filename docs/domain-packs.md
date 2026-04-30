# Domain packs

A *domain pack* is a self-contained Python package under
`backend/app/domains/<name>/` that teaches `raggo` how to ingest, search,
and answer questions about a specific dataset. The two packs that ship
with the project are `flights` (default) and `support_tickets`. Both
packs expose the same contract, exercised by `backend/tests/contract/`.

This document describes the contract, the directory layout, and the
walkthrough you'll follow when adding a new pack in Phase 7.

## Directory layout

Every pack contains the following files:

```
backend/app/domains/<name>/
  __init__.py        # registers the DomainPack instance
  init.sql           # idempotent DDL: tables, vector column, indexes
  models.py          # SQLAlchemy declarative models for the pack
  schemas.py         # (optional) pydantic schemas for pack-specific endpoints
  seed.py            # deterministic seeder for the primary tables
  prompts.py         # prompt-construction helpers for the pack
  intent_rules.py    # IntentRule list + default plan
  sql_tools.py       # SafeSqlTool definitions registered with the registry
```

These files **must not** import test fixtures. Per the Phase 3 spec,
fixtures live under `backend/tests/contract/fixtures/<pack>/` — not
inside the pack — to keep the runtime image free of test data.

## File contracts

### `__init__.py`

Exports a single `DomainPack` instance through `get_pack()`. The pack
exposes:

- `name` — pack identifier matching the directory name.
- `display.title`, `display.version`, `display.description` — surfaced
  by `/domain` and the dashboard.
- `sqlalchemy_base` — Base class shared by every model in the pack so
  the test infrastructure can `metadata.drop_all` exactly the pack's
  tables.
- `embeddable_resources: list[EmbeddableResource]` — at least one entry
  describing the table holding the embedding vector. Each entry names
  the model, the text column to embed, and the embedding column to
  write into.
- `safe_sql_registry` — `SafeSqlRegistry` populated from
  `sql_tools.py`.
- `intent_rules`, `default_intent_plan` — see `docs/agent.md`.
- `prompts.evidence_projection` — function `(evidence_item) -> dict`
  used by the prompt builder.
- `seed(session, *, force=False)` — idempotent seeder; honours
  environment variables like `SEED_FLIGHT_COUNT` for deterministic
  test sizing.
- `init_sql_path` — absolute path to `init.sql`.

### `init.sql`

Idempotent DDL. Always uses `CREATE TABLE IF NOT EXISTS`,
`CREATE INDEX IF NOT EXISTS`, and `CREATE EXTENSION IF NOT EXISTS vector`.
The contract test suite drops and re-runs this file per test. IVFFlat
indexes are dropped during contract tests because they silently miss
hits at small data sizes — the production image keeps them.

### `models.py`

Standard SQLAlchemy declarative models. The embeddable model must
declare:

- a `vector(N)` column (default dim 384 to match
  `sentence-transformers/all-MiniLM-L6-v2`),
- a `embedding_model TEXT` column,
- an `embedding_dim INTEGER` column,
- an `embedded_at TIMESTAMPTZ` column,

so the ingestion pipeline has somewhere to record provenance.

### `seed.py`

A pure-Python deterministic seeder. **Must** be idempotent (a re-run on
an already-seeded DB is a no-op). It must read its row counts from env
variables when those exist so the contract test suite can ask for a
small dataset (e.g. `SEED_FLIGHT_COUNT=40`). Tests assert that running
the seeder twice produces the same row count.

### `intent_rules.py`

Provides:

- `INTENT_RULES: list[IntentRule]` — keyword-driven rules; first match
  wins. Each rule emits a strategy plus the SQL/vector calls to run.
- `DEFAULT_INTENT_PLAN` — fallback when no rule matches; usually a
  pure `vector_only` plan against the primary embeddable resource.

### `sql_tools.py`

Defines `SafeSqlTool` instances and registers them in a
`SafeSqlRegistry`. Every tool has a unique name, an argument schema,
and a parameterised SQLAlchemy implementation. **No string-built SQL
fragments are allowed anywhere in the pack** — the AST lint test under
`backend/tests/lint/test_no_raw_sql.py` enforces this.

For the `flights` pack, the following tool names are required by
`AGENTS.md`:

- `get_delayed_flights`
- `get_incidents_by_severity`
- `get_flights_by_airport`
- `get_logs_by_flight`
- `get_top_delay_airports`

The `support_tickets` pack ships analogous tools (`get_open_tickets`,
`get_tickets_by_priority`, `get_tickets_by_customer`,
`get_messages_by_ticket`, `get_top_ticket_categories`).

### `prompts.py`

Implements `evidence_projection(item) -> dict` which the agent uses to
render each evidence row into a numbered prompt block. Should always
include the `type` and `id` so the model can cite specific records and
the contract test can verify the answer references retrieved IDs only.

## Fixture layout

The tests for a pack live separately under:

```
backend/tests/contract/fixtures/<pack>/
  agent_questions.json   # canned questions + expected_strategies / expected_tools_any
  queries.json           # (optional) canned vector-search queries with expected_id sets
```

The contract suite parametrises over every registered pack. Adding a
new pack means adding the matching fixtures so the contract tests can
replay them.

### `agent_questions.json` schema

```json
{
  "agent_questions": [
    {
      "question": "Were there any critical safety incidents this week?",
      "expected_strategies": ["sql_only", "vector_and_sql"],
      "expected_tools_any": ["get_incidents_by_severity"]
    }
  ]
}
```

- `expected_strategies` — the test asserts the actual `agent_trace.strategy`
  is one of these.
- `expected_tools_any` — at least one of these tool names must appear
  in `agent_trace.tools_used`. Use `"vector_search"` to require the
  vector branch.

### `queries.json` schema

```json
{
  "queries": [
    {
      "query": "engine vibration during climb",
      "resource": "flight_logs",
      "top_k": 5,
      "expected_ids_subset_of": [1, 2, 3, 4, 5, 6, 7, 8]
    }
  ]
}
```

The test asserts every returned hit's id is contained in
`expected_ids_subset_of` (lets you pin "the answer must come from
this slice of the seed data" without nailing exact ranking).

## Contract checklist

When you add or modify a pack, the following must hold:

- [ ] `init.sql` is idempotent (re-running the file is a no-op).
- [ ] All models use a single `sqlalchemy_base` so tests can drop the
      pack's tables atomically.
- [ ] `seed()` is idempotent and honours `SEED_*` env vars.
- [ ] At least one `EmbeddableResource` is declared with a vector
      column, embedding-model column, embedding-dim column, and
      `embedded_at` column.
- [ ] Every `SafeSqlTool` is parameterised — no f-strings, no `%`,
      no string concatenation building SQL. Verified by
      `backend/tests/lint/test_no_raw_sql.py`.
- [ ] The pack supplies enough `IntentRule`s to drive every strategy
      class (`sql_only`, `vector_only`, `vector_and_sql`) needed by
      the contract fixtures.
- [ ] `backend/tests/contract/fixtures/<pack>/agent_questions.json`
      exists and the suite is green when run with the pack registered.
- [ ] `evidence_projection` always emits `type` and `id`.
- [ ] No test fixtures live inside the pack directory itself.

## Adding a new pack (Phase 7)

> Out of scope for Phase 3. The pack-loader machinery currently expects
> packs to live at `backend/app/domains/<name>/`. Phase 7 introduces the
> formal "how to add a pack" walkthrough, including:
>
> - A scaffolding script that writes the eight files above with the
>   right imports.
> - A migration of `init.sql` → a typed `schema.py` so DDL is visible
>   to SQLAlchemy at import time.
> - Per-pack `fixtures/` directory at the test-suite level (not in the
>   pack), populated automatically.
> - Sample data generators with deterministic seed values.
>
> Until then, copy `backend/app/domains/support_tickets/` as the
> minimal template — it has no historical baggage from being the
> default pack — and follow the contract checklist above.
