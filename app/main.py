"""FastAPI application: privacy-budgeted cohort count service.

Endpoints
---------
``POST /import``  one-shot CSV cohort import (rejected once data exists)
``POST /query``   Laplace-noised count by region/outcome with epsilon cost
``GET  /budget``  current global privacy budget
``GET  /status``  import + budget summary
``GET  /health``  liveness probe
``GET  /docs``    Swagger UI (auto-generated)
"""

from __future__ import annotations

import sqlite3
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from .config import get_settings
from .csv_import import CsvImportError, parse_cohort_csv
from .database import connect, get_connection, initialize, write_transaction
from .privacy import laplace_noise
from .schemas import (
    BudgetResponse,
    ImportResponse,
    QueryRequest,
    QueryResponse,
    StatusResponse,
)

settings = get_settings()
ConnectionDep = Annotated[sqlite3.Connection, Depends(get_connection)]


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize on startup so the schema and seeded budget row exist before
    # the first request, including after a container restart.
    with connect() as conn:
        initialize(conn)
    yield


app = FastAPI(
    title="隐私预算队列统计 API",
    description=(
        "按地区与 0/1 结局返回加 Laplace 噪声的随访人数估计。"
        "全局隐私预算初始为 1，每次新请求按 epsilon 扣费；"
        "同一 request_id 重试幂等返回原值且不再扣费。"
    ),
    version="1.0.0",
    lifespan=lifespan,
)


@app.exception_handler(CsvImportError)
async def csv_import_error_handler(_request, exc: CsvImportError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


# --------------------------------------------------------------------------- #
# One-shot import
# --------------------------------------------------------------------------- #
@app.post(
    "/import",
    response_model=ImportResponse,
    status_code=201,
    summary="首次导入队列 CSV（仅允许一次）",
)
async def import_cohort(
    conn: ConnectionDep,
    file: Annotated[UploadFile, File(description="含 subject_id,region,outcome 三列的 CSV")],
) -> ImportResponse:
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="上传文件为空")
    if len(raw) > settings.max_upload_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"文件超过大小上限 {settings.max_upload_bytes} 字节",
        )

    # Full validation happens before any database write.
    rows = parse_cohort_csv(raw, settings.max_import_rows)

    try:
        with write_transaction(conn) as tx:
            already = tx.execute("SELECT 1 FROM import_meta WHERE id = 1").fetchone()
            if already is not None:
                # Raising inside the transaction triggers a rollback; nothing
                # about the existing cohort changes.
                raise HTTPException(
                    status_code=409,
                    detail="队列数据已导入过，本服务不支持替换或重复导入",
                )

            tx.executemany(
                "INSERT INTO subjects (subject_id, region, outcome) VALUES (?, ?, ?)",
                [(row.subject_id, row.region, row.outcome) for row in rows],
            )
            distinct_regions = tx.execute(
                "SELECT COUNT(DISTINCT region) AS n FROM subjects"
            ).fetchone()["n"]
            tx.execute(
                "INSERT INTO import_meta (id, file_name, row_count) VALUES (1, ?, ?)",
                (file.filename or "upload.csv", len(rows)),
            )
    except sqlite3.IntegrityError as exc:
        # Defense in depth: a duplicate subject_id that survived parsing.
        raise HTTPException(status_code=400, detail=f"导入数据违反唯一性约束：{exc}") from exc

    return ImportResponse(
        file_name=file.filename or "upload.csv",
        imported_rows=len(rows),
        distinct_regions=distinct_regions,
    )


