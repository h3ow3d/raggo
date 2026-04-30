"""Static check that bans raw-SQL string interpolation in `backend/app`.

Phase 3 exit criterion (`PHASE_3_BACKEND_AGENT.md`):

    No raw SQL string interpolation exists in the codebase
    (verified by a lint rule or test).

This test walks every Python module under ``backend/app`` with the
:mod:`ast` module and fails if any of the following appear:

* ``sqlalchemy.text(...)`` (or ``text(...)`` imported from sqlalchemy)
  with anything other than a plain string literal as its first argument.
* ``Connection.execute(...)`` / ``Connection.exec_driver_sql(...)`` /
  ``Session.execute(...)`` whose first argument is a string built via
  f-string, ``%`` formatting, ``str.format(...)``, or ``+``-style
  concatenation. Strings that are SQLAlchemy ``Select`` / ``Insert`` /
  etc. expressions (i.e. anything that's not a string at all) are fine.

A small explicit allowlist covers the legitimate places where a string
literal is passed straight through:

* ``backend/app/main.py`` — ``text("SELECT 1")`` health pings,
  ``text("CREATE EXTENSION IF NOT EXISTS vector")`` bootstrap, and
  ``conn.exec_driver_sql(sql_content)`` where ``sql_content`` is the
  bytes of a domain-pack ``init.sql`` file read from disk.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Iterable, List, Tuple

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
APP_ROOT = REPO_ROOT / "backend" / "app"

# Methods that take SQL as their first positional argument.
SQL_EXECUTE_METHODS: frozenset[str] = frozenset({"execute", "exec_driver_sql", "executemany"})

# Allowlisted (file_relative_to_repo, line) pairs for `exec_driver_sql`
# called with a non-literal argument. Today this is only the init.sql
# runner that loads a vetted on-disk SQL file.
EXEC_DRIVER_SQL_ALLOWLIST: frozenset[Tuple[str, str]] = frozenset(
    {
        # main.py reads the domain pack's init.sql and runs it whole.
        # The argument is file content, not interpolated user input.
        ("backend/app/main.py", "exec_driver_sql"),
    }
)


def _iter_app_python_files() -> Iterable[Path]:
    for path in sorted(APP_ROOT.rglob("*.py")):
        # Skip __pycache__ and similar.
        if any(part.startswith(".") or part == "__pycache__" for part in path.parts):
            continue
        yield path


def _is_string_literal(node: ast.AST) -> bool:
    """True if the node is a plain string-literal Constant."""
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def _is_dynamic_string(node: ast.AST) -> bool:
    """True if the node is *clearly* a dynamically-built string.

    Catches f-strings (``ast.JoinedStr``), ``%`` formatting on a string
    literal, ``str.format(...)`` calls on a string literal, and ``+``
    concatenation involving a string literal.

    We deliberately do not flag plain ``Name`` references — those are
    typically already-built ``Select`` / ``Insert`` SQLAlchemy
    expressions, or vetted file content. Per-call allowlists handle the
    rare exceptions.
    """
    if isinstance(node, ast.JoinedStr):
        return True

    if isinstance(node, ast.BinOp):
        # "..." % args  or  "..." + var
        if isinstance(node.op, (ast.Mod, ast.Add)):
            if _is_string_literal(node.left) or _is_string_literal(node.right):
                return True
            # Recurse into nested concatenations.
            return _is_dynamic_string(node.left) or _is_dynamic_string(node.right)

    if isinstance(node, ast.Call):
        # "...".format(...) where the receiver is a string literal.
        if isinstance(node.func, ast.Attribute) and node.func.attr == "format":
            if _is_string_literal(node.func.value):
                return True

    return False


def _called_name(node: ast.Call) -> str:
    """Return the (rightmost) name of the called function/method."""
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _scan_file(path: Path) -> List[str]:
    """Return a list of human-readable violation messages for `path`."""
    rel = path.relative_to(REPO_ROOT).as_posix()
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:  # pragma: no cover - defensive
        return [f"{rel}:{exc.lineno}: failed to parse: {exc.msg}"]

    violations: List[str] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _called_name(node)

        # 1. sqlalchemy.text(...) must take a string literal.
        if name == "text" and node.args:
            first = node.args[0]
            if not _is_string_literal(first):
                violations.append(
                    f"{rel}:{node.lineno}: sqlalchemy.text(...) called with a "
                    f"non-literal argument; use parameterised SQLAlchemy "
                    f"expressions instead."
                )
            continue

        # 2. .execute(...) / .exec_driver_sql(...) called with a
        #    dynamically-built string.
        if name in SQL_EXECUTE_METHODS and node.args:
            first = node.args[0]
            if _is_dynamic_string(first):
                violations.append(
                    f"{rel}:{node.lineno}: {name}(...) called with a "
                    f"dynamically-built SQL string (f-string / % / .format "
                    f"/ concatenation). Use bound parameters."
                )
                continue

            # Non-literal, non-dynamic (e.g. a Name like `sql_content`)
            # is the grey zone: file-loaded SQL is fine, but only via
            # the explicit allowlist above. We restrict this to
            # `exec_driver_sql`, which is the multi-statement entry
            # point — `execute` with a Name is almost always a
            # SQLAlchemy expression.
            if name == "exec_driver_sql" and not _is_string_literal(first):
                key = (rel, name)
                if key not in EXEC_DRIVER_SQL_ALLOWLIST:
                    violations.append(
                        f"{rel}:{node.lineno}: exec_driver_sql(...) called "
                        f"with a non-literal argument and no allowlist entry."
                    )

    return violations


@pytest.mark.parametrize("path", list(_iter_app_python_files()), ids=lambda p: p.name)
def test_no_raw_sql_interpolation(path: Path) -> None:
    """Each module under ``backend/app`` is free of raw-SQL interpolation."""
    violations = _scan_file(path)
    assert not violations, "Raw-SQL interpolation found:\n  " + "\n  ".join(violations)


def test_app_root_is_scanned() -> None:
    """Sanity check: the test discovered a non-trivial number of files.

    Without this, a future refactor that moves ``backend/app`` would
    silently make the lint test pass by scanning nothing.
    """
    files = list(_iter_app_python_files())
    assert len(files) >= 10, f"Expected to scan many app files, found {len(files)}: {files}"
