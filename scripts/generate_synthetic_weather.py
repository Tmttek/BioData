"""Offline stand-in for weather.py: writes a schema-exact weather_records CSV.

Deterministic (seeded per district) and plausible for the Central African
Republic: a wet season roughly May-October, a dry season with near-zero rain,
and rainfall_cum4wk_mm computed as a true 4-week rolling sum -- so the first
3 weeks are NaN, exactly as dq_checks.check_ingestion demands.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from weather import DISTRICTS, OUTPUT_START_DATE, WEATHER_CSV  # noqa: E402

N_WEEKS = 113  # 2024-01-01 .. 2026-02-23, all Mondays


def synth_district(district_id: str, lat: float, lon: float, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    weeks = pd.date_range(OUTPUT_START_DATE, periods=N_WEEKS, freq="7D")
    t = np.arange(N_WEEKS, dtype=float)
    season = -np.cos(2 * np.pi * t / 52.25)          # -1 in Jan, +1 in Jul
    wet = np.clip(season, 0.0, 1.0) ** 1.5

    tmean = 25.0 + 3.5 * season + rng.normal(0, 0.6, N_WEEKS)
    rain = rng.gamma(shape=0.7, scale=30.0, size=N_WEEKS) * wet
    rain[rng.random(N_WEEKS) < 0.4 * (1 - wet)] = 0.0

    df = pd.DataFrame({
        "district_id": district_id,
        "week_start": weeks.strftime("%Y-%m-%d"),
        "air_temperature_2m_max_c": (tmean + 4.5 + rng.normal(0, 0.5, N_WEEKS)).round(2),
        "air_temperature_2m_min_c": (tmean - 4.0 + rng.normal(0, 0.5, N_WEEKS)).round(2),
        "air_temperature_2m_mean_c": tmean.round(2),
        "relative_humidity_2m_max_pct": np.clip(88 + 6 * season + rng.normal(0, 2, N_WEEKS), 40, 100).round(2),
        "relative_humidity_2m_min_pct": np.clip(58 + 14 * season + rng.normal(0, 3, N_WEEKS), 25, 95).round(2),
        "relative_humidity_2m_mean_pct": np.clip(72 + 11 * season + rng.normal(0, 2.5, N_WEEKS), 30, 98).round(2),
        "rainfall_mm": rain.round(2),
        "wind_speed_10m_max_ms": (4.0 + 2.5 * rng.random(N_WEEKS) + 1.5 * (1 - wet)).round(2),
    })
    df["rainfall_cum4wk_mm"] = df["rainfall_mm"].rolling(4, min_periods=4).sum().round(2)
    return df


def main() -> None:
    WEATHER_CSV.parent.mkdir(exist_ok=True)
    out = pd.concat(
        [synth_district(did, lat, lon, seed=1000 + i)
         for i, (did, (_, lat, lon)) in enumerate(DISTRICTS.items())],
        ignore_index=True,
    )
    out.to_csv(WEATHER_CSV, index=False)
    print(f"wrote {WEATHER_CSV.name}: {len(out)} rows, {out['district_id'].nunique()} districts (synthetic)")


if __name__ == "__main__":
    main()