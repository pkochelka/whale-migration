"""OBIS occurrences via pyobis, in year windows per species (one parquet per window)."""
from __future__ import annotations

import json
import logging
from datetime import date

import pandas as pd

from ..http import write_manifest
from ..util import slug

log = logging.getLogger(__name__)

FIELDS = [
    "id", "occurrenceID", "scientificName", "originalScientificName", "aphiaID", "speciesid",
    "decimalLatitude", "decimalLongitude", "coordinateUncertaintyInMeters", "eventDate",
    "verbatimEventDate", "date_year", "year", "month", "day", "basisOfRecord", "individualCount",
    "organismID", "occurrenceStatus", "absence", "dataset_id", "datasetName", "institutionCode",
    "samplingProtocol", "identificationRemarks", "occurrenceRemarks", "flags", "license",
    "bibliographicCitation", "eventID",
]


def fetch(cfg, http, force: bool = False, name: str = "obis") -> None:
    from pyobis import occurrences

    sc = cfg.source("obis")
    root = cfg.raw_dir("obis")
    w = int(sc.get("window_years", 10))
    end_year = date.today().year
    for sp in cfg.species_names:
        d = root / slug(sp)
        d.mkdir(exist_ok=True)
        total = http.get_json(f"{sc['api']}/occurrence", params={"scientificname": sp, "size": 0})["total"]
        cap = sc.get("max_records_per_species")
        got = 0
        for y0 in range(int(sc.get("start_year", 1700)), end_year + 1, w):
            y1 = min(y0 + w - 1, end_year)
            out = d / f"{y0}_{y1}.parquet"
            if out.exists() and not force:
                got += pd.read_parquet(out, columns=["id"]).shape[0]
                continue
            n = http.get_json(f"{sc['api']}/occurrence", params={
                "scientificname": sp, "startdate": f"{y0}-01-01", "enddate": f"{y1}-12-31", "size": 0})["total"]
            if n == 0:
                pd.DataFrame(columns=FIELDS).to_parquet(out)
                continue
            if cap and got >= cap:
                break
            size = min(n, cap - got) if cap else n
            log.info("OBIS %s %d-%d: %d records", sp, y0, y1, size)
            df = occurrences.search(scientificname=sp, startdate=f"{y0}-01-01", enddate=f"{y1}-12-31",
                                    fields=",".join(FIELDS), size=size, cache=False).execute()
            df = df.reindex(columns=FIELDS).astype("string")
            df.to_parquet(out)
            got += len(df)
        write_manifest(d / "manifest.json", source="OBIS", query={"scientificname": sp},
                       api=sc["api"], total_reported=total, total_fetched=got,
                       note="year-windowed queries exclude records without eventDate")
    _fetch_datasets(cfg, http, force)


def _fetch_datasets(cfg, http, force: bool) -> None:
    """Dataset-level metadata (license, citation, title) for every dataset seen."""
    root = cfg.raw_dir("obis")
    path = root / "datasets.json"
    known = json.loads(path.read_text(encoding="utf-8")) if path.exists() and not force else {}
    ids: set[str] = set()
    for p in root.glob("*/*.parquet"):
        ids |= set(pd.read_parquet(p, columns=["dataset_id"])["dataset_id"].dropna().unique())
    todo = sorted(ids - set(known))
    log.info("OBIS dataset metadata: %d new of %d", len(todo), len(ids))
    for i, ds in enumerate(todo):
        try:
            r = http.get_json(f"{cfg.source('obis')['api']}/dataset/{ds}")
            rec = (r.get("results") or [{}])[0]
            known[ds] = {k: rec.get(k) for k in ("id", "url", "title", "citation", "license",
                                                 "intellectualrights", "institutes", "doi")}
        except Exception as e:
            log.warning("dataset %s: %s", ds, e)
        if i % 50 == 0:
            path.write_text(json.dumps(known, indent=1), encoding="utf-8")
    path.write_text(json.dumps(known, indent=1), encoding="utf-8")
