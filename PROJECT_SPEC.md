You are a senior DevSecOps, full-stack, database, and AI engineer.

Build a complete, flexible, secure proof-of-concept for a local agentic RAG system using Docker Compose.

The project is called:

rag-flight-lab

It is a flight-log investigation platform designed to teach and demonstrate:

- PostgreSQL
- pgvector
- embeddings
- vector search
- RAG
- basic AI agents
- secure local model deployment
- CPU/GPU-flexible model hosting

The system must run locally with:

```bash
docker compose up --build
```

Do not use external APIs at runtime.

Do not require OpenAI, Anthropic, cloud services, or hosted vector databases.

---

# 1. Core Product Goal

Build a local web application where users can:

1. view a dashboard of seeded flight operations data
2. add new flight log records
3. trigger ingestion into a vector store
4. run direct vector searches
5. ask natural-language questions through a basic AI agent
6. see the evidence used by the agent

The data domain is commercial flight operations.

The database should contain realistic operational records such as:

- flights
- flight logs
- incidents
- maintenance-style notes
- delays
- weather disruption
- safety-related events
- noisy irrelevant operational logs

---

# 2. Required Architecture

Use this architecture:

```text
                        ┌────────────────────┐
                        │      Browser        │
                        └─────────┬──────────┘
                                  │
                                  ▼
                        ┌────────────────────┐
                        │      Frontend       │
                        │  React + Vite       │
                        └─────────┬──────────┘
                                  │ frontend_net
                                  ▼
                        ┌────────────────────┐
                        │      Backend        │
                        │ FastAPI + Python    │
                        └──────┬──────┬──────┘
                               │      │
                    database_net│      │model_net internal
                               │      │
                               ▼      ▼
                 ┌────────────────┐  ┌────────────────────┐
                 │ PostgreSQL      │  │ Embedding Model     │
                 │ pgvector        │  │ CPU-first           │
                 └────────────────┘  └────────────────────┘
                                      ┌────────────────────┐
                                      │ Generation Model    │
                                      │ CPU default, GPU opt │
                                      └────────────────────┘
```

---

# 3. Docker Compose Requirements

Create:

```text
docker-compose.yml
docker-compose.gpu.yml
.env.example
```

## Default mode

The default `docker-compose.yml` must run on CPU.

```bash
docker compose up --build
```

## Optional GPU mode

The GPU override should be documented as:

```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up --build
```

GPU support should only apply to the generation model service.

Do not require GPU for the default setup.

---

# 4. Docker Networks and Isolation

Create explicit Docker networks:

```yaml
networks:
  frontend_net:
  database_net:
    internal: true
  model_net:
    internal: true
```

Service placement:

- frontend:
  - connected only to `frontend_net`
  - exposed to host

- backend:
  - connected to:
    - `frontend_net`
    - `database_net`
    - `model_net`
  - may expose an API port to host for development

- postgres:
  - connected only to `database_net`
  - no host ports

- embedding-model:
  - connected only to `model_net`
  - no host ports

- generation-model:
  - connected only to `model_net`
  - no host ports

The model containers must not be reachable from:

- host
- frontend
- postgres
- internet

The backend is the only service allowed to call model services.

---

# 5. Model Security Requirements

The embedding model and generation model containers are high-security isolated components.

For both model containers:

- no `ports:` mapping
- no external network access at runtime
- run as non-root where possible
- use `read_only: true` where practical
- use `cap_drop: ["ALL"]`
- use `security_opt: ["no-new-privileges:true"]`
- avoid unnecessary Linux capabilities
- avoid unnecessary writable paths
- do not bind-mount model weights, source code, or any host directories into
  model containers (weights must be baked into the image; see §6.0)
- only use read-only model files at runtime
- do not call external APIs
- do not download anything at runtime

If writable temp space is required, use explicit tmpfs:

```yaml
tmpfs:
  - /tmp
```

Set offline environment variables in model containers:

```env
HF_HUB_OFFLINE=1
TRANSFORMERS_OFFLINE=1
HF_DATASETS_OFFLINE=1
```

The runtime model code must load only from local filesystem paths.

---

# 6. Model Strategy

Use two separate model services.

## 6.0 Packaging Pattern (Required)

Both model services must follow the **multi-stage Docker build with weights baked
into the image** pattern. This is the chosen approach for this project.

