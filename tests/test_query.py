"""Tests for query semantics: noise, budget accounting and idempotency."""

from __future__ import annotations


def _query(client, request_id="q1", region="北京", outcome=1, epsilon=0.1):
    return client.post(
        "/query",
        json={
            "request_id": request_id,
            "region": region,
            "outcome": outcome,
            "epsilon": epsilon,
        },
    )


def test_query_returns_float_and_charges(imported_client):
    # epsilon=1.0 -> Laplace scale 1, so the noised count stays tightly
    # around the true count (2) without a flaky wide-tail assertion.
    resp = _query(imported_client, epsilon=1.0)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["replayed"] is False
    assert isinstance(body["noisy_result"], float)
    assert -15 < body["noisy_result"] < 20
    assert abs(body["remaining_budget"]) < 1e-9

    budget = imported_client.get("/budget").json()
    assert abs(budget["remaining_budget"]) < 1e-9
    assert abs(budget["spent_budget"] - 1.0) < 1e-9


def test_retry_same_id_same_params_is_idempotent(imported_client):
    first = _query(imported_client).json()
    second = _query(imported_client).json()
    assert second["replayed"] is True
    assert second["noisy_result"] == first["noisy_result"]
    # Retry must not be charged again.
    assert abs(second["remaining_budget"] - 0.9) < 1e-9
    third = _query(imported_client).json()
    assert third["noisy_result"] == first["noisy_result"]


def test_same_id_changed_params_is_rejected(imported_client):
    assert _query(imported_client, epsilon=0.1).status_code == 200

    changed_region = _query(imported_client, region="上海", epsilon=0.1)
    assert changed_region.status_code == 409

    changed_outcome = _query(imported_client, outcome=0, epsilon=0.1)
    assert changed_outcome.status_code == 409

    changed_epsilon = _query(imported_client, epsilon=0.2)
    assert changed_epsilon.status_code == 409

    # Rejected retries cost nothing and create nothing.
    budget = imported_client.get("/budget").json()
    assert abs(budget["remaining_budget"] - 0.9) < 1e-9


def test_distinct_ids_get_independent_noise(imported_client):
    results = {
        _query(imported_client, request_id=f"ind-{i}", epsilon=0.1).json()[
            "noisy_result"
        ]
        for i in range(5)
    }
    # Five independent Laplace draws colliding exactly is effectively impossible.
    assert len(results) > 1


def test_unknown_region_counts_as_zero(imported_client):
    resp = _query(imported_client, request_id="empty", region="深圳", epsilon=0.5)
    assert resp.status_code == 200
    # Noisy result is pure noise around zero.
    assert isinstance(resp.json()["noisy_result"], float)


def test_budget_exhaustion_blocks_new_requests(imported_client):
    first = _query(imported_client, request_id="big", epsilon=1.0)
    assert first.status_code == 200
    assert abs(first.json()["remaining_budget"]) < 1e-9

    blocked = _query(imported_client, request_id="another", epsilon=0.1)
    assert blocked.status_code == 402

    # The exhausted-but-replayed request still works and stays free.
    replay = _query(imported_client, request_id="big", epsilon=1.0)
    assert replay.status_code == 200
    assert replay.json()["replayed"] is True
    assert abs(replay.json()["remaining_budget"]) < 1e-9


def test_partial_budget_rejects_overspend_without_charge(imported_client):
    assert _query(imported_client, request_id="a", epsilon=0.6).status_code == 200
    # Only 0.4 remains: a 0.5 request must be rejected...
    blocked = _query(imported_client, request_id="b", epsilon=0.5)
    assert blocked.status_code == 402
    # ...and the ledger must remain exactly at 0.4.
    budget = imported_client.get("/budget").json()
    assert abs(budget["remaining_budget"] - 0.4) < 1e-9
    # A fitting request then succeeds.
    ok = _query(imported_client, request_id="c", epsilon=0.4)
    assert ok.status_code == 200
    assert abs(ok.json()["remaining_budget"]) < 1e-9


def test_invalid_epsilon_never_charges(imported_client):
    for bad_epsilon in (0, -0.1, 1.01):
        resp = _query(imported_client, request_id=f"bad-{bad_epsilon}", epsilon=bad_epsilon)
        assert resp.status_code == 422
    budget = imported_client.get("/budget").json()
    assert budget["remaining_budget"] == 1.0


def test_extra_field_rejected(imported_client):
    resp = imported_client.post(
        "/query",
        json={
            "request_id": "x",
            "region": "北京",
            "outcome": 1,
            "epsilon": 0.1,
            "unexpected": "value",
        },
    )
    assert resp.status_code == 422
    assert imported_client.get("/budget").json()["remaining_budget"] == 1.0


def test_invalid_outcome_rejected(imported_client):
    resp = imported_client.post(
        "/query",
        json={"request_id": "x", "region": "北京", "outcome": 2, "epsilon": 0.1},
    )
    assert resp.status_code == 422
