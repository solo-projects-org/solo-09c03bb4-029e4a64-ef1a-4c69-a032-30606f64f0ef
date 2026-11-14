"""Unit tests for CSV parsing edge cases."""

from __future__ import annotations

import pytest

from app.csv_import import CsvImportError, parse_cohort_csv


def test_parses_bom_and_crlf():
    raw = b"\xef\xbb\xbfsubject_id,region,outcome\r\nS1,\xe5\x8c\x97\xe4\xba\xac,1\r\n"
    rows = parse_cohort_csv(raw, max_rows=100)
    assert len(rows) == 1
    assert rows[0].subject_id == "S1"
    assert rows[0].region == "北京"
    assert rows[0].outcome == 1


def test_header_order_insensitive():
    raw = b"outcome,subject_id,region\n0,S1,\xe5\x8c\x97\xe4\xba\xac\n"
    rows = parse_cohort_csv(raw, max_rows=100)
    assert rows[0] is not None
    assert rows[0].outcome == 0
    assert rows[0].subject_id == "S1"


def test_extra_column_rejected():
    with pytest.raises(CsvImportError, match="表头"):
        parse_cohort_csv(
            "subject_id,region,outcome,extra\nS1,北京,1,x\n".encode(), 100
        )


def test_duplicate_id_rejected():
    with pytest.raises(CsvImportError, match="重复"):
        parse_cohort_csv(
            "subject_id,region,outcome\nS1,北京,1\nS1,上海,0\n".encode(),
            100,
        )


def test_outcome_must_be_binary():
    with pytest.raises(CsvImportError, match="outcome"):
        parse_cohort_csv("subject_id,region,outcome\nS1,北京,2\n".encode(), 100)


def test_max_rows_enforced():
    raw = b"subject_id,region,outcome\nS1,a,0\nS2,b,1\n"
    with pytest.raises(CsvImportError, match="上限"):
        parse_cohort_csv(raw, max_rows=1)


def test_blank_lines_are_skipped():
    raw = b"subject_id,region,outcome\nS1,a,0\n\nS2,b,1\n\n"
    rows = parse_cohort_csv(raw, max_rows=100)
    assert len(rows) == 2


def test_non_utf8_rejected():
    with pytest.raises(CsvImportError, match="UTF-8"):
        parse_cohort_csv(b"subject_id,region,outcome\nS1,\xff\xfe,0\n", 100)
