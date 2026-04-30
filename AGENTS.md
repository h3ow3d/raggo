# AGENTS.md

## Purpose

This repository contains a secure, local-first proof of concept for an agentic RAG system called `rag-flight-lab`.

The system is a flight-log investigation platform using:

- Docker Compose
- PostgreSQL with pgvector
- FastAPI backend
- React frontend
- isolated local model containers
- separate embedding and generation models
- a basic extensible RAG agent

`PROJECT_SPEC.md` is the source of truth for the product requirements.

---

## Primary Instruction

Always read and follow `PROJECT_SPEC.md` before making changes.

Implement the system incrementally by phase.

Do not attempt to build the entire system in one uncontrolled pass.

---

## Implementation Phases

Work in this order:

1. Project structure, Docker Compose networks, PostgreSQL, pgvector, schema, and seed data
2. Embedding model service
3. Generation model service
4. Backend API, ingestion pipeline, and vector search
5. Basic RAG agent
6. Frontend dashboard, log entry, vector search, and agent chat
7. README, security review, troubleshooting, and cleanup

Complete and verify each phase before moving to the next.

---

## Security Requirements

Security requirements are mandatory.

Do not expose the following services to the host:

- PostgreSQL
- embedding model service
- generation model service

Only the frontend and, for development purposes, the backend API may expose host ports.

The model services must:

- run only on the internal Docker `model_net`
- not expose ports to the host
- not have outbound internet access at runtime
- only receive requests from the backend
- not be reachable by the frontend
- not be reachable directly from the host
- run as non-root where practical
- use `read_only: true` where practical
- use `cap_drop: ["ALL"]`
- use `security_opt: ["no-new-privileges:true"]`
- avoid unnecessary writable mounts
- load models from local filesystem paths only
- not download models at runtime
- not call external APIs

Use internal Docker networks for sensitive services.

---

## Runtime API Policy

Do not use external AI APIs at runtime.

Do not require:

- OpenAI
- Anthropic
- hosted Hugging Face inference APIs
- hosted vector databases
- cloud services

All model inference must happen locally through the model containers.

---

## Model Requirements

Use separate services for:

- embeddings
- text generation

Models should be configurable at build time using build arguments.

Default embedding model:

```text
sentence-transformers/all-MiniLM-L6-v2
```

Default generation model:

```text
Qwen/Qwen2.5-0.5B-Instruct
```

or another small CPU-friendly instruct model if necessary.

Runtime must use local model files only.

Set offline-related environment variables where appropriate:

```env
HF_HUB_OFFLINE=1
TRANSFORMERS_OFFLINE=1
HF_DATASETS_OFFLINE=1
```

---

## Database Requirements

Use PostgreSQL with pgvector.

Do not replace PostgreSQL with a hosted vector database.

The database must support:

- seeded flight data
- flight logs
- incidents
- embeddings stored in pgvector
- vector similarity search
- structured SQL queries

Do not expose PostgreSQL to the host.

---

## Data Requirements

Seed realistic medium-sized flight operations data.

The data should include:

- flights
- logs
- incidents
- delays
- maintenance events
- weather disruption
- safety events
- noisy operational records
- inconsistent wording
- duplicate-looking events
- vague messages
- realistic timestamps

Do not use tiny toy data unless explicitly working on a quick test.

---

## Backend Requirements

Use FastAPI.

The backend is responsible for:

- database access
- data seeding
- ingestion
- embedding calls
- vector search
- safe SQL tools
- basic agent orchestration
- serving frontend API requests

Use clear, modular code.

Suggested modules:

```text
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

---

## SQL Safety Requirements

Never allow the model to execute arbitrary generated SQL.

Do not build an unrestricted text-to-SQL system.

Use predefined safe SQL tools only.

Examples:

- `get_delayed_flights`
- `get_incidents_by_severity`
- `get_flights_by_airport`
- `get_logs_by_flight`
- `get_top_delay_airports`

All SQL must be parameterised.

All queries must have sensible limits.

---

## Agent Requirements

Start with a basic, reliable RAG agent.

The agent should:

1. receive a user question
2. classify the likely intent
3. choose safe SQL, vector search, or both
4. retrieve evidence
5. send a grounded prompt to the local generation model
6. return:
   - answer
   - evidence
   - agent trace

The agent must not invent data.

The final answer should be based only on retrieved evidence.

The `/query` response should include an `agent_trace` showing:

- selected strategy
- tools used
- vector queries used
- safe SQL tools used
- retrieved IDs

Design the agent so it can later support:

- memory
- query rewriting
- multi-step planning
- tool chaining
- audit trails
- human approval gates

Do not implement advanced memory in the first version unless explicitly requested.

---

## Frontend Requirements

Use a simple, useful frontend.

Prefer React with Vite and TypeScript.

The UI should include:

- dashboard
- add flight log form
- vector search page
- agent chat page

Do not over-engineer styling.

Do not make the UI flashy at the expense of functionality.

---

## Docker Requirements

The default setup must run with:

```bash
docker compose up --build
```

Optional GPU mode should use:

```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up --build
```

Do not require GPU for the default setup.

Use Docker Compose networks intentionally.

Kubernetes is a supported production install target via the `raggo` Helm chart published to `oci://ghcr.io/h3ow3d/raggo/charts/raggo`. Docker Compose remains the default development and small-deployment path. See the phase plan in `PHASE_0_FOUNDATIONS.md` through `PHASE_8_OPERATIONS.md`.

---

## Build and Verification

After each phase, verify that:

- Docker Compose syntax is valid
- containers build
- health checks work where implemented
- imports are correct
- required services can communicate
- restricted services are not exposed to the host
- core functionality works before moving on

Prefer small, working increments.

---

## Code Style

Prefer:

- simple readable code
- explicit configuration
- clear module boundaries
- helpful comments for learning
- practical error handling
- small functions
- predictable behaviour

Avoid:

- clever abstractions
- unnecessary frameworks
- hidden magic
- placeholder implementations
- large untested rewrites
- unnecessary dependencies

---

## Documentation Requirements

Update `README.md` as the project evolves.

The README should explain:

- what the project does
- architecture
- security model
- Docker networks
- CPU mode
- GPU mode
- changing models
- build-time model inclusion
- offline runtime behaviour
- seeding
- ingestion
- vector search
- basic agent behaviour
- example questions
- troubleshooting
- reset commands

---

## Acceptance Criteria

The project is not complete until:

- `docker compose up --build` starts the stack
- frontend is reachable from the host
- backend health endpoint works
- PostgreSQL is not exposed to the host
- model services are not exposed to the host
- model services are only on the internal model network
- database is seeded automatically
- dashboard stats work
- new flight logs can be added
- ingestion creates embeddings
- direct vector search works
- agent query endpoint works
- agent returns answer, evidence, and trace
- no runtime external AI APIs are required
- default mode runs on CPU
- GPU override is present and documented
- model names are configurable at build time
- SQL execution is safe and allowlisted
- README is complete

---

## Important Rule

Do not leave core functionality as TODOs.

If a requirement is too large to finish in one pass, implement the smallest working version that satisfies the current phase, then document the extension point clearly.
