"""
main.py
-------
Pipeline orchestrator. Run this file to execute the full pipeline:

    python main.py

Stages:
  1. Ingest raw CSVs
  2. Clean each table
  3. Save cleaned outputs
  4. Build curated analytics table
  5. Save curated output + DQ report
"""

import sys
from pathlib import Path

import pandas as pd

from src import config
from src.logger import get_logger
from src.ingest import load_csv
from src.clean.objects import clean_objects
from src.clean.actuals import clean_actuals
from src.clean.forecasts import clean_forecasts
from src.curate import build_analytics
from src.quality import run_all_checks

logger = get_logger("aox.main")


def run_pipeline() -> None:
    logger.info("=" * 60)
    logger.info("AOX Trade Data Pipeline — START")
    logger.info("=" * 60)

    # Ensure output directories exist
    config.CLEANED_DIR.mkdir(parents=True, exist_ok=True)
    config.CURATED_DIR.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Stage 1: Ingest
    # ------------------------------------------------------------------
    logger.info("[1/4] Ingesting raw data")
    raw_objects   = load_csv(config.RAW_OBJECTS,   "objects")
    raw_actuals   = load_csv(config.RAW_ACTUALS,   "actuals")
    raw_forecasts = load_csv(config.RAW_FORECASTS, "forecasts")

    # ------------------------------------------------------------------
    # Stage 2: Clean
    # ------------------------------------------------------------------
    logger.info("[2/4] Cleaning tables")
    clean_obj  = clean_objects(raw_objects)
    clean_act  = clean_actuals(raw_actuals)
    clean_fct  = clean_forecasts(raw_forecasts)

    # ------------------------------------------------------------------
    # Stage 3: Save cleaned outputs
    # ------------------------------------------------------------------
    logger.info("[3/4] Saving cleaned outputs")
    _save(clean_obj,  config.CLEANED_OBJECTS,   "objects_cleaned")
    _save(clean_act,  config.CLEANED_ACTUALS,   "actuals_cleaned")
    _save(clean_fct,  config.CLEANED_FORECASTS, "forecasts_cleaned")

    # ------------------------------------------------------------------
    # Stage 4: Curate
    # ------------------------------------------------------------------
    logger.info("[4/4] Building analytics table")
    analytics = build_analytics(clean_act, clean_fct, clean_obj)
    # Run standalone cross-table DQ checks (supplementary report)
    dq_cross = run_all_checks(clean_act, clean_fct, clean_obj)
    _save(dq_cross, config.CURATED_DIR / "dq_cross_table.csv", "dq_cross_table")

    _save(analytics, config.CURATED_ANALYTICS, "analytics")

    # Save standalone DQ report (only rows with issues, for easy review)
    dq_report = analytics[analytics["data_quality_issue"] != ""].copy()
    _save(dq_report, config.DQ_REPORT, "dq_report")

    logger.info("=" * 60)
    logger.info("Pipeline complete. Outputs written to: %s", config.BASE_DIR / "data" / "output")
    logger.info("Full log: %s", config.BASE_DIR / "pipeline.log")
    logger.info("=" * 60)


def _save(df: pd.DataFrame, path: Path, label: str) -> None:
    df.to_csv(path, index=False)
    logger.info("Saved %s → %s (%d rows)", label, path.relative_to(config.BASE_DIR), len(df))


if __name__ == "__main__":
    try:
        run_pipeline()
    except Exception as exc:
        logger.exception("Pipeline failed: %s", exc)
        sys.exit(1)
