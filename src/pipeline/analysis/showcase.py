"""Aggregates for the visualization showcase (input: data/processed; output: showcase/results/showcase.json).

One small table per figure, so the figures in showcase/figures/ can be redrawn without the dataset:

  migration      humpback records per 2-degree latitude band and month, four populations
  monthly_map    North Pacific humpback records per 2-degree cell and month
  connectivity   photo-identified humpbacks seen in two regions (individuals per region pair)
  life_histories every sighting of the 40 humpbacks followed for the most years
  tracks         daily positions of the three tagged humpbacks that travelled furthest north-south
  whaling        sailing-era vessel-days and sperm-whale encounter days per 5-degree cell and era
  record_types   records per decade and kind of record (catch, sighting, acoustic, photo-ID, tag)
  niche          sea temperature and chlorophyll at sightings, per species (histograms)
  ship_strike    North Atlantic right whale records and ship density per 0.5-degree cell
  mpa            share of sightings inside marine protected areas, per species
  provenance     source combinations of merged records + cleaning losses per source
  coast          distance-to-coast distribution per species (empirical CDF)
  land           simplified Natural Earth coastline for map backgrounds (+ a detailed one for ship_strike)

Shared record filter (`VISUAL`): sightings and photo-ID encounters, no acoustic detections (one
hydrophone array off Kauai is 62% of all fin whale records) and positions known to <= 60 km.
Samples are deterministic (ordered by a hash of record_id), so re-runs give the same figures.
"""
from __future__ import annotations

import json
import logging

import duckdb
import geopandas as gpd
import numpy as np
import pandas as pd
import xarray as xr

from ..geo import natural_earth_path

log = logging.getLogger(__name__)

VISUAL = """event_type IN ('sighting', 'encounter')
    AND coalesce(quality_flags, '') NOT LIKE '%acoustic%'
    AND coalesce(coord_uncertainty_m, 0) <= 60000"""
SAMPLE_PER_SPECIES = 20_000

# Humpback populations for the latitude-by-month view: (name, lat0, lat1, lon0, lon1).
POPULATIONS = [
    ("North Pacific", 0, 75, -180, -100),
    ("North Atlantic", 0, 80, -82, 0),
    ("South-east Pacific & Antarctic Peninsula", -75, 12, -95, -50),
    ("East Australia", -75, 0, 140, 170),
]

# Humpback feeding and breeding regions for the connectivity map: (name, lat0, lat1, lon0, lon1).
# First match wins. Boxes follow the populations named in the photo-ID literature.
REGIONS = [
    ("Hawaii", 15, 26, -162, -150),
    ("SE Alaska & BC", 47, 61, -142, -122),
    ("Gulf of Alaska & Bering", 50, 66, -180, -142),
    ("US West Coast", 32, 47, -130, -115),
    ("Mexico", 15, 32, -118, -100),
    ("Central America", 5, 15, -100, -80),
    ("Asia", 10, 60, 120, 180),
    ("West Indies", 10, 23, -75, -58),
    ("Gulf of Maine & E Canada", 38, 52, -72, -50),
    ("Greenland & Iceland", 55, 72, -60, -10),
    ("Norway", 62, 80, 0, 40),
    ("Colombia & Ecuador", -5, 12, -85, -75),
    ("Chile & Peru", -50, -5, -85, -70),
    ("Antarctic Peninsula", -72, -55, -80, -50),
    ("Brazil", -25, -5, -45, -30),
    ("Southern Africa", -36, -10, 10, 50),
    ("West Australia", -36, -15, 110, 125),
    ("East Australia", -40, -10, 145, 160),
    ("Oceania", -25, -10, -180, -160),
    ("East Antarctica", -72, -55, 60, 180),
]
MIN_SHARED = 10            # a region pair is drawn if at least this many whales were seen in both

WHALING_ERAS = [(1780, 1829), (1830, 1849), (1850, 1869), (1870, 1920)]
WHALING_CELL = 5           # degrees
NARW_BOX = (27, 47, -81, -60)   # lat0, lat1, lon0, lon1: Florida calving grounds to the Gulf of St Lawrence
SHIP_CELL = 0.5


