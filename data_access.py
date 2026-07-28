"""Read layer for the Data API: load CSVs once, serve filtered slices."""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger("dq_checks.data_access")

DATA_FILES = {
    "weather": "weather_records_openmeteo.csv",
    "lagged_long": "weather_lagged_features_openmeteo.csv",
    "lagged_wide": "weather_lagged_features_wide.csv",
    "surveillance": "malaria_surveillance_synthetic.csv",
    "outbreak": "outbreak_dataset_v1.csv",
}

MAX_PAGE_SIZE = 500


class DataStore:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.frames: dict[str, pd.DataFrame] = {}
        self.load_errors: dict[str, str] = {}
        self.reload()

    def reload(self) -> dict:
        frames: dict[str, pd.DataFrame] = {}
        errors: dict[str, str] = {}
        for key, filename in DATA_FILES.items():
            path = self.data_dir / filename
            if not path.exists():
                continue
            try:
                frames[key] = pd.read_csv(path)
                logger.info("loaded %s: %d rows", filename, len(frames[key]))
            except Exception as exc:
                errors[key] = str(exc)
                logger.error("failed to load %s: %s", filename, exc)
        # atomic swap -- readers never see a half-loaded store
        self.frames, self.load_errors = frames, errors
        return {"loaded": {k: len(v) for k, v in frames.items()}, "errors": errors}

    def get(self, key: str) -> pd.DataFrame | None:
        return self.frames.get(key)

    def districts(self) -> list[str]:
        ids: set[str] = set()
        for df in self.frames.values():
            if "district_id" in df.columns:
                ids.update(df["district_id"].dropna().astype(str))
        return sorted(ids)


def apply_filters(df: pd.DataFrame, **filters) -> pd.DataFrame:
    for col, value in filters.items():
        if value is not None and col in df.columns:
            df = df[df[col] == value]
    return df


def apply_date_range(df: pd.DataFrame, col: str, date_from: str | None,
                     date_to: str | None) -> pd.DataFrame:
    if col not in df.columns:
        return df
    if date_from:
        df = df[df[col].astype(str) >= date_from]
    if date_to:
        df = df[df[col].astype(str) <= date_to]
    return df


def apply_fields(df: pd.DataFrame, fields: str | None) -> pd.DataFrame:
    if not fields:
        return df
    wanted = [f.strip() for f in fields.split(",") if f.strip()]
    unknown = [f for f in wanted if f not in df.columns]
    if unknown:
        raise KeyError(f"unknown fields: {unknown}")
    return df[wanted]


def paginate(df: pd.DataFrame, page: int, page_size: int) -> tuple[pd.DataFrame, int]:
    page = max(page, 1)
    page_size = max(min(page_size, MAX_PAGE_SIZE), 1)
    total = len(df)
    start = (page - 1) * page_size
    return df.iloc[start:start + page_size], total


def df_to_records(df: pd.DataFrame) -> list[dict]:
    # round-trip through JSON so NaN -> null and numpy types become native
    return pd.read_json(df.to_json(orient="records"), convert_dates=False).to_dict("records") if len(df) else []