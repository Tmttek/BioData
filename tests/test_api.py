"""API tests: envelope shape, 404/503 semantics, upload runs."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app as app_module

WEATHER_CSV = "district_id,week_start,rainfall_mm\nAAA,2024-01-01,12.5\nAAA,2024-01-08,0.0\n"


@pytest.fixture()
def client(tmp_path):
    (tmp_path / "weather_records_openmeteo.csv").write_text(WEATHER_CSV)
    app_module.store.data_dir = tmp_path
    app_module.store.reload()
    with TestClient(app_module.app) as c:
        yield c


def test_health(client):
    body = client.get("/api/health").json()
    assert body["datasets"]["weather"]["loaded"] is True
    assert body["datasets"]["outbreak"]["loaded"] is False


def test_list_envelope(client):
    body = client.get("/api/weather?page_size=1").json()
    assert set(body) == {"total", "page", "page_size", "count", "results"}
    assert body["total"] == 2 and body["count"] == 1


def test_single_record_and_404(client):
    assert client.get("/api/weather/AAA/2024-01-01").status_code == 200
    assert client.get("/api/weather/AAA/1999-01-01").status_code == 404


def test_missing_dataset_is_503_not_404(client):
    assert client.get("/api/outbreak-dataset").status_code == 503


def test_run_with_upload(client):
    r = client.post("/api/run", files={"weather": ("w.csv", WEATHER_CSV, "text/csv")})
    assert r.status_code == 200
    body = r.json()
    assert body["overall"]["passed"] is False or body["overall"]["passed"] is True  # ran, didn't crash
    assert "ingestion" not in body["skipped"]


def test_run_with_no_files_is_400(client):
    assert client.post("/api/run").status_code == 400


def test_reload_picks_up_new_files(client, tmp_path):
    assert client.get("/api/outbreak-dataset").status_code == 503
    (tmp_path / "outbreak_dataset_v1.csv").write_text("district_id,week_start\nAAA,2024-01-01\n")
    client.post("/api/reload")
    assert client.get("/api/outbreak-dataset").status_code == 200