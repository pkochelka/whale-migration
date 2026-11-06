"""Clean stage: raw -> per-source tables in the unified schema (data/interim/)."""
from __future__ import annotations

import importlib
from pathlib import Path

OCCURRENCE_SOURCES = ["whalinghistory", "obis", "gbif", "seamap", "movebank"]
ENVIRONMENT_SOURCES = ["sst", "chlorophyll", "sea_ice", "mpa", "shipping"]
ALL = OCCURRENCE_SOURCES + ENVIRONMENT_SOURCES
MODULES = {"whalinghistory": "whalinghistory", "obis": "obis", "gbif": "gbif", "seamap": "seamap",
           "movebank": "movebank", "sst": "environment", "chlorophyll": "environment",
           "sea_ice": "environment", "mpa": "environment", "shipping": "environment"}


class SourceUnavailable(RuntimeError):
    """Raw data for a source is missing (not fetched / stubbed)."""


def get(name: str):
    return importlib.import_module(f"{__name__}.{MODULES[name]}")


def output_path(cfg, name: str) -> Path:
    if name in OCCURRENCE_SOURCES:
        return cfg.interim / f"{name}.parquet"
    return cfg.processed / "env" / {"sst": "sst_monthly.nc", "chlorophyll": "chlorophyll_monthly.nc",
                                    "sea_ice": "sea_ice_concentration_monthly.nc",
                                    "mpa": "marine_protected_areas.parquet",
                                    "shipping": "ship_density.nc"}[name]