def _df(con, sql: str) -> pd.DataFrame:
    return con.execute(sql).df()


def _sample(con, columns: str, where: str) -> pd.DataFrame:
    """Up to SAMPLE_PER_SPECIES records per species, same rows on every run."""
    return _df(con, f"""
        SELECT species_common AS sp, {columns} FROM occurrences WHERE {where}
        QUALIFY row_number() OVER (PARTITION BY species_common ORDER BY hash(record_id)) <= {SAMPLE_PER_SPECIES}""")


def box_name(lat: np.ndarray, lon: np.ndarray, boxes: list[tuple]) -> np.ndarray:
    out = np.full(len(lat), None, dtype=object)
    for name, a, b, c, d in reversed(boxes):  # reversed so the first matching box wins
        out[(lat >= a) & (lat < b) & (lon >= c) & (lon < d)] = name
    return out


# -- 1 seasonal migration ----------------------------------------------------------------------
def migration(con) -> dict:
    d = _df(con, f"""
        SELECT lat, lon, month(timestamp_utc) AS month FROM occurrences
        WHERE species_common = 'Humpback whale' AND {VISUAL}
          AND date_precision IN ('day', 'month') AND year(timestamp_utc) BETWEEN 1990 AND 2025""")
    d["pop"] = box_name(d.lat.to_numpy(), d.lon.to_numpy(), POPULATIONS)
    d["band"] = (np.floor(d.lat / 2) * 2).astype(int)
    out = []
    for name, *_ in POPULATIONS:
        g = d[d["pop"] == name]
        counts = g.groupby(["band", "month"]).size().rename("n").reset_index()
        out.append({"population": name, "records": int(len(g)),
                    "cells": counts.values.tolist()})          # [band (lower edge), month, n]
    return {"band_deg": 2, "populations": out}


def monthly_map(con) -> dict:
    d = _df(con, f"""
        SELECT floor(lat / 2) * 2 AS la, floor(lon / 2) * 2 AS lo, month(timestamp_utc) AS month, count(*) AS n
        FROM occurrences
        WHERE species_common = 'Humpback whale' AND {VISUAL}
          AND date_precision IN ('day', 'month') AND year(timestamp_utc) BETWEEN 1990 AND 2025
          AND lat BETWEEN 0 AND 70 AND lon BETWEEN -180 AND -100
        GROUP BY ALL""")
    return {"cell_deg": 2, "extent": [-180, -100, 0, 70],
            "cells": d.astype(int).values.tolist()}            # [lat0, lon0, month, n]


# -- 2-3 individuals: connectivity and life histories ------------------------------------------
def _photo_id(con) -> pd.DataFrame:
    d = _df(con, f"""
        SELECT individual_id AS ind, timestamp_utc AS ts, lat, lon FROM occurrences
        WHERE species_common = 'Humpback whale' AND event_type = 'encounter' AND individual_id <> ''
          AND {VISUAL}""")
    d["region"] = box_name(d.lat.to_numpy(), d.lon.to_numpy(), REGIONS)
    return d


def connectivity(d: pd.DataFrame) -> dict:
    r = d.dropna(subset=["region"])
    per_ind = r.groupby("ind")["region"].agg(lambda s: sorted(set(s)))
    multi = per_ind[per_ind.str.len() > 1]
    pairs = pd.Series([(a, b) for regs in multi for i, a in enumerate(regs) for b in regs[i + 1:]]).value_counts()
    centre = r.groupby("region").agg(lat=("lat", "median"), lon=("lon", "median"),
                                     whales=("ind", "nunique"), records=("ind", "size"))
    return {
        "min_shared": MIN_SHARED,
        "whales_with_region": int(per_ind.size), "whales_multi_region": int(multi.size),
        "regions": [{"name": k, "lat": round(v.lat, 2), "lon": round(v.lon, 2), "whales": int(v.whales),
                     "records": int(v.records)} for k, v in centre.iterrows()],
        "links": [[a, b, int(n)] for (a, b), n in pairs.items() if n >= MIN_SHARED],
        "links_below_min": int((pairs < MIN_SHARED).sum()),
    }


