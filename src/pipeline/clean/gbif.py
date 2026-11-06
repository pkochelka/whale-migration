"""GBIF (search partitions or download-API zip) -> unified occurrences."""
from __future__ import annotations

import json
import zipfile

import pandas as pd

from ..report import CleaningReport
from . import SourceUnavailable
from .darwincore import clean_dwc, tidy_citation

SOURCE = "gbif"


def _read(d) -> pd.DataFrame:
    frames = []
    for p in sorted(d.glob("*/*.parquet")):
        f = pd.read_parquet(p)
        f["raw_ref"] = f"{p.parent.name}/{p.name}#key=" + f["key"].astype(str)
        frames.append(f)
    for z in sorted(d.glob("*/download.zip")):  # download API (SIMPLE_CSV, tab separated)
        with zipfile.ZipFile(z) as zz:
            name = next(n for n in zz.namelist() if n.endswith(".csv"))
            with zz.open(name) as fh:
                f = pd.read_csv(fh, sep="\t", dtype=str, quoting=3)
        f = f.rename(columns={"gbifID": "key"})
        f["raw_ref"] = f"{z.parent.name}/{z.name}#gbifID=" + f["key"].astype(str)
        frames.append(f)
    if not frames:
        raise SourceUnavailable("no GBIF data; run `python -m pipeline fetch gbif`")
    return pd.concat(frames, ignore_index=True).drop_duplicates("key")


def clean(cfg, name: str = SOURCE) -> pd.DataFrame:
    d = cfg.raw / SOURCE
    if not d.exists():
        raise SourceUnavailable("no GBIF data; run `python -m pipeline fetch gbif`")
    raw = _read(d)
    meta = json.loads((d / "datasets.json").read_text(encoding="utf-8")) if (d / "datasets.json").exists() else {}
    m = pd.DataFrame.from_dict(meta, orient="index")
    title = raw["datasetKey"].map(m["title"]) if "title" in m else raw.get("datasetName")
    cit = raw["datasetKey"].map(m["citation"]).map(tidy_citation) if "citation" in m else pd.Series(pd.NA, index=raw.index)
    citation = cit.fillna(pd.Series(title, index=raw.index)).fillna("GBIF dataset " + raw["datasetKey"])
    exclude = {b.lower().replace("_", "") for b in cfg.source("gbif").get("exclude_basis_of_record", [])}
    report = CleaningReport(SOURCE)
    report.note(f"{len(raw):,} unique GBIF records from {raw['datasetKey'].nunique()} datasets")
    basis = raw["basisOfRecord"].fillna("").str.lower().str.replace("_", "")
    df = pd.DataFrame({
        "local_id": raw["key"], "occurrenceID": raw["occurrenceID"],
        "scientificName": raw["acceptedScientificName"].fillna(raw["scientificName"]).str.replace(
            r"\s+\(?[A-Z][^\d]*\d{4}\)?$", "", regex=True),
        "decimalLatitude": raw["decimalLatitude"], "decimalLongitude": raw["decimalLongitude"],
        "eventDate": raw["eventDate"], "year": raw["year"], "month": raw["month"], "day": raw["day"],
        "individualCount": raw["individualCount"], "organismID": raw.get("organismID"),
        "coordinateUncertaintyInMeters": raw["coordinateUncertaintyInMeters"],
        "basisOfRecord": raw["basisOfRecord"].where(~basis.isin(exclude), "FossilSpecimen"),
        "occurrenceStatus": raw["occurrenceStatus"],
        "dataset_id": "gbif:" + raw["datasetKey"], "dataset_title": title,
        "license": raw["license"], "citation": citation + " Accessed via GBIF.org (https://www.gbif.org/dataset/"
                                                + raw["datasetKey"] + ").",
        "samplingProtocol": raw.get("samplingProtocol"),
        "identificationRemarks": raw.get("identificationRemarks"),
        "occurrenceRemarks": raw.get("occurrenceRemarks"), "issues": raw.get("issues"),
        "raw_ref": raw["raw_ref"],
    })
    return clean_dwc(df, SOURCE, report, cfg)
