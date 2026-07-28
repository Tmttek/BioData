"""Stage 2a: long-format lagged weather features.

For each district, base variable and lag L, emit one row per week whose
L-weeks-earlier value exists:

    value(district, week, var, L) = weather[var] at (week - L weeks)

rainfall_cum4wk_mm carries a 3-week warm-up of its own, so its first usable
lagged value sits L + 3 weeks into the series. Rows per (district, var, lag)
are exactly max(n_weeks - lag - warmup, 0) -- the contract
dq_checks.check_lagged_long verifies.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

# single source of truth: the QA gate defines the contract
from dq_checks import BASE_VARS, CUM4WK_WARMUP, KEY_COLS, LAG_WEEKS

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
WEATHER_CSV = DATA_DIR / "weather_records_openmeteo.csv"
OUTPUT_CSV = DATA_DIR / "weather_lagged_features_openmeteo.csv"


def build_lagged_long(weather: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for district_id, group in weather.groupby("district_id", sort=True):
        g = group.sort_values("week_start").reset_index(drop=True)
        weeks = g["week_start"].tolist()
        n = len(weeks)
        for var in BASE_VARS:
            warmup = CUM4WK_WARMUP if var == "rainfall_cum4wk_mm" else 0
            values = g[var].astype(float)
            for lag in LAG_WEEKS:
                for i in range(lag + warmup, n):
                    rows.append((district_id, weeks[i], var, lag,
                                 round(float(values.iloc[i - lag]), 6)))
    return pd.DataFrame(rows, columns=KEY_COLS + ["base_variable", "lag_weeks", "value"])


def main() -> None:
    weather = pd.read_csv(WEATHER_CSV)
    out = build_lagged_long(weather)
    out.to_csv(OUTPUT_CSV, index=False)
    print(f"wrote {OUTPUT_CSV.name}: {len(out)} rows "
          f"({out['district_id'].nunique()} districts x {len(BASE_VARS)} vars x {len(LAG_WEEKS)} lags)")


if __name__ == "__main__":
    main()