def life_histories(d: pd.DataFrame, n: int = 40, feeding_lat: float = 30.0) -> dict:
    """The n whales seen in the most distinct years; zone = feeding (|lat| >= feeding_lat) or breeding."""
    d = d.assign(year=d.ts.dt.year)
    per = d.groupby("ind").agg(years=("year", "nunique"), first=("year", "min"), last=("year", "max"),
                               sightings=("year", "size"))
    top = per.sort_values(["years", "sightings"], ascending=False).head(n)
    whales = []
    for ind, row in top.iterrows():
        g = d[d.ind == ind].sort_values("ts")
        t = g.ts.dt.year + (g.ts.dt.dayofyear - 1) / 365.25
        whales.append({"id": ind.split(":")[1], "years": int(row.years), "first": int(row["first"]),
                       "last": int(row["last"]),
                       "sightings": [[round(float(a), 3), int(abs(la) >= feeding_lat)] for a, la in zip(t, g.lat)]})
    return {"feeding_lat": feeding_lat, "whales": whales,
            "followed_10y": int((per["last"] - per["first"] >= 10).sum()), "photo_id_whales": int(len(per))}


# -- 4 tag tracks --------------------------------------------------------------------------------
def tracks(con, n: int = 3) -> dict:
    d = _df(con, """
        SELECT coalesce(nullif(individual_id, ''), regexp_extract(platform, 'device id= ?(\\w+)', 1)) AS tid,
               timestamp_utc AS ts, lat, lon, any_value(left(citation, 200)) OVER (PARTITION BY dataset_id) AS cit
        FROM occurrences WHERE event_type = 'tag_fix' AND species_common = 'Humpback whale'""")
    d = d[d.tid != ""]
    span = d.groupby("tid").lat.agg(lambda s: s.max() - s.min()).sort_values(ascending=False)
    out = []
    for tid in span.index[:n]:
        g = d[d.tid == tid].sort_values("ts")
        day = (g.ts - g.ts.iloc[0]).dt.total_seconds() / 86400
        daily = g.assign(day=day.astype(int)).groupby("day")[["lat", "lon"]].median().reset_index()
        out.append({"tag": tid, "start": str(g.ts.iloc[0].date()), "days": int(day.iloc[-1]), "fixes": int(len(g)),
                    "lat_span": round(float(span[tid]), 1), "citation": g.cit.iloc[0],
                    "track": daily.round({"lat": 2, "lon": 2}).values.tolist()})   # [day, lat, lon]
    return {"tracks": out, "tagged_humpbacks": int(d.tid.nunique())}


# -- 5-6 whaling and the history of records ------------------------------------------------------
def whaling(con) -> dict:
    c = WHALING_CELL
    d = _df(con, f"""
        SELECT year(timestamp_utc) AS year, floor(lat / {c}) * {c} AS la, floor(lon / {c}) * {c} AS lo,
               encounter IN ('Sight', 'Strike') AND species_raw = 'Sperm' AS sperm
        FROM effort_logbook WHERE NOT coalesce(track_outlier, false)""")
    eras = []
    for y0, y1 in WHALING_ERAS:
        g = d[d.year.between(y0, y1)]
        cells = g.groupby(["la", "lo"]).agg(days=("sperm", "size"), sperm=("sperm", "sum")).reset_index()
        eras.append({"from": y0, "to": y1, "vessel_days": int(len(g)), "sperm_days": int(g.sperm.sum()),
                     "cells": cells.astype(int).values.tolist()})   # [lat0, lon0, vessel-days, sperm days]
    return {"cell_deg": c, "eras": eras}


def record_types(con) -> dict:
    d = _df(con, """
        SELECT year(timestamp_utc) // 10 * 10 AS decade,
               CASE WHEN coalesce(quality_flags, '') LIKE '%acoustic%' THEN 'acoustic' ELSE event_type END AS kind,
               count(*) AS n
        FROM occurrences GROUP BY ALL""")
    t = d.pivot_table(index="decade", columns="kind", values="n", fill_value=0).astype(int)
    return {"kinds": list(t.columns), "rows": [[int(dec), *map(int, row)] for dec, row in t.iterrows()]}


