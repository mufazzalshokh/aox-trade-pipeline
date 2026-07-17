"""
curate.py
---------
Builds the final analytics table at (timestamp, object_id) grain.

Join strategy:
  - Start with a FULL OUTER JOIN of actuals and forecasts so we capture
    every timestamp that exists in either dataset.
  - LEFT JOIN the cleaned objects table to bring in metadata.
  - Compute derived metrics: abs_error, is_valid_record, data_quality_issue.

is_valid_record = False when ANY of:
  - Object metadata is missing (no match in objects table)
  - Object is inactive at the time of the record
  - Object was not yet active at the time of the record
  - actual_mwh is negative for a non-BESS type
  - forecast_mwh is negative for a non-BESS type
  - Object has a negative or suspicious installed_capacity
"""

import pandas as pd

from src import config
from src.logger import get_logger

logger = get_logger("aox.curate")


def build_analytics(
    actuals: pd.DataFrame,
    forecasts: pd.DataFrame,
    objects: pd.DataFrame,
) -> pd.DataFrame:
    """
    Join cleaned actuals, forecasts, and objects into one analytics table.

    Parameters
    ----------
    actuals : pd.DataFrame
        Output of clean_actuals().
    forecasts : pd.DataFrame
        Output of clean_forecasts().
    objects : pd.DataFrame
        Output of clean_objects().

    Returns
    -------
    pd.DataFrame
        Analytics table sorted by (timestamp, object_id) with
        `abs_error`, `is_valid_record`, and `data_quality_issue` columns.
    """
    logger.info(
        "Building analytics table — actuals: %d, forecasts: %d, objects: %d",
        len(actuals), len(forecasts), len(objects),
    )

    # ------------------------------------------------------------------ #
    # 1. Full outer join actuals + forecasts on (timestamp, object_id)
    # ------------------------------------------------------------------ #
    if actuals.empty:
        ts_actuals = pd.DataFrame(columns=["timestamp", "object_id", "actual_mwh"])
    else:
        ts_actuals = actuals[["timestamp", "object_id", "actual_mwh"]].copy()
    if forecasts.empty:
        ts_forecasts = pd.DataFrame(columns=["timestamp", "object_id", "forecast_mwh"])
    else:
        ts_forecasts = forecasts[["timestamp", "object_id", "forecast_mwh"]].copy()

    merged = pd.merge(
        ts_actuals,
        ts_forecasts,
        on=["timestamp", "object_id"],
        how="outer",
    )
    logger.info("After outer join actuals ∪ forecasts: %d rows", len(merged))

    # ------------------------------------------------------------------ #
    # 2. Left join object metadata
    # ------------------------------------------------------------------ #
    obj_cols = [
        "object_id", "client_id", "object_type", "area",
        "active_from", "active_to", "installed_capacity_mw", "status",
        "dq_flags",   # object-level DQ flags
    ]
    obj_meta = objects[obj_cols].copy().rename(columns={"dq_flags": "obj_dq_flags"})

    df = pd.merge(merged, obj_meta, on="object_id", how="left")
    logger.info("After left join objects: %d rows", len(df))

    # ------------------------------------------------------------------ #
    # 3. Compute abs_error (only when both values present)
    # ------------------------------------------------------------------ #
    both_present = df["actual_mwh"].notna() & df["forecast_mwh"].notna()
    df["abs_error"] = pd.NA
    df.loc[both_present, "abs_error"] = (
        df.loc[both_present, "actual_mwh"] - df.loc[both_present, "forecast_mwh"]
    ).abs().round(6)

    # ------------------------------------------------------------------ #
    # 4. Row-level DQ flags (context-aware — needs object_type)
    # ------------------------------------------------------------------ #
    df["row_dq_flags"] = [[] for _ in range(len(df))]

    is_bess = df["object_type"] == "bess"

    # Missing object metadata
    _flag_where(df, df["client_id"].isna(), config.DQ_MISSING_OBJECT_METADATA)

    # Inactive object
    inactive_status = df["status"] == "inactive"
    _flag_where(df, inactive_status, config.DQ_INACTIVE_OBJECT)

    # Object not yet active at timestamp (active_from > record timestamp)
    not_yet_active = (
        df["active_from"].notna()
        & df["timestamp"].notna()
        & (df["timestamp"] < df["active_from"])
    )
    _flag_where(df, not_yet_active, config.DQ_OBJECT_NOT_YET_ACTIVE)

    # Object decommissioned at timestamp (active_to < record timestamp)
    # Catches cases where status field was not updated but active_to has passed
    past_active_to = (
        df["active_to"].notna()
        & df["timestamp"].notna()
        & (df["timestamp"] > df["active_to"])
    )
    _flag_where(df, past_active_to, config.DQ_INACTIVE_OBJECT)

    # Negative actual for non-BESS
    neg_actual = df["actual_mwh"].notna() & (df["actual_mwh"] < 0) & ~is_bess
    _flag_where(df, neg_actual, config.DQ_NEGATIVE_ACTUAL)

    # Negative forecast for non-BESS
    neg_forecast = df["forecast_mwh"].notna() & (df["forecast_mwh"] < 0) & ~is_bess
    _flag_where(df, neg_forecast, config.DQ_NEGATIVE_FORECAST)

    # No actual
    _flag_where(df, df["actual_mwh"].isna(), config.DQ_NO_ACTUAL)

    # No forecast
    _flag_where(df, df["forecast_mwh"].isna(), config.DQ_NO_FORECAST)

    # ------------------------------------------------------------------ #
    # 5. Merge row-level flags with object-level flags
    # ------------------------------------------------------------------ #
    df["row_dq_flags"] = df["row_dq_flags"].apply(lambda f: "|".join(f) if f else "")

    def _combine_flags(row: pd.Series) -> str:
        # obj_dq_flags can be NaN when the object join has no match
        parts = [
            p for p in [row["obj_dq_flags"], row["row_dq_flags"]]
            if p and not (isinstance(p, float) and pd.isna(p))
        ]
        return "|".join(parts)

    df["data_quality_issue"] = df.apply(_combine_flags, axis=1)

    # ------------------------------------------------------------------ #
    # 6. is_valid_record
    #    A record is invalid if it has any "critical" DQ flag.
    #    Informational-only flags (no_actual, no_forecast when the other
    #    side exists) do NOT by themselves invalidate a record.
    # ------------------------------------------------------------------ #
    CRITICAL_FLAGS = {
        config.DQ_MISSING_OBJECT_METADATA,
        config.DQ_INACTIVE_OBJECT,
        config.DQ_OBJECT_NOT_YET_ACTIVE,
        config.DQ_NEGATIVE_ACTUAL,
        config.DQ_NEGATIVE_FORECAST,
        config.DQ_NEGATIVE_CAPACITY,
        config.DQ_SUSPICIOUS_CAPACITY,
        config.DQ_INVALID_OBJECT_ID_FORMAT,
    }

    def _has_critical_flag(flags_str: str) -> bool:
        if not flags_str:
            return False
        return bool(CRITICAL_FLAGS & set(flags_str.split("|")))

    df["is_valid_record"] = ~df["data_quality_issue"].apply(_has_critical_flag)

    # ------------------------------------------------------------------ #
    # 7. Select and order final columns
    # ------------------------------------------------------------------ #
    final_cols = [
        "timestamp",
        "object_id",
        "client_id",
        "object_type",
        "area",
        "actual_mwh",
        "forecast_mwh",
        "abs_error",
        "is_valid_record",
        "data_quality_issue",
    ]
    df = df[final_cols].sort_values(["timestamp", "object_id"]).reset_index(drop=True)

    n_valid   = df["is_valid_record"].sum()
    n_invalid = (~df["is_valid_record"]).sum()
    logger.info(
        "Analytics table complete — %d rows | %d valid | %d invalid",
        len(df), n_valid, n_invalid,
    )
    _log_flag_summary(df)

    return df


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _flag_where(df: pd.DataFrame, mask: pd.Series, flag: str) -> None:
    count = int(mask.sum())
    if count:
        logger.debug("analytics flag '%s': %d row(s)", flag, count)
    for idx in df.index[mask]:
        df.at[idx, "row_dq_flags"].append(flag)


def _log_flag_summary(df: pd.DataFrame) -> None:
    from collections import Counter
    all_flags = [
        f
        for flags in df["data_quality_issue"]
        for f in flags.split("|")
        if f
    ]
    if all_flags:
        for flag, count in sorted(Counter(all_flags).items()):
            logger.info("  ↳ analytics DQ | %-40s : %d", flag, count)
