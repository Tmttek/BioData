"""End-to-end smoke test: data -> QA gate -> every API endpoint.

Usage:  python scripts/smoke_test.py
Exits non-zero on the first failure. Populate data/ first:
  python scripts/generate_synthetic_weather.py   (or weather.py with network)
  python lagged.py && python lagged_to_wide.py && python merge.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import app as app_module                                   # noqa: E402
from data_access import DATA_FILES                         # noqa: E402
from dq_checks import run_pipeline_checks                  # noqa: E402

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def step(label: str) -> None:
    print(f"\n== {label}")


def main() -> int:
    step("load data/")
    frames = {}
    for key, filename in DATA_FILES.items():
        path = DATA_DIR / filename
        if not path.exists():
            print(f"MISSING {path} -- run the pipeline first"); return 1
        frames[key] = pd.read_csv(path)
        print(f"  {filename}: {len(frames[key])} rows")

    step("run the QA gate on disk data")
    result = run_pipeline_checks(frames)
    print(result["log_text"])
    assert result["overall"]["passed"], "QA gate failed on disk data"

    step("exercise the API")
    app_module.store.reload()
    client = TestClient(app_module.app, raise_server_exceptions=False)

    r = client.get("/api/health")
    assert r.status_code == 200 and all(d["loaded"] for d in r.json()["datasets"].values())
    print("  /api/health: all datasets loaded")

    r = client.get("/api/districts")
    districts = r.json()["districts"]
    assert len(districts) == 9, districts
    print(f"  /api/districts: {len(districts)} districts")

    for path in ["/api/weather?district_id=BNG&page_size=5",
                 "/api/surveillance?page_size=5",
                 "/api/lagged-features?base_variable=rainfall_mm&lag_weeks=4&page_size=5",
                 "/api/outbreak-dataset?fields=district_id,week_start,confirmed_cases&page_size=5"]:
        r = client.get(path)
        body = r.json()
        assert r.status_code == 200 and set(body) == {"total", "page", "page_size", "count", "results"}, path
        assert body["count"] == len(body["results"]) and body["count"] <= 5, path
        print(f"  {path}: {body['count']}/{body['total']} rows, envelope ok")

    r = client.get("/api/weather/BNG/2024-01-01")
    assert r.status_code == 200 and r.json()["district_id"] == "BNG"
    print("  /api/weather/BNG/2024-01-01: single record ok")

    assert client.get("/api/weather/ZZZ/1999-01-01").status_code == 404
    print("  404 path ok")

    step("POST /api/run with the weather file")
    csv_bytes = (DATA_DIR / DATA_FILES["weather"]).read_bytes()
    r = client.post("/api/run", files={"weather": ("weather.csv", csv_bytes, "text/csv")})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["stages"][0]["passed"] and body["skipped"] == ["lagged_long", "lagged_wide", "merge"]
    print(f"  stage 1 passed on upload; skipped: {body['skipped']}")

    assert client.post("/api/run").status_code == 400
    print("  400 on empty run ok")

    print("\nSMOKE TEST PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())