# --------------------------------------------------------------------------- #
# Privacy-budgeted query
# --------------------------------------------------------------------------- #
@app.post(
    "/query",
    response_model=QueryResponse,
    summary="按地区/结局查询加噪人数并扣减隐私预算",
)
async def query_count(payload: QueryRequest, conn: ConnectionDep) -> QueryResponse:
    """Return a Laplace(1/epsilon)-noised count.

    Concurrency and failure semantics are entirely enforced by one
    ``BEGIN IMMEDIATE`` transaction:

    * a retried ``request_id`` with identical parameters returns the stored
      noisy result and is never charged again;
    * the same id with different parameters is rejected (409);
    * budget check, noise generation, result insert and ledger update commit
      together, so a failed/save-error request leaves no result and no charge;
    * concurrent requests are serialized on the write lock and cannot
      overspend, because each sees the ledger state left by the previous one.
    """
    tol = settings.epsilon_tolerance

    with write_transaction(conn) as tx:
        if tx.execute("SELECT 1 FROM import_meta WHERE id = 1").fetchone() is None:
            raise HTTPException(status_code=409, detail="尚未导入队列数据，请先调用 /import")

        existing = tx.execute(
            "SELECT region, outcome, epsilon, noisy_result "
            "FROM query_requests WHERE request_id = ?",
            (payload.request_id,),
        ).fetchone()

        if existing is not None:
            same_parameters = (
                existing["region"] == payload.region
                and existing["outcome"] == payload.outcome
                and abs(existing["epsilon"] - payload.epsilon) <= tol
            )
            if not same_parameters:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"request_id={payload.request_id!r} 已绑定其他参数，"
                        "同号改参数被拒绝；请使用新的 request_id"
                    ),
                )
            # Idempotent replay: reuse the stored answer, charge nothing.
            remaining = tx.execute(
                "SELECT remaining FROM privacy_budget WHERE id = 1"
            ).fetchone()["remaining"]
            return QueryResponse(
                request_id=payload.request_id,
                region=payload.region,
                outcome=payload.outcome,
                epsilon=existing["epsilon"],
                noisy_result=existing["noisy_result"],
                remaining_budget=remaining,
                replayed=True,
            )

        ledger = tx.execute(
            "SELECT total, remaining FROM privacy_budget WHERE id = 1"
        ).fetchone()
        if ledger["remaining"] + tol < payload.epsilon:
            raise HTTPException(
                status_code=402,
                detail=(
                    f"剩余预算 {ledger['remaining']:.9g} 不足以支付 "
                    f"epsilon={payload.epsilon:g}"
                ),
            )

        true_count = tx.execute(
            "SELECT COUNT(*) AS n FROM subjects WHERE region = ? AND outcome = ?",
            (payload.region, payload.outcome),
        ).fetchone()["n"]

        # Independent fresh noise for every *new* request only.
        noisy = float(true_count) + laplace_noise(1.0 / payload.epsilon)

        tx.execute(
            "INSERT INTO query_requests "
            "(request_id, region, outcome, epsilon, true_count, noisy_result) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                payload.request_id,
                payload.region,
                payload.outcome,
                payload.epsilon,
                true_count,
                noisy,
            ),
        )
        tx.execute(
            # Clamp at zero: the ledger CHECK constraint forbids negatives and
            # the budget check above guarantees affordability, but repeated
            # floating-point subtraction can produce a value like -1e-17.
            "UPDATE privacy_budget "
            "SET remaining = MAX(0.0, remaining - ?) WHERE id = 1",
            (payload.epsilon,),
        )
        remaining = tx.execute(
            "SELECT remaining FROM privacy_budget WHERE id = 1"
        ).fetchone()["remaining"]

    return QueryResponse(
        request_id=payload.request_id,
        region=payload.region,
        outcome=payload.outcome,
        epsilon=payload.epsilon,
        noisy_result=noisy,
        remaining_budget=remaining,
        replayed=False,
    )


# --------------------------------------------------------------------------- #
# Read-only introspection
# --------------------------------------------------------------------------- #
@app.get("/budget", response_model=BudgetResponse, summary="查询全局剩余隐私预算")
async def get_budget(conn: ConnectionDep) -> BudgetResponse:
    row = conn.execute(
        "SELECT total, remaining FROM privacy_budget WHERE id = 1"
    ).fetchone()
    if row is None:  # pragma: no cover - seeded at startup
        raise HTTPException(status_code=503, detail="预算账本尚未初始化")
    return BudgetResponse(
        total_budget=row["total"],
        remaining_budget=row["remaining"],
        spent_budget=row["total"] - row["remaining"],
    )


@app.get("/status", response_model=StatusResponse, summary="导入状态与预算概览")
async def get_status(conn: ConnectionDep) -> StatusResponse:
    meta = conn.execute("SELECT row_count FROM import_meta WHERE id = 1").fetchone()
    row = conn.execute(
        "SELECT total, remaining FROM privacy_budget WHERE id = 1"
    ).fetchone()
    return StatusResponse(
        imported=meta is not None,
        imported_rows=meta["row_count"] if meta else 0,
        total_budget=row["total"],
        remaining_budget=row["remaining"],
        spent_budget=row["total"] - row["remaining"],
    )


@app.get("/health", summary="存活探针")
async def health() -> dict[str, str]:
    return {"status": "ok"}
