"""OBIS-SEAMAP / Happywhale DwC-A archives -> unified occurrences."""
from __future__ import annotations

import io
import json
import logging
import re
import zipfile

import pandas as pd

from ..report import CleaningReport
from . import SourceUnavailable
from .darwincore import clean_dwc, tidy_citation

log = logging.getLogger(__name__)
SOURCE = "seamap"


def eml_license(path) -> str | None:
    """Dataset-level license from eml.xml <intellectualRights> (URL if present, else text)."""
    with zipfile.ZipFile(path) as z:
        if "eml.xml" not in z.namelist():
            return None
        e = z.read("eml.xml").decode("utf-8", errors="replace")
    m = re.search(r"<intellectualRights>(.*?)</intellectualRights>", e, re.S)
    if not m:
        return None
    url = re.search(r'url="([^"]+)"', m.group(1))
    text = " ".join(re.sub(r"<[^>]+>", " ", m.group(1)).split())
    return url.group(1) if url else (text or None)


def read_archive(path) -> pd.DataFrame:
    with zipfile.ZipFile(path) as z:
        meta = z.read("meta.xml").decode("utf-8")
        loc = re.search(r"<core.*?<location>(.*?)</location>", meta, re.S).group(1)
        sep = "\t" if 'fieldsTerminatedBy="\\t"' in meta else ","
        return pd.read_csv(io.BytesIO(z.read(loc)), sep=sep, dtype=str, quoting=3 if sep == "\t" else 0,
                           keep_default_na=False, na_values=[""])


def clean(cfg, name: str = SOURCE) -> pd.DataFrame:
    d = cfg.raw / SOURCE
    zips = sorted(d.glob("zd_*.zip")) if d.exists() else []
    if not zips:
        raise SourceUnavailable("no SEAMAP archives; run `python -m pipeline fetch seamap`")
    # Keep only the newest archive per resource.
    latest: dict[str, object] = {}
    for z in zips:
        latest[z.stem.rsplit("_", 1)[0]] = z
    frames = []
    for res, z in sorted(latest.items()):
        m = json.loads(z.with_name(z.name + ".manifest.json").read_text(encoding="utf-8")) \
            if z.with_name(z.name + ".manifest.json").exists() else {}
        df = read_archive(z)
        df["resource"] = res
        df["raw_ref"] = z.name + "#id=" + df["id"]
        df["gbif_dataset_key"] = m.get("gbifKey")
        # Records without their own license inherit the dataset license (CC BY-NC for Happywhale).
        lic = df["license"] if "license" in df else pd.Series(pd.NA, index=df.index)
        df["license"] = lic.fillna(eml_license(z) or pd.NA)
        frames.append(df)
    raw = pd.concat(frames, ignore_index=True)
    report = CleaningReport(SOURCE)
    report.note(f"{len(latest)} IPT archives, {len(raw):,} rows")

    cit = raw["bibliographicCitation"].map(tidy_citation)
    seamap_link = " Data via OBIS-SEAMAP dataset " + raw["datasetID"] + \
                  " (https://seamap.env.duke.edu/dataset/" + raw["datasetID"] + ")."
    df = pd.DataFrame({
        "local_id": raw["resource"] + ":" + raw["id"],
        "occurrenceID": raw.get("occurrenceID"),
        "scientificName": raw["scientificName"],
        "decimalLatitude": raw["decimalLatitude"], "decimalLongitude": raw["decimalLongitude"],
        "eventDate": raw["eventDate"], "year": raw.get("year"), "month": raw.get("month"), "day": raw.get("day"),
        "individualCount": raw.get("individualCount"), "organismID": raw.get("organismID"),
        "coordinateUncertaintyInMeters": raw.get("coordinateUncertaintyInMeters"),
        "basisOfRecord": raw.get("basisOfRecord"), "occurrenceStatus": raw.get("occurrenceStatus"),
        "dataset_id": "seamap:" + raw["datasetID"], "dataset_title": raw.get("datasetName"),
        "license": raw.get("license"), "citation": (cit.fillna("") + seamap_link).str.strip(),
        "samplingProtocol": raw.get("samplingProtocol"),
        "identificationRemarks": raw.get("identificationRemarks"),
        "occurrenceRemarks": raw.get("occurrenceRemarks"), "raw_ref": raw["raw_ref"],
    })
    return clean_dwc(df, SOURCE, report, cfg)
