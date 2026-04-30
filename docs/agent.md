# Agent

The `raggo` agent is a small, deterministic RAG orchestrator. Given a
user question, it picks a retrieval strategy, calls allowlisted tools,
and asks the local generation model for a grounded answer. It has no
ability to execute arbitrary SQL, no memory between turns, and no
ability to call out to the internet at runtime.

This document describes the moving parts:

- [Intent classification](#intent-classification) — what strategy gets picked
- [Tool selection](#tool-selection) — which tools the strategy enables
- [Prompt construction](#prompt-construction) — how evidence flows into the LLM call
- [`agent_trace` JSON schema](#agent_trace-json-schema) — what every field means
- [Worked examples](#worked-examples) — one structured, one semantic, one hybrid

## Intent classification

`backend/app/core/agent/intent.py` is a rule-based classifier. Each
domain pack registers a list of `IntentRule` objects in its
`intent_rules.py`; rules match on lowercased question keywords. The
first matching rule wins. If no rule matches the pack's
`default_intent_plan` is used instead.

Every classified intent has three fields the orchestrator cares about:

| Field         | Meaning |
| ------------- | ------- |
| `strategy`    | One of `sql_only`, `vector_only`, `vector_and_sql`. Determines which retrieval branches run. |
| `sql_calls`   | A list of `{tool: <safe-sql-tool-name>, args: {...}}` entries to invoke. Tools are looked up in the pack's `SafeSqlRegistry`; unknown names are recorded in `agent_trace.errors` and skipped. |
| `vector_query`| Free-text query to embed and search against the pack's primary embeddable resource. Required when `strategy ∈ {vector_only, vector_and_sql}`. |

Optional fields (`top_k`, `recent_window`) let a rule clamp result
counts and translate phrases like "this week" or "lately" into
parameter values for SQL tools.

### Decision table

| Question shape | Picked strategy | Why |
| -------------- | --------------- | --- |
| Asks for counts / aggregates / "top N" / structured fields ("which airports", "how many delayed", "tickets by priority") | `sql_only` | Structured questions are answered exactly by a SQL tool — no need to retrieve free text. |
| Asks for natural-language descriptions, root causes, themes, or quotes ("why are flights delayed", "what did pilots report about icing") | `vector_only` | Best answered by free-text retrieval over `flight_logs` / `ticket_messages`. |
| Mentions structured filters and free text in one breath ("critical incidents this week involving birds", "open billing tickets where the customer is angry") | `vector_and_sql` | SQL fetches the structured slice, vector search refines on free text within or alongside it. |

A pack can override every column in the table by adding rules to its
`intent_rules.py`. The `flights` and `support_tickets` packs both ship
with rules that exercise all three branches; the contract test suite
in `backend/tests/contract/fixtures/<pack>/agent_questions.json` is
the canonical example list.

## Tool selection

There are two retrieval surfaces, both bounded by the orchestrator:

### Safe SQL tools

Defined in each pack's `sql_tools.py`, registered through
`SafeSqlRegistry`, and called via `app.core.safe_sql.run_tool`. The
registry is the **only** way the agent ever runs SQL. Each tool:

- has a fixed name (e.g. `get_delayed_flights`,
  `get_incidents_by_severity`, `get_flights_by_airport`,
  `get_logs_by_flight`, `get_top_delay_airports` — those five are
  required by `AGENTS.md` for the `flights` pack and present in
  `support_tickets` analogues),
- declares a typed argument schema; `run_tool` validates the args,
  rejects unknown keys, and clamps any `limit` argument to
  `MAX_LIMIT` (defensive bound),
- only ever issues parameterised queries through SQLAlchemy — no
  string-built SQL, enforced by the
  `backend/tests/lint/test_no_raw_sql.py` AST check.

If the rule mentions a tool name that isn't registered, the
orchestrator records `f"<tool>: <error>"` in `agent_trace.errors`
and continues without that tool's evidence — it never crashes the
turn.

### Vector search

Runs through `app.core.vector_search.search` against the resource
named by the rule (or the pack's first `embeddable_resources` entry).
The orchestrator clamps `top_k` to the per-request setting (default 10)
and applies any whitelist-validated filters from the intent.
Embedding-service errors are mapped to a `vector_search dependency:`
entry in `agent_trace.errors` and the turn falls back to whatever SQL
evidence was already collected.

## Prompt construction

Once both retrieval branches finish, `app.core.agent.prompt.build_prompt`
assembles the message sent to the local generation model. It is
deliberately blunt:

1. A short system instruction telling the model to answer **only** from
   the listed evidence and to refuse otherwise.
2. The user question, whitespace-stripped.
3. A numbered list of evidence items rendered by the pack's
   `evidence_projection` callable. Each item carries its `type` and
   `id` so they can be cited verbatim.

The orchestrator then calls `GenerationClient.generate(...)` with a
small `max_new_tokens` budget. If the call fails or times out, the
orchestrator falls back to a deterministic "evidence-only" answer:
either a refusal (if no evidence was retrieved) or a brief summary
that lists the evidence IDs without inventing prose. The model is
**never** trusted to call SQL.

## `agent_trace` JSON schema

Every `/query` response includes an `agent_trace` object with the
following fields. The shape is stable enough that the contract suite
asserts on it and the frontend renders it verbatim in `<pre>`.

| Field | Type | Meaning |
| ----- | ---- | ------- |
| `strategy` | `"sql_only" \| "vector_only" \| "vector_and_sql"` | Strategy chosen by `intent.classify`. Always present. |
| `notes` | `string` | Free-text rationale from the matching `IntentRule` (e.g. "structured tools-only because the question asks for counts"). May be empty. |
| `tools_used` | `string[]` | Flat list of every tool actually invoked, in call order: SQL tool names plus `"vector_search"` if a vector branch ran. |
| `vector_queries` | `string[]` | The concrete query strings sent to vector search this turn (one per `vector_search` call; usually 0 or 1). |
| `vector_calls` | `Array<{ resource: string, query: string, top_k: number, filters?: object, hits: number }>` | Per-call vector trace, including the resource name, top-k, optional filter dict, and number of hits returned. |
| `sql_calls` | `Array<{ tool: string, args: object, rows: number }>` | Per-call SQL trace, including the tool name, the (validated) args dict, and the row count returned. |
| `retrieved_ids` | `Record<string, number[]>` | All evidence IDs grouped by `evidence.type`, deduplicated and sorted ascending. |
| `evidence_count` | `number` | Length of the `evidence` array in the same response. |
| `generation` | `{ model: string, finish_reason: string }` | Name of the local generation model and its self-reported finish reason. `finish_reason: "n/a"` means the orchestrator used the deterministic fallback. |
| `errors` | `string[]` | Any per-step errors that did not crash the turn. Each entry is prefixed with the failing step (`"vector_search dependency: …"`, `"<tool>: …"`, `"generation: …"`). |

The frontend MUST treat unknown additional fields as forward-compatible
metadata and not validate against an exact schema.

## Worked examples

The fixtures referenced below live under
`backend/tests/contract/fixtures/<pack>/agent_questions.json` and are
replayed by `backend/tests/contract/test_agent.py`.

### Structured (`sql_only`) — flights pack

> **Question:** "Which airports are most associated with delays?"

1. `intent.classify` matches the "top delay airports" rule and emits
   `strategy="sql_only"`, `sql_calls=[{tool: "get_top_delay_airports", args: {limit: 5}}]`.
2. The orchestrator calls `run_tool("get_top_delay_airports", {limit: 5})`.
   The tool issues a parameterised aggregate query and returns five
   `{airport, delay_count, avg_delay_minutes}` rows.
3. Vector branch is skipped.
4. Prompt lists the five airports as numbered evidence items; the
   model is asked to summarise the ranking in one paragraph.
5. `agent_trace.strategy = "sql_only"`,
   `tools_used = ["get_top_delay_airports"]`,
   `vector_queries = []`,
   `retrieved_ids = { "delay_airport": [1, 2, 3, 4, 5] }`,
   `errors = []`.

### Semantic (`vector_only`) — flights pack

> **Question:** "Why are flights delayed lately?"

1. `intent.classify` matches the "free-text delay narrative" rule and
   emits `strategy="vector_only"`, `vector_query="why are flights delayed lately"`.
2. SQL branch is skipped.
3. The orchestrator calls
   `vector_search.search(resource=flight_logs, query_text=…, top_k=10)`
   and gets ten log rows back, each with id, score, distance, text,
   metadata.
4. Prompt lists the ten log messages as evidence; the model writes a
   short paragraph synthesising recurring causes.
5. `agent_trace.strategy = "vector_only"`,
   `tools_used = ["vector_search"]`,
   `vector_queries = ["why are flights delayed lately"]`,
   `retrieved_ids = { "flight_log": [11, 19, 24, …] }`.

### Hybrid (`vector_and_sql`) — support_tickets pack

> **Question:** "Any critical urgent tickets we should escalate?"

1. `intent.classify` matches the "critical/urgent escalation" rule and
   emits `strategy="vector_and_sql"`,
   `sql_calls=[{tool: "get_tickets_by_priority", args: {priority: "urgent", limit: 10}}]`,
   `vector_query="critical urgent tickets that should be escalated"`.
2. The orchestrator runs the SQL tool first; it returns the urgent
   ticket rows.
3. The vector branch then runs against `ticket_messages`, surfacing
   message bodies that talk about escalation, refund threats, etc.
4. Prompt lists both the urgent tickets and the relevant messages.
   The model writes an escalation summary that cites both kinds of
   evidence by id.
5. `agent_trace.strategy = "vector_and_sql"`,
   `tools_used = ["get_tickets_by_priority", "vector_search"]`,
   `vector_queries = ["critical urgent tickets that should be escalated"]`,
   `retrieved_ids = { "ticket": [3, 7, 19], "ticket_message": [42, 51, 88] }`.

In every case the answer is grounded in `retrieved_ids`. The contract
suite asserts that any numeric ID the model emits in its answer is a
subset of the IDs in `retrieved_ids` — the agent never invents records.