# -- 7 environmental niche -------------------------------------------------------------------------
def niche(cfg, con) -> dict:
    d = _sample(con, "lat, lon, date_trunc('month', timestamp_utc) AS month",
                f"{VISUAL} AND date_precision IN ('day', 'month') AND year(timestamp_utc) BETWEEN 1998 AND 2025")
    t = xr.DataArray(pd.to_datetime(d.month).dt.tz_localize(None).values, dims="p")
    at = dict(lat=xr.DataArray(d.lat.values, dims="p"), lon=xr.DataArray(d.lon.values, dims="p"), time=t)
    env = cfg.processed / "env"
    d["sst"] = xr.open_dataset(env / "sst_monthly.nc")["sst"].sel(**at, method="nearest").values
    d["chl"] = xr.open_dataset(env / "chlorophyll_monthly.nc")["chlor_a"].sel(**at, method="nearest").values
    sst_bins = np.arange(-2, 32.5, 0.5)
    chl_bins = np.arange(-2, 1.61, 0.1)          # log10 mg/m3
    out = []
    for sp, g in d.groupby("sp"):
        s = g.sst.dropna()
        both = g.dropna(subset=["sst", "chl"])
        h2, _, _ = np.histogram2d(both.sst, np.log10(both.chl.clip(lower=0.01)), bins=[sst_bins[::2], chl_bins])
        out.append({"species": sp, "n": int(len(g)), "n_sst": int(len(s)), "n_both": int(len(both)),
                    "sst_hist": np.histogram(s, bins=sst_bins)[0].tolist(),
                    "sst_quantiles": [round(float(q), 1) for q in s.quantile([0.1, 0.5, 0.9])],
                    "joint_hist": h2.astype(int).tolist()})
    return {"sst_bins": sst_bins.tolist(), "joint_sst_bins": sst_bins[::2].tolist(),
            "joint_log_chl_bins": chl_bins.round(1).tolist(), "species": out}


# -- 8-9 human pressure ------------------------------------------------------------------------------
def ship_strike(cfg, con) -> dict:
    la0, la1, lo0, lo1 = NARW_BOX
    c = SHIP_CELL
    w = _df(con, f"""
        SELECT floor(lat / {c}) * {c} AS la, floor(lon / {c}) * {c} AS lo, count(*) AS n FROM occurrences
        WHERE species_common = 'North Atlantic right whale' AND {VISUAL} AND year(timestamp_utc) >= 2000
          AND lat BETWEEN {la0} AND {la1} AND lon BETWEEN {lo0} AND {lo1}
        GROUP BY ALL""")
    ship = xr.open_dataset(cfg.processed / "env" / "ship_density.nc")["ship_density"]
    ship = ship.sel(lat=slice(la0, la1), lon=slice(lo0, lo1))
    cells = ship.coarsen(lat=int(round(c / 0.1)), lon=int(round(c / 0.1)), boundary="trim").mean()
    s = cells.to_dataframe().reset_index().dropna()
    s["la"] = (np.floor((s.lat + 1e-6) / c) * c).round(2)
    s["lo"] = (np.floor((s.lon + 1e-6) / c) * c).round(2)
    m = s[["la", "lo", "ship_density"]].merge(w, on=["la", "lo"], how="left").fillna({"n": 0})
    m = m[m.ship_density > 0]  # land and no-traffic cells carry no information here
    total = int(w.n.sum())
    return {"cell_deg": c, "box": NARW_BOX, "whale_records": total,
            "records_outside_ship_cells": total - int(m.n.sum()),
            "cells": [[r.la, r.lo, round(float(r.ship_density), 1), int(r.n)] for r in m.itertuples()]}


def mpa(cfg, con) -> dict:
    d = _sample(con, "lat, lon", f"{VISUAL} AND year(timestamp_utc) >= 2000")
    areas = gpd.read_parquet(cfg.processed / "env" / "marine_protected_areas.parquet")
    pts = gpd.GeoDataFrame(d, geometry=gpd.points_from_xy(d.lon, d.lat), crs=4326).to_crs(areas.crs)
    hit = gpd.sjoin(pts, areas[["NO_TAKE", "geometry"]], predicate="within", how="left")
    # Overlapping designations: a point counts as no-take if any area containing it is fully no-take.
    status = hit.groupby(level=0).NO_TAKE.agg(
        lambda s: "no_take" if (s == "All").any() else ("other" if s.notna().any() else "outside"))
    d["status"] = status
    out = [{"species": sp, "n": int(len(g)), **{k: round(float((g.status == k).mean()), 4)
                                                for k in ("no_take", "other", "outside")}}
           for sp, g in d.groupby("sp")]
    return {"areas": int(len(areas)), "species": sorted(out, key=lambda r: -(r["no_take"] + r["other"]))}


