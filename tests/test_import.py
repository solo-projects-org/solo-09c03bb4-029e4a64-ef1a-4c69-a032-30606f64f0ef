"""Tests for the one-shot CSV import endpoint and validation."""

from __future__ import annotations


def test_health_and_initial_status(client):
    assert client.get("/health").json() == {"status": "ok"}
    status = client.get("/status").json()
    assert status["imported"] is False
    assert status["imported_rows"] == 0
    assert status["total_budget"] == 1.0
    assert status["remaining_budget"] == 1.0


def _import(client, content, name="cohort.csv"):
    return client.post(
        "/import", files={"file": (name, content, "text/csv")}
    )


def test_import_success(client):
    csv_bytes = b"subject_id,region,outcome\nS1,\xe5\x8c\x97\xe4\xba\xac,1\nS2,\xe4\xb8\x8a\xe6\xb5\xb7,0\n"
    resp = _import(client, csv_bytes)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["imported_rows"] == 2
    assert body["distinct_regions"] == 2
    assert client.get("/status").json()["imported"] is True


def test_import_rejects_second_import(imported_client):
    resp = _import(
        imported_client,
        "subject_id,region,outcome\nS9,深圳,1\n".encode("utf-8"),
    )
    assert resp.status_code == 409
    # First cohort must be untouched.
    status = imported_client.get("/status").json()
    assert status["imported_rows"] == 8


def test_import_duplicate_subject_id(client):
    resp = _import(
        client,
        "subject_id,region,outcome\nS1,北京,1\nS1,上海,0\n".encode("utf-8"),
    )
    assert resp.status_code == 400
    assert "重复" in resp.json()["detail"]
    assert client.get("/status").json()["imported"] is False


def test_import_bad_outcome(client):
    resp = _import(
        client,
        "subject_id,region,outcome\nS1,北京,2\n".encode("utf-8"),
    )
    assert resp.status_code == 400
    assert "outcome" in resp.json()["detail"]


def test_import_bad_header(client):
    resp = _import(
        client,
        "id,region,outcome\nS1,北京,1\n".encode("utf-8"),
    )
    assert resp.status_code == 400
    assert "表头" in resp.json()["detail"]


def test_import_empty_file(client):
    assert _import(client, b"").status_code == 400


def test_import_no_data_rows(client):
    resp = _import(client, b"subject_id,region,outcome\n")
    assert resp.status_code == 400


def test_import_blank_fields(client):
    resp = _import(
        client,
        "subject_id,region,outcome\nS1,,1\n".encode("utf-8"),
    )
    assert resp.status_code == 400


def test_import_ragged_row(client):
    resp = _import(
        client,
        "subject_id,region,outcome\nS1,北京,1,extra\n".encode("utf-8"),
    )
    assert resp.status_code == 400


def test_query_before_import_is_rejected_and_free(client):
    resp = client.post(
        "/query",
        json={"request_id": "q1", "region": "北京", "outcome": 1, "epsilon": 0.1},
    )
    assert resp.status_code == 409
    budget = client.get("/budget").json()
    assert budget["remaining_budget"] == 1.0
