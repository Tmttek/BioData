"""Stage 2b: pivot the long lagged file to wide -- one column per (var, lag)."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from dq_checks import KEY_COLS

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
LONG_CSV = DATA_DIR / "weather_lagged_features_openmeteo.csv"
WIDE_CSV = DATA_DIR / "weather_lagged_features_wide.csv"


def pivot_wide(lagged_long: pd.DataFrame) -> pd.DataFrame:
    df = lagged_long.copy()
    df["feature"] = df["base_variable"] + "_lag" + df["lag_weeks"].astype(str) + "wk"
    wide = df.pivot_table(index=KEY_COLS, columns="feature", values="value",
                          aggfunc="first").reset_index()
    wide.columns.name = None
    feature_cols = sorted(c for c in wide.columns if c not in KEY_COLS)
    return wide[KEY_COLS + feature_cols].sort_values(KEY_COLS).reset_index(drop=True)


def main() -> None:
    wide = pivot_wide(pd.read_csv(LONG_CSV))
    if wide.isna().any().any():
        raise SystemExit("pivot produced NaN cells -- long file has missing (var, lag) combos")
    wide.to_csv(WIDE_CSV, index=False)
    print(f"wrote {WIDE_CSV.name}: {wide.shape[0]} rows x {wide.shape[1]} cols")


if __name__ == "__main__":
    main()