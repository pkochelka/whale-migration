"""Fetch-stage cache: a piece on disk is either complete or absent, so re-runs fetch only what is missing.

Files are written to a temporary name and renamed into place, so an interrupted run never leaves a
truncated file that a later run would mistake for a finished one.
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)


def _tmp(path: Path) -> Path:
    return path.with_name(f"{path.name}.{os.getpid()}.tmp")


def write_parquet(df: pd.DataFrame, path: Path) -> None:
    tmp = _tmp(path)
    df.to_parquet(tmp)
    os.replace(tmp, path)


def write_text(path: Path, text: str) -> None:
    tmp = _tmp(path)
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def parquet_done(path: Path) -> bool:
    """True if `path` exists and is a readable parquet file (runs before atomic writes may not be)."""
    if not path.exists():
        return False
    try:
        import pyarrow.parquet as pq
        pq.read_metadata(path)
        return True
    except Exception as e:
        log.warning("%s is unreadable (%s), probably from an interrupted run; fetching it again", path, e)
        return False


def fresh(path: Path, max_age_s: float) -> bool:
    """True if `path` exists and was written less than `max_age_s` seconds ago."""
    return path.exists() and time.time() - path.stat().st_mtime < max_age_s
