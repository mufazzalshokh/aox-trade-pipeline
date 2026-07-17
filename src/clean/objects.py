"""
clean/objects.py
----------------
Cleaning logic for the objects (metering point metadata) table.

Cleaning steps applied (in order):
  1. Parse date columns to datetime
  2. Normalise string fields (lowercase object_type, uppercase area)
  3. Remove fully duplicate rows, flag partial duplicates (same object_id)
  4. Flag rows with missing client_id or area
  5. Flag invalid object_id format
  6. Flag negative or suspicious installed_capacity_mw
  7. Flag objects whose status is 'inactive' or whose active_to is in the past

Each flagged issue is recorded in a `dq_flags` list column, which is
later serialised to a pipe-separated string.
"""

import re
import pandas as pd

from src import config
from src.logger import get_logger

logger = get_logger("aox.clean.objects")


def clean_objects(df: pd.DataFrame) -> pd.DataFrame:
    """
    Apply all cleaning and quality-flag steps to the raw objects table.

    Parameters
    ----------
    df : pd.DataFrame
        Raw objects DataFrame from ingest.load_csv.

    Returns
    -------
    pd.DataFrame
        Cleaned DataFrame with an additional `dq_flags` string column and
        a boolean `has_dq_issue` convenience column.
    """
    logger.info("Cleaning objects table — %d rows in", len(df))

    df = df.copy()

    # ------------------------------------------------------------------ #
    # 1. Parse date columns
    # ------------------------------------------------------------------ #
    for col in ("active_from", "active_to"):
        df[col] = pd.to_datetime(df[col], errors="coerce")
        bad = df[col].isna() & df[col.replace("active_", "active_")].notna() if col == "active_to" else pd.Series(False, index=df.index)
        # active_to is nullable (null = no end date), so only warn on active_from
        if col == "active_from":
            n_bad = df[col].isna().sum()
            if n_bad:
                logger.warning("objects: %d unparseable 'active_from' value(s)", n_bad)

    # ------------------------------------------------------------------ #
    # 2. Normalise strings
    # ------------------------------------------------------------------ #
    df["object_type"] = df["object_type"].str.lower().str.strip()
    df["area"]        = df["area"].str.upper().str.strip()
    df["status"]      = df["status"].str.lower().str.strip()

    # ------------------------------------------------------------------ #
    # 3. Duplicate rows
    # ------------------------------------------------------------------ #
    # Fully identical rows → drop silently (data pipeline noise)
    before = len(df)
    df = df.drop_duplicates()
    dropped_full = before - len(df)
    if dropped_full:
        logger.info("objects: dropped %d fully duplicate row(s)", dropped_full)

    # Same object_id but different metadata → keep first, flag all occurrences
    dup_mask = df.duplicated(subset=["object_id"], keep=False)
    n_dup_id = dup_mask.sum()
    if n_dup_id:
        logger.warning(
            "objects: %d row(s) share a duplicate object_id — keeping first occurrence",
            n_dup_id,
        )
    # We'll flag all rows with duplicate IDs before deduplicating
    df["_dup_object_id"] = dup_mask
    df = df.drop_duplicates(subset=["object_id"], keep="first").reset_index(drop=True)

    # ------------------------------------------------------------------ #
    # 4. Build dq_flags column — one list per row
    # ------------------------------------------------------------------ #
    df["dq_flags"] = [[] for _ in range(len(df))]

    _flag_where(df, df["_dup_object_id"],                                  config.DQ_DUPLICATE_OBJECT)
    _flag_where(df, df["client_id"].isna(),                                config.DQ_MISSING_CLIENT_ID)
    _flag_where(df, df["area"].isna(),                                     config.DQ_MISSING_AREA)
    _flag_where(df, ~df["object_id"].str.match(config.OBJECT_ID_PATTERN, na=False), config.DQ_INVALID_OBJECT_ID_FORMAT)
    _flag_where(df, df["installed_capacity_mw"] < config.CAPACITY_MIN_MW, config.DQ_NEGATIVE_CAPACITY)
    _flag_where(df, df["installed_capacity_mw"] > config.CAPACITY_MAX_MW, config.DQ_SUSPICIOUS_CAPACITY)
    _flag_where(df, df["status"] == "inactive",                            config.DQ_INACTIVE_OBJECT)

    # ------------------------------------------------------------------ #
    # 5. Serialise flags to pipe-separated string
    # ------------------------------------------------------------------ #
    df["dq_flags"]   = df["dq_flags"].apply(lambda f: "|".join(f) if f else "")
    df["has_dq_issue"] = df["dq_flags"] != ""

    df = df.drop(columns=["_dup_object_id"])

    n_issues = df["has_dq_issue"].sum()
    logger.info(
        "objects: cleaning complete — %d rows out, %d with DQ issues", len(df), n_issues
    )
    _log_flag_summary(df)

    return df


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _flag_where(df: pd.DataFrame, mask: pd.Series, flag: str) -> None:
    """Append `flag` to the dq_flags list for every row where mask is True."""
    count = mask.sum()
    if count:
        logger.debug("objects flag '%s': %d row(s)", flag, count)
    for idx in df.index[mask]:
        df.at[idx, "dq_flags"].append(flag)


def _log_flag_summary(df: pd.DataFrame) -> None:
    from collections import Counter
    all_flags = [
        flag
        for flags in df["dq_flags"]
        for flag in flags.split("|")
        if flag
    ]
    if all_flags:
        summary = Counter(all_flags)
        for flag, count in sorted(summary.items()):
            logger.info("  ↳ objects DQ | %-40s : %d", flag, count)
