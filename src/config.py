"""
config.py
---------
Central configuration for the AOX Trade pipeline.
All thresholds, valid values, and file paths live here so they can be
changed in one place without touching business logic.
"""

from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent

RAW_DIR     = BASE_DIR / "data" / "raw"
CLEANED_DIR = BASE_DIR / "data" / "output" / "cleaned"
CURATED_DIR = BASE_DIR / "data" / "output" / "curated"

RAW_OBJECTS   = RAW_DIR / "objects.csv"
RAW_ACTUALS   = RAW_DIR / "actuals.csv"
RAW_FORECASTS = RAW_DIR / "forecasts.csv"

CLEANED_OBJECTS   = CLEANED_DIR / "objects_cleaned.csv"
CLEANED_ACTUALS   = CLEANED_DIR / "actuals_cleaned.csv"
CLEANED_FORECASTS = CLEANED_DIR / "forecasts_cleaned.csv"

CURATED_ANALYTICS = CURATED_DIR / "analytics.csv"
DQ_REPORT         = CURATED_DIR / "dq_report.csv"

# ---------------------------------------------------------------------------
# Data window
# ---------------------------------------------------------------------------
DATA_WINDOW_START = "2026-04-01"
DATA_WINDOW_END   = "2026-04-03 23:45:00"

# ---------------------------------------------------------------------------
# Domain constants
# ---------------------------------------------------------------------------

# Object types where negative MWh values are physically valid (BESS can charge)
NEGATIVE_ALLOWED_TYPES = {"bess"}

# Valid object types accepted in the system
VALID_OBJECT_TYPES = {"solar", "wind", "bess", "consumer", "hydro"}

# Valid Baltic market area codes
VALID_AREAS = {"LV", "EE", "LT", "FI", "SE", "PL"}

# Regex pattern for object_id: OBJ_ followed by exactly 4 digits
OBJECT_ID_PATTERN = r"^OBJ_\d{4}$"

# Installed capacity thresholds (MW)
CAPACITY_MIN_MW = 0.0
CAPACITY_MAX_MW = 500.0

# Expected interval between timestamps (minutes)
INTERVAL_MINUTES = 15

# ---------------------------------------------------------------------------
# Data quality issue labels  (pipe-separated tags in curated output)
# ---------------------------------------------------------------------------
DQ_DUPLICATE_OBJECT          = "duplicate_object"
DQ_MISSING_CLIENT_ID         = "missing_client_id"
DQ_MISSING_AREA              = "missing_area"
DQ_NEGATIVE_CAPACITY         = "negative_capacity"
DQ_SUSPICIOUS_CAPACITY       = "suspicious_capacity"
DQ_INVALID_OBJECT_ID_FORMAT  = "invalid_object_id_format"
DQ_INACTIVE_OBJECT           = "inactive_object"
DQ_OBJECT_NOT_YET_ACTIVE     = "object_not_yet_active"
DQ_MISSING_OBJECT_METADATA   = "missing_object_metadata"
DQ_DUPLICATE_TIMESERIES      = "duplicate_timeseries_key"
DQ_NEGATIVE_ACTUAL           = "negative_actual_invalid_type"
DQ_NEGATIVE_FORECAST         = "negative_forecast_invalid_type"
DQ_OUTSIDE_DATA_WINDOW       = "outside_data_window"
DQ_MISALIGNED_TIMESTAMP      = "misaligned_timestamp"
DQ_MALFORMED_TIMESTAMP       = "malformed_timestamp"
DQ_NO_ACTUAL                 = "no_actual"
DQ_NO_FORECAST               = "no_forecast"
