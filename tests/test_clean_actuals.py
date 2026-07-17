"""
Tests for clean/actuals.py
"""
import pandas as pd
import pytest
from src.clean.actuals import clean_actuals
from src import config


def _base_row(**kwargs) -> dict:
    defaults = dict(
        timestamp="2026-04-01 08:00:00",
        object_id="OBJ_1001",
        actual_mwh=3.5,
    )
    defaults.update(kwargs)
    return defaults


def make_df(*rows) -> pd.DataFrame:
    return pd.DataFrame(list(rows))


# ---------------------------------------------------------------------------

def test_deduplicates_keeps_last():
    r1 = _base_row(actual_mwh=1.0)
    r2 = _base_row(actual_mwh=2.0)  # same key, higher value = correction
    df = make_df(r1, r2)
    result = clean_actuals(df)
    assert len(result) == 1
    assert result.iloc[0]["actual_mwh"] == 2.0


def test_flags_duplicate_timeseries_key():
    r1 = _base_row(actual_mwh=1.0)
    r2 = _base_row(actual_mwh=2.0)
    df = make_df(r1, r2)
    result = clean_actuals(df)
    assert config.DQ_DUPLICATE_TIMESERIES in result.iloc[0]["dq_flags"]


def test_flags_malformed_timestamp():
    df = make_df(_base_row(timestamp="not-a-date"))
    result = clean_actuals(df)
    assert config.DQ_MALFORMED_TIMESTAMP in result.iloc[0]["dq_flags"]


def test_flags_outside_window():
    df = make_df(_base_row(timestamp="2025-01-01 00:00:00"))
    result = clean_actuals(df)
    assert config.DQ_OUTSIDE_DATA_WINDOW in result.iloc[0]["dq_flags"]


def test_flags_misaligned_timestamp():
    df = make_df(_base_row(timestamp="2026-04-01 08:07:00"))
    result = clean_actuals(df)
    assert config.DQ_MISALIGNED_TIMESTAMP in result.iloc[0]["dq_flags"]


def test_aligned_timestamp_not_flagged():
    for minute in ["00", "15", "30", "45"]:
        df = make_df(_base_row(timestamp=f"2026-04-01 08:{minute}:00"))
        result = clean_actuals(df)
        assert config.DQ_MISALIGNED_TIMESTAMP not in result.iloc[0]["dq_flags"], \
            f"False positive for minute={minute}"


def test_valid_row_has_no_flags():
    df = make_df(_base_row())
    result = clean_actuals(df)
    assert result.iloc[0]["dq_flags"] == ""


def test_invalid_object_id_flagged():
    df = make_df(_base_row(object_id="WRONG_001"))
    result = clean_actuals(df)
    assert config.DQ_INVALID_OBJECT_ID_FORMAT in result.iloc[0]["dq_flags"]