The pattern:

1. **Stage 1 — `weights`**: a build-time stage that has network access. It runs
   `download_model.py` to fetch the model from Hugging Face and saves it to a
   well-known path inside the stage (e.g. `/models/embedding` or
   `/models/generation`).
2. **Stage 2 — runtime**: a minimal runtime image that uses
   `COPY --from=weights /models /models` to copy the weights into the final
   image. The runtime stage must **not** include Hugging Face download tooling,
   git, or other network utilities beyond what is required to serve requests.
3. The final image is fully self-contained. No weights are downloaded at
   runtime. No weights are bind-mounted from the host.

Rules that follow from this pattern:

- Do **not** download models on the host and bind-mount them into the
  container.
- Do **not** use a shared named Docker volume that the model containers
  populate at runtime.
- Do **not** rely on any runtime network access from the model containers.
- The model files inside the final image must be readable by the non-root
  runtime user but should not be writable.
- `read_only: true` on the container is expected to work because weights live
  on the image's read-only layers.
- Model selection is controlled at build time via build args
  (`EMBEDDING_MODEL_NAME`, `GENERATION_MODEL_NAME`). Changing the model
  requires rebuilding the relevant image.

### Generation runtime engine

The generation service exposes a stable HTTP contract (`POST /generate`) so
the underlying inference engine can be swapped without affecting the backend.

- **Default engine**: `transformers` + `torch` (CPU). This matches the default
  Hugging Face model ID in `.env.example` directly and requires no model
  conversion. Use this for the first working version.
- **Optional engine**: `llama.cpp` via `llama-cpp-python`. When this engine is
  selected, the build-time `download_model.py` must fetch a pre-quantised
  **GGUF** artifact (for example from a `*-GGUF` repository on Hugging Face)
  and save it to `/models/generation`. The runtime `app.py` loads the GGUF
  file and serves the same `/generate` contract. This engine is preferred
  when CPU throughput or memory footprint matters.

Engine selection is controlled by the existing build arg `MODEL_RUNTIME`,
which may take the values:

```text
cpu        # transformers + torch on CPU (default)
gpu        # transformers + torch on GPU (used by docker-compose.gpu.yml)
llamacpp   # llama-cpp-python with a GGUF artifact baked into the image
```

The embedding service must use `sentence-transformers`. Do not use
`llama.cpp` for embeddings in this project.

## 6.1 Embedding Model Service

Default model:

```text
sentence-transformers/all-MiniLM-L6-v2
```

Default embedding dimension:

```text
384
```

Expose only this internal endpoint:

```text
POST /embed
```

Request:

```json
{
  "texts": ["engine vibration noted during climb"]
}
```

Response:

```json
{
  "embeddings": [[0.1, 0.2, 0.3]]
}
```

Requirements:

- CPU-first
- Fast enough for PoC ingestion
- build-time configurable
- no runtime downloads
- batch embedding support
- health endpoint only internal to Docker network

Support build args:

```text
EMBEDDING_MODEL_NAME
EMBEDDING_DIM
```

At build time (multi-stage; see §6.0):

1. in the `weights` stage, download the model
2. save it under `/models/embedding`
3. `COPY --from=weights /models/embedding /models/embedding` into the runtime stage
4. runtime must load from `/models/embedding` only

## 6.2 Generation Model Service

Default model should be small enough to run on CPU for a PoC.

Use one of these reasonable defaults:

```text
Qwen/Qwen2.5-0.5B-Instruct
```

or, if easier:

```text
TinyLlama/TinyLlama-1.1B-Chat-v1.0
```

Expose only this internal endpoint:

```text
POST /generate
```

Request:

```json
{
  "prompt": "Answer using the supplied evidence...",
  "max_new_tokens": 512,
  "temperature": 0.2
}
```

Response:

```json
{
  "text": "The likely cause was..."
}
```

Requirements:

- CPU default
- optional GPU mode
- build-time configurable
- no runtime downloads
- local filesystem model loading only
- simple deterministic defaults
- timeout handling

Support build args:

```text
GENERATION_MODEL_NAME
MODEL_RUNTIME
```

At build time (multi-stage; see §6.0):

1. in the `weights` stage, download the model (a Hugging Face repo for the
   `transformers` engine, or a GGUF artifact for the `llamacpp` engine)
