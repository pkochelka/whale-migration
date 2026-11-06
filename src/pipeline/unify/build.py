"""Build data/processed/: occurrences.parquet (GeoParquet), effort, duplicates log, DuckDB, summaries."""
from __future__ import annotations

import logging

import duckdb
import geopandas as gpd
import pandas as pd

from .. import schema
from ..clean import OCCURRENCE_SOURCES
from ..geo import distance_to_coast_km, natural_earth_path
from .dedup import deduplicate

log = logging.getLogger(__name__)

EXTRA_COLUMNS = {"merged_sources": "string", "n_duplicates": "int32", "dist_coast_km": "float32"}


def load_interim(cfg) -> pd.DataFrame:
    frames = []
    for s in OCCURRENCE_SOURCES:
        p = cfg.interim / f"{s}.parquet"
        if p.exists():
            f = pd.read_parquet(p)
            log.info("loaded %s: %d", s, len(f))
            frames.append(f)
        else:
            log.warning("no interim table for %s (not fetched/cleaned); skipped", s)
    if not frames:
        raise SystemExit("nothing to unify: run `python -m pipeline clean all` first")
    return schema.conform(pd.concat(frames, ignore_index=True))


def to_geoparquet(df: pd.DataFrame, path) -> None:
    gdf = gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(df["lon"], df["lat"]), crs="EPSG:4326")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    gdf.to_parquet(tmp, index=False, compression="zstd", write_covering_bbox=True)
    tmp.replace(path)


def unify(cfg) -> pd.DataFrame:
    df = load_interim(cfg)
    n_in = len(df)
    occ, dups = deduplicate(df, cfg)
    occ = occ.sort_values(["species_scientific", "timestamp_utc", "record_id"]).reset_index(drop=True)
    occ["dist_coast_km"] = distance_to_coast_km(occ["lat"], occ["lon"], natural_earth_path(cfg.raw)).astype("float32")
    cols = list(schema.COLUMNS) + list(EXTRA_COLUMNS)
    occ = occ[cols]
    problems = schema.validate(occ)
    if problems:
        raise ValueError(f"unified table invalid: {problems}")

    out = cfg.processed
    to_geoparquet(occ, out / "occurrences.parquet")
    dups.to_parquet(out / "duplicates.parquet", index=False)
    eff_p = cfg.interim / "whalinghistory_effort.parquet"
    if eff_p.exists():
        eff = pd.read_parquet(eff_p).dropna(subset=["lat", "lon"])
        to_geoparquet(eff, out / "effort_logbook.parquet")
    log.info("unified: %d in -> %d out (%d duplicates removed)", n_in, len(occ), len(dups))

    write_summaries(cfg, occ, dups, n_in)
    write_duckdb(cfg)
    return occ


def write_summaries(cfg, occ: pd.DataFrame, dups: pd.DataFrame, n_in: int) -> None:
    out = cfg.processed
    occ = occ.assign(decade=(occ["timestamp_utc"].dt.year // 10 * 10).astype("Int64"))
    (occ.groupby(["source", "species_scientific", "decade"]).size().rename("records").reset_index()
        .to_csv(out / "summary_source_species_decade.csv", index=False))
    by_src = occ.groupby("source").agg(records=("record_id", "size"),
                                       with_individual_id=("individual_id", lambda s: int(s.notna().sum())),
                                       first_year=("timestamp_utc", lambda s: int(s.dt.year.min())),
                                       last_year=("timestamp_utc", lambda s: int(s.dt.year.max())))
    by_src["duplicates_removed_from_source"] = dups.groupby("dropped_source").size().reindex(by_src.index).fillna(0).astype(int)
    by_src.to_csv(out / "summary_by_source.csv")
    pd.Series({"records_in": n_in, "records_out": len(occ), "duplicates_removed": len(dups),
               "exact_duplicates": int((dups["pass"] == "exact").sum()),
               "fuzzy_duplicates": int((dups["pass"] == "fuzzy").sum())}).to_json(out / "summary_totals.json", indent=1)


def write_duckdb(cfg) -> None:
    """DuckDB file with views over the parquet outputs (paths relative to the project root)."""
    db = cfg.processed / "whales.duckdb"
    db.unlink(missing_ok=True)
    con = duckdb.connect(str(db))
    rel = lambda p: p.relative_to(cfg.root).as_posix()  # noqa: E731
    views = {"occurrences": cfg.processed / "occurrences.parquet",
             "duplicates": cfg.processed / "duplicates.parquet",
             "effort_logbook": cfg.processed / "effort_logbook.parquet",
             "marine_protected_areas": cfg.processed / "env" / "marine_protected_areas.parquet"}
    for name, p in views.items():
        if p.exists():
            con.execute(f"CREATE VIEW {name} AS SELECT * FROM read_parquet('{rel(p)}')")
    con.execute("""
        CREATE MACRO lon_0_360(lon) AS (lon + 360) % 360;
    """)
    con.close()
    log.info("wrote %s (run queries from the project root)", db)
