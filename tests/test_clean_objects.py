"""
Tests for clean/objects.py
"""
import pandas as pd
import pytest
from src.clean.objects import clean_objects
from src import config


def _base_row(**kwargs) -> dict:
    defaults = dict(
        object_id="OBJ_1001",
        client_id="CL_01",
        object_type="solar",
        area="LV",
        active_from="2026-01-01",
        active_to=pd.NA,
        installed_capacity_mw=4.5,
        status="active",
    )
    defaults.update(kwargs)
    return defaults


def make_df(*rows) -> pd.DataFrame:
    return pd.DataFrame(list(rows))


# ---------------------------------------------------------------------------

def test_clean_removes_fully_duplicate_rows():
    row = _base_row()
    df = make_df(row, row)
    result = clean_objects(df)
    assert len(result) == 1


def test_flags_duplicate_object_id():
    r1 = _base_row(object_id="OBJ_1002", client_id="CL_01")
    r2 = _base_row(object_id="OBJ_1002", client_id="CL_02")  # different client
    df = make_df(r1, r2)
    result = clean_objects(df)
    assert len(result) == 1  # deduped
    assert config.DQ_DUPLICATE_OBJECT in result.iloc[0]["dq_flags"]


def test_flags_missing_client_id():
    df = make_df(_base_row(client_id=pd.NA))
    result = clean_objects(df)
    assert config.DQ_MISSING_CLIENT_ID in result.iloc[0]["dq_flags"]


def test_flags_missing_area():
    df = make_df(_base_row(area=pd.NA))
    result = clean_objects(df)
    assert config.DQ_MISSING_AREA in result.iloc[0]["dq_flags"]


def test_flags_negative_capacity():
    df = make_df(_base_row(installed_capacity_mw=-1.5))
    result = clean_objects(df)
    assert config.DQ_NEGATIVE_CAPACITY in result.iloc[0]["dq_flags"]


def test_flags_suspicious_capacity():
    df = make_df(_base_row(installed_capacity_mw=9999.0))
    result = clean_objects(df)
    assert config.DQ_SUSPICIOUS_CAPACITY in result.iloc[0]["dq_flags"]


def test_flags_inactive_status():
    df = make_df(_base_row(status="inactive"))
    result = clean_objects(df)
    assert config.DQ_INACTIVE_OBJECT in result.iloc[0]["dq_flags"]


def test_flags_invalid_object_id_format():
    df = make_df(_base_row(object_id="BADFORMAT"))
    result = clean_objects(df)
    assert config.DQ_INVALID_OBJECT_ID_FORMAT in result.iloc[0]["dq_flags"]


def test_valid_row_has_no_flags():
    df = make_df(_base_row())
    result = clean_objects(df)
    assert result.iloc[0]["dq_flags"] == ""
    assert result.iloc[0]["has_dq_issue"] == False


def test_object_type_normalised_to_lowercase():
    df = make_df(_base_row(object_type="Solar"))
    result = clean_objects(df)
    assert result.iloc[0]["object_type"] == "solar"


def test_area_normalised_to_uppercase():
    df = make_df(_base_row(area="lv"))
    result = clean_objects(df)
    assert result.iloc[0]["area"] == "LV"