2. save it under `/models/generation`
3. `COPY --from=weights /models/generation /models/generation` into the runtime stage
4. runtime must load from `/models/generation` only

---

# 7. Fast Iteration Requirement

This is a PoC, so optimise for fast iteration while keeping security boundaries.

Do:

- use small default models
- use clear Python modules
- avoid unnecessary framework complexity
- avoid microservice sprawl beyond the required services
- keep frontend simple
- keep README commands clear
- include reset commands
- include troubleshooting

Do not compromise:

- model isolation
- no runtime internet for model containers
- no direct host access to model containers
- safe SQL practices

---

# 8. Database Requirements

Use PostgreSQL with pgvector.

The schema can be created either by:

- `db/init.sql`, or
- backend startup migration code

Prefer simple, understandable setup.

Create these tables.

## flights

Columns:

- id
- flight_number
- airline
- aircraft_type
- tail_number
- origin
- destination
- scheduled_departure
- actual_departure
- scheduled_arrival
- actual_arrival
- status
- created_at

## flight_logs

Columns:

- id
- flight_id
- log_time
- log_type
- source_system
- severity
- message
- structured_metadata JSONB
- embedding vector
- embedding_model
- embedding_dim
- embedded_at
- created_at

Important:

- pgvector dimension must match `EMBEDDING_DIM`
- default is 384
- use an index suitable for vector similarity search
- include indexes for:
  - flight_id
  - log_time
  - severity
  - source_system
  - status where relevant

## incidents

Columns:

- id
- flight_id
- incident_time
- severity
- category
- description
- resolution_status
- created_at

Use foreign keys.

---

# 9. Seed Data Requirements

On first startup, automatically seed a medium-sized realistic dataset.

Target:

```text
5,000 flights
50,000 flight logs
500 incidents
```

Make these values configurable via environment variables:

```env
SEED_FLIGHT_COUNT=5000
SEED_LOG_COUNT=50000
SEED_INCIDENT_COUNT=500
```

Do not reseed if data already exists unless explicitly requested.

Provide a reset command in README.

The seed data must include realistic messiness:

- varied phrasing
- abbreviations
- duplicate-looking messages
- vague messages
- irrelevant logs
- operational noise
- inconsistent wording
- realistic timestamps
- varied severities

Include categories such as:

- weather
- maintenance
- engine
- hydraulic
- avionics
- crew
- passenger
- ATC
- runway
- gate
- fuelling
- de-icing
- turbulence
- security
- medical
- baggage
- catering
- cleaning
- false alarm

Example messages:

```text
ENG vibration noted during climb, within tolerance but flagged for post-flight inspection.
ATC hold assigned due to congestion at destination.
Late inbound aircraft causing estimated 42 minute delay.
Cabin crew reported unusual odour near rear galley; MX inspection requested.
HYD pressure fluctuation detected; no immediate action required.
Destination weather below approach minima; holding pattern initiated.
Passenger medical event reported during boarding; departure paused.
De-icing completed, queue delay due to pad congestion.
```

---

# 10. Ingestion Pipeline

Implement an ingestion pipeline in the backend.

It must:

1. find flight logs with no embedding
2. batch them
3. call the embedding model service
4. store embeddings in pgvector
5. record `embedding_model`, `embedding_dim`, and `embedded_at`
6. run on startup after seed data exists
7. run when a new log is submitted
8. be manually callable

Expose:

```text
POST /ingest
```

Request:

```json
{
  "limit": 1000
}
```

Response:

```json
{
  "scanned": 1000,
  "embedded": 1000,
  "errors": []
}
```

For first startup, do not block forever trying to embed all 50,000 logs before the UI is usable.

Implement one of:

- background ingestion worker, or
- startup ingestion limited to a configurable batch size

Use:

```env
INGEST_BATCH_SIZE=128
STARTUP_INGEST_LIMIT=2000
```

The UI should show how many logs are embedded.

---

# 11. Backend Requirements

Use:

- Python
- FastAPI
- SQLAlchemy 2.x or psycopg 3
- Pydantic
- httpx for model service calls

Backend modules:

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

Expose endpoints:

```text
GET  /health
GET  /stats
GET  /flights
GET  /flights/{id}
GET  /logs
POST /logs
POST /ingest
POST /search/vector
POST /query
```

---

# 12. Safe SQL Requirement

