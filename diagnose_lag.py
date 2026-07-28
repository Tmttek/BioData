"""Pinpoint why lagged values disagree with weather.shift(lag).

For one district/variable/lag, compare the lagged file's stored value against
four interpretations of what it 'should' be. Whichever diff column is ~0 tells
us the exact cause.

Run:  python diagnose_lag.py
"""
from pathlib import Path

import pandas as pd

DATA = Path("data")
w = pd.read_csv(DATA / "weather_records_openmeteo.csv", parse_dates=["week_start"])
long = pd.read_csv(DATA / "weather_lagged_features_openmeteo.csv", parse_dates=["week_start"])

d = long["district_id"].min()
var = "temp_max_c"
lag = 1

wd = w[w["district_id"] == d].set_index("week_start").sort_index()
g = long[(long["district_id"] == d) & (long["base_variable"] == var) & (long["lag_weeks"] == lag)]
g = g.set_index("week_start").sort_index()

cmp = pd.DataFrame({
    "lagged_value": g["value"],
    "weather_here": wd[var],                 # no shift at all
    "shift_plus": wd[var].shift(lag),        # value L weeks EARLIER (my check's assumption)
    "shift_minus": wd[var].shift(-lag),      # value L weeks LATER
}).dropna(subset=["lagged_value"]).head(12)

for ref in ["weather_here", "shift_plus", "shift_minus"]:
    cmp[f"diff_{ref}"] = (cmp["lagged_value"] - cmp[ref]).abs().round(4)

print(f"district={d}  variable={var}  lag={lag}\n")
print(cmp.to_string())
print()
print("Read the diff columns:")
print("  diff_weather_here ~ 0  -> lagged week_start is the SOURCE week; no shift in alignment")
print("  diff_shift_plus   ~ 0  -> my convention is right; the 12768 are a provenance/rounding issue")
print("  diff_shift_minus  ~ 0  -> your pipeline lags in the OPPOSITE direction; my check is wrong")
print("  all diffs large        -> this weather file isn't the one the lagged file was built from")