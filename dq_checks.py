"""Data-quality gate for the ZHOID-2026-002 pipeline.

Runs the same checks that gate each pipeline stage, either:
  - in-process via run_pipeline_checks({...})  -- used by POST /api/run
  - from disk via `python dq_checks.py`        -- checks whatever is in data/

Design rules:
  - checks never raise on bad data; they log FAIL and continue
  - missing inputs skip a stage (reported under "skipped"), never crash the run
  - all output goes to an isolated in-memory log, so concurrent runs don't mix
"""
from __future__ import annotations

import argparse
import logging
import sys
from io import StringIO
from pathlib import Path

import numpy as np
import pandas as pd

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"

KEY_COLS = ["district_id", "week_start"]
LAG_WEEKS = [1, 2, 4, 6, 8]
CUM4WK_WARMUP = 3  # rolling(min_periods=4) leaves 3 NaN weeks

BASE_VARS = [
    "temp_max_c",
    "temp_min_c",
    "temp_mean_c",
    "humidity_pct",
    "rainfall_mm",
    "rainfall_anomaly_mm",
    "soil_moisture_pct",
    "wind_speed_kmh",
    "rainfall_cum4wk_mm",   # derived -- keep last; the warm-up checks key off this name
]
# RAW_VARS and LAGGED_WIDE_COLS derive from this automatically
# RAW_VARS and LAGGED_WIDE_COLS derive from BASE_VARS automatically; nothing else changes
# RAW_VARS and LAGGED_WIDE_COLS derive from BASE_VARS automatically; nothing else changes
RAW_VARS = [v for v in BASE_VARS if v != "rainfall_cum4wk_mm"]
LAGGED_WIDE_COLS = [f"{v}_lag{lag}wk" for v in BASE_VARS for lag in LAG_WEEKS]

STAGE_ORDER = ["ingestion", "lagged_long", "lagged_wide", "merge"]
STAGE_TITLES = {
    "ingestion": "STAGE 1: INGESTION (weather_records)",
    "lagged_long": "STAGE 2a: PROCESSING (lagged features, long)",
    "lagged_wide": "STAGE 2b: PROCESSING (lagged features, wide)",
    "merge": "STAGE 3: VALIDATION (outbreak_dataset_v1)",
}


class _RunLog:
    """Per-run logger writing to its own buffer -- no global state, no crosstalk."""

    def __init__(self) -> None:
        self.buffer = StringIO()
        self.logger = logging.getLogger(f"dq_checks.run.{id(self)}")
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False
        for h in list(self.logger.handlers):
            self.logger.removeHandler(h)
        handler = logging.StreamHandler(self.buffer)
        handler.setFormatter(logging.Formatter("%(levelname)-5s %(message)s"))
        self.logger.addHandler(handler)

    def text(self) -> str:
        return self.buffer.getvalue()


def _check(log, checks, label, ok, detail=""):
    status = "pass" if ok else "fail"
    log.info(f"{status.upper():<5} {label}" + (f" -- {detail}" if detail else ""))
    checks.append({"label": label, "status": status, "detail": detail})
    return ok


def _warn(log, warnings, label, detail=""):
    log.warning(f"WARN  {label}" + (f" -- {detail}" if detail else ""))
    warnings.append({"label": label, "detail": detail})


def _stage(key, checks, warnings):
    return {
        "key": key,
        "title": STAGE_TITLES[key],
        "checks": checks,
        "warnings": warnings,
        "passed": all(c["status"] == "pass" for c in checks),
    }


# ---------------------------------------------------------------------------
# stage checks
# ---------------------------------------------------------------------------