Do not allow the LLM to generate arbitrary SQL.

Implement safe structured query functions instead.

Examples:

```text
get_delayed_flights(start_time, end_time, limit)
get_incidents_by_severity(severity, start_time, end_time, limit)
get_flights_by_airport(airport, start_time, end_time, limit)
get_logs_by_flight(flight_id, limit)
get_top_delay_airports(start_time, end_time, limit)
```

The agent may choose among these safe tools, but it must not execute raw model-generated SQL.

All SQL must be parameterised.

Add reasonable limits to all queries.

---

# 13. Vector Search Requirement

Implement pgvector similarity search.

Endpoint:

```text
POST /search/vector
```

Request:

```json
{
  "query": "engine vibration during climb",
  "top_k": 10,
  "filters": {
    "severity": "warning",
    "source_system": "ACARS"
  }
}
```

Response must include:

- log id
- flight id
- flight number
- origin
- destination
- log time
- severity
- message
- similarity score

---

# 14. Basic Agent Requirement

Build a basic but extensible agent.

Prefer a lightweight custom agent implementation over a heavy framework for version 1.

If LangChain or LlamaIndex is used, hide it behind an interface so it can be replaced later.

The first version should be simple and reliable.

The agent must:

1. receive a user question
2. classify intent deterministically where possible
3. decide whether to use:
   - safe SQL tools
   - vector search
   - both
4. retrieve evidence
5. construct a grounded prompt for the generation model
6. return an answer with evidence and trace

Do not let the generation model invent data.

The final prompt sent to the generation model must instruct it:

- answer only from the supplied evidence
- say when evidence is insufficient
- cite log IDs and incident IDs where relevant
- keep answer concise

Endpoint:

```text
POST /query
```

Request:

```json
{
  "question": "Were there any recent engine-related safety issues?",
  "top_k": 10
}
```

Response:

```json
{
  "answer": "Yes. Several logs mention engine vibration...",
  "evidence": [
    {
      "type": "flight_log",
      "id": 123,
      "message": "ENG vibration noted during climb..."
    }
  ],
  "agent_trace": {
    "strategy": "vector_and_sql",
    "tools_used": ["vector_search", "get_incidents_by_severity"],
    "vector_queries": ["engine safety issue vibration"],
    "sql_filters": {
      "severity": ["warning", "critical"]
    }
  }
}
```

The agent should support questions like:

```text
Why are flights delayed this week?
Were there any safety issues recently?
Which flights had engine-related problems?
Are there recurring hydraulic issues?
Show me severe incidents related to weather.
Which airports are most associated with delays?
Find logs similar to hydraulic pressure fluctuation.
```

Design the agent so it can later be extended with:

- memory
- query rewriting
- multi-step planning
- tool chaining
- audit trails
- human approval gates

But do not implement advanced memory in version 1.

---

# 15. Frontend Requirements

Use:

- React
- Vite
- TypeScript preferred
- simple CSS or lightweight component styling

Do not make it flashy.

Make it useful.

Pages:

## Dashboard

Show:

- total flights
- total logs
- total incidents
- embedded logs count
- unembedded logs count
- recent severe incidents
- ingestion status

## Add Flight Log

Form fields:

- flight selector/search
- log type
- source system
- severity
- message
- metadata JSON text box

On submit:

- create log
- trigger embedding for that log or show pending ingestion status

## Vector Search

Fields:

- query text
- top_k
- optional severity filter
- optional source system filter

Show:

- matching logs
- similarity scores
- flight metadata

## Agent Chat

Chat-style interface.

Show:

- user question
- agent answer
- evidence logs
- agent trace

---

# 16. Project Structure

Generate this structure:

```text
rag-flight-lab/
  docker-compose.yml
  docker-compose.gpu.yml
  .env.example
  README.md

  db/
    init.sql

  backend/
    Dockerfile
    requirements.txt
    app/
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

  models/
    embedding/
      Dockerfile
      requirements.txt
      app.py
      download_model.py

    generation/
      Dockerfile
      requirements.txt
      app.py
      download_model.py

  frontend/
    Dockerfile
    package.json
    index.html
    src/
      main.tsx
      App.tsx
      api.ts
      components/
```

---

# 17. Environment Variables

Include `.env.example`:

