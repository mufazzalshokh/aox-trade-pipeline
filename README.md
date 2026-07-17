# AOX Trade — Data Engineering Pipeline

A structured, medallion-style data pipeline that ingests raw energy metering data,
cleans it, flags quality issues, and produces a trusted analytics table at
`(timestamp, object_id)` grain.

---

## How to Run

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Place raw CSVs in data/raw/
#    objects.csv, actuals.csv, forecasts.csv

# 3. Run the full pipeline
python main.py

# 4. Run the test suite
python -m pytest tests/ -v
```

Output files land in `data/output/`:
- `cleaned/objects_cleaned.csv`
- `cleaned/actuals_cleaned.csv`
- `cleaned/forecasts_cleaned.csv`
- `curated/analytics.csv`   ← main deliverable
- `curated/dq_report.csv`   ← rows with DQ issues only

Full structured log is written to `pipeline.log`.

---

## Project Structure

```
aox-trade-pipeline/
├── README.md
├── main.py                  # Orchestrator
├── requirements.txt
├── pipeline.log             # Generated at runtime
├── data/
│   ├── raw/                 # Input CSVs (never modified)
│   └── output/
│       ├── cleaned/         # One cleaned CSV per source table
│       └── curated/         # Final analytics table + DQ report
├── src/
│   ├── config.py            # All constants, paths, thresholds
│   ├── logger.py            # Centralised logging
│   ├── ingest.py            # Schema validation + CSV loading
│   ├── clean/
│   │   ├── objects.py       # Clean objects/metadata table
│   │   ├── actuals.py       # Clean actuals timeseries
│   │   └── forecasts.py     # Clean forecasts timeseries
│   └── curate.py            # Build analytics table with DQ flags
└── tests/
    ├── test_clean_objects.py
    ├── test_clean_actuals.py
    └── test_curate.py
```

---

## Data Quality Issues Found

### objects.csv
| Object | Issue |
|--------|-------|
| OBJ_1002 | Fully duplicate row — removed |
| OBJ_1004 | Missing `client_id` |
| OBJ_1005 | Missing `area` |
| OBJ_1006 | Negative `installed_capacity_mw` (-1.5 MW) — physically impossible |
| OBJ_1013 | Suspicious `installed_capacity_mw` (9999 MW) — likely a data entry error |
| OBJ_1003 | Status = inactive, `active_to` = 2026-03-31 (expired before data window) |

### actuals.csv
| Issue | Count |
|-------|-------|
| 1 unparseable `actual_mwh` value (coerced to NaN) | 1 |
| 1 malformed timestamp (NaT) | 1 |
| Duplicate `(timestamp, object_id)` pairs — kept last as correction | 3 pairs → 3 rows dropped |

### forecasts.csv
| Issue | Count |
|-------|-------|
| 1 unparseable `forecast_mwh` value | 1 |
| 1 malformed timestamp | 1 |
| Duplicate `(timestamp, object_id)` pairs | 4 pairs → 4 rows dropped |

### analytics table (final)
| Flag | Rows |
|------|------|
| `missing_object_metadata` | 301 — object IDs present in actuals/forecasts but absent from objects |
| `missing_client_id` | 288 — OBJ_1004 all slots |
| `missing_area` | 288 — OBJ_1005 all slots |
| `negative_capacity` | 288 — OBJ_1006 all slots |
| `inactive_object` | 16 — OBJ_1003 slots |
| `negative_actual_invalid_type` | 1 |
| `no_actual` | 289 — forecast exists but no actual |
| `no_forecast` | 2 — actual exists but no forecast |

---

## Assumptions

1. **BESS negative values are valid** — battery storage systems can charge (import = negative MWh).
   All other object types (solar, wind, hydro, consumer) must have non-negative values.
2. **`active_to = null` means still active** — treated as no end date.
3. **15-minute intervals** — timestamps must land on `:00`, `:15`, `:30`, `:45`.
   Off-grid timestamps are flagged `misaligned_timestamp`.
4. **Duplicate actuals → keep last row** — a later row for the same key is treated as a correction.
5. **Data window is 2026-04-01 to 2026-04-03** — records outside are flagged but not dropped.
6. **Object ID format** is `OBJ_` followed by exactly 4 digits.
7. **Installed capacity threshold**: < 0 = physically invalid; > 500 MW = suspicious for a
   single metering point in this market context.
8. **`is_valid_record = False`** for: missing metadata, inactive/not-yet-active object,
   negative value on non-BESS type, negative/suspicious capacity. Informational flags
   (`no_actual`, `no_forecast`) do not by themselves invalidate a record.

---

## How I Would Improve This in Production

### Orchestration
Replace `main.py` with **Apache Airflow** or **Prefect** for scheduling, retries,
SLA monitoring, and dependency management. Each stage becomes a task with its own
retry policy.

### Storage
Move from CSV to a **columnar format** (Parquet on S3/GCS) for the cleaned and curated
layers. Partition by `delivery_date` for efficient downstream queries.

### Transformation layer
Use **dbt** for the curate step. Each check becomes a dbt test; the analytics table
becomes a dbt model. This gives free lineage, docs, and CI-level data contract enforcement.

### Data quality
Integrate **Great Expectations** or **dbt tests** to enforce schema contracts and
value distributions. Alert on deviation from expected row counts (e.g. < 85% coverage
on 15-min slots per object).

### Incremental loads
Switch from full-refresh to **incremental loads** using a `loaded_at` watermark.
Only process new or updated records each run.

---

## Recommended Target Data Model

```
dim_objects          — slowly-changing dimension (SCD Type 2) for metering metadata
fct_actuals          — append-only fact table with version_id and loaded_at
fct_forecasts        — same structure
fct_analytics        — pre-joined mart at (timestamp, object_id) grain
fct_dq_events        — audit log of every quality flag with ts, object, flag type
```

---

## Bonus: Late-Arriving Corrections to Historical Actuals

Energy imbalance settlement runs at T+2. If a corrected actual arrives after
the submission deadline, three things need to happen:

### 1. Data model change — append-only with versioning

Instead of updating the row in place, append a new version:

```
fct_actuals
  submission_id      UUID       -- unique per submitted batch
  object_id          TEXT
  delivery_ts        TIMESTAMP  -- the 15-min interval being reported
  actual_mwh         FLOAT
  version            INTEGER    -- 1 = first submission, 2 = first correction, ...
  loaded_at          TIMESTAMP  -- when this row arrived in the pipeline
  is_current         BOOLEAN    -- True for the latest version of each (object_id, delivery_ts)
  submitted_for_settlement BOOLEAN  -- True if this version was sent to the grid operator
```

The version that was sent for invoicing is never mutated — `submitted_for_settlement = True`
and `version = 1` (or whichever version hit the T+2 deadline) is the immutable audit record.

### 2. Correction handling

When a late correction arrives:
- Insert it as a new row with `version = N+1`, `is_current = True`
- Flip `is_current = False` on the previous row for that `(object_id, delivery_ts)` key
- The analytics mart re-materialises using `WHERE is_current = True`
- The invoicing/settlement view uses `WHERE submitted_for_settlement = True`

### 3. Alerting downstream consumers

- Write a correction event to `fct_dq_events` with `flag = 'late_correction'`
- Trigger a pipeline alert (Slack / PagerDuty) with: object_id, delivery_ts, delta MWh,
  days since original submission
- If the delta exceeds a materiality threshold (e.g. > 0.1 MWh), flag for manual review
  before the correction propagates to invoicing
