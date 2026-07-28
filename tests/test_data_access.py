"""Unit tests for the read layer."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from data_access import (DataStore, apply_date_range, apply_fields,
                         apply_filters, df_to_records, paginate)


@pytest.fixture()
def df():
    return pd.DataFrame({
        "district_id": ["AAA", "AAA", "BBB", "BBB"],
        "week_start": ["2024-01-01", "2024-01-08", "2024-01-01", "2024-01-08"],
        "value": [1.0, np.nan, 3.0, 4.0],
    })


def test_paginate_caps_page_size(df):
    sliced, total = paginate(df, 1, 10_000)
    assert total == 4 and len(sliced) == 4


def test_paginate_out_of_range_is_empty(df):
    sliced, total = paginate(df, 99, 10)
    assert len(sliced) == 0 and total == 4


def test_apply_filters(df):
    assert len(apply_filters(df, district_id="AAA")) == 2
    assert len(apply_filters(df, district_id=None)) == 4


def test_apply_date_range(df):
    assert len(apply_date_range(df, "week_start", "2024-01-02", "2024-01-31")) == 2


def test_apply_fields_rejects_unknown(df):
    with pytest.raises(KeyError):
        apply_fields(df, "district_id,nope")


def test_df_to_records_nan_becomes_null(df):
    records = df_to_records(df)
    assert records[1]["value"] is None


def test_datastore_reload_is_tolerant(tmp_path):
    (tmp_path / "weather_records_openmeteo.csv").write_text("district_id,week_start\nAAA,2024-01-01\n")
    store = DataStore(tmp_path)
    assert store.get("weather") is not None
    assert store.get("outbreak") is None
    assert store.districts() == ["AAA"]
    report = store.reload()
    assert "weather" in report["loaded"]