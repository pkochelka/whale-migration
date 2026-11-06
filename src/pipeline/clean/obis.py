"""OBIS (pyobis windows) -> unified occurrences."""
from __future__ import annotations

import json

import pandas as pd

from ..report import CleaningReport
from . import SourceUnavailable
from .darwincore import clean_dwc, tidy_citation

SOURCE = "obis"


def clean(cfg, name: str = SOURCE) -> pd.DataFrame:
    d = cfg.raw / SOURCE
    files = sorted(d.glob("*/*.parquet")) if d.exists() else []
    if not files:
        raise SourceUnavailable("no OBIS data; run `python -m pipeline fetch obis`")
    frames = []
    for p in files:
        f = pd.read_parquet(p)
        if len(f):
            f["raw_ref"] = f"{p.parent.name}/{p.name}#id=" + f["id"].astype(str)
            frames.append(f)
    raw = pd.concat(frames, ignore_index=True).drop_duplicates("id")
    meta = json.loads((d / "datasets.json").read_text(encoding="utf-8")) if (d / "datasets.json").exists() else {}
    m = pd.DataFrame.from_dict(meta, orient="index")
    title = raw["dataset_id"].map(m["title"]) if "title" in m else pd.Series(pd.NA, index=raw.index)
    ds_lic = raw["dataset_id"].map(m["intellectualrights"]) if "intellectualrights" in m else pd.NA
    ds_cit = raw["dataset_id"].map(m["citation"]).map(tidy_citation) if "citation" in m else pd.NA
    ds_url = raw["dataset_id"].map(m["url"]) if "url" in m else pd.NA
    report = CleaningReport(SOURCE)
    report.note(f"{len(files)} window files, {len(raw):,} unique rows; dataset metadata for {len(meta)} datasets")
    lic = raw["license"] if "license" in raw else pd.Series(pd.NA, index=raw.index)
    citation = pd.Series(ds_cit, index=raw.index).fillna(title.fillna("OBIS dataset " + raw["dataset_id"].fillna("?")))
    citation = citation + " Accessed via OBIS (https://obis.org/dataset/" + raw["dataset_id"].fillna("") + ")."
    df = pd.DataFrame({
        "local_id": raw["id"], "occurrenceID": raw["occurrenceID"],
        "scientificName": raw["scientificName"],
        "decimalLatitude": raw["decimalLatitude"], "decimalLongitude": raw["decimalLongitude"],
        "eventDate": raw["eventDate"], "year": raw["date_year"].fillna(raw.get("year")),
        "month": raw.get("month"), "day": raw.get("day"),
        "individualCount": raw["individualCount"], "organismID": raw["organismID"],
        "coordinateUncertaintyInMeters": raw["coordinateUncertaintyInMeters"],
        "basisOfRecord": raw["basisOfRecord"], "occurrenceStatus": raw["occurrenceStatus"],
        "absence": raw["absence"], "dataset_id": "obis:" + raw["dataset_id"].fillna("unknown"),
        "dataset_title": title, "dataset_url": ds_url,
        "license": lic.fillna(pd.Series(ds_lic, index=raw.index)),
        "citation": citation, "samplingProtocol": raw["samplingProtocol"],
        "identificationRemarks": raw["identificationRemarks"], "occurrenceRemarks": raw["occurrenceRemarks"],
        "flags": raw["flags"], "raw_ref": raw["raw_ref"],
    })
    return clean_dwc(df, SOURCE, report, cfg)