def check_ingestion(weather: pd.DataFrame, log) -> tuple[list, list]:
    checks: list[dict] = []
    warnings: list[dict] = []

    required = KEY_COLS + BASE_VARS
    missing = [c for c in required if c not in weather.columns]
    if not _check(log, checks, "required columns present", not missing,
                  f"missing: {missing}" if missing else f"{len(required)} columns ok"):
        return checks, warnings

    df = weather.copy()
    df["week_start"] = pd.to_datetime(df["week_start"], errors="coerce")
    _check(log, checks, "week_start parses as dates",
           int(df["week_start"].isna().sum()) == 0,
           f"{int(df['week_start'].isna().sum())} unparseable")
    _check(log, checks, "week_start falls on Mondays",
           int((df["week_start"].dt.weekday != 0).sum()) == 0)
    _check(log, checks, "district_id never null",
           int(df["district_id"].isna().sum()) == 0)
    dupes = int(df.duplicated(KEY_COLS).sum())
    _check(log, checks, "no duplicate (district_id, week_start)", dupes == 0,
           f"{dupes} duplicates" if dupes else "")

    for var in RAW_VARS:
        vals = df[var].astype(float)
        n_inf = int(np.isinf(vals).sum())
        _check(log, checks, f"{var}: no infinite values", n_inf == 0,
               f"{n_inf} inf" if n_inf else "")
        n_nan = int(vals.isna().sum())
        if n_nan:
            _warn(log, warnings, f"{var}: {n_nan} missing values")

    ok_warmup = ok_sum = True
    n_districts = 0
    for _, g in df.groupby("district_id"):
        g = g.sort_values("week_start")
        actual = g["rainfall_cum4wk_mm"].astype(float)
        expected = g["rainfall_mm"].astype(float).rolling(4, min_periods=4).sum()
        nan_mask = actual.isna()
        if int(nan_mask.sum()) != CUM4WK_WARMUP or not bool(nan_mask.iloc[:CUM4WK_WARMUP].all()):
            ok_warmup = False
        if ((actual - expected).abs().dropna() > 0.011).any():
            ok_sum = False
        n_districts += 1
    _check(log, checks, f"rainfall_cum4wk_mm: exactly {CUM4WK_WARMUP} warm-up NaNs per district",
           ok_warmup, f"{n_districts} districts")
    _check(log, checks, "rainfall_cum4wk_mm equals 4-week rolling sum of rainfall_mm", ok_sum)
    return checks, warnings


def check_lagged_long(weather: pd.DataFrame, lagged: pd.DataFrame, log) -> tuple[list, list]:
    checks: list[dict] = []
    warnings: list[dict] = []

    expected_cols = KEY_COLS + ["base_variable", "lag_weeks", "value"]
    missing = [c for c in expected_cols if c not in lagged.columns]
    _check(log, checks, "long file has required columns", not missing,
           f"missing: {missing}" if missing else "")
    if missing:
        return checks, warnings

    bad_vars = sorted(set(lagged["base_variable"]) - set(BASE_VARS))
    _check(log, checks, "base_variable values are known variables", not bad_vars,
           f"unknown: {bad_vars}" if bad_vars else "")
    bad_lags = sorted(set(lagged["lag_weeks"]) - set(LAG_WEEKS))
    _check(log, checks, "lag_weeks values are in {1,2,4,6,8}", not bad_lags,
           f"unknown: {bad_lags}" if bad_lags else "")

    vals = lagged["value"].astype(float)
    _check(log, checks, "value: no NaN / infinite", int(vals.isna().sum() + np.isinf(vals).sum()) == 0)
    dupes = int(lagged.duplicated(KEY_COLS + ["base_variable", "lag_weeks"]).sum())
    _check(log, checks, "no duplicate (district, week, variable, lag) keys", dupes == 0,
           f"{dupes} duplicates" if dupes else "")

    w = weather.copy()
    w["week_start"] = pd.to_datetime(w["week_start"])
    n_weeks = w.groupby("district_id").size().to_dict()

    bad_counts = 0
    mismatches = 0
    for (district, var, lag), g in lagged.groupby(["district_id", "base_variable", "lag_weeks"]):
        n = n_weeks.get(district, 0)
        warmup = CUM4WK_WARMUP if var == "rainfall_cum4wk_mm" else 0
        expected_rows = max(n - lag - warmup, 0)
        if len(g) != expected_rows:
            bad_counts += 1
            log.info(f"FAIL  row count {district}/{var}/lag{lag}: got {len(g)}, expected {expected_rows}")
        wd = w[w["district_id"] == district].set_index("week_start").sort_index()
        exp_vals = wd[var].astype(float).shift(lag)
        got = g.assign(week_start=pd.to_datetime(g["week_start"])).set_index("week_start")["value"].astype(float)
        mismatches += int((got - exp_vals.reindex(got.index)).abs().gt(1e-4).sum())

    _check(log, checks, "row counts match max(n_weeks - lag - warmup, 0) per combo",
           bad_counts == 0, f"{bad_counts} combos off" if bad_counts else "")
    _check(log, checks, "lagged values equal weather.shift(lag) at each week",
           mismatches == 0, f"{mismatches} mismatched cells" if mismatches else "")
    return checks, warnings


