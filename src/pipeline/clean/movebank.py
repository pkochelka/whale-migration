"""Movebank tag data -> unified occurrences (event_type = tag_fix).

* Argos location classes outside `argos_keep_classes` (default 3, 2, 1, G) are removed.
* Fixes marked invisible / outliers by the data owner (`visible == false`) are removed.
* Speed outliers (> max_speed_km_per_h on both legs) are flagged, not removed.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from ..dates import parse_dates
from ..report import CleaningReport
from . import SourceUnavailable, common, tracks

SOURCE = "movebank"
SENSOR = {"653": "gps", "82798": "argos"}


def filter_argos(df: pd.DataFrame, keep: list[str], report: CleaningReport) -> pd.DataFrame:
    lc = df["argos_lc"].astype("string").str.upper().str.strip()
    is_argos = df["sensor"] == "argos"
    bad = is_argos & ~lc.isin([k.upper() for k in keep])
    return report.drop(df, bad, "argos_low_quality_class")


def flag_speed(df: pd.DataFrame, vmax_kmh: float, report: CleaningReport) -> pd.DataFrame:
    df = df.sort_values(["individual", "ts"])
    sp = tracks.leg_speeds(df, "individual", "ts", min_dt_days=1 / 1440)
    vmax = vmax_kmh * 24
    out = (sp["speed_in"] > vmax) & (sp["speed_out"].isna() | (sp["speed_out"] > vmax))
    return report.flag(df, out, "speed_outlier")


def clean(cfg, name: str = SOURCE) -> pd.DataFrame:
    d = cfg.raw / SOURCE
    if (d / "STUB.json").exists() or not list(d.glob("study_*_events.csv")):
        raise SourceUnavailable("Movebank not fetched (no credentials); stubbed")
    sc = cfg.source(SOURCE)
    studies = pd.read_csv(d / "studies.csv", dtype=str).set_index("id")
    frames = []
    for p in sorted(d.glob("study_*_events.csv")):
        sid = p.stem.split("_")[1]
        f = pd.read_csv(p, dtype=str)
        f["study_id"] = sid
        f["raw_ref"] = p.name + "#event_id=" + f["event_id"].astype(str)
        frames.append(f)
    raw = pd.concat(frames, ignore_index=True)
    report = CleaningReport(SOURCE)
    report.rows_in = len(raw)
    df = raw.rename(columns={"location_lat": "lat", "location_long": "lon"})
    df["sensor"] = df["sensor_type_id"].map(SENSOR).fillna("other")
    df = report.drop(df, df.get("visible", pd.Series("true", index=df.index)).str.lower() == "false",
                     "marked_outlier_by_owner")
    df = filter_argos(df, sc.get("argos_keep_classes", ["3", "2", "1", "G"]), report)
    df = common.taxonomy(cfg).apply(df, "individual_taxon_canonical_name", report)
    parsed = parse_dates(df["timestamp"])
    df = common.apply_dates(df, parsed, report)
    df["ts"] = df["timestamp_utc"]
    df["lat"], df["lon"] = pd.to_numeric(df["lat"], errors="coerce"), pd.to_numeric(df["lon"], errors="coerce")
    df["individual"] = df["study_id"] + ":" + df["individual_local_identifier"].fillna(df["tag_local_identifier"])
    df = df.dropna(subset=["lat", "lon"])
    df = flag_speed(df, float(sc.get("max_speed_km_per_h", 15)), report)
    lic = df["study_id"].map(studies.get("license_type", pd.Series(dtype=str))).fillna("see Movebank study terms")
    cit = df["study_id"].map(studies.get("citation", pd.Series(dtype=str)))
    cit = cit.fillna("Movebank study " + df["study_id"] + ": " + df["study_id"].map(studies["name"]).fillna(""))
    out = pd.DataFrame({
        "record_id": "movebank:" + df["study_id"] + ":" + df["event_id"].astype(str),
        "dataset_id": "movebank:" + df["study_id"],
        "species_scientific": df["species_scientific"], "species_common": df["species_common"],
        "worms_aphia_id": df["worms_aphia_id"], "individual_id": "movebank:" + df["individual"],
        "event_type": "tag_fix", "timestamp_utc": df["timestamp_utc"], "date_precision": "day",
        "lat": df["lat"], "lon": df["lon"],
        "position_quality": np.where(df["sensor"] == "gps", "gps", "argos"),
        "count": 1, "platform": df["sensor"] + " tag " + df["tag_local_identifier"].fillna(""),
        "license": lic, "citation": cit, "raw_ref": df["raw_ref"],
        "occurrence_id": "movebank:" + df["event_id"].astype(str),
        "quality_flags": df.get("quality_flags"),
    }, index=df.index)
    out = common.clean_coordinates(out, report, cfg)
    report.note("studies: " + json.dumps(sorted(df["study_id"].unique().tolist())))
    return common.finalize(out, report, cfg, SOURCE)
