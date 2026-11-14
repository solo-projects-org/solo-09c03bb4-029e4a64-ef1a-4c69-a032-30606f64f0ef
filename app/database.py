"""SQLite persistence layer.

The database is the single source of truth for:

* the imported cohort (imports happen at most once);
* every query request, its parameters, its noisy answer and the epsilon it
  consumed;
* the global privacy ledger, with ``remaining`` updated atomically inside the
  same transaction that persists a new query result.

WAL mode plus ``BEGIN IMMEDIATE`` serialise all mutating transactions, which is
what guarantees that concurrent requests can never overspend the budget or
double-charge a retried request id.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from .config import TOTAL_BUDGET, get_settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS import_meta (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    file_name TEXT NOT NULL,
    imported_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    row_count INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS subjects (
    subject_id TEXT PRIMARY KEY,
    region TEXT NOT NULL,
    outcome INTEGER NOT NULL CHECK (outcome IN (0, 1))
);

CREATE INDEX IF NOT EXISTS idx_subjects_region_outcome
    ON subjects (region, outcome);

CREATE TABLE IF NOT EXISTS query_requests (
    request_id TEXT PRIMARY KEY,
    region TEXT NOT NULL,
    outcome INTEGER NOT NULL CHECK (outcome IN (0, 1)),
    epsilon REAL NOT NULL CHECK (epsilon > 0 AND epsilon <= 1),
    true_count INTEGER NOT NULL,
    noisy_result REAL NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS privacy_budget (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    total REAL NOT NULL,
    remaining REAL NOT NULL CHECK (remaining >= 0)
);
"""


def connect(database_path: str | None = None) -> sqlite3.Connection:
    """Open a connection with sane concurrency settings."""
    path = database_path or get_settings().database_path
    # check_same_thread=False: each request gets its own short-lived
    # connection, but FastAPI's sync thread pool (and test clients) may run
    # different requests on different worker threads. WAL + BEGIN IMMEDIATE
    # still serialise all writes, so sharing the driver across threads is safe.
    conn = sqlite3.connect(path, timeout=30.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # Driver-level autocommit: Python must never open an implicit transaction
    # for us, because every mutating path opens its own BEGIN IMMEDIATE
    # explicitly. Works on both Python 3.11 and 3.12+.
    conn.isolation_level = None
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=FULL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def initialize(conn: sqlite3.Connection) -> None:
    """Create the schema and seed the budget row exactly once."""
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT OR IGNORE INTO privacy_budget (id, total, remaining) "
        "VALUES (1, ?, ?)",
        (TOTAL_BUDGET, TOTAL_BUDGET),
    )
    conn.commit()


@contextmanager
def write_transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Open an immediate write transaction.

    ``BEGIN IMMEDIATE`` acquires the WAL write lock up-front, so two
    concurrent budget-consuming requests are serialised and the budget check
    plus deduction are atomic. Any failure rolls the whole transaction back,
    meaning no budget is ever charged for a request whose result is not
    durably saved.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except Exception:
        conn.rollback()
        raise
    else:
        conn.commit()


def get_connection() -> Iterator[sqlite3.Connection]:
    """FastAPI dependency: one short-lived connection per request.

    Kept as a bare generator function (without ``@contextmanager``) because
    FastAPI's dependency system drives the generator itself.
    """
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()