def check_lagged_wide(lagged_long: pd.DataFrame, lagged_wide: pd.DataFrame, log) -> tuple[list, list]:
    checks: list[dict] = []
    warnings: list[dict] = []

    expected_cols = set(KEY_COLS + LAGGED_WIDE_COLS)
    actual_cols = set(lagged_wide.columns)
    missing = sorted(expected_cols - actual_cols)
    extra = sorted(actual_cols - expected_cols)
    _check(log, checks, f"wide file has exactly the {len(expected_cols)} expected columns",
           not missing and not extra,
           f"missing: {missing[:4]}{'…' if len(missing) > 4 else ''} | "
           f"extra: {extra[:4]}{'…' if len(extra) > 4 else ''}" if (missing or extra) else "")

    n_nan = int(lagged_wide.isna().sum().sum())
    _check(log, checks, "wide file has zero NaN cells", n_nan == 0,
           f"{n_nan} NaN" if n_nan else "")
    dupes = int(lagged_wide.duplicated(KEY_COLS).sum())
    _check(log, checks, "no duplicate (district_id, week_start)", dupes == 0,
           f"{dupes} duplicates" if dupes else "")

    if not set(KEY_COLS).issubset(actual_cols):
        _warn(log, warnings, "row-count and spot checks skipped -- key columns missing")
        return checks, warnings

    n_long_keys = lagged_long[KEY_COLS].drop_duplicates().shape[0]
    _check(log, checks, "wide row count equals distinct (district, week) in long file",
           lagged_wide.shape[0] == n_long_keys,
           f"wide={lagged_wide.shape[0]}, long keys={n_long_keys}")

    if missing:
        _warn(log, warnings, f"cell-level spot check skipped -- {len(missing)} expected columns missing",
              f"first missing: {missing[:3]}")
        return checks, warnings

    long_idx = lagged_long.copy()
    long_idx["week_start"] = pd.to_datetime(long_idx["week_start"])
    wide_idx = lagged_wide.copy()
    wide_idx["week_start"] = pd.to_datetime(wide_idx["week_start"])
    sample = [LAGGED_WIDE_COLS[0], LAGGED_WIDE_COLS[len(LAGGED_WIDE_COLS) // 2], LAGGED_WIDE_COLS[-1]]
    bad_cells = 0
    for col in sample:
        var, lag = col.rsplit("_lag", 1)
        lag = int(lag[:-2])
        src = long_idx[(long_idx["base_variable"] == var) & (long_idx["lag_weeks"] == lag)]
        joined = wide_idx[KEY_COLS + [col]].merge(src, on=KEY_COLS, how="outer")
        bad_cells += int((joined[col] - joined["value"]).abs().gt(1e-4).sum())
    _check(log, checks, f"spot check: {len(sample)} feature columns match the long file",
           bad_cells == 0, f"{bad_cells} mismatched cells" if bad_cells else "")
    return checks, warnings


def check_merge(weather: pd.DataFrame, surveillance: pd.DataFrame,
                merged: pd.DataFrame, log) -> tuple[list, list]:
    checks: list[dict] = []
    warnings: list[dict] = []

    colliders = [c for c in merged.columns if c.endswith(("_x", "_y", "_dup"))]
    _check(log, checks, "no merge-collision columns (_x/_y)", not colliders,
           f"found: {colliders}" if colliders else "")
    dupes = int(merged.duplicated(KEY_COLS).sum())
    _check(log, checks, "no duplicate (district_id, week_start)", dupes == 0,
           f"{dupes} duplicates" if dupes else "")
    n_nan = int(merged.isna().sum().sum())
    _check(log, checks, "merged dataset is complete (zero NaN)", n_nan == 0,
           f"{n_nan} NaN" if n_nan else "")

    if {"confirmed_cases", "suspected_cases"}.issubset(merged.columns):
        bad = int((merged["confirmed_cases"] > merged["suspected_cases"]).sum())
        _check(log, checks, "confirmed_cases <= suspected_cases everywhere", bad == 0,
               f"{bad} violations" if bad else "")
    if {"confirmed_cases", "tests_performed", "test_positivity_rate"}.issubset(merged.columns):
        tested = merged["tests_performed"] > 0
        recomputed = merged.loc[tested, "confirmed_cases"] / merged.loc[tested, "tests_performed"] * 100
        bad = int((recomputed - merged.loc[tested, "test_positivity_rate"]).abs().gt(0.11).sum())
        _check(log, checks, "test_positivity_rate recomputes from confirmed/tests", bad == 0,
               f"{bad} mismatches" if bad else "")

    m = merged.copy()
    m["week_start"] = pd.to_datetime(m["week_start"])
    w = weather.copy()
    w["week_start"] = pd.to_datetime(w["week_start"])
    s = surveillance.copy()
    s["week_start"] = pd.to_datetime(s["week_start"])

    bad_districts = 0
    for district, g in m.groupby("district_id"):
        n_w = (w["district_id"] == district).sum()
        n_s = (s["district_id"] == district).sum()
        if len(g) != min(n_w, n_s):
            bad_districts += 1
    _check(log, checks, "merged rows per district == min(weather, surveillance) rows",
           bad_districts == 0, f"{bad_districts} districts off" if bad_districts else "")

    weather_cols = [c for c in KEY_COLS + BASE_VARS if c in m.columns]
    joined = m[weather_cols].merge(w[weather_cols], on=KEY_COLS, how="left", suffixes=("", "_src"))
    drift = 0
    for c in BASE_VARS:
        if f"{c}_src" in joined.columns:
            drift += int((joined[c].astype(float) - joined[f"{c}_src"].astype(float)).abs().gt(0.011).sum())
    _check(log, checks, "weather block in merged matches the weather file", drift == 0,
           f"{drift} drifted cells" if drift else "")
    return checks, warnings


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------

def run_pipeline_checks(dataframes: dict[str, pd.DataFrame | None]) -> dict:
    """Run every stage whose inputs are present. Never raises on bad data."""
    run = _RunLog()
    log = run.logger

    weather = dataframes.get("weather")
    lagged_long = dataframes.get("lagged_long")
    lagged_wide = dataframes.get("lagged_wide")
    surveillance = dataframes.get("surveillance")
    merged = dataframes.get("merged")

    stages: list[dict] = []
    skipped: list[str] = []

    def run_stage(key: str, fn) -> None:
        try:
            checks, warnings = fn()
        except Exception as exc:
            # a check must never take the whole run down -- report, don't raise
            log.exception(f"FAIL  {STAGE_TITLES[key]} crashed internally")
            checks = [{"label": f"internal check error (bug in the gate, not your data): {exc}",
                       "status": "fail", "detail": ""}]
            warnings = []
        stages.append(_stage(key, checks, warnings))

    if weather is not None:
        run_stage("ingestion", lambda: check_ingestion(weather, log))
    else:
        skipped.append("ingestion")

    if weather is not None and lagged_long is not None:
        run_stage("lagged_long", lambda: check_lagged_long(weather, lagged_long, log))
    else:
        skipped.append("lagged_long")

    if lagged_long is not None and lagged_wide is not None:
        run_stage("lagged_wide", lambda: check_lagged_wide(lagged_long, lagged_wide, log))
    else:
        skipped.append("lagged_wide")

    if weather is not None and surveillance is not None and merged is not None:
        run_stage("merge", lambda: check_merge(weather, surveillance, merged, log))
    else:
        skipped.append("merge")

    for s in skipped:
        log.info(f"SKIP  {STAGE_TITLES[s]} -- inputs not supplied")

    n_checks = sum(len(s["checks"]) for s in stages)
    n_warns = sum(len(s["warnings"]) for s in stages)
    passed = all(s["passed"] for s in stages)
    summary = f"{sum(s['passed'] for s in stages)}/{len(stages)} stages passed, {n_checks} checks, {n_warns} warnings"
    log.info(f"{'PASS' if passed else 'FAIL'}  overall -- {summary}")

    return {
        "stages": stages,
        "skipped": skipped,
        "overall": {"passed": passed, "summary": summary},
        "log_text": run.text(),
    }


def main() -> int:
    from data_access import DATA_FILES

    frames: dict[str, pd.DataFrame | None] = {}
    for key, filename in DATA_FILES.items():
        path = DATA_DIR / filename
        frames[key] = pd.read_csv(path) if path.exists() else None
        if frames[key] is None:
            print(f"note: {filename} not found -- its stages will be skipped", file=sys.stderr)

    result = run_pipeline_checks(frames)
    print(result["log_text"])
    return 0 if result["overall"]["passed"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    sys.exit(main())