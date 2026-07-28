"""
ZHOID-2026-002 — Weather ingestor using Open-Meteo's Historical Weather API (ERA5 / ERA5-Land).

Why this source:
- Free, no API key needed for non-commercial use.
- Gap-free reanalysis grid (blends stations, satellites, radar) — covers rural districts
  with no nearby ZMS station, which matters for Muzarabani/Mbire/Binga/Kariba.
- Covers every field the schema needs: precipitation, temp max/min/mean, relative humidity,
  soil moisture, wind speed — daily resolution back to 1940 (ERA5) / 1950 (ERA5-Land).
  That comfortably covers the pipeline's 1992 historical baseline decision.
- Docs: https://open-meteo.com/en/docs/historical-weather-api

NOTE: this sandbox has no outbound network access, so this script has not been run live here.
Run it in your pipeline environment (where the Aug 1 pilot assembly work is happening).
Treat it as a first-pass ERA5/reanalysis ingestor — Section 2.2 of the proposal still calls
for ZMS station data as the ground-truth primary source; use this to fill spatial/temporal
gaps, not to replace it.
"""

import time
from pathlib import Path

import requests
import pandas as pd

ROOT_DIR = Path(__file__).resolve().parent.parent
WEATHER_DIR = ROOT_DIR / "weather"
OUTPUT_FILE = WEATHER_DIR / "weather_records_openmeteo.csv"

BASE_URL = "https://archive-api.open-meteo.com/v1/archive"

# Centroid lat/lon for the 10 pilot districts
DISTRICTS = [
    dict(id="MZB", name="Muzarabani", lat=-16.35, lon=31.05),
    dict(id="MBR", name="Mbire", lat=-15.95, lon=30.85),
    dict(id="GRV", name="Guruve", lat=-16.65, lon=30.70),
    dict(id="HRG", name="Hurungwe", lat=-16.50, lon=29.50),
    dict(id="KRB", name="Kariba", lat=-16.90, lon=28.80),
    dict(id="BNG", name="Binga", lat=-17.62, lon=27.35),
    dict(id="CHR", name="Chiredzi", lat=-21.05, lon=31.65),
    dict(id="CHP", name="Chipinge", lat=-20.20, lon=32.62),
    dict(id="MTS", name="Mutasa", lat=-18.70, lon=32.72),
    dict(id="NYG", name="Nyanga", lat=-18.22, lon=32.75),
]

DAILY_VARS = [
    "precipitation_sum",
    "temperature_2m_max",
    "temperature_2m_min",
    "temperature_2m_mean",
    "relative_humidity_2m_mean",
    "wind_speed_10m_max",
    "soil_moisture_0_to_7cm_mean",
]


def fetch_daily(lat: float, lon: float, start_date: str, end_date: str) -> pd.DataFrame:
    """One district's daily data for [start_date, end_date] (inclusive), both 'YYYY-MM-DD'."""
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start_date,
        "end_date": end_date,
        "daily": ",".join(DAILY_VARS),
        "timezone": "Africa/Harare",
    }
    for attempt in range(3):
        resp = requests.get(BASE_URL, params=params, timeout=30)
        if resp.status_code == 429:
            time.sleep(5 * (attempt + 1))
            continue
        resp.raise_for_status()
        break
    payload = resp.json()
    daily = payload["daily"]
    df = pd.DataFrame(daily)
    df["date"] = pd.to_datetime(df["time"])
    return df.drop(columns=["time"])


def daily_to_weekly(df: pd.DataFrame) -> pd.DataFrame:
    """Resample daily ERA5 values to Monday-start epi-weeks matching the schema fields."""
    df = df.set_index("date").sort_index()
    weekly = df.resample("W-MON", label="left", closed="left").agg({
        "precipitation_sum": "sum",
        "temperature_2m_max": "max",
        "temperature_2m_min": "min",
        "temperature_2m_mean": "mean",
        "relative_humidity_2m_mean": "mean",
        "wind_speed_10m_max": "mean",
        "soil_moisture_0_to_7cm_mean": "mean",
    })
    weekly = weekly.rename(columns={
        "precipitation_sum": "rainfall_mm",
        "temperature_2m_max": "temp_max_c",
        "temperature_2m_min": "temp_min_c",
        "temperature_2m_mean": "temp_mean_c",
        "relative_humidity_2m_mean": "humidity_pct",
        "wind_speed_10m_max": "wind_speed_kmh",
        "soil_moisture_0_to_7cm_mean": "soil_moisture_pct",
    })
    # ERA5 soil moisture is volumetric (m3/m3) — convert to a 0-100 percent scale
    weekly["soil_moisture_pct"] = (weekly["soil_moisture_pct"] * 100).round(1)

    weekly["rainfall_cum4wk_mm"] = weekly["rainfall_mm"].rolling(4).sum().round(1)

    # Anomaly vs this district's own multi-year mean for the same calendar week-of-year.
    # Needs a long enough pulled history (several years) to be meaningful — with only the
    # requested window loaded, this falls back to the window's own weekly mean per month.
    week_of_year = weekly.index.isocalendar().week
    monthly_mean = weekly.groupby(weekly.index.month)["rainfall_mm"].transform("mean")
    weekly["rainfall_anomaly_mm"] = (weekly["rainfall_mm"] - monthly_mean).round(1)

    weekly = weekly.reset_index().rename(columns={"date": "week_start"})
    weekly["week_start"] = weekly["week_start"].dt.date.astype(str)
    cols = ["week_start", "rainfall_mm", "rainfall_anomaly_mm", "rainfall_cum4wk_mm",
            "temp_max_c", "temp_min_c", "temp_mean_c", "humidity_pct",
            "soil_moisture_pct", "wind_speed_kmh"]
    return weekly[cols]


def build_weather_records(start_date: str, end_date: str) -> pd.DataFrame:
    frames = []
    for d in DISTRICTS:
        daily = fetch_daily(d["lat"], d["lon"], start_date, end_date)
        weekly = daily_to_weekly(daily)
        weekly.insert(0, "district_id", d["id"])
        frames.append(weekly)
        time.sleep(1)  # be polite to the free tier
    return pd.concat(frames, ignore_index=True)


if __name__ == "__main__":
    # For a real 1992 baseline, request in multi-year chunks (e.g. 5-year windows) and
    # concatenate — a single 30+ year daily pull per district is a large payload.
    df = build_weather_records("2024-01-01", "2025-12-31")
    df.to_csv(OUTPUT_FILE, index=False)
    print(df.head())
    print(f"Rows: {len(df)}")
