"""Shared cleaning for Darwin Core occurrence tables (OBIS, GBIF, OBIS-SEAMAP).

Callers rename their columns to the standard Darwin Core terms below and pass a DataFrame with
at least: local_id, occurrenceID, scientificName, decimalLatitude, decimalLongitude, eventDate.
Optional: year, month, day, individualCount, organismID, coordinateUncertaintyInMeters,
basisOfRecord, occurrenceStatus, absence, dataset_id, dataset_title, license, citation,
samplingProtocol, identificationRemarks, occurrenceRemarks, issues, raw_ref.
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

from ..dates import from_parts, parse_dates
from ..licenses import normalize_series
from ..report import CleaningReport
from . import common

DROP_BASIS = {"fossilspecimen", "fossil_specimen", "livingspecimen", "living_specimen"}
# Upstream QC flags worth carrying as quality_flags.
GBIF_ISSUES = {"ZERO_COORDINATE", "COORDINATE_INVALID", "COORDINATE_OUT_OF_RANGE",
               "PRESUMED_SWAPPED_COORDINATE", "PRESUMED_NEGATED_LATITUDE", "PRESUMED_NEGATED_LONGITUDE",
               "COUNTRY_COORDINATE_MISMATCH", "RECORDED_DATE_INVALID", "RECORDED_DATE_UNLIKELY",
               "COORDINATE_ROUNDED", "TAXON_MATCH_FUZZY", "TAXON_MATCH_HIGHERRANK"}
OBIS_FLAGS = {"on_land", "no_depth", "depth_exceeds_bath", "date_in_future", "date_before_1700"}

HAPPYWHALE_ID = re.compile(r"happywhale\.com/individual/(\d+)", re.I)


def col(df: pd.DataFrame, name: str) -> pd.Series:
    return df[name] if name in df.columns else pd.Series(pd.NA, index=df.index, dtype="string")


def normalize_individual(org: pd.Series) -> pd.Series:
    org = org.astype("string").str.strip()
    org = org.mask(org.isin(["", "nan", "None", "NA", "unknown"]))
    hw = org.str.extract(HAPPYWHALE_ID)[0]
    return ("happywhale:" + hw).where(hw.notna(), org).astype("string")


def classify_event(df: pd.DataFrame) -> tuple[pd.Series, pd.DataFrame]:
    """Return event_type and boolean hint columns (acoustic, stranding, argos, gps)."""
    meta_text = (col(df, "dataset_title").fillna("") + " | " + col(df, "samplingProtocol").fillna("")).str.lower()
    text = (meta_text + " | " + col(df, "identificationRemarks").fillna("") + " | "
            + col(df, "occurrenceRemarks").fillna("")).str.lower()
    basis = col(df, "basisOfRecord").fillna("").str.lower().str.replace("_", "")
    has_ind = col(df, "individual_id").notna()
    hints = pd.DataFrame({
        "photo": text.str.contains(r"photo.?id|photo identification|happywhale|photograph", regex=True),
        "tag": text.str.contains(r"\btag|telemetry|satellite|argos|tracking", regex=True) & (basis == "machineobservation"),
        # Dataset-level only: remarks like "caught in fishing gear" are entanglements, not catches.
        "catch": meta_text.str.contains(r"whaling|catch|harvest|commercial kill", regex=True),
        # Only machine observations: many visual surveys mention "acoustic" in their title.
        "acoustic": text.str.contains(r"acoustic|hydrophone|\bpam\b", regex=True) & (basis == "machineobservation"),
        "stranding": text.str.contains(r"strand", regex=True),
        "argos": text.str.contains(r"argos", regex=True),
        "gps": text.str.contains(r"\bgps\b|fastloc", regex=True),
    })
    ev = pd.Series("sighting", index=df.index, dtype="string")
    ev[hints["catch"]] = "catch"
    ev[hints["photo"] & has_ind] = "encounter"
    ev[hints["tag"]] = "tag_fix"
    return ev, hints


def clean_dwc(df: pd.DataFrame, source: str, report: CleaningReport, cfg) -> pd.DataFrame:
    report.rows_in = len(df)
    status = col(df, "occurrenceStatus").fillna("").str.lower()
    absence = col(df, "absence").fillna("").astype(str).str.lower().isin(["true", "1"])
    df = report.drop(df, (status == "absent") | absence, "absence_record")
    basis = col(df, "basisOfRecord").fillna("").str.lower()
    df = report.drop(df, basis.isin(DROP_BASIS), "fossil_or_living_specimen")

    df = common.taxonomy(cfg).apply(df, "scientificName", report)

    parsed = parse_dates(col(df, "eventDate"))
    missing = parsed["ts"].isna()
    if missing.any() and "year" in df.columns:
        alt = from_parts(df.loc[missing, "year"], col(df, "month")[missing], col(df, "day")[missing])
        parsed.loc[missing, ["ts", "precision"]] = alt[["ts", "precision"]].values
        parsed.loc[missing & alt["ts"].reindex(parsed.index).notna(), "flags"] = "date_from_year_month_day"
    df = common.apply_dates(df, parsed, report)

    df = df.copy()
    df["individual_id"] = normalize_individual(col(df, "organismID"))
    ev, hints = classify_event(df)
    for h in ("acoustic", "stranding"):
        df = report.flag(df, hints[h], h)

    lic = normalize_series(col(df, "license"))
    df = report.flag(df, lic.isna(), "license_unknown")
    lic = lic.fillna("unspecified (see dataset metadata)")
    restricted = lic.str.contains(r"no use without permission|all rights reserved", case=False, regex=True)
    df = report.flag(df, restricted, "license_all_rights_reserved")
    if (cfg.get("licenses") or {}).get("drop_all_rights_reserved", False):
        keep = ~restricted
        df, lic = report.drop(df, restricted, "license_all_rights_reserved"), lic[keep]

    cnt = pd.to_numeric(col(df, "individualCount"), errors="coerce")
    df = report.flag(df, cnt > 5000, "implausible_count_removed")
    cnt = cnt.where((cnt > 0) & (cnt <= 5000))

    # Upstream QC flags.
    if "issues" in df.columns:
        iss = df["issues"].fillna("")
        for i in GBIF_ISSUES:
            df = report.flag(df, iss.str.contains(i, regex=False), f"gbif:{i.lower()}")
    if "flags" in df.columns:
        fl = df["flags"].fillna("")
        for f in OBIS_FLAGS:
            df = report.flag(df, fl.str.contains(f, regex=False), f"obis:{f}")

    pq = pd.Series("reported", index=df.index, dtype="string")
    pq[ev == "tag_fix"] = "argos"
    pq[(ev == "tag_fix") & hints["gps"]] = "gps"

    out = pd.DataFrame({
        "record_id": source + ":" + df["local_id"].astype("string"),
        "dataset_id": col(df, "dataset_id"),
        "species_scientific": df["species_scientific"],
        "species_common": df["species_common"],
        "worms_aphia_id": df["worms_aphia_id"],
        "individual_id": df["individual_id"],
        "event_type": ev,
        "timestamp_utc": df["timestamp_utc"],
        "date_precision": df["date_precision"],
        "lat": col(df, "decimalLatitude"), "lon": col(df, "decimalLongitude"),
        "position_quality": pq,
        "count": cnt,
        "platform": col(df, "samplingProtocol").str.slice(0, 120),
        "license": lic,
        "citation": col(df, "citation").fillna(col(df, "dataset_title")).fillna(source),
        "raw_ref": col(df, "raw_ref"),
        "occurrence_id": col(df, "occurrenceID").astype("string").str.strip(),
        "coord_uncertainty_m": pd.to_numeric(col(df, "coordinateUncertaintyInMeters"), errors="coerce"),
        "quality_flags": col(df, "quality_flags"),
    }, index=df.index)
    out = common.clean_coordinates(out, report, cfg)
    return common.finalize(out, report, cfg, source)


def tidy_citation(text) -> str | None:
    """Collapse whitespace; turn IPT 'crossref' JSON citations into plain text."""
    if text is None or (isinstance(text, float) and np.isnan(text)) or text is pd.NA:
        return None
    s = str(text)
    if s.lstrip().startswith("[{") or s.lstrip().startswith("{"):
        import json
        try:
            end = s.rindex("}]") + 2 if "}]" in s else s.rindex("}") + 1
            obj = json.loads(s[:end])
            rest = s[end:].strip()
            items = obj if isinstance(obj, list) else [obj]
            parts = []
            for it in items:
                ci = (it.get("crossref") or {}).get("citeinfo") or {}
                if ci:
                    parts.append(f"{ci.get('origin', '')} ({ci.get('pubdate', '')}). {ci.get('title', '')}. "
                                 f"{ci.get('onlink', '')}".strip())
            s = " ".join(parts + ([rest] if rest else []))
        except ValueError:
            pass
    return " ".join(s.split())
