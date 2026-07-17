"""
ingest.py
---------
Responsible for loading raw CSV files and enforcing the expected schema
(column names, dtypes, presence of required columns).

Raises clear errors early so downstream cleaning steps receive predictable
DataFrames and never have to guess about structure.
"""

from pathlib import Path
import pandas as pd

from src.logger import get_logger

logger = get_logger("aox.ingest")

# Expected schemas: column name -> pandas dtype (None = infer later)
_SCHEMAS: dict[str, dict[str, str | None]] = {
    "objects": {
        "object_id": "str",
        "client_id": "str",
        "object_type": "str",
        "area": "str",
        "active_from": "str",   # parsed to datetime in cleaning step
        "active_to": "str",     # parsed to datetime in cleaning step
        "installed_capacity_mw": "float64",
        "status": "str",
    },
    "actuals": {
        "timestamp": "str",     # parsed to datetime in cleaning step
        "object_id": "str",
        "actual_mwh": "float64",
    },
    "forecasts": {
        "timestamp": "str",
        "object_id": "str",
        "forecast_mwh": "float64",
    },
}


def load_csv(path: Path, table_name: str) -> pd.DataFrame:
    """
    Load a CSV file and validate that all required columns are present.

    Parameters
    ----------
    path : Path
        Absolute path to the CSV file.
    table_name : str
        One of "objects", "actuals", "forecasts" — used to look up the
        expected schema.

    Returns
    -------
    pd.DataFrame
        Raw DataFrame with string columns for fields that need parsing,
        and numeric dtypes for numeric fields.

    Raises
    ------
    FileNotFoundError
        If the CSV does not exist at the given path.
    ValueError
        If required columns are missing from the file.
    """
    if not path.exists():
        raise FileNotFoundError(f"Raw data file not found: {path}")

    logger.info("Loading %s from %s", table_name, path)

    # Keep everything as object first, then cast below — avoids silent
    # coercion that pandas sometimes applies on read
    df = pd.read_csv(path, dtype=str, keep_default_na=False)

    # Strip accidental whitespace from column names and string values
    df.columns = [c.strip() for c in df.columns]
    str_cols = df.select_dtypes("object").columns
    df[str_cols] = df[str_cols].apply(lambda s: s.str.strip())

    schema = _SCHEMAS[table_name]
    _validate_columns(df, schema, table_name)
    df = _cast_numerics(df, schema, table_name)

    # Replace empty strings with proper NaN for nullable columns
    df.replace("", pd.NA, inplace=True)

    logger.info(
        "Loaded %s: %d rows x %d cols", table_name, len(df), len(df.columns)
    )
    return df


def _validate_columns(
    df: pd.DataFrame, schema: dict, table_name: str
) -> None:
    required = set(schema.keys())
    present = set(df.columns)
    missing = required - present
    if missing:
        raise ValueError(
            f"[{table_name}] Missing expected columns: {missing}. "
            f"Found: {present}"
        )
    extra = present - required
    if extra:
        logger.warning("[%s] Unexpected extra columns (ignored): %s", table_name, extra)


def _cast_numerics(
    df: pd.DataFrame, schema: dict, table_name: str
) -> pd.DataFrame:
    """Cast columns declared as float64 in the schema, coercing bad values to NaN."""
    for col, dtype in schema.items():
        if dtype == "float64" and col in df.columns:
            original_nulls = df[col].isna().sum()
            df[col] = pd.to_numeric(df[col], errors="coerce")
            new_nulls = df[col].isna().sum()
            coerced = new_nulls - original_nulls
            if coerced > 0:
                logger.warning(
                    "[%s] Column '%s': %d value(s) could not be parsed as "
                    "numeric and were set to NaN",
                    table_name, col, coerced,
                )
    return df
