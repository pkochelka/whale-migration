"""Are whale sightings shifting poleward? Latitude trends per species and hemisphere.

Presence-only data mostly record where observers were (new whale-watch hubs, survey programmes,
the growth of Happywhale), so a raw "mean latitude per year" trend is an effort trend. This module
therefore reports the same trend under increasingly strict sampling control (a *sensitivity
ladder*), so the reader can see whether a shift survives:

  raw                   median |latitude| of all records per year, 1985-2025
  footprint (y0, share) only 1-degree cells where the species was recorded in >= `share` of the
                        years y0..2025. Each occupied cell counts once per year, so effort intensity
                        inside a cell is removed; the statistic is the mean |latitude| of the
                        occupied cells. The stricter the footprint, the closer it is to a balanced
                        panel, where a trend can only come from cells being newly occupied/vacated.

Trend: Theil-Sen slope with 95% CI, in km/decade (1 deg = 111.2 km), positive = poleward.
SST context: HadISST annual mean over the strict footprint's cells (degC/decade), reported
separately, never on a shared axis.

Records: sightings, photo-ID encounters, catches. Acoustic detections and tag fixes are excluded
(a single hydrophone array or tagged animal would dominate a year), as are positions with stated
uncertainty > 60 km.
"""
from __future__ import annotations

import json
import logging

import duckdb
import numpy as np
import pandas as pd
import xarray as xr
from scipy.stats import theilslopes

log = logging.getLogger(__name__)
KM_PER_DEG = 111.2
Y1 = 2025
LADDER = [  # (label, first year, minimum share of years a cell must be sampled)
    ("Footprint: cells seen in ≥25% of years, 1985–2025", 1985, 0.25),
    ("≥50% of years, 1995–2025", 1995, 0.50),
    ("≥60% of years, 2000–2025", 2000, 0.60),
    ("≥75% of years, 2000–2025", 2000, 0.75),
    ("≥80% of years, 2005–2025", 2005, 0.80),
]
STRICT = 3          # index in LADDER used for the strict time series and SST
MIN_CELLS = 5       # a footprint/year needs this many occupied cells


def theil(years, values, scale=1.0):
    years, values = np.asarray(years, float), np.asarray(values, float)
    ok = ~np.isnan(values)
    if ok.sum() < 10 or np.nanstd(values) == 0 and ok.sum() < 10:
        return None
    s, _, lo, hi = theilslopes(values[ok], years[ok])
    return {"slope": round(float(s * 10 * scale), 1), "lo": round(float(lo * 10 * scale), 1),
            "hi": round(float(hi * 10 * scale), 1), "n_years": int(ok.sum())}


def footprint_series(g: pd.DataFrame, y0: int, share: float):
    years = np.arange(y0, Y1 + 1)
    cy = g[g.year >= y0].drop_duplicates(["clat", "clon", "year"])
    sh = cy.groupby(["clat", "clon"]).year.nunique() / len(years)
    foot = sh[sh >= share].index
    if len(foot) < MIN_CELLS:
        return None, foot, years
    fc = cy.set_index(["clat", "clon"])
    fc = fc[fc.index.isin(foot)].reset_index()
    per = fc.groupby("year").agg(mean=("clat", lambda s: float(np.abs(s).mean())), cells=("clat", "size")).reindex(years)
    per.loc[per["cells"].fillna(0) < MIN_CELLS, "mean"] = np.nan
    return per, foot, years


def build(cfg) -> dict:
    con = duckdb.connect(str(cfg.processed / "whales.duckdb"), read_only=True)
    d = con.execute(f"""
        SELECT species_scientific AS sp, species_common AS common, year(timestamp_utc) AS year,
               lat, floor(lat) + 0.5 AS clat, floor(lon) + 0.5 AS clon
        FROM occurrences
        WHERE event_type IN ('sighting', 'encounter', 'catch')
          AND coalesce(quality_flags, '') NOT LIKE '%acoustic%'
          AND year(timestamp_utc) BETWEEN 1985 AND {Y1}
          AND coalesce(coord_uncertainty_m, 0) <= 60000
    """).df()
    con.close()
    d["hemi"] = np.where(d["lat"] >= 0, "N", "S")
    sst = xr.open_dataset(cfg.processed / "env" / "sst_monthly.nc")["sst"]
    sst_ann = sst.sel(time=slice("1985-01-01", f"{Y1}-12-31")).groupby("time.year").mean()

    panels = []
    for (sp, hemi), g in d.groupby(["sp", "hemi"]):
        years = np.arange(1985, Y1 + 1)
        raw = g.assign(alat=g.lat.abs()).groupby("year").agg(raw=("alat", "median"), n=("alat", "size")).reindex(years)
        ladder = [{"label": "Raw: median latitude of all records, 1985–2025", "cells": None,
                   **(theil(years, raw["raw"], KM_PER_DEG) or {})}]
        strict = None
        for k, (label, y0, share) in enumerate(LADDER):
            per, foot, yrs = footprint_series(g, y0, share)
            t = theil(yrs, per["mean"]) if per is not None else None
            t = {kk: (round(v * KM_PER_DEG, 1) if kk in ("slope", "lo", "hi") else v) for kk, v in t.items()} if t else {}
            ladder.append({"label": label, "cells": int(len(foot)), **t})
            if k == STRICT and per is not None:
                strict = (per, foot, yrs)
        if strict is None:
            continue
        per, foot, yrs = strict
        fl = np.array([c[0] for c in foot]); fo = np.array([c[1] for c in foot])
        s_vals = sst_ann.sel(lat=xr.DataArray(fl, dims="c"), lon=xr.DataArray(fo, dims="c"), method="nearest")
        s_ser = s_vals.mean("c", skipna=True).reindex(year=years).values
        loose, _, _ = footprint_series(g, 1985, 0.25)
        common = g["common"].dropna().iloc[0] if g["common"].notna().any() else sp
        nz = lambda v: None if v is None or (isinstance(v, float) and np.isnan(v)) else round(float(v), 2)  # noqa: E731
        panels.append({
            "species": sp, "common": common, "hemisphere": hemi, "records": int(len(g)),
            "strict_cells": int(len(foot)), "strict_from": int(LADDER[STRICT][1]),
            "ladder": ladder, "sst_trend": theil(yrs, s_ser[-len(yrs):]),
            "series": [{"year": int(y), "n": int(0 if np.isnan(n) else n), "raw": nz(r),
                        "loose": nz(loose["mean"].get(y)) if loose is not None else None,
                        "strict": nz(per["mean"].get(y)), "sst": nz(t)}
                       for y, n, r, t in zip(years, raw["n"], raw["raw"], s_ser)],
        })
        log.info("%s %s: %d records, strict footprint %d cells", sp, hemi, len(g), len(foot))
    panels.sort(key=lambda p: -p["records"])
    return {"ladder": [lab for lab, *_ in LADDER], "strict_index": STRICT, "panels": panels}


def run(cfg) -> None:
    out = cfg.root / "results"
    out.mkdir(parents=True, exist_ok=True)
    res = build(cfg)
    (out / "climate_latitude.json").write_text(json.dumps(res, ensure_ascii=False, separators=(",", ":")),
                                               encoding="utf-8")
    log.info("wrote %s", out / "climate_latitude.json")
