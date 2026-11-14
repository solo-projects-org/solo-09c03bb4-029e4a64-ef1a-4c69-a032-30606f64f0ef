"""Concurrency and restart-persistence tests.

These exercise the real SQLite write-lock path: several OS threads open their
own connections against the same database file, exactly like multiple
uvicorn worker requests (or multiple processes) would.
"""

from __future__ import annotations

import threading

SAMPLE_CSV = (
    "subject_id,region,outcome\n"
    "S1,北京,1\nS2,北京,0\nS3,北京,1\nS4,北京,0\n"
    "S5,上海,0\nS6,上海,1\nS7,上海,0\nS8,上海,1\n"
).encode("utf-8")


def _post(client, payload, results, index):
    results[index] = client.post("/query", json=payload)


def test_concurrent_same_request_id_charged_once(make_client, db_path):
    # Seed data through one client.
    seed = make_client()
    resp = seed.post("/import", files={"file": ("c.csv", SAMPLE_CSV, "text/csv")})
    assert resp.status_code == 201

    n_threads = 10
    payload = {"request_id": "race-1", "region": "北京", "outcome": 1, "epsilon": 0.25}
    results: list = [None] * n_threads
    threads = []
    for i in range(n_threads):
        client = make_client()
        t = threading.Thread(target=_post, args=(client, payload, results, i))
        threads.append(t)

    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert all(r.status_code == 200 for r in results)
    bodies = [r.json() for r in results]
    # Every caller sees the identical noisy answer...
    noisy_values = {b["noisy_result"] for b in bodies}
    assert len(noisy_values) == 1
    # ...exactly one request was charged, all others were replays.
    assert sum(1 for b in bodies if b["replayed"] is False) == 1
    assert sum(1 for b in bodies if b["replayed"] is True) == n_threads - 1
    assert all(abs(b["remaining_budget"] - 0.75) < 1e-9 for b in bodies)


def test_concurrent_distinct_requests_never_overspend(make_client):
    seed = make_client()
    assert seed.post(
        "/import", files={"file": ("c.csv", SAMPLE_CSV, "text/csv")}
    ).status_code == 201

    n_threads = 10  # each asks epsilon 0.2, total demand 2.0 against budget 1.0
    results: list = [None] * n_threads
    threads = []
    for i in range(n_threads):
        client = make_client()
        payload = {
            "request_id": f"race-{i}",
            "region": "北京",
            "outcome": 1,
            "epsilon": 0.2,
        }
        t = threading.Thread(target=_post, args=(client, payload, results, i))
        threads.append(t)

    for t in threads:
        t.start()
    for t in threads:
        t.join()

    succeeded = [r for r in results if r.status_code == 200]
    rejected = [r for r in results if r.status_code == 402]
    assert len(succeeded) == 5
    assert len(rejected) == 5

    budget = seed.get("/budget").json()
    assert abs(budget["spent_budget"] - 1.0) < 1e-9
    assert abs(budget["remaining_budget"]) < 1e-9
    # Every successful response already reported the post-charge ledger.
    for r in succeeded:
        assert r.json()["remaining_budget"] >= -1e-12


def test_restart_replay_returns_saved_value(make_client):
    first_client = make_client()
    assert first_client.post(
        "/import", files={"file": ("c.csv", SAMPLE_CSV, "text/csv")}
    ).status_code == 201
    original = first_client.post(
        "/query",
        json={"request_id": "persist-1", "region": "上海", "outcome": 1, "epsilon": 0.3},
    ).json()

    # Simulate a process restart: a brand-new client reinitialises against
    # the same database file (as the container would after being recreated).
    restarted = make_client()
    with restarted:
        status = restarted.get("/status").json()
        assert status["imported"] is True
        replay = restarted.post(
            "/query",
            json={"request_id": "persist-1", "region": "上海", "outcome": 1, "epsilon": 0.3},
        )

    assert replay.status_code == 200
    body = replay.json()
    assert body["replayed"] is True
    assert body["noisy_result"] == original["noisy_result"]
    assert abs(body["remaining_budget"] - 0.7) < 1e-9

    # A fresh request id after restart keeps drawing from the same ledger.
    fresh = restarted.post(
        "/query",
        json={"request_id": "persist-2", "region": "上海", "outcome": 0, "epsilon": 0.7},
    )
    assert fresh.status_code == 200
    assert abs(fresh.json()["remaining_budget"]) < 1e-9
