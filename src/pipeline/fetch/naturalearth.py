"""Natural Earth land polygons (public domain): land mask and coastline distance."""
from __future__ import annotations

from ..geo import NE_URL


def fetch(cfg, http, force: bool = False, name: str = "naturalearth") -> None:
    d = cfg.raw_dir("naturalearth")
    for scale in ("10m", "50m"):
        http.download(NE_URL.format(scale=scale, layer="land"), d / f"ne_{scale}_land.zip", force=force,
                      meta={"license": "Public domain", "citation": "Made with Natural Earth."})
