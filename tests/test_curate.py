"""
Tests for curate.py — analytics table build and DQ logic.
"""
import pandas as pd
import pytest
from src.curate import build_analytics
from src import config


def _object(object_id="OBJ_1001", object_type="solar", status="active",
            active_from="2026-01-01", active_to=None, client_id="CL_01",
            area="LV", capacity=5.0):
    return {
        "object_id": object_id, "client_id": client_id,
        "object_type": object_type, "area": area,
        "active_from": pd.Timestamp(active_from),
        "active_to": pd.Timestamp(active_to) if active_to else pd.NaT,
        "installed_capacity_mw": capacity, "status": status,
        "dq_flags": "", "has_dq_issue": False,
    }


def _actual(ts="2026-04-01 08:00:00", oid="OBJ_1001", val=3.0):
    return {"timestamp": pd.Timestamp(ts), "object_id": oid, "actual_mwh": val,
            "dq_flags": "", "has_dq_issue": False}


def _forecast(ts="2026-04-01 08:00:00", oid="OBJ_1001", val=3.5):
    return {"timestamp": pd.Timestamp(ts), "object_id": oid, "forecast_mwh": val,
            "dq_flags": "", "has_dq_issue": False}


def make_objects(*rows): return pd.DataFrame(list(rows))
def make_actuals(*rows): return pd.DataFrame(list(rows))
def make_forecasts(*rows): return pd.DataFrame(list(rows))


# ---------------------------------------------------------------------------

def test_abs_error_computed_correctly():
    act = make_actuals(_actual(val=3.0))
    fct = make_forecasts(_forecast(val=3.5))
    obj = make_objects(_object())
    result = build_analytics(act, fct, obj)
    assert abs(result.iloc[0]["abs_error"] - 0.5) < 1e-9


def test_abs_error_null_when_actual_missing():
    act = make_actuals()  # empty
    fct = make_forecasts(_forecast())
    obj = make_objects(_object())
    result = build_analytics(act, fct, obj)
    assert pd.isna(result.iloc[0]["abs_error"])


def test_invalid_when_object_metadata_missing():
    act = make_actuals(_actual(oid="OBJ_9999"))
    fct = make_forecasts()
    obj = make_objects(_object())  # OBJ_1001 only
    result = build_analytics(act, fct, obj)
    row = result[result["object_id"] == "OBJ_9999"]
    assert not row.empty
    assert not row.iloc[0]["is_valid_record"]
    assert config.DQ_MISSING_OBJECT_METADATA in row.iloc[0]["data_quality_issue"]


def test_invalid_when_negative_actual_non_bess():
    act = make_actuals(_actual(val=-1.0))
    fct = make_forecasts()
    obj = make_objects(_object(object_type="solar"))
    result = build_analytics(act, fct, obj)
    assert not result.iloc[0]["is_valid_record"]
    assert config.DQ_NEGATIVE_ACTUAL in result.iloc[0]["data_quality_issue"]


def test_valid_when_negative_actual_bess():
    act = make_actuals(_actual(oid="OBJ_1005", val=-1.0))
    fct = make_forecasts()
    obj = make_objects(_object(object_id="OBJ_1005", object_type="bess"))
    result = build_analytics(act, fct, obj)
    assert config.DQ_NEGATIVE_ACTUAL not in result.iloc[0]["data_quality_issue"]


def test_invalid_when_object_not_yet_active():
    act = make_actuals(_actual(ts="2026-01-01 00:00:00"))
    fct = make_forecasts()
    obj = make_objects(_object(active_from="2026-04-01"))
    result = build_analytics(act, fct, obj)
    assert not result.iloc[0]["is_valid_record"]
    assert config.DQ_OBJECT_NOT_YET_ACTIVE in result.iloc[0]["data_quality_issue"]


def test_invalid_when_inactive_object():
    act = make_actuals(_actual())
    fct = make_forecasts()
    obj = make_objects(_object(status="inactive"))
    result = build_analytics(act, fct, obj)
    assert not result.iloc[0]["is_valid_record"]
    assert config.DQ_INACTIVE_OBJECT in result.iloc[0]["data_quality_issue"]


def test_valid_record_with_no_issues():
    act = make_actuals(_actual(val=2.0))
    fct = make_forecasts(_forecast(val=2.5))
    obj = make_objects(_object())
    result = build_analytics(act, fct, obj)
    assert result.iloc[0]["is_valid_record"]
    # no_actual / no_forecast flags should not be present
    issue = result.iloc[0]["data_quality_issue"]
    assert config.DQ_NO_ACTUAL not in issue
    assert config.DQ_NO_FORECAST not in issue


def test_output_sorted_by_timestamp_and_object_id():
    act = make_actuals(
        _actual(ts="2026-04-02 00:00:00", oid="OBJ_1002"),
        _actual(ts="2026-04-01 00:00:00", oid="OBJ_1001"),
    )
    fct = make_forecasts()
    obj = make_objects(_object(object_id="OBJ_1001"), _object(object_id="OBJ_1002"))
    result = build_analytics(act, fct, obj)
    assert result.iloc[0]["timestamp"] <= result.iloc[1]["timestamp"]


def test_invalid_when_object_past_active_to():
    """Record exists after the object's decommissioning date — should be invalid."""
    act = make_actuals(_actual(ts="2026-04-02 00:00:00"))
    fct = make_forecasts()
    # active_to = Apr 1 but record is Apr 2
    obj = make_objects(_object(active_from="2026-01-01", active_to="2026-04-01"))
    result = build_analytics(act, fct, obj)
    assert not result.iloc[0]["is_valid_record"]
    assert config.DQ_INACTIVE_OBJECT in result.iloc[0]["data_quality_issue"]
