"""Steps shared by every occurrence source: coordinates, land mask, dates, schema, output."""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from .. import schema
from ..geo import coordinate_issues, distance_to_coast_km, natural_earth_path, normalize_lon, on_land
from ..report import CleaningReport
from ..taxonomy import Taxonomy

log = logging.getLogger(__name__)


def taxonomy(cfg) -> Taxonomy:
    return Taxonomy(cfg, cache_path=cfg.interim / "worms_cache.json")


def clean_coordinates(df: pd.DataFrame, report: CleaningReport, cfg, land: bool = True) -> pd.DataFrame:
    """Drop invalid coordinates, wrap longitudes, drop/flag points on land."""
    iss = coordinate_issues(df["lat"], df["lon"])
    df = df.assign(lat=pd.to_numeric(df["lat"], errors="coerce"), lon=pd.to_numeric(df["lon"], errors="coerce"))
    df = report.drop(df, iss["missing"], "coords_missing")
    df = report.drop(df, iss["lat_out_of_range"].reindex(df.index), "lat_out_of_range")
    df = report.drop(df, iss["lon_out_of_range"].reindex(df.index), "lon_out_of_range")
    if cfg["geo"].get("drop_null_island", True):
        df = report.drop(df, iss["null_island"].reindex(df.index), "null_island_0_0")
    wrapped = df["lon"] > 180  # 180 itself is just the antimeridian; -> -180
    df = report.flag(df, wrapped, "lon_wrapped_from_0_360")
    df["lon"] = normalize_lon(df["lon"])
    if land and len(df):
        df = land_check(df, report, cfg)
    return df


def land_check(df: pd.DataFrame, report: CleaningReport, cfg) -> pd.DataFrame:
    zip_path = natural_earth_path(cfg.raw, "land", "10m")
    inland = on_land(df["lat"], df["lon"], zip_path, cache_dir=cfg.interim / "cache")
    if not inland.any():
        return df
    sub = df[inland]
    dist = distance_to_coast_km(sub["lat"], sub["lon"], zip_path)
    tol_km = np.maximum(cfg["geo"].get("land_tolerance_km", 5),
                        pd.to_numeric(sub.get("coord_uncertainty_m"), errors="coerce").fillna(0) / 1000.0)
    far = pd.Series(False, index=df.index)
    far[sub.index] = dist > tol_km
    near = inland & ~far
    df = report.flag(df, near, "on_land_within_tolerance")
    return report.drop(df, far, "on_land")


def apply_dates(df: pd.DataFrame, parsed: pd.DataFrame, report: CleaningReport) -> pd.DataFrame:
    df = df.copy()
    df["timestamp_utc"] = parsed["ts"].array
    df["date_precision"] = parsed["precision"].values
    flags = pd.Series(parsed["flags"].values, index=df.index).fillna("")
    for f in [f for f in set(";".join(flags.unique()).split(";")) if f]:
        has = flags.str.split(";").apply(lambda xs, f=f: f in xs)
        if f in ("unparseable_date", "date_range_too_wide", "date_before_1700", "date_in_future"):
            continue  # those rows are dropped below with a specific reason
        df = report.flag(df, has, f)
    for reason in ("date_range_too_wide", "date_before_1700", "date_in_future"):
        df = report.drop(df, flags.reindex(df.index).str.contains(reason, regex=False), reason)
    return report.drop(df, df["timestamp_utc"].isna(), "date_missing_or_unparseable")


def finalize(df: pd.DataFrame, report: CleaningReport, cfg, source: str) -> pd.DataFrame:
    """Conform to schema, de-duplicate record ids, validate and write interim parquet + report."""
    df = df.copy()
    df["source"] = source
    df = report.drop(df, df["record_id"].duplicated(keep="first"), "duplicate_record_id")
    if "quality_flags" in df:
        df["quality_flags"] = df["quality_flags"].fillna("")
    out = schema.conform(df)
    problems = schema.validate(out)
    if problems:
        raise ValueError(f"{source}: schema violations: {problems}")
    report.rows_out = len(out)
    cfg.interim.mkdir(parents=True, exist_ok=True)
    path = cfg.interim / f"{source}.parquet"
    out.to_parquet(path, index=False)
    report.save(cfg.interim / "reports")
    log.info("%s: %d in -> %d out (%d dropped); wrote %s", source, report.rows_in, report.rows_out,
             sum(report.dropped.values()), path)
    return out
