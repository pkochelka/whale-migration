"""Global shipping traffic density (IMF / World Bank, CC BY 4.0), AIS 2015-2021, ~500 m raster.

EMODnet vessel density was evaluated but only covers European seas; this global raster is used
for ship-strike risk instead.
"""
from __future__ import annotations


def fetch(cfg, http, force: bool = False, name: str = "shipping") -> None:
    sc = cfg.source("shipping")
    d = cfg.raw_dir("shipping")
    http.download(sc["url"], d / "shipdensity_global.zip", force=force,
                  meta={"license": sc["license"], "citation": sc["citation"].strip()})
