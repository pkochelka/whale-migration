"""OBIS occurrences via pyobis, in year windows per species (one parquet per window)."""
from __future__ import annotations

import json
import logging
from datetime import date

import pandas as pd

from ..cache import parquet_done, write_parquet
from ..http import write_manifest
from ..parallel import pmap
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
    sc = cfg.source("obis")
    # pyobis pages each query itself, outside HttpClient's throttle: keep `workers` small.
    pmap(lambda sp: _fetch_species(cfg, http, sc, sp, force), cfg.species_names, int(sc.get("workers", 1)))
    _fetch_datasets(cfg, http, force)


def _fetch_species(cfg, http, sc: dict, sp: str, force: bool) -> None:
    from pyobis import occurrences

    d = cfg.raw_dir("obis") / slug(sp)
    d.mkdir(exist_ok=True)
    w = int(sc.get("window_years", 10))
    end_year = date.today().year
    total = http.get_json(f"{sc['api']}/occurrence", params={"scientificname": sp, "size": 0})["total"]
    cap = sc.get("max_records_per_species")
    got = 0
    for y0 in range(int(sc.get("start_year", 1700)), end_year + 1, w):
        y1 = min(y0 + w - 1, end_year)
        out = d / f"{y0}_{y1}.parquet"
        if not force and parquet_done(out):
            got += pd.read_parquet(out, columns=["id"]).shape[0]
            continue
        n = http.get_json(f"{sc['api']}/occurrence", params={
            "scientificname": sp, "startdate": f"{y0}-01-01", "enddate": f"{y1}-12-31", "size": 0})["total"]
        if n == 0:
            write_parquet(pd.DataFrame(columns=FIELDS), out)
            continue
        if cap and got >= cap:
            break
        size = min(n, cap - got) if cap else n
        log.info("OBIS %s %d-%d: %d records", sp, y0, y1, size)
        df = occurrences.search(scientificname=sp, startdate=f"{y0}-01-01", enddate=f"{y1}-12-31",
                                fields=",".join(FIELDS), size=size, cache=False).execute()
        df = df.reindex(columns=FIELDS).astype("string")
        write_parquet(df, out)
        got += len(df)
    write_manifest(d / "manifest.json", source="OBIS", query={"scientificname": sp},
                   api=sc["api"], total_reported=total, total_fetched=got,
                   note="year-windowed queries exclude records without eventDate")


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

    def one(ds):
        try:
            r = http.get_json(f"{cfg.source('obis')['api']}/dataset/{ds}")
            rec = (r.get("results") or [{}])[0]
            return ds, {k: rec.get(k) for k in ("id", "url", "title", "citation", "license",
                                                "intellectualrights", "institutes", "doi")}
        except Exception as e:
            log.warning("dataset %s: %s", ds, e)
            return ds, None

    workers = int(cfg.source("obis").get("workers", 1))
    for i in range(0, len(todo), 50):  # save every 50 so an interrupted run keeps its progress
        for ds, rec in pmap(one, todo[i:i + 50], workers):
            if rec:
                known[ds] = rec
        path.write_text(json.dumps(known, indent=1), encoding="utf-8")
    path.write_text(json.dumps(known, indent=1), encoding="utf-8")
