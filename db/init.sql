-- raggo database initialization
-- Runs automatically on first PostgreSQL container start
-- (mounted at /docker-entrypoint-initdb.d/init.sql).
--
-- Minimal stub that only enables the pgvector extension.
-- Domain-specific schemas are loaded by the backend at startup.

CREATE EXTENSION IF NOT EXISTS vector;
