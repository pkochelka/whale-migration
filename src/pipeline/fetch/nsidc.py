"""NSIDC sea ice: monthly concentration CDR (G02202 v6) + Sea Ice Index monthly extent (G02135)."""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def fetch(cfg, http, force: bool = False, name: str = "sea_ice") -> None:
    sc = cfg.source("sea_ice")
    d = cfg.raw_dir("nsidc")
    meta = {"license": sc.get("license"), "citation": sc.get("citation", "").strip()}
    for hemi, rel in sc["concentration_files"].items():
        http.download(f"{sc['base']}/{rel}", d / rel.split("/")[-1], force=force, meta={**meta, "hemisphere": hemi})
    for hemi, H in (("north", "N"), ("south", "S")):
        for m in range(1, 13):
            rel = sc["extent_dir"].format(hemi=hemi, H=H, mm=f"{m:02d}")
            http.download(f"{sc['base']}/{rel}", d / "extent" / rel.split("/")[-1], force=force, meta=meta)
