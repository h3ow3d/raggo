-- rag-flight-lab database initialization
-- Runs automatically on first PostgreSQL container start
-- (mounted at /docker-entrypoint-initdb.d/init.sql).
--
-- Phase 1: enable pgvector and create schema for flights, flight_logs,
-- and incidents. The embedding column dimension defaults to 384 to match
-- sentence-transformers/all-MiniLM-L6-v2 (configurable in later phases).

CREATE EXTENSION IF NOT EXISTS vector;

-- ---------------------------------------------------------------------------
-- flights
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS flights (
    id                   SERIAL PRIMARY KEY,
    flight_number        TEXT        NOT NULL,
    airline              TEXT        NOT NULL,
    aircraft_type        TEXT        NOT NULL,
    tail_number          TEXT        NOT NULL,
    origin               TEXT        NOT NULL,
    destination          TEXT        NOT NULL,
    scheduled_departure  TIMESTAMPTZ NOT NULL,
    actual_departure     TIMESTAMPTZ,
    scheduled_arrival    TIMESTAMPTZ NOT NULL,
    actual_arrival       TIMESTAMPTZ,
    status               TEXT        NOT NULL,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_flights_flight_number       ON flights (flight_number);
CREATE INDEX IF NOT EXISTS idx_flights_origin              ON flights (origin);
CREATE INDEX IF NOT EXISTS idx_flights_destination         ON flights (destination);
CREATE INDEX IF NOT EXISTS idx_flights_status              ON flights (status);
CREATE INDEX IF NOT EXISTS idx_flights_scheduled_departure ON flights (scheduled_departure);

-- ---------------------------------------------------------------------------
-- flight_logs
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS flight_logs (
    id                  SERIAL PRIMARY KEY,
    flight_id           INTEGER     NOT NULL REFERENCES flights (id) ON DELETE CASCADE,
    log_time            TIMESTAMPTZ NOT NULL,
    log_type            TEXT        NOT NULL,
    source_system       TEXT        NOT NULL,
    severity            TEXT        NOT NULL,
    message             TEXT        NOT NULL,
    structured_metadata JSONB       NOT NULL DEFAULT '{}'::jsonb,
    embedding           vector(384),
    embedding_model     TEXT,
    embedding_dim       INTEGER,
    embedded_at         TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_flight_logs_flight_id     ON flight_logs (flight_id);
CREATE INDEX IF NOT EXISTS idx_flight_logs_log_time      ON flight_logs (log_time);
CREATE INDEX IF NOT EXISTS idx_flight_logs_severity      ON flight_logs (severity);
CREATE INDEX IF NOT EXISTS idx_flight_logs_source_system ON flight_logs (source_system);

-- IVFFlat index for vector similarity search. Created up-front; PostgreSQL
-- will fall back to sequential scan until the table has data and ANALYZE
-- has been run.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes WHERE indexname = 'idx_flight_logs_embedding'
    ) THEN
        EXECUTE 'CREATE INDEX idx_flight_logs_embedding
                 ON flight_logs USING ivfflat (embedding vector_cosine_ops)
                 WITH (lists = 100)';
    END IF;
END $$;

-- ---------------------------------------------------------------------------
-- incidents
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS incidents (
    id                SERIAL PRIMARY KEY,
    flight_id         INTEGER     NOT NULL REFERENCES flights (id) ON DELETE CASCADE,
    incident_time     TIMESTAMPTZ NOT NULL,
    severity          TEXT        NOT NULL,
    category          TEXT        NOT NULL,
    description       TEXT        NOT NULL,
    resolution_status TEXT        NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_incidents_flight_id     ON incidents (flight_id);
CREATE INDEX IF NOT EXISTS idx_incidents_incident_time ON incidents (incident_time);
CREATE INDEX IF NOT EXISTS idx_incidents_severity      ON incidents (severity);
CREATE INDEX IF NOT EXISTS idx_incidents_category      ON incidents (category);
