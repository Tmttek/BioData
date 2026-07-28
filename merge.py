"""Stage 3: inner-join weather + lagged-wide + surveillance on (district_id, week_start)."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from dq_checks import KEY_COLS

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"

PATHS = {
    "weather": DATA_DIR / "weather_records_openmeteo.csv",
    "lagged_wide": DATA_DIR / "weather_lagged_features_wide.csv",
    "surveillance": DATA_DIR / "malaria_surveillance_synthetic.csv",
}
OUTPUT_CSV = DATA_DIR / "outbreak_dataset_v1.csv"


def load(name: str) -> pd.DataFrame:
    # All three parse week_start as datetime. Merging str against datetime64
    # raises in pandas 2.x, so dtype consistency here is load-bearing.
    return pd.read_csv(PATHS[name], parse_dates=["week_start"])


def merge_all(weather: pd.DataFrame, lagged_wide: pd.DataFrame,
              surveillance: pd.DataFrame) -> pd.DataFrame:
    merged = weather.merge(lagged_wide, on=KEY_COLS, how="inner", suffixes=("", "_dup"))
    merged = merged.merge(surveillance, on=KEY_COLS, how="inner", suffixes=("", "_dup"))
    colliders = [c for c in merged.columns if c.endswith("_dup")]
    if colliders:
        raise SystemExit(f"overlapping non-key columns between inputs: {colliders}")
    if "confirmed_cases" in merged.columns:
        merged = merged.dropna(subset=["confirmed_cases"])
    merged["week_start"] = merged["week_start"].dt.strftime("%Y-%m-%d")
    return merged.sort_values(KEY_COLS).reset_index(drop=True)


def main() -> None:
    frames = {name: load(name) for name in PATHS}
    out = merge_all(frames["weather"], frames["lagged_wide"], frames["surveillance"])
    out.to_csv(OUTPUT_CSV, index=False)
    print(f"wrote {OUTPUT_CSV.name}: {out.shape[0]} rows x {out.shape[1]} cols")


if __name__ == "__main__":
    main()