```env
POSTGRES_DB=rag_flight_lab
POSTGRES_USER=rag
POSTGRES_PASSWORD=rag_dev_password

EMBEDDING_MODEL_NAME=sentence-transformers/all-MiniLM-L6-v2
EMBEDDING_DIM=384

GENERATION_MODEL_NAME=Qwen/Qwen2.5-0.5B-Instruct
MODEL_RUNTIME=cpu

SEED_FLIGHT_COUNT=5000
SEED_LOG_COUNT=50000
SEED_INCIDENT_COUNT=500

INGEST_BATCH_SIZE=128
STARTUP_INGEST_LIMIT=2000

BACKEND_PORT=8000
FRONTEND_PORT=3000
```

---

# 18. README Requirements

The README must include:

1. what the project does
2. architecture diagram
3. security model
4. Docker network explanation
5. how to run CPU mode
6. how to run GPU mode
7. how to change embedding model
8. how to change generation model
9. how build-time model inclusion works (multi-stage build, weights baked into the image)
10. how offline runtime works
11. how to switch the generation runtime engine (`transformers` default vs `llama.cpp`/GGUF)
12. how seeding works
13. how ingestion works
14. how vector search works
15. how the basic agent works
16. example questions
17. troubleshooting
18. reset commands

Include commands:

```bash
docker compose up --build
docker compose down -v
docker compose logs backend
docker compose logs embedding-model
docker compose logs generation-model
```

---

# 19. Acceptance Criteria

The project is complete only when all of these are true:

- `docker compose up --build` starts the stack
- frontend is reachable from host
- backend health endpoint works
- postgres is not exposed to host
- model services are not exposed to host
- model services are only on internal Docker network
- database is seeded automatically
- user can view dashboard stats
- user can add a new flight log
- ingestion creates embeddings
- direct vector search works
- agent query endpoint works
- agent returns answer, evidence, and trace
- no runtime external APIs are required
- default mode runs on CPU
- GPU override is present and documented
- model names are configurable at build time
- SQL execution is safe and allowlisted
- README is complete

---

# 20. Implementation Rules

Do not leave placeholder files.

Do not write “TODO implement later” for core functionality.

Prefer simple, readable code over clever abstractions.

Keep the first implementation understandable for someone learning RAG from scratch.

Use clear comments where helpful.

If a requirement is too large to implement fully in one pass, implement the smallest working version that satisfies the acceptance criteria, then document extension points.
```

---

# 2. AGENTS.md Repository Instruction File

Create a separate file in the repo root called `AGENTS.md` and paste this into it.

```md
# Agent Instructions

Follow `PROJECT_SPEC.md` as the source of truth.

Implement the project incrementally by phase.

Do not skip security requirements.

Do not expose PostgreSQL or model services to the host.

Do not allow the LLM to generate unrestricted SQL.

Do not use external APIs at runtime.

Prefer working, simple, readable code over clever abstractions.

After each phase, verify Docker Compose, imports, dependencies, and basic health checks before moving on.

When unsure, choose the simplest implementation that satisfies the acceptance criteria.

Do not create placeholder implementations for core functionality.
```

---

# 21. Install Targets, Image Distribution, Signing, and Releases

This section was added after the initial spec to record decisions that govern how `raggo` is built, distributed, and installed. The phased delivery plan that operationalises this section lives in the root of the repository as `PHASE_0_FOUNDATIONS.md` through `PHASE_8_OPERATIONS.md`.

## 21.1 Supported install targets

`raggo` supports two install paths from the same source tree:

1. **Docker Compose** — the default for development and small deployments. `docker compose up --build` must continue to work.
2. **Kubernetes via Helm** — the production install path. The chart is named `raggo` and is published as an OCI artifact at `oci://ghcr.io/h3ow3d/raggo/charts/raggo`.

Both install paths consume the same container images, pinned by digest, and must satisfy the same security model defined in this spec (no host exposure of Postgres or model services, internal-only networks, no runtime egress from model containers).

## 21.2 Container image distribution (GHCR)

All container images are published to GitHub Container Registry under `ghcr.io/h3ow3d/raggo/`:

- `ghcr.io/h3ow3d/raggo/backend`
- `ghcr.io/h3ow3d/raggo/frontend`
- `ghcr.io/h3ow3d/raggo/embedding-model`
- `ghcr.io/h3ow3d/raggo/generation-model`
- `ghcr.io/h3ow3d/raggo/postgres-pgvector`

