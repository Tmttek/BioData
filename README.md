# ZHOID-2026-002 — Weather ↔ Malaria Surveillance Pipeline + QA Console

Pipeline: `weather.py → lagged.py → lagged_to_wide.py → merge.py`, gated by
`dq_checks.py`, served by a FastAPI app with a browser QA console and a
read-only REST data API.

## Quickstart (fully offline)

    pip install -r requirements.txt

    python scripts/generate_synthetic_weather.py   # stand-in for weather.py
    python lagged.py
    python lagged_to_wide.py
    python merge.py                                # needs malaria_surveillance_synthetic.csv
    python dq_checks.py                            # gate the disk data, exit code 0/1

    uvicorn app:app --reload
    # console:  http://localhost:8000
    # API docs: http://localhost:8000/docs

With network access, replace the first step with `python weather.py`
(fetches real Open-Meteo archive data).

## Verify & smoke-test

    pytest tests/ -q
    python scripts/smoke_test.py

## Data API

All list endpoints share one envelope:
`{"total", "page", "page_size", "count", "results"}` — `page_size` capped at 500.

| Method | Path | Notes |
|---|---|---|
| POST | /api/run | multipart upload; checks run in memory, files discarded (25 MB cap) |
| GET  | /api/districts | canonical IDs from loaded datasets |
| POST | /api/reload | re-read data/ without restart |
| GET  | /api/weather | `district_id`, `week_start_from/to`, `fields`, `page`, `page_size` |
| GET  | /api/weather/{district_id}/{week_start} | single record |
| GET  | /api/surveillance (+ /{district_id}/{week_start}) | same filters |
| GET  | /api/lagged-features | adds `base_variable`, `lag_weeks` |
| GET  | /api/outbreak-dataset (+ /{district_id}/{week_start}) | `fields` recommended (~66 cols) |
| GET  | /api/health | per-dataset load status |

Missing datasets return **503** (fixable via `/api/reload`), unknown records **404**.

## Contracts worth knowing

- Lagged rows per (district, variable, lag) = `max(n_weeks − lag − warmup, 0)`,
  warmup = 3 for `rainfall_cum4wk_mm` — enforced by Stage 2a.
- `rainfall_cum4wk_mm` must equal the 4-week rolling sum of `rainfall_mm` with
  exactly 3 leading NaNs per district.
- `BASE_VARS`, `LAG_WEEKS`, `KEY_COLS` live in `dq_checks.py`; the pipeline
  scripts import them, so the constants can't drift.