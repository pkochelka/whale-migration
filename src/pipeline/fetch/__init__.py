"""Fetch stage: download raw data into data/raw/<source>/ (cached, never edited)."""
from __future__ import annotations

import importlib

# name -> module (each exposes `fetch(cfg, http, force=False)`)
OCCURRENCE_SOURCES = ["whalinghistory", "obis", "gbif", "seamap", "movebank"]
ENVIRONMENT_SOURCES = ["naturalearth", "sst", "chlorophyll", "sea_ice", "mpa", "shipping"]
MODULES = {
    "whalinghistory": "whalinghistory", "obis": "obis", "gbif": "gbif", "seamap": "seamap",
    "movebank": "movebank", "naturalearth": "naturalearth", "sst": "erddap",
    "chlorophyll": "erddap", "sea_ice": "nsidc", "mpa": "wdpa", "shipping": "shipping",
}
ALL = OCCURRENCE_SOURCES + ENVIRONMENT_SOURCES


def get(name: str):
    return importlib.import_module(f"{__name__}.{MODULES[name]}")
