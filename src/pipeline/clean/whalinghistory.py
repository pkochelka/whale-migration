"""American Offshore Whaling Logbook (AOWL) -> unified occurrences + daily effort table.

Notes on the 2025-09 release
* There is no PosType column (an earlier assumption); position_quality is "gazetteer" when the
  row names a place (positions in port derived from the place name) and "logbook" otherwise.
* Positions are noon fixes. Transcription errors (wrong hemisphere, swapped or wrong digits) are
  found as speed spikes along each voyage track and corrected only when a unique fix explains
  the spike (see clean/tracks.py). Uncorrectable spikes are dropped from occurrences and kept,
  flagged, in the effort table.
* "NoEnc" rows (no whales) are vessel-days, i.e. search effort, and go to
  data/interim/whalinghistory_effort.parquet for sampling-bias analysis.
* "Right" is resolved by region; see `right_whale_by_region`.
"""
from __future__ import annotations

import io
import logging
import zipfile

import numpy as np
import pandas as pd

from . import SourceUnavailable
from ..dates import from_parts
from ..report import CleaningReport
from . import common, tracks

log = logging.getLogger(__name__)

SOURCE = "whalinghistory"


def right_whale_by_region(lat: pd.Series, lon: pd.Series) -> pd.Series:
    """Map logbook 'Right' whales to a Eubalaena species from position.

    S hemisphere -> E. australis; N Atlantic (100W-45E) -> E. glacialis; N Pacific -> E. japonica.
    North of 60N whalers' "right whales" were often bowheads, and the northern Indian Ocean has no
    right whale population: both stay at genus level ("Eubalaena") and follow the higher-taxon policy.
    """
    out = pd.Series("Eubalaena", index=lat.index, dtype="string")
    south = lat < 0
    north = (lat >= 0) & (lat < 60)
    atlantic = (lon >= -100) & (lon <= 45)
    pacific = (lon > 100) | (lon < -100)
    out[south] = "Eubalaena australis"
    out[north & atlantic] = "Eubalaena glacialis"
    out[north & pacific] = "Eubalaena japonica"
    return out


def read_raw(cfg) -> tuple[pd.DataFrame, pd.DataFrame, str]:
    d = cfg.raw / SOURCE
    zl, zv = d / "AmericanOffshoreWhalingLogbookData.zip", d / "AmericanOffshoreWhalingVoyages.zip"
    if not zl.exists():
        raise SourceUnavailable(f"{zl} missing; run `python -m pipeline fetch whalinghistory`")
    with zipfile.ZipFile(zl) as z:
        name = next(n for n in z.namelist() if n.split("/")[-1].startswith("aowl_") and n.endswith(".txt"))
        logs = pd.read_csv(io.BytesIO(z.read(name)), sep="\t", dtype=str, na_values=["NULL"],
                           keep_default_na=False, encoding="utf-8")
    voyages = pd.DataFrame(columns=["voyageID", "vessel", "rig"])
    if zv.exists():
        with zipfile.ZipFile(zv) as z:
            vn = next(n for n in z.namelist() if n.split("/")[-1].startswith("voyages_"))
            voyages = pd.read_csv(io.BytesIO(z.read(vn)), sep="\t", dtype=str,
                                  usecols=["voyageID", "vessel", "rig"])
    return logs, voyages, name.split("/")[-1]


def build_tracks(logs: pd.DataFrame, vmax: float, report: CleaningReport) -> pd.DataFrame:
    """Parse dates/positions for every row, detect & correct track spikes."""
    df = logs.rename(columns={"Lat": "lat", "Lon": "lon"}).copy()
    df["seq"] = pd.to_numeric(df["sequence"], errors="coerce")
    df["lat"] = pd.to_numeric(df["lat"], errors="coerce")
    df["lon"] = pd.to_numeric(df["lon"], errors="coerce")
    parsed = from_parts(df["Year"], df["Month"], df["Day"])
    df["ts"] = parsed["ts"].array
    df["date_precision"] = parsed["precision"].values
    df["quality_flags"] = pd.Series(parsed["flags"].values, index=df.index).fillna("").astype("string")
    df = df.sort_values(["VoyageID", "seq"]).reset_index(drop=True)
    df["orig_lat"], df["orig_lon"] = df["lat"], df["lon"]

    ok = df["lat"].notna() & df["lon"].notna() & df["ts"].notna()
    sp = tracks.leg_speeds(df[ok], "VoyageID", "ts")
    spikes = tracks.find_spikes(sp, vmax)
    fixes = tracks.correct_spikes(sp, spikes, vmax, "ts", "VoyageID")
    good = fixes["kind"].notna()
    report.note(f"track spikes (> {vmax} km/day in and out): {int(spikes.sum())}; "
                f"corrected {int(good.sum())}; uncorrectable {int((~good).sum())}")
    if good.any():
        kinds = fixes.loc[good, "kind"].value_counts()
        report.note("corrections by kind: " + ", ".join(f"{k} ({v})" for k, v in kinds.items()))
    for idx, row in fixes[good].iterrows():
        df.at[idx, "lat"], df.at[idx, "lon"] = row["new_lat"], row["new_lon"]
    df = report.flag(df, pd.Series(df.index.isin(fixes.index[good]), index=df.index), "position_corrected")
    kind = pd.Series(pd.NA, index=df.index, dtype="string")
    kind[fixes.index[good]] = fixes.loc[good, "kind"].astype("string").values
    df["correction_kind"] = kind
    df["track_outlier"] = df.index.isin(fixes.index[~good])
    return df


