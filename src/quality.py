"""
quality.py
----------
Cross-table data quality checks that require joining two or more cleaned tables.

Each check function returns a DataFrame with a consistent schema:
    object_id | timestamp | issue_type | detail

run_all_checks() consolidates them into a single DQ report that is saved
alongside the curated output.

Design principle: checks are READ-ONLY.  They observe and report;
mutation decisions live in curate.py.
"""

import pandas as pd

from src import config
from src.logger import get_logger

logger = get_logger("aox.quality")

# Canonical schema every check function must return
DQ_SCHEMA = ["object_id", "timestamp", "issue_type", "detail"]


def _empty() -> pd.DataFrame:
    return pd.DataFrame(columns=DQ_SCHEMA)


# ---------------------------------------------------------------------------
# Individual check functions
# ---------------------------------------------------------------------------

def check_missing_object_metadata(
    timeseries: pd.DataFrame,
    objects: pd.DataFrame,
    label: str,
) -> pd.DataFrame:
    """Flag timeseries rows whose object_id has no match in the objects table."""
    known_ids = set(objects["object_id"].dropna())
    mask = ~timeseries["object_id"].isin(known_ids)
    flagged = timeseries.loc[mask, ["object_id", "timestamp"]].copy()
    flagged["issue_type"] = config.DQ_MISSING_OBJECT_METADATA
    flagged["detail"] = f"object_id absent from objects table ({label})"
    logger.debug("check_missing_object_metadata [%s]: %d issue(s)", label, len(flagged))
    return flagged[DQ_SCHEMA]


def check_inactive_object_timeseries(
    timeseries: pd.DataFrame,
    objects: pd.DataFrame,
    label: str,
) -> pd.DataFrame:
    """
    Flag timeseries rows for objects that were inactive at the record timestamp.

    Two sub-cases:
      a) object.status == 'inactive'
      b) record timestamp is before object.active_from  (not yet commissioned)
      c) record timestamp is after object.active_to  (decommissioned)
    """
    merged = timeseries[["object_id", "timestamp"]].merge(
        objects[["object_id", "status", "active_from", "active_to"]],
        on="object_id",
        how="inner",
    )

    inactive_status = merged["status"] == "inactive"

    not_yet_active = (
        merged["active_from"].notna()
        & merged["timestamp"].notna()
        & (merged["timestamp"] < merged["active_from"])
    )

    past_active_to = (
        merged["active_to"].notna()
        & merged["timestamp"].notna()
        & (merged["timestamp"] > merged["active_to"])
    )

    combined = inactive_status | not_yet_active | past_active_to

    def _reason(row: pd.Series) -> str:
        if row["status"] == "inactive":
            return f"{label}: record for inactive object"
        if pd.notna(row["active_from"]) and row["timestamp"] < row["active_from"]:
            return f"{label}: record before active_from ({row['active_from'].date()})"
        return f"{label}: record after active_to ({row['active_to'].date()})"

    flagged = merged[combined][["object_id", "timestamp"]].copy()
    flagged["issue_type"] = merged.loc[combined].apply(
        lambda r: (
            config.DQ_OBJECT_NOT_YET_ACTIVE if not_yet_active[r.name]
            else config.DQ_INACTIVE_OBJECT
        ),
        axis=1,
    ).values
    flagged["detail"] = merged[combined].apply(_reason, axis=1).values

    logger.debug("check_inactive_object_timeseries [%s]: %d issue(s)", label, len(flagged))
    return flagged[DQ_SCHEMA]


def check_negative_mwh(
    timeseries: pd.DataFrame,
    objects: pd.DataFrame,
    value_col: str,
    label: str,
) -> pd.DataFrame:
    """
    Flag negative MWh values for object types that must always be non-negative.
    BESS is exempt (charging = negative).
    """
    merged = timeseries[["object_id", "timestamp", value_col]].merge(
        objects[["object_id", "object_type"]],
        on="object_id",
        how="left",
    )

    is_negative  = merged[value_col].notna() & (merged[value_col] < 0)
    is_non_exempt = ~merged["object_type"].isin(config.NEGATIVE_ALLOWED_TYPES)
    mask = is_negative & is_non_exempt

    flagged = merged.loc[mask, ["object_id", "timestamp"]].copy()
    issue_flag = (
        config.DQ_NEGATIVE_ACTUAL
        if value_col == "actual_mwh"
        else config.DQ_NEGATIVE_FORECAST
    )
    flagged["issue_type"] = issue_flag
    flagged["detail"] = merged.loc[mask, "object_type"].apply(
        lambda t: f"negative {value_col} for object_type='{t}' (BESS exempt)"
    ).values

    logger.debug("check_negative_mwh [%s]: %d issue(s)", label, len(flagged))
    return flagged[DQ_SCHEMA]


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def run_all_checks(
    actuals: pd.DataFrame,
    forecasts: pd.DataFrame,
    objects: pd.DataFrame,
) -> pd.DataFrame:
    """
    Run all cross-table DQ checks and return a consolidated report.

    Returns
    -------
    pd.DataFrame  — columns: object_id, timestamp, issue_type, detail
    """
    logger.info("Running cross-table DQ checks")

    results = [
        check_missing_object_metadata(actuals,   objects, "actuals"),
        check_missing_object_metadata(forecasts, objects, "forecasts"),
        check_inactive_object_timeseries(actuals,   objects, "actuals"),
        check_inactive_object_timeseries(forecasts, objects, "forecasts"),
        check_negative_mwh(actuals,   objects, "actual_mwh",   "actuals"),
        check_negative_mwh(forecasts, objects, "forecast_mwh", "forecasts"),
    ]

    report = (
        pd.concat(results, ignore_index=True)
        .sort_values(["object_id", "timestamp"], na_position="last")
        .reset_index(drop=True)
    )

    n_issues = len(report)
    n_types  = report["issue_type"].nunique()
    logger.info("DQ checks complete — %d issue(s) across %d issue type(s)", n_issues, n_types)

    for issue, count in report.groupby("issue_type").size().items():
        logger.info("  ↳ quality check | %-40s : %d", issue, count)

    return report
