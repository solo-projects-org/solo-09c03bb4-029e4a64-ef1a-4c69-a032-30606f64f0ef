"""Parsing and validation of the cohort CSV.

Expected header (exactly these three columns, order-insensitive)::

    subject_id,region,outcome

``outcome`` must be 0 or 1, subject ids must be unique, and every field is
required. The parser works on fully buffered bytes (uploads are size-capped by
the endpoint) and performs *all* validation before any row is written, so a
malformed import never mutates the database.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass

REQUIRED_COLUMNS = ("subject_id", "region", "outcome")


class CsvImportError(ValueError):
    """Raised with a human-readable, row-aware message on invalid input."""


@dataclass(frozen=True)
class CohortRow:
    subject_id: str
    region: str
    outcome: int


def parse_cohort_csv(raw: bytes, max_rows: int) -> list[CohortRow]:
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise CsvImportError("CSV 文件必须是 UTF-8 编码") from exc

    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration as exc:
        raise CsvImportError("CSV 文件为空，缺少表头") from exc

    header = [column.strip() for column in header]
    if set(header) != set(REQUIRED_COLUMNS) or len(header) != len(REQUIRED_COLUMNS):
        raise CsvImportError(
            f"表头必须恰好包含 {list(REQUIRED_COLUMNS)} 三列，实际为 {header}"
        )

    index = {name: header.index(name) for name in REQUIRED_COLUMNS}
    rows: list[CohortRow] = []
    seen_subject_ids: set[str] = set()

    for line_number, raw_row in enumerate(reader, start=2):
        # Skip purely blank lines (e.g. a trailing newline), reject ragged ones.
        if not raw_row or all(cell == "" for cell in raw_row):
            continue
        if len(raw_row) != len(header):
            raise CsvImportError(
                f"第 {line_number} 行字段数为 {len(raw_row)}，应为 {len(header)}"
            )

        subject_id = raw_row[index["subject_id"]].strip()
        region = raw_row[index["region"]].strip()
        outcome_raw = raw_row[index["outcome"]].strip()

        if not subject_id:
            raise CsvImportError(f"第 {line_number} 行 subject_id 不能为空")
        if not region:
            raise CsvImportError(f"第 {line_number} 行 region 不能为空")
        if outcome_raw not in ("0", "1"):
            raise CsvImportError(
                f"第 {line_number} 行 outcome 必须为 0 或 1，实际为 {outcome_raw!r}"
            )
        if subject_id in seen_subject_ids:
            raise CsvImportError(f"第 {line_number} 行 subject_id 重复：{subject_id}")
        seen_subject_ids.add(subject_id)

        rows.append(CohortRow(subject_id=subject_id, region=region, outcome=int(outcome_raw)))
        if len(rows) > max_rows:
            raise CsvImportError(f"导入行数超过上限 {max_rows}")

    if not rows:
        raise CsvImportError("CSV 不包含任何数据行")
    return rows
