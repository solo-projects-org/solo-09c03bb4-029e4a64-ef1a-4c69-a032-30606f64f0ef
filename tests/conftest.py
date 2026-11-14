"""Shared pytest fixtures.

Each test runs against an isolated SQLite file. Multiple clients pointing at
the same file are used to simulate real concurrency and process restarts.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

SAMPLE_CSV = (
    "subject_id,region,outcome\n"
    "S1,北京,1\nS2,北京,0\nS3,北京,1\nS4,北京,0\n"
    "S5,上海,0\nS6,上海,1\nS7,上海,0\nS8,上海,1\n"
).encode("utf-8")


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "test_dpapi.db"
    monkeypatch.setenv("DPAPI_DB_PATH", str(path))
    return str(path)


@pytest.fixture
def client(db_path):
    # Imported lazily so the DPAPI_DB_PATH environment variable is read at
    # connection time rather than at module import.
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def make_client(db_path):
    """Factory producing independent, already-started clients on one DB file."""
    from app.main import app

    created: list[TestClient] = []

    def _make():
        c = TestClient(app)
        # Start the lifespan (and its portal) without leaving the with-block:
        # close() in teardown runs the matching __exit__.
        c.__enter__()
        created.append(c)
        return c

    yield _make

    for c in created:
        c.close()


@pytest.fixture
def imported_client(client):
    resp = client.post(
        "/import", files={"file": ("cohort.csv", SAMPLE_CSV, "text/csv")}
    )
    assert resp.status_code == 201, resp.text
    return client
