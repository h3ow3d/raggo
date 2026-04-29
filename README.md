# rag-flight-lab (raggo)

A secure, local-first proof-of-concept for an agentic RAG system on flight
operations data. See `PROJECT_SPEC.md` for the full specification and
`AGENTS.md` for build instructions.

This repository is being implemented in phases. **Phase 1** is complete:
project structure, Docker Compose, PostgreSQL with pgvector, schema, and
seed data generation.

## Phase 1 quick start

```bash
cp .env.example .env
docker compose up --build
```

Once the stack is up:

- Backend health: <http://localhost:8000/health>
- Basic stats:    <http://localhost:8000/stats>

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

## Security model (Phase 1)

- PostgreSQL is **not** exposed to the host. It is only attached to the
  internal `database_net` Docker network.
- `database_net` and `model_net` are declared `internal: true`, blocking
  outbound access from any container attached only to those networks.
- The backend bridges `frontend_net`, `database_net`, and (later)
  `model_net`. Only the backend may reach the database; only the backend
  will reach model services.
- The backend exposes port 8000 to the host **for development only**.

## Reset

```bash
docker compose down -v
```

## GPU mode (placeholder)

The GPU override file exists but is a placeholder until the model
services are introduced in later phases:

```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up --build
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
    main.py        # FastAPI app, /health, /stats, startup seed
    config.py      # env-driven settings
    database.py    # SQLAlchemy engine/session
    models.py      # ORM: Flight, FlightLog, Incident
    schemas.py     # Pydantic responses
    seed.py        # realistic data generation
```

Subsequent phases will add the embedding service, generation service,
ingestion pipeline, vector search, agent, and frontend.