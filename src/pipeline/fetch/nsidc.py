"""NSIDC sea ice: monthly concentration CDR (G02202 v6) + Sea Ice Index monthly extent (G02135).

The monthly concentration aggregate is renamed by NSIDC every month (its name ends with the latest
month, e.g. sic_psn25_197811-202610_v06r00.nc), so the newest name is read from the folder
listing at fetch time rather than written in config.yaml. When a newer aggregate replaces an
older one, the older local copy is removed.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from ..parallel import pmap

log = logging.getLogger(__name__)

HEMI_LETTER = {"north": "n", "south": "s"}
# Monthly aggregate: <start yyyymm>-<end yyyymm>. Yearly daily files (yyyymmdd-yyyymmdd) do not match.
AGGREGATE = re.compile(r"sic_ps([ns])25_(\d{6})-(\d{6})_v06r00\.nc")


def latest_aggregate(text: str, hemi: str) -> str | None:
    """Newest monthly aggregate for `hemi` ("north"/"south") named anywhere in `text`."""
    found = {m.group(0): m.group(3) for m in AGGREGATE.finditer(text) if m.group(1) == HEMI_LETTER[hemi]}
    return max(found, key=found.get) if found else None


def local_aggregate(d: Path, hemi: str) -> Path | None:
    """The newest downloaded aggregate for `hemi` in the raw folder `d`, if any."""
    name = latest_aggregate(" ".join(p.name for p in d.glob("sic_ps*25_*_v06r00.nc")), hemi)
    return d / name if name else None


def fetch(cfg, http, force: bool = False, name: str = "sea_ice") -> None:
    sc = cfg.source("sea_ice")
    d = cfg.raw_dir("nsidc")
    meta = {"license": sc.get("license"), "citation": sc.get("citation", "").strip()}
    jobs = []
    for hemi, folder in sc["concentration_dirs"].items():
        listing = http.get(f"{sc['base']}/{folder.strip('/')}/").text
        latest = latest_aggregate(listing, hemi)
        if not latest:
            raise RuntimeError(f"no monthly sea-ice aggregate found in {sc['base']}/{folder}")
        jobs.append((f"{folder.strip('/')}/{latest}", d / latest, {**meta, "hemisphere": hemi}))
    for hemi, H in (("north", "N"), ("south", "S")):
        for m in range(1, 13):
            rel = sc["extent_dir"].format(hemi=hemi, H=H, mm=f"{m:02d}")
            jobs.append((rel, d / "extent" / rel.split("/")[-1], meta))
    # Two large concentration files and 24 small CSVs: overlap them instead of queueing.
    pmap(lambda j: http.download(f"{sc['base']}/{j[0]}", j[1], force=force, meta=j[2]), jobs,
         int(sc.get("workers", 1)))
    for _, dest, _ in jobs[:len(sc["concentration_dirs"])]:
        for old in d.glob(dest.name[:10] + "*_v06r00.nc"):  # "sic_psn25_": same hemisphere
            if old != dest and AGGREGATE.fullmatch(old.name):
                log.info("removing superseded %s (replaced by %s)", old.name, dest.name)
                old.unlink()
                old.with_name(old.name + ".manifest.json").unlink(missing_ok=True)
