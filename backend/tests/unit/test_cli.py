"""Unit tests for ``app.cli``.

The CLI is consumed by the Helm `migrate`, `seed`, and `helm test`
hooks. These tests exercise control flow (subcommand wiring, retry
behaviour on `OperationalError`, idempotent init.sql application,
seed delegation) with mocks so the suite does not need a live
Postgres+pgvector. End-to-end behaviour against a real database is
already covered by the contract suite via `app.main`'s lifespan,
which shares its implementation with the CLI.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from app import cli
from sqlalchemy.exc import OperationalError


@pytest.fixture(autouse=True)
def _no_real_sleep():
    """Patch out `time.sleep` so retry loops don't hold up the suite."""
    with patch.object(cli.time, "sleep") as sleep:
        yield sleep


def _engine_connect_ok() -> MagicMock:
    """Build a MagicMock engine.connect() context manager that succeeds."""
    conn = MagicMock()
    cm = MagicMock()
    cm.__enter__.return_value = conn
    cm.__exit__.return_value = False
    return cm


def _engine_begin_ok() -> MagicMock:
    """Same shape as `_engine_connect_ok` but for `engine.begin()`."""
    return _engine_connect_ok()


def test_build_parser_wires_all_subcommands() -> None:
    parser = cli.build_parser()
    args = parser.parse_args(["migrate"])
    assert args.func is cli.cmd_migrate
    args = parser.parse_args(["seed"])
    assert args.func is cli.cmd_seed
    args = parser.parse_args(["check"])
    assert args.func is cli.cmd_check


def test_build_parser_requires_subcommand() -> None:
    parser = cli.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])


def test_wait_for_database_returns_when_reachable() -> None:
    with patch.object(cli, "engine") as engine:
        engine.connect.return_value = _engine_connect_ok()
        cli._wait_for_database(max_attempts=3, delay_seconds=0)
        engine.connect.assert_called_once()


def test_wait_for_database_retries_then_succeeds(_no_real_sleep) -> None:
    with patch.object(cli, "engine") as engine:
        engine.connect.side_effect = [
            OperationalError("stmt", {}, Exception("boom")),
            OperationalError("stmt", {}, Exception("boom")),
            _engine_connect_ok(),
        ]
        cli._wait_for_database(max_attempts=5, delay_seconds=0)
        assert engine.connect.call_count == 3
        assert _no_real_sleep.call_count == 2


def test_wait_for_database_gives_up() -> None:
    with patch.object(cli, "engine") as engine:
        engine.connect.side_effect = OperationalError("stmt", {}, Exception("nope"))
        with pytest.raises(RuntimeError, match="did not become ready"):
            cli._wait_for_database(max_attempts=2, delay_seconds=0)


def test_ensure_pgvector_extension_runs_create() -> None:
    with patch.object(cli, "engine") as engine:
        ctx = _engine_begin_ok()
        engine.begin.return_value = ctx
        cli._ensure_pgvector_extension()
        conn = ctx.__enter__.return_value
        assert conn.execute.call_count == 1
        # Argument is a SQLAlchemy text() clause; the rendered SQL is
        # a literal so we can compare on its string representation.
        rendered = str(conn.execute.call_args.args[0])
        assert "CREATE EXTENSION IF NOT EXISTS vector" in rendered


def _domain_with_resource(table_name: str, init_sql: str | None) -> MagicMock:
    """Build a DomainPack-shaped mock with a single embeddable resource."""
    domain = MagicMock()
    resource = MagicMock()
    resource.model.__tablename__ = table_name
    domain.embeddable_resources = (resource,)
    domain.init_sql_path = init_sql
    return domain


def test_run_domain_init_sql_skips_when_no_resources() -> None:
    domain = MagicMock()
    domain.embeddable_resources = ()
    # Should return without touching the engine; no exception is enough.
    cli._run_domain_init_sql(domain)


def test_run_domain_init_sql_skips_when_table_exists() -> None:
    domain = _domain_with_resource("flight_logs", "/nope/init.sql")
    inspector = MagicMock()
    inspector.get_table_names.return_value = ["flight_logs", "flights"]
    with (
        patch.object(cli, "engine"),
        patch("app.cli.inspect", return_value=inspector),
    ):
        cli._run_domain_init_sql(domain)
        # No begin() call because we returned before applying SQL.
        assert not cli.engine.begin.called  # type: ignore[attr-defined]


