# syntax=docker/dockerfile:1
#
# postgres-pgvector image for raggo.
#
# Thin wrapper around the upstream pgvector/pgvector image that bakes in
# the project's minimal init.sql (which enables the `vector` extension).
# Domain-specific schemas are still applied by the backend at runtime.
#
# This image is published to ghcr.io/h3ow3d/raggo/postgres-pgvector and
# is consumed unchanged by the Helm chart and the air-gap bundle.

FROM pgvector/pgvector:pg16

# init.sql runs once on first container start via the upstream
# entrypoint (`/docker-entrypoint-initdb.d/`). It is owned by root and
# read-only, which matches the upstream image conventions.
COPY init.sql /docker-entrypoint-initdb.d/00-init.sql

# The upstream pgvector image does not ship a HEALTHCHECK. Add a basic
# one using `pg_isready` shipped with the postgres image. The default
# POSTGRES_USER value is `postgres`, which `pg_isready` defaults to.
HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=10 \
    CMD pg_isready -U "${POSTGRES_USER:-postgres}" -d "${POSTGRES_DB:-postgres}" || exit 1
