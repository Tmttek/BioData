"""Stage 1: INGESTION -- weekly Open-Meteo history for all districts.

Fetches daily archive data from 2023-01-01 (one extra year so lag features
have history), aggregates to epi weeks (Monday-anchored), derives the
4-week cumulative rainfall, and writes records from 2024-01-01 onward.

Needs network. Offline? Run scripts/generate_synthetic_weather.py instead.
"""
from __future__ import annotations

import time
from datetime import date
from pathlib import Path

import pandas as pd
import requests

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
WEATHER_CSV = DATA_DIR / "weather_records_openmeteo.csv"

API_URL = "https://archive-api.open-meteo.com/v1/archive"
START_DATE = "2023-01-01"
OUTPUT_START_DATE = "2024-01-01"

# (name, lat, lon) -- coordinates reconstructed; verify against your originals.
DISTRICTS = {
    "BNG": ("Bangui", 4.3947, 18.5582),
    "BIM": ("Bimbo", 4.2567, 18.4158),
    "MBR": ("Mbaiki", 3.8689, 17.9892),
    "KEM": ("Sibut", 5.7180, 19.0739),
    "CHR": ("Bria", 6.5423, 21.9863),
    "NGR": ("Kaga-Bandoro", 6.9833, 19.1833),
    "OBO": ("Ouadda", 8.0777, 22.4007),
    "BMB": ("Bambari", 5.7680, 20.6757),
    "YAL": ("Yaloke", 5.1109, 18.2828),
}

DAILY_TO_WEEKLY = {
    "temperature_2m_max": ("air_temperature_2m_max_c", "max"),
    "temperature_2m_min": ("air_temperature_2m_min_c", "min"),
    "temperature_2m_mean": ("air_temperature_2m_mean_c", "mean"),
    "relative_humidity_2m_max": ("relative_humidity_2m_max_pct", "max"),
    "relative_humidity_2m_min": ("relative_humidity_2m_min_pct", "min"),
    "relative_humidity_2m_mean": ("relative_humidity_2m_mean_pct", "mean"),
    "rainfall": ("rainfall_mm", "sum"),
    "wind_speed_10m_max": ("wind_speed_10m_max_ms", "max"),
}


def fetch_district(district_id: str, name: str, lat: float, lon: float) -> pd.DataFrame:
    resp = requests.get(
        API_URL,
        params={
            "latitude": lat, "longitude": lon,
            "daily": ",".join(DAILY_TO_WEEKLY),
            "start_date": START_DATE, "end_date": date.today().isoformat(),
            "timezone": "UTC",
        },
        timeout=60,
    )
    resp.raise_for_status()
    daily = pd.DataFrame({"time": pd.to_datetime(resp.json()["daily"]["time"]),
                          **{k: resp.json()["daily"][k] for k in DAILY_TO_WEEKLY}})
    daily["week_start"] = daily["time"] - pd.to_timedelta(daily["time"].dt.weekday, unit="D")
    weekly = daily.groupby("week_start", as_index=False).agg(
        {k: how for k, (_, how) in DAILY_TO_WEEKLY.items()}
    )
    weekly = weekly.rename(columns={k: out for k, (out, _) in DAILY_TO_WEEKLY.items()})
    weekly.insert(0, "district_id", district_id)
    weekly["week_start"] = weekly["week_start"].dt.strftime("%Y-%m-%d")
    weekly["rainfall_cum4wk_mm"] = (
        weekly["rainfall_mm"].rolling(4, min_periods=4).sum().round(2)
    )
    weekly = weekly[weekly["week_start"] >= OUTPUT_START_DATE]
    return weekly.round(2)


def main() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    frames = []
    for district_id, (name, lat, lon) in DISTRICTS.items():
        print(f"fetching {district_id} ({name})…", flush=True)
        frames.append(fetch_district(district_id, name, lat, lon))
        time.sleep(0.3)  # be polite to the archive API
    out = pd.concat(frames, ignore_index=True)
    out.to_csv(WEATHER_CSV, index=False)
    print(f"wrote {WEATHER_CSV.name}: {len(out)} rows, {out['district_id'].nunique()} districts")


if __name__ == "__main__":
    main()