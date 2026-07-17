"""
logger.py
---------
Centralised logging configuration.
Every module calls get_logger(__name__) to get a consistent logger.
Logs go to both stdout (INFO+) and a file (DEBUG+) so the full trace
is always preserved without polluting the terminal.
"""

import logging
import sys
from pathlib import Path

LOG_FILE = Path(__file__).resolve().parent.parent / "pipeline.log"

_configured = False


def get_logger(name: str) -> logging.Logger:
    """
    Return a named logger. The root pipeline logger is configured once on
    first call; subsequent calls just return child loggers.
    """
    global _configured

    if not _configured:
        _setup_root_logger()
        _configured = True

    return logging.getLogger(name)


def _setup_root_logger() -> None:
    root = logging.getLogger("aox")
    root.setLevel(logging.DEBUG)

    fmt = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console handler — INFO and above
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(fmt)

    # File handler — DEBUG and above (full trace)
    file_handler = logging.FileHandler(LOG_FILE, mode="w", encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(fmt)

    root.addHandler(console)
    root.addHandler(file_handler)
