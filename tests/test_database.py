"""Direct tests for the transaction and ledger primitives."""

from __future__ import annotations

import sqlite3

import pytest

from app.database import connect, initialize, write_transaction


def test_write_transaction_rolls_back_on_failure(db_path):
    conn = connect(db_path)
    initialize(conn)

    before = conn.execute(
        "SELECT remaining FROM privacy_budget WHERE id = 1"
    ).fetchone()["remaining"]

    with pytest.raises(RuntimeError):
        with write_transaction(conn) as tx:
            tx.execute(
                "UPDATE privacy_budget SET remaining = remaining - 0.3 WHERE id = 1"
            )
            raise RuntimeError("simulated save failure")

    after = conn.execute(
        "SELECT remaining FROM privacy_budget WHERE id = 1"
    ).fetchone()["remaining"]
    # A failed transaction must leave the ledger exactly as it was.
    assert after == before
    conn.close()


def test_budget_check_constraint_forbids_negative(db_path):
    conn = connect(db_path)
    initialize(conn)
    with pytest.raises(sqlite3.IntegrityError):
        with write_transaction(conn) as tx:
            tx.execute(
                "UPDATE privacy_budget SET remaining = -0.01 WHERE id = 1"
            )
    conn.close()


def test_epsilon_column_constraints(db_path, imported_client):
    # The schema itself rejects out-of-range epsilon even if API validation
    # were bypassed.
    conn = connect(db_path)
    with pytest.raises(sqlite3.IntegrityError):
        with write_transaction(conn) as tx:
            tx.execute(
                "INSERT INTO query_requests "
                "(request_id, region, outcome, epsilon, true_count, noisy_result) "
                "VALUES ('x', '北京', 1, 0.0, 0, 0.0)"
            )
    conn.close()
