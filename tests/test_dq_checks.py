"""Unit tests for the QA gate: each check family gets a pass and a fail case."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from dq_checks import BASE_VARS, CUM4WK_WARMUP, run_pipeline_checks
from lagged import build_lagged_long
from lagged_to_wide import pivot_wide
from merge import merge_all


def _weather(n=20, districts=("AAA", "BBB")):
    frames = []
    for d in districts:
        weeks = pd.date_range("2024-01-01", periods=n, freq="7D")
        rng = np.random.default_rng(7)
        rain = rng.gamma(0.7, 20, n).round(2)
        row = {
            "district_id": d,
            "week_start": weeks.strftime("%Y-%m-%d"),
            "air_temperature_2m_max_c": 34.0, "air_temperature_2m_min_c": 21.0,
            "air_temperature_2m_mean_c": 27.0,
            "relative_humidity_2m_max_pct": 90.0, "relative_humidity_2m_min_pct": 55.0,
            "relative_humidity_2m_mean_pct": 72.0,
            "rainfall_mm": rain,
            "wind_speed_10m_max_ms": 5.0,
        }
        df = pd.DataFrame(row)
        df["rainfall_cum4wk_mm"] = df["rainfall_mm"].rolling(4, min_periods=4).sum().round(2)
        frames.append(df)
    return pd.concat(frames, ignore_index=True)


def _surveillance(weather):
    df = weather[["district_id", "week_start"]].copy()
    df["confirmed_cases"] = 4
    df["suspected_cases"] = 10
    df["tests_performed"] = 20
    df["test_positivity_rate"] = 20.0
    return df


def _result(frames):
    return run_pipeline_checks(frames)


def test_full_pipeline_passes():
    w = _weather()
    long = build_lagged_long(w)
    wide = pivot_wide(long)
    merged = merge_all(w, wide, _surveillance(w))
    result = _result({"weather": w, "lagged_long": long, "lagged_wide": wide,
                      "surveillance": _surveillance(w), "merged": merged})
    assert result["overall"]["passed"], result["log_text"]
    assert result["skipped"] == []
    assert len(result["stages"]) == 4


def test_missing_inputs_skip_not_fail():
    result = _result({"weather": _weather()})
    assert result["overall"]["passed"]
    assert result["skipped"] == ["lagged_long", "lagged_wide", "merge"]


def test_duplicate_week_fails_ingestion():
    w = _weather(districts=("AAA",))
    w = pd.concat([w, w.iloc[[5]]], ignore_index=True)
    result = _result({"weather": w})
    assert not result["stages"][0]["passed"]


def test_broken_cum4wk_fails_ingestion():
    w = _weather(districts=("AAA",))
    w.loc[10, "rainfall_cum4wk_mm"] = 999.0
    result = _result({"weather": w})
    assert not result["stages"][0]["passed"]


def test_dropped_lagged_row_is_caught():
    w = _weather(districts=("AAA",))
    long = build_lagged_long(w).drop(index=3)
    result = _result({"weather": w, "lagged_long": long})
    stage = result["stages"][1]
    assert not stage["passed"]


def test_tampered_lagged_value_is_caught():
    w = _weather(districts=("AAA",))
    long = build_lagged_long(w)
    long.loc[0, "value"] += 5.0
    result = _result({"weather": w, "lagged_long": long})
    assert not result["stages"][1]["passed"]


def test_wide_nan_is_caught():
    w = _weather(districts=("AAA",))
    long = build_lagged_long(w)
    wide = pivot_wide(long)
    wide.iloc[0, 2] = np.nan
    result = _result({"weather": w, "lagged_long": long, "lagged_wide": wide})
    assert not result["stages"][2]["passed"]


def test_confirmed_above_suspected_fails_merge():
    w = _weather(districts=("AAA",))
    long = build_lagged_long(w)
    wide = pivot_wide(long)
    surv = _surveillance(w)
    merged = merge_all(w, wide, surv)
    merged.loc[0, "confirmed_cases"] = 99
    result = _result({"weather": w, "lagged_long": long, "lagged_wide": wide,
                      "surveillance": surv, "merged": merged})
    assert not result["stages"][3]["passed"]


def test_log_text_is_isolated_between_runs():
    a = _result({"weather": _weather()})
    b = _result({"weather": _weather()})
    assert a["log_text"] == b["log_text"]  # deterministic, no crosstalk