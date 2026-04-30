"""Backend CLI used by the Helm `migrate` and `seed` jobs.

The FastAPI application also performs migration and seeding during its
lifespan, but Kubernetes deployments split those concerns into Helm
hooks so that:

- `migrate` runs as a `pre-install`/`pre-upgrade` job before the
  backend Deployment is rolled out.
- `seed` runs as a `post-install` (and optionally `post-upgrade`)
  job, gated by `seed.enabled`.

Both subcommands share the same settings and database engine as the
running backend, so behaviour stays identical to the Compose stack.

Subcommands:

    python -m app.cli migrate   # wait for DB, enable pgvector, run init.sql
    python -m app.cli seed      # run domain pack seed if data is absent
    python -m app.cli check     # connect to the DB and SELECT 1; used by helm test

Each subcommand exits non-zero on failure so Helm marks the hook as
failed and the install/upgrade is rolled back.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from typing import Sequence

from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.core.config import get_settings
from app.core.database import engine, session_scope
from app.core.domain import DomainPack, load_domain

logger = logging.getLogger("app.cli")


def _wait_for_database(max_attempts: int = 120, delay_seconds: float = 1.0) -> None:
    """Block until PostgreSQL accepts connections.

    Defaults are intentionally generous so the migrate hook tolerates a
    cold StatefulSet boot on a freshly provisioned PVC.
    """
    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            logger.info("Database is reachable (attempt %d).", attempt)
            return
        except OperationalError as exc:
            last_error = exc
            logger.info("Database not ready yet (attempt %d/%d)…", attempt, max_attempts)
            time.sleep(delay_seconds)
    raise RuntimeError(f"Database did not become ready in time: {last_error}")


def _ensure_pgvector_extension() -> None:
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))


def _run_domain_init_sql(domain: DomainPack) -> None:
    """Apply the domain's `init.sql` if its first table is missing.

    This mirrors the lifespan-time logic in `app.main` so the hook stays
    a no-op when re-applied to an already-initialised database.
    """
    from pathlib import Path

    if not domain.embeddable_resources:
        logger.info("Domain has no embeddable resources; skipping init.sql.")
        return

    table_name = domain.embeddable_resources[0].model.__tablename__
    from sqlalchemy import inspect

    if table_name in inspect(engine).get_table_names():
        logger.info("Domain table %r already exists; skipping init.sql.", table_name)
        return

    init_sql_path = Path(domain.init_sql_path) if domain.init_sql_path else None
    if init_sql_path is None or not init_sql_path.exists():
        logger.warning("Domain init.sql not found at %s", domain.init_sql_path)
        return

    logger.info("Applying domain init.sql from %s", init_sql_path)
    sql = init_sql_path.read_text()
    with engine.begin() as conn:
        conn.exec_driver_sql(sql)
    logger.info("Domain init.sql applied successfully.")


def cmd_migrate(_: argparse.Namespace) -> int:
    settings = get_settings()
    logger.info("Running migrate for domain=%s", settings.raggo_domain)
    _wait_for_database()
    _ensure_pgvector_extension()
    domain = load_domain(settings.raggo_domain)
    _run_domain_init_sql(domain)
    logger.info("Migrate complete.")
    return 0


def cmd_seed(_: argparse.Namespace) -> int:
    settings = get_settings()
    logger.info("Running seed for domain=%s", settings.raggo_domain)
    _wait_for_database()
    domain = load_domain(settings.raggo_domain)
    with session_scope() as session:
        result = domain.seed(session)
    logger.info("Seed result: %s", result)
    return 0


def cmd_check(_: argparse.Namespace) -> int:
    """Lightweight connectivity probe used by `helm test`."""
    _wait_for_database(max_attempts=10, delay_seconds=1.0)
    logger.info("Database connectivity OK.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="app.cli", description="raggo backend CLI")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate", help="Apply pgvector and active domain schema").set_defaults(
        func=cmd_migrate
    )
    sub.add_parser("seed", help="Seed active domain data if empty").set_defaults(func=cmd_seed)
    sub.add_parser("check", help="Probe database connectivity").set_defaults(func=cmd_check)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
