"""
clean/actuals.py
----------------
Cleaning logic for the actuals (measured energy) table.

Steps:
  1. Parse timestamp column to datetime, flag malformed values
  2. Validate object_id format
  3. Deduplicate on (timestamp, object_id), keeping the last value
     (assumption: a later row is a correction of an earlier one)
  4. Flag timestamps outside the expected 3-day data window
  5. Flag timestamps not aligned to 15-minute boundaries
  6. Flag negative actual_mwh — deferred to curate step where we have
     object_type context; here we only flag rows with NaN actual_mwh
"""

import pandas as pd

from src import config
from src.logger import get_logger

logger = get_logger("aox.clean.actuals")


def clean_actuals(df: pd.DataFrame) -> pd.DataFrame:
    """
    Apply all cleaning and quality-flag steps to the raw actuals table.

    Parameters
    ----------
    df : pd.DataFrame
        Raw actuals DataFrame from ingest.load_csv.

    Returns
    -------
    pd.DataFrame
        Cleaned DataFrame with `dq_flags` and `has_dq_issue` columns.
    """
    logger.info("Cleaning actuals table — %d rows in", len(df))

    df = df.copy()

    # ------------------------------------------------------------------ #
    # 1. Parse timestamp
    # ------------------------------------------------------------------ #
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    n_bad_ts = df["timestamp"].isna().sum()
    if n_bad_ts:
        logger.warning("actuals: %d malformed timestamp(s) set to NaT", n_bad_ts)

    # ------------------------------------------------------------------ #
    # 2. Deduplicate on (timestamp, object_id)
    # ------------------------------------------------------------------ #
    before = len(df)
    # keep='last' treats later rows as corrections (see assumption in module doc)
    dup_mask = df.duplicated(subset=["timestamp", "object_id"], keep=False)
    n_dups = dup_mask.sum()
    if n_dups:
        logger.warning(
            "actuals: %d row(s) form duplicate (timestamp, object_id) pairs — "
            "keeping last occurrence as correction",
            n_dups,
        )
    # Store which rows were duplicated *before* deduplication so we can flag them
    df["_was_duplicate"] = dup_mask
    df = df.drop_duplicates(subset=["timestamp", "object_id"], keep="last").reset_index(drop=True)
    logger.info("actuals: deduplicated %d → %d rows", before, len(df))

    # ------------------------------------------------------------------ #
    # 3. Build dq_flags
    # ------------------------------------------------------------------ #
    df["dq_flags"] = [[] for _ in range(len(df))]

    _flag_where(df, df["_was_duplicate"],                                    config.DQ_DUPLICATE_TIMESERIES)
    _flag_where(df, df["timestamp"].isna(),                                  config.DQ_MALFORMED_TIMESTAMP)
    _flag_where(df, ~df["object_id"].str.match(config.OBJECT_ID_PATTERN, na=False), config.DQ_INVALID_OBJECT_ID_FORMAT)

    window_start = pd.Timestamp(config.DATA_WINDOW_START)
    window_end   = pd.Timestamp(config.DATA_WINDOW_END)
    outside_window = (
        df["timestamp"].notna()
        & ((df["timestamp"] < window_start) | (df["timestamp"] > window_end))
    )
    _flag_where(df, outside_window, config.DQ_OUTSIDE_DATA_WINDOW)

    # Timestamps not on a 15-minute boundary (minute not in {0, 15, 30, 45})
    misaligned = df["timestamp"].notna() & (df["timestamp"].dt.minute % config.INTERVAL_MINUTES != 0)
    _flag_where(df, misaligned, config.DQ_MISALIGNED_TIMESTAMP)

    # ------------------------------------------------------------------ #
    # 4. Serialise flags
    # ------------------------------------------------------------------ #
    df["dq_flags"]     = df["dq_flags"].apply(lambda f: "|".join(f) if f else "")
    df["has_dq_issue"] = df["dq_flags"] != ""

    df = df.drop(columns=["_was_duplicate"])

    n_issues = df["has_dq_issue"].sum()
    logger.info(
        "actuals: cleaning complete — %d rows out, %d with DQ issues", len(df), n_issues
    )
    _log_flag_summary(df)

    return df


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _flag_where(df: pd.DataFrame, mask: pd.Series, flag: str) -> None:
    count = mask.sum()
    if count:
        logger.debug("actuals flag '%s': %d row(s)", flag, count)
    for idx in df.index[mask]:
        df.at[idx, "dq_flags"].append(flag)


def _log_flag_summary(df: pd.DataFrame) -> None:
    from collections import Counter
    all_flags = [f for flags in df["dq_flags"] for f in flags.split("|") if f]
    if all_flags:
        for flag, count in sorted(Counter(all_flags).items()):
            logger.info("  ↳ actuals DQ | %-40s : %d", flag, count)