# -- 10-11 provenance and coast ------------------------------------------------------------------------
def provenance(cfg, con) -> dict:
    combos = _df(con, "SELECT merged_sources AS s, count(*) AS n FROM occurrences GROUP BY 1 ORDER BY n DESC")
    reports = {}
    for p in sorted((cfg.interim / "reports").glob("*.json")):
        r = json.loads(p.read_text(encoding="utf-8"))
        reports[r["source"]] = {"rows_in": r["rows_in"], "rows_out": r["rows_out"], "dropped": r["dropped"]}
    totals = json.loads((cfg.processed / "summary_totals.json").read_text(encoding="utf-8"))
    return {"combinations": combos.values.tolist(), "cleaning": reports, "unify": totals}


def coast(con) -> dict:
    d = _df(con, f"""
        SELECT species_common AS sp, dist_coast_km AS km FROM occurrences
        WHERE {VISUAL} AND year(timestamp_utc) >= 1990 AND dist_coast_km IS NOT NULL""")
    grid = np.logspace(-1, 3.5, 91)
    out = []
    for sp, g in d.groupby("sp"):
        km = np.sort(g.km.to_numpy())
        out.append({"species": sp, "n": int(len(km)), "median": round(float(np.median(km)), 1),
                    "cdf": (np.searchsorted(km, grid, side="right") / len(km)).round(4).tolist()})
    return {"grid_km": grid.round(3).tolist(), "species": out}


def land(cfg, tolerance: float = 0.1, min_area: float = 0.05, scale: str = "50m", box=None) -> list:
    """Coastline rings, simplified (map backgrounds only). `box` = (lat0, lat1, lon0, lon1) clips to a region."""
    g = gpd.read_file(f"zip://{natural_earth_path(cfg.raw, scale=scale)}").to_crs(4326).geometry
    if box:
        la0, la1, lo0, lo1 = box
        g = g.clip((lo0 - 1, la0 - 1, lo1 + 1, la1 + 1))
    g = g.simplify(tolerance)
    return [[[round(x, 3), round(y, 3)] for x, y in p.exterior.coords]
            for geom in g if not geom.is_empty for p in getattr(geom, "geoms", [geom]) if p.area >= min_area]


def build(cfg) -> dict:
    con = duckdb.connect(str(cfg.processed / "whales.duckdb"), read_only=True)
    photo = _photo_id(con)
    res = {}
    for name, fn in [("migration", lambda: migration(con)), ("monthly_map", lambda: monthly_map(con)),
                     ("connectivity", lambda: connectivity(photo)), ("life_histories", lambda: life_histories(photo)),
                     ("tracks", lambda: tracks(con)), ("whaling", lambda: whaling(con)),
                     ("record_types", lambda: record_types(con)), ("niche", lambda: niche(cfg, con)),
                     ("ship_strike", lambda: ship_strike(cfg, con)), ("mpa", lambda: mpa(cfg, con)),
                     ("provenance", lambda: provenance(cfg, con)), ("coast", lambda: coast(con)),
                     ("land", lambda: land(cfg)),
                     ("land_narw", lambda: land(cfg, 0.02, 0.002, "10m", NARW_BOX))]:
        log.info("showcase: %s", name)
        res[name] = fn()
    con.close()
    return res


def run(cfg) -> None:
    out = cfg.root / "showcase" / "results"
    out.mkdir(parents=True, exist_ok=True)
    res = build(cfg)
    (out / "showcase.json").write_text(json.dumps(res, ensure_ascii=False, separators=(",", ":"), default=str),
                                       encoding="utf-8")
    log.info("wrote %s", out / "showcase.json")