Rules:

- **Visibility:** packages are **public**. No pull secret is required for installs.
- **Architectures:** `linux/amd64` and `linux/arm64` for application images; model images may be amd64-only if size requires it.
- **Tagging:** semver (`vMAJOR.MINOR.PATCH`), git short SHA (`sha-<short>`), and `latest` only on the default branch. **Release and air-gap deployment artifacts** (the published Helm chart's default values, the air-gap bundle's pinned Compose overlay, and any installer scripts) reference images **by digest**, never by `:latest`. The development `docker-compose.yml` at the repo root is allowed to use `build:` contexts and local tags (`rag-flight-lab/*:latest`, `pgvector/pgvector:pg16`) for fast iteration; it is not a deployment artifact.
- **PR previews:** PR builds publish ephemeral images tagged `pr-<num>-sha-<short>` and are expired after 30 days.

## 21.3 Image signing and provenance

- All release images are signed with **cosign using a long-lived key pair**. The public key is committed to the repository at `.github/cosign.pub` and published in the GitHub Release notes. The private key lives in the `COSIGN_PRIVATE_KEY` GitHub Actions secret with passphrase in `COSIGN_PASSWORD`.
- Each release image has an attached **SBOM** (syft, SPDX JSON) and **SLSA provenance** attestation.
- The Helm chart OCI artifact is signed with the same cosign key.

## 21.4 Helm chart

- Chart name: `raggo`.
- Chart location in the repo: `helm/raggo/`.
- Published to: `oci://ghcr.io/h3ow3d/raggo/charts/raggo`.
- Image references default to digests on GHCR. `global.image.registry` allows redirecting to an internal mirror for air-gapped installs.
- `values.schema.json` is enforced and rejects `latest` tags and `imagePullPolicy: Always`.
- Default posture: NetworkPolicies enabled, `runAsNonRoot`, `readOnlyRootFilesystem` where feasible, `allowPrivilegeEscalation: false`, `capabilities.drop: [ALL]`, seccomp `RuntimeDefault`.
- Postgres ships as a chart-managed StatefulSet by default (no Bitnami/community chart dependencies). An external Postgres can be supplied via `postgres.external.enabled=true`.
- Secrets are never baked into the chart: either `secret.create=true` for dev or reference an existing Secret in production.
- A GPU values overlay (`values-gpu.yaml`) mirrors the existing `docker-compose.gpu.yml`.

## 21.5 Versioning and releases

- The repository follows **Conventional Commits**.
- Releases are **automated** (e.g. `release-please` or `semantic-release`): merging conventional commits to the default branch produces version bumps, changelog updates, and tags. Tagging triggers the release workflow that builds and pushes images, packages and pushes the Helm chart, generates SBOMs and provenance, signs everything with cosign, builds the air-gap bundle, and creates the GitHub Release.
- Squash-merge with linear history. Signed commits are required on the default branch.

## 21.6 Air-gap installs

- A reproducible offline bundle is produced on every release: image tarballs (pulled by digest), packaged Helm chart `.tgz`, SBOMs, vulnerability scan reports, and an `install.sh` for the Compose path.
- Kubernetes air-gap installs use `helm pull` on a connected jumpbox plus `global.image.registry` to point at the customer's internal mirror. The procedure is documented in `helm/raggo/AIRGAP.md`.
- Air-gap smoke tests run in CI for both the Compose path and the Helm path with model containers having no egress.

## 21.7 Phased delivery

Implementation follows the phases described in:

- `PHASE_0_FOUNDATIONS.md`
- `PHASE_1_GHCR_IMAGES.md`
- `PHASE_2_CORE_CI.md`
- `PHASE_3_BACKEND_AGENT.md`
- `PHASE_4_HELM_CHART.md`
- `PHASE_5_AIRGAP_BUNDLE.md`
- `PHASE_6_RELEASE_PIPELINE.md`
- `PHASE_7_DOMAIN_PACKS.md`
- `PHASE_8_OPERATIONS.md`

Each phase has its own exit criteria. A phase is not complete until its exit criteria are met, including any updates to this spec, `AGENTS.md`, and `README.md` that the phase introduces.

Keep the project understandable for someone learning RAG, vectors, agents, and databases from scratch.