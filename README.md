# rag-flight-lab (raggo)

A secure, local-first proof-of-concept for an agentic RAG system on flight
operations data. See `PROJECT_SPEC.md` for the full specification and
`AGENTS.md` for build instructions.

This repository is being implemented in phases. **Phase 6** is now in
place: project structure, Docker Compose, PostgreSQL with pgvector, seed
data, the embedding service, the generation service, the FastAPI backend
with ingestion / vector search / agent endpoints, and a React + Vite
frontend served via nginx.

## Quick start

```bash
cp .env.example .env
docker compose up --build
```

Once the stack is up:

- Frontend dashboard: <http://localhost:3000>
- Backend health:     <http://localhost:8000/health>
- Backend stats:      <http://localhost:8000/stats>

The frontend has four pages:

- **Dashboard** — live counts of flights, logs, incidents, embedded /
  unembedded logs, plus a button to trigger an ingestion top-up.
- **Add Flight Log** — pick a flight, fill in log type / source system /
  severity / message / metadata JSON, and submit. The backend stores the
  log and immediately requests an embedding for it from the local
  embedding service.
- **Vector Search** — manual pgvector similarity search with optional
  severity and source-system filters.
- **Agent Chat** — ask the backend agent a natural-language question and
  see the answer, the retrieved evidence, and the full `agent_trace`.

The frontend talks **only** to the backend via `/api/*` (proxied by
nginx). It never reaches the embedding or generation services directly —
those live on the internal `model_net`.

The backend automatically:

1. waits for PostgreSQL,
2. ensures the `vector` extension exists,
3. seeds `flights`, `flight_logs`, and `incidents` if the database is empty.

Seed sizes are configurable in `.env`:

```env
SEED_FLIGHT_COUNT=5000
SEED_LOG_COUNT=50000
SEED_INCIDENT_COUNT=500
```

## Security model

- PostgreSQL is **not** exposed to the host. It is only attached to the
  internal `database_net` Docker network.
- The embedding-model and generation-model services are **not** exposed
  to the host and are only attached to the internal `model_net`. They
  are only reachable from the backend.
- `database_net` and `model_net` are declared `internal: true`, blocking
  outbound access from any container attached only to those networks.
- The backend bridges `frontend_net`, `database_net`, and `model_net`.
  Only the backend may reach the database or the model services.
- The frontend is attached to `frontend_net` only. It cannot reach the
  database or the model services. All API calls go to `/api/*`, which
  nginx proxies to the backend.
- The backend exposes port 8000 to the host **for development only**.

## Reset

```bash
docker compose down -v
```

## GPU mode

```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up --build
```

## Troubleshooting

### Agent answers come back with "Generation model unavailable" (504s in logs)

On CPU-only hosts (especially Apple Silicon via Docker Desktop) the
local generation model can be slow enough that a single answer exceeds
the per-request budget. The backend log will show messages like:

```
generation service returned 504: {"detail":"generation timed out after Ns"}
```

The defaults are sized for CPU inference of `Qwen2.5-0.5B-Instruct`,
but you can tune them via environment variables (see `.env.example`):

- `GEN_TIMEOUT_SECONDS` — wall-clock budget inside the generation
  container (default `240`).
- `GENERATION_REQUEST_TIMEOUT` — backend HTTP timeout for `/generate`
  calls. Keep this **>=** `GEN_TIMEOUT_SECONDS` (default `240`).
- `AGENT_MAX_NEW_TOKENS` — cap on new tokens per agent turn (default
  `256`). Lower this on very slow hardware; raise it on GPU.

After changing values, restart the affected services:

```bash
docker compose up -d --no-deps backend generation-model
```

## Layout

```
docker-compose.yml
docker-compose.gpu.yml
.env.example
db/init.sql
backend/
  Dockerfile
  requirements.txt
  app/
    main.py        # FastAPI app: /health, /stats, /flights, /logs,
                   #              /ingest, /search/vector, /query
    config.py      # env-driven settings
    database.py    # SQLAlchemy engine/session
    models.py      # ORM: Flight, FlightLog, Incident
    schemas.py     # Pydantic request/response models
    seed.py        # realistic data generation
    ingestion.py   # batch + single-row embedding ingestion
    vector_search.py
    agent.py
    safe_sql_tools.py
    model_clients.py
models/
  embedding/       # offline sentence-transformers service
  generation/      # offline local LLM service
frontend/
  Dockerfile       # node build → nginx runtime
  nginx.conf       # serves SPA, proxies /api/* to backend
  package.json
  vite.config.ts
  tsconfig.json
  index.html
  src/
    main.tsx
    App.tsx
    api.ts         # single fetch wrapper, all calls hit /api/*
    styles.css
    pages/
      Dashboard.tsx
      AddLog.tsx
      VectorSearch.tsx
      AgentChat.tsx
```