"""Application configuration.

All settings come from environment variables so the same image can run in
Docker Compose and in the test suite without code changes.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


#: Global privacy budget. The specification fixes the total at 1.
TOTAL_BUDGET = 1.0

#: Absolute tolerance when comparing two epsilon values and when deciding
#: whether the global ledger can still afford a request. It only absorbs
#: floating-point round-off, it never relaxes the 0 < epsilon <= 1 rule.
EPSILON_TOLERANCE = 1e-9

#: Hard cap on an uploaded CSV file (10 MiB), to avoid unbounded memory use.
MAX_UPLOAD_BYTES = 10 * 1024 * 1024

#: Maximum number of rows accepted in a single CSV import.
MAX_IMPORT_ROWS = 1_000_000


@dataclass(frozen=True)
class Settings:
    database_path: str
    total_budget: float = TOTAL_BUDGET
    epsilon_tolerance: float = EPSILON_TOLERANCE
    max_upload_bytes: int = MAX_UPLOAD_BYTES
    max_import_rows: int = MAX_IMPORT_ROWS


def get_settings() -> Settings:
    return Settings(database_path=os.getenv("DPAPI_DB_PATH", "/data/dpapi.db"))
