"""Contract: every domain pack seeds non-zero rows on a fresh DB."""

from __future__ import annotations


def test_seed_inserts_rows(domain_pack, db_session):
    # Each pack's stats() is the canonical "primary tables" view.
    stats = domain_pack.stats(db_session)
    # Every value in the stats dict must be an integer; at least one
    # primary table must have rows.
    assert stats, f"Pack {domain_pack.name} returned empty stats dict"
    counts = [v for v in stats.values() if isinstance(v, int)]
    assert any(c > 0 for c in counts), f"Pack {domain_pack.name} stats showed zero rows: {stats}"


def test_seed_is_idempotent(domain_pack, db_session):
    """Re-running seed without ``force`` is a no-op."""
    before = domain_pack.stats(db_session)
    domain_pack.seed(db_session)  # default force=False
    db_session.commit()
    after = domain_pack.stats(db_session)
    assert before == after


def test_pack_has_required_metadata(domain_pack):
    """Every pack must define the contract surface the agent relies on."""
    assert domain_pack.name
    assert domain_pack.embeddable_resources, "pack must declare ≥1 embeddable resource"
    assert domain_pack.sql_tools, "pack must declare ≥1 safe SQL tool"
    assert domain_pack.intent_rules is not None
    assert callable(domain_pack.evidence_formatter)
    assert domain_pack.domain_context
