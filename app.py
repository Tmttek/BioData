"""ZHOID-2026-002 -- FastAPI app: QA console + read-only REST data API.

Run:  uvicorn app:app --reload
Then: http://localhost:8000       -- QA console
      http://localhost:8000/docs  -- Swagger UI
"""
from __future__ import annotations

import io
import logging
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.staticfiles import StaticFiles

from data_access import DATA_FILES, DataStore, apply_date_range, apply_fields, apply_filters, df_to_records, paginate
from dq_checks import run_pipeline_checks

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
STATIC_DIR = BASE_DIR / "static"
MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # real CSVs here are ~1-5 MB

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s  %(levelname)-5s  %(name)s  %(message)s")
logger = logging.getLogger("dq_checks.app")

store = DataStore(DATA_DIR)

app = FastAPI(
    title="ZHOID-2026-002 -- Pipeline QA Console & Data API",
    version="1.0.0",
    description="QA console around dq_checks.py plus a read-only REST API over the pipeline CSVs.",
)


def _paginated_response(df, filters, date_from, date_to, fields, page, page_size):
    df = apply_filters(df, **filters)
    df = apply_date_range(df, "week_start", date_from, date_to)
    try:
        df = apply_fields(df, fields)
    except KeyError as exc:
        raise HTTPException(400, str(exc))
    sliced, total = paginate(df, page, page_size)
    return {
        "total": total,
        "page": max(page, 1),
        "page_size": max(min(page_size, 500), 1),
        "count": len(sliced),
        "results": df_to_records(sliced),
    }


def _require(key: str) -> pd.DataFrame:
    df = store.get(key)
    if df is None:
        reason = store.load_errors.get(key) or f"{DATA_FILES[key]} not found in {DATA_DIR}"
        raise HTTPException(503, f"dataset '{key}' is not loaded ({reason}). "
                                 f"Add the file to data/ and call POST /api/reload.")
    return df


def _single(key: str, district_id: str, week_start: str) -> dict:
    df = _require(key)
    match = df[(df["district_id"] == district_id) & (df["week_start"].astype(str) == week_start)]
    if match.empty:
        raise HTTPException(404, f"no '{key}' record for ({district_id}, {week_start})")
    return df_to_records(match)[0]


@app.post("/api/run", tags=["qa-console"])
async def run_checks(
    weather: UploadFile | None = File(None),
    lagged_long: UploadFile | None = File(None),
    lagged_wide: UploadFile | None = File(None),
    surveillance: UploadFile | None = File(None),
    merged: UploadFile | None = File(None),
) -> dict:
    """Run every dq_checks stage whose inputs were uploaded. Files are checked
    in memory and discarded -- nothing is written to disk."""
    frames: dict[str, pd.DataFrame | None] = {}
    for key, upload in {"weather": weather, "lagged_long": lagged_long,
                        "lagged_wide": lagged_wide, "surveillance": surveillance,
                        "merged": merged}.items():
        if upload is None or not upload.filename:
            frames[key] = None
            continue
        raw = await upload.read()
        if len(raw) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, f"'{upload.filename}' exceeds the 25 MB upload cap")
        try:
            frames[key] = pd.read_csv(io.BytesIO(raw))
        except Exception as exc:
            raise HTTPException(422, f"'{upload.filename}' (slot '{key}') is not readable CSV: {exc}")

    if all(v is None for v in frames.values()):
        raise HTTPException(400, "no files supplied -- attach at least one CSV")
    try:
        return run_pipeline_checks(frames)
    except Exception as exc:
        logger.exception("check run crashed")
        raise HTTPException(422, f"check run crashed on the supplied data: {exc}")


@app.get("/api/districts", tags=["data"])
def list_districts() -> dict:
    return {"districts": store.districts()}


@app.post("/api/reload", tags=["data"])
def reload_data() -> dict:
    return store.reload()


@app.get("/api/health", tags=["data"])
def health() -> dict:
    return {
        "status": "ok",
        "datasets": {
            key: {"loaded": store.get(key) is not None,
                  "rows": len(store.get(key)) if store.get(key) is not None else None,
                  "file": DATA_FILES[key]}
            for key in DATA_FILES
        },
        "load_errors": store.load_errors,
    }


@app.get("/api/weather", tags=["data"])
def list_weather(district_id: str | None = None, week_start_from: str | None = None,
                 week_start_to: str | None = None, fields: str | None = None,
                 page: int = Query(1, ge=1), page_size: int = Query(50, ge=1)) -> dict:
    return _paginated_response(_require("weather"), {"district_id": district_id},
                               week_start_from, week_start_to, fields, page, page_size)


@app.get("/api/weather/{district_id}/{week_start}", tags=["data"])
def get_weather_record(district_id: str, week_start: str) -> dict:
    return _single("weather", district_id, week_start)


@app.get("/api/surveillance", tags=["data"])
def list_surveillance(district_id: str | None = None, week_start_from: str | None = None,
                      week_start_to: str | None = None, fields: str | None = None,
                      page: int = Query(1, ge=1), page_size: int = Query(50, ge=1)) -> dict:
    return _paginated_response(_require("surveillance"), {"district_id": district_id},
                               week_start_from, week_start_to, fields, page, page_size)


@app.get("/api/surveillance/{district_id}/{week_start}", tags=["data"])
def get_surveillance_record(district_id: str, week_start: str) -> dict:
    return _single("surveillance", district_id, week_start)


@app.get("/api/lagged-features", tags=["data"])
def list_lagged_features(district_id: str | None = None, base_variable: str | None = None,
                         lag_weeks: int | None = None, week_start_from: str | None = None,
                         week_start_to: str | None = None, fields: str | None = None,
                         page: int = Query(1, ge=1), page_size: int = Query(50, ge=1)) -> dict:
    return _paginated_response(
        _require("lagged_long"),
        {"district_id": district_id, "base_variable": base_variable, "lag_weeks": lag_weeks},
        week_start_from, week_start_to, fields, page, page_size)


@app.get("/api/outbreak-dataset", tags=["data"])
def list_outbreak_dataset(district_id: str | None = None, week_start_from: str | None = None,
                          week_start_to: str | None = None, fields: str | None = None,
                          page: int = Query(1, ge=1), page_size: int = Query(50, ge=1)) -> dict:
    return _paginated_response(_require("outbreak"), {"district_id": district_id},
                               week_start_from, week_start_to, fields, page, page_size)


@app.get("/api/outbreak-dataset/{district_id}/{week_start}", tags=["data"])
def get_outbreak_record(district_id: str, week_start: str) -> dict:
    return _single("outbreak", district_id, week_start)


# mounted last so explicit routes win; html=True serves index.html at /
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")