def clean(cfg, name: str = SOURCE) -> pd.DataFrame:
    sc = cfg.source(SOURCE)
    report = CleaningReport(SOURCE)
    logs, voyages, raw_name = read_raw(cfg)
    report.rows_in = len(logs)
    report.note(f"raw file {raw_name}: {len(logs):,} daily rows "
                f"({(logs['Encounter'] == 'NoEnc').sum():,} without encounters)")

    df = build_tracks(logs, float(sc.get("max_speed_km_per_day", 450)), report)
    v = voyages.drop_duplicates("voyageID").set_index("voyageID")
    vessel = df["VoyageID"].map(v["vessel"].str.strip()) if len(v) else pd.Series(pd.NA, index=df.index)
    rig = df["VoyageID"].map(v["rig"].str.strip()) if len(v) else pd.Series(pd.NA, index=df.index)
    df["platform"] = (vessel.fillna("unknown vessel") + rig.map(lambda r: f" ({r})" if isinstance(r, str) and r else ""))

    _write_effort(cfg, df)

    no_enc = df["Encounter"] == "NoEnc"
    report.dropped["no_encounter_vessel_day (kept in effort table)"] += int(no_enc.sum())
    occ = df[~no_enc].copy()
    occ = report.drop(occ, occ["Encounter"] == "Spoke", "gam_with_other_vessel")
    occ = report.drop(occ, occ["Species"].isna() | (occ["Species"].str.strip() == ""), "species_missing")
    occ = report.drop(occ, occ["track_outlier"], "track_outlier_uncorrectable")

    smap = dict(sc.get("species_map") or {})
    raw_sp = occ["Species"].str.strip()
    sci = raw_sp.map(lambda s: smap.get(s, s))
    is_right = sci == "@right_whale_by_region"
    sci = sci.astype("string")
    sci[is_right] = right_whale_by_region(occ.loc[is_right, "lat"], occ.loc[is_right, "lon"])
    occ["name_for_taxonomy"] = sci
    occ = report.flag(occ, is_right & (sci != "Eubalaena"), "species_inferred_from_region")
    occ = common.taxonomy(cfg).apply(occ, "name_for_taxonomy", report)

    struck = pd.to_numeric(occ["NStruck"], errors="coerce")
    tried = pd.to_numeric(occ["NTried"], errors="coerce")
    whole_deg = (occ["orig_lat"] % 1 == 0) & (occ["orig_lon"] % 1 == 0)
    out = pd.DataFrame({
        "record_id": "whalinghistory:" + occ["sequence"].astype(str),
        "dataset_id": "AOWL:" + occ["Source"].fillna("unknown"),
        "species_scientific": occ["species_scientific"],
        "species_common": occ["species_common"],
        "worms_aphia_id": occ["worms_aphia_id"],
        "individual_id": pd.NA,
        "event_type": np.where(occ["Encounter"] == "Strike", "catch", "sighting"),
        "timestamp_utc": occ["ts"],
        "date_precision": occ["date_precision"],
        "lat": occ["lat"], "lon": occ["lon"],
        "position_quality": np.where(occ["Place"].notna() & (occ["Place"].str.strip() != ""), "gazetteer", "logbook"),
        # Whales struck for catches; sightings carry no count in AOWL.
        "count": np.where(occ["Encounter"] == "Strike", struck.where(struck > 0, tried), np.nan),
        "platform": occ["platform"],
        "license": sc["license"],
        "citation": " ".join(sc["citation"].split()),
        "raw_ref": (raw_name + "#sequence=" + occ["sequence"].astype(str) + ";voyage=" + occ["VoyageID"]
                    + ";struck=" + occ["NStruck"].fillna("") + ";tried=" + occ["NTried"].fillna("")
                    + np.where(occ["correction_kind"].notna(),
                               ";corrected=" + occ["correction_kind"].fillna("") + ":" + occ["orig_lat"].astype(str)
                               + "," + occ["orig_lon"].astype(str), "")),
        "occurrence_id": "AOWL:" + occ["sequence"].astype(str),
        # Noon fixes by dead reckoning / sextant: ~10 km; place-name positions: ~25 km;
        # whole-degree abstracts: ~55 km. These are estimates, not stated by the source.
        "coord_uncertainty_m": np.select(
            [whole_deg, occ["Place"].notna() & (occ["Place"].str.strip() != "")], [55_000.0, 25_000.0], 10_000.0),
        "quality_flags": occ["quality_flags"],
    }, index=occ.index)
    out = report.flag(out, pd.Series(whole_deg.values, index=out.index), "whole_degree_position")
    out = common.clean_coordinates(out, report, cfg)
    return common.finalize(out, report, cfg, SOURCE)


def _write_effort(cfg, df: pd.DataFrame) -> None:
    eff = pd.DataFrame({
        "voyage_id": df["VoyageID"], "sequence": df["seq"].astype("Int64"),
        "timestamp_utc": df["ts"], "date_precision": df["date_precision"],
        "lat": df["lat"], "lon": df["lon"], "platform": df["platform"],
        "encounter": df["Encounter"], "species_raw": df["Species"], "logbook_source": df["Source"],
        "track_outlier": df["track_outlier"], "position_corrected": df["correction_kind"].notna(),
    })
    cfg.interim.mkdir(parents=True, exist_ok=True)
    eff.to_parquet(cfg.interim / "whalinghistory_effort.parquet", index=False)