def test_run_domain_init_sql_warns_when_path_missing(tmp_path: Path) -> None:
    domain = _domain_with_resource("flight_logs", str(tmp_path / "nope.sql"))
    inspector = MagicMock()
    inspector.get_table_names.return_value = []
    with (
        patch.object(cli, "engine") as engine,
        patch("app.cli.inspect", return_value=inspector),
    ):
        cli._run_domain_init_sql(domain)
        # Path missing → no exec_driver_sql.
        engine.begin.assert_not_called()


def test_run_domain_init_sql_warns_when_path_none() -> None:
    domain = _domain_with_resource("flight_logs", None)
    inspector = MagicMock()
    inspector.get_table_names.return_value = []
    with (
        patch.object(cli, "engine") as engine,
        patch("app.cli.inspect", return_value=inspector),
    ):
        cli._run_domain_init_sql(domain)
        engine.begin.assert_not_called()


def test_run_domain_init_sql_applies_sql(tmp_path: Path) -> None:
    sql_path = tmp_path / "init.sql"
    sql_path.write_text("CREATE TABLE flight_logs ();")
    domain = _domain_with_resource("flight_logs", str(sql_path))
    inspector = MagicMock()
    inspector.get_table_names.return_value = []
    with (
        patch.object(cli, "engine") as engine,
        patch("app.cli.inspect", return_value=inspector),
    ):
        ctx = _engine_begin_ok()
        engine.begin.return_value = ctx
        cli._run_domain_init_sql(domain)
        conn = ctx.__enter__.return_value
        conn.exec_driver_sql.assert_called_once_with("CREATE TABLE flight_logs ();")


def test_cmd_migrate_runs_full_pipeline() -> None:
    domain = MagicMock()
    settings = MagicMock(raggo_domain="flights")
    with (
        patch.object(cli, "get_settings", return_value=settings),
        patch.object(cli, "_wait_for_database") as wait,
        patch.object(cli, "_ensure_pgvector_extension") as ensure,
        patch.object(cli, "load_domain", return_value=domain) as load,
        patch.object(cli, "_run_domain_init_sql") as run_init,
    ):
        rc = cli.cmd_migrate(MagicMock())
        assert rc == 0
        wait.assert_called_once()
        ensure.assert_called_once()
        load.assert_called_once_with("flights")
        run_init.assert_called_once_with(domain)


def test_cmd_seed_invokes_domain_seed() -> None:
    domain = MagicMock()
    domain.seed.return_value = {"flights": 5, "skipped": False}
    settings = MagicMock(raggo_domain="flights")
    session = MagicMock()
    scope = MagicMock()
    scope.__enter__.return_value = session
    scope.__exit__.return_value = False
    with (
        patch.object(cli, "get_settings", return_value=settings),
        patch.object(cli, "_wait_for_database"),
        patch.object(cli, "load_domain", return_value=domain),
        patch.object(cli, "session_scope", return_value=scope),
    ):
        rc = cli.cmd_seed(MagicMock())
        assert rc == 0
        domain.seed.assert_called_once_with(session)


def test_cmd_check_probes_database() -> None:
    with patch.object(cli, "_wait_for_database") as wait:
        rc = cli.cmd_check(MagicMock())
        assert rc == 0
        wait.assert_called_once_with(max_attempts=10, delay_seconds=1.0)


def test_main_dispatches_to_subcommand() -> None:
    with patch.object(cli, "cmd_migrate", return_value=0) as migrate:
        rc = cli.main(["migrate"])
        assert rc == 0
        migrate.assert_called_once()


def test_main_returns_zero_when_handler_returns_none() -> None:
    with patch.object(cli, "cmd_check", return_value=None):
        rc = cli.main(["check"])
        assert rc == 0


def test_main_propagates_nonzero_exit_code() -> None:
    with patch.object(cli, "cmd_seed", return_value=2):
        rc = cli.main(["seed"])
        assert rc == 2
