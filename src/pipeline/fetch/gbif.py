"""GBIF occurrences via pygbif.

Two modes:
* Download API (preferred, asynchronous, gives a citable DOI) when GBIF_USERNAME, GBIF_PASSWORD
  and GBIF_EMAIL are set in the environment.
* Otherwise the search API, which caps offset+limit at 100 000 per query. Each species is split
  into year (and if needed month) partitions using facet counts so every partition stays below
  `partition_max`, then paged.
"""
from __future__ import annotations

import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

from ..http import HttpClient, write_manifest
from ..util import slug

log = logging.getLogger(__name__)

FIELDS = [
    "key", "datasetKey", "occurrenceID", "scientificName", "acceptedScientificName", "species",
    "taxonRank", "decimalLatitude", "decimalLongitude", "coordinateUncertaintyInMeters",
    "eventDate", "verbatimEventDate", "year", "month", "day", "basisOfRecord", "individualCount",
    "organismID", "occurrenceStatus", "license", "datasetName", "institutionCode",
    "samplingProtocol", "identificationRemarks", "occurrenceRemarks", "catalogNumber", "issues",
    "publishingOrgKey",
]
SEARCH_CAP = 100_000


def _creds() -> tuple[str, str, str] | None:
    u, p, e = (os.environ.get(k) for k in ("GBIF_USERNAME", "GBIF_PASSWORD", "GBIF_EMAIL"))
    return (u, p, e) if u and p and e else None


def taxon_key(http, api: str, name: str) -> int:
    r = http.get_json(f"{api}/species/match", params={"name": name, "strict": "true"})
    if not r.get("usageKey"):
        raise ValueError(f"GBIF backbone has no match for {name!r}")
    return int(r.get("acceptedUsageKey") or r["usageKey"])


def plan_partitions(year_counts: dict[int, int], limit: int) -> list[tuple[int, int]]:
    """Group consecutive years so each group's count <= limit (single years may exceed it)."""
    parts, start, acc = [], None, 0
    for y in sorted(year_counts):
        c = year_counts[y]
        if start is not None and acc + c > limit:
            parts.append((start, prev))
            start, acc = None, 0
        if start is None:
            start = y
        acc += c
        prev = y
    if start is not None:
        parts.append((start, prev))
    return parts


def _facet(http, api, params, field) -> dict[int, int]:
    r = http.get_json(f"{api}/occurrence/search", params={
        **params, "limit": 0, "facet": field, "facetLimit": 1000})
    facets = (r.get("facets") or [{}])[0].get("counts", [])
    return {int(f["name"]): int(f["count"]) for f in facets}


def _page_all(http, api, params, total, page_size) -> pd.DataFrame:
    offsets = list(range(0, min(total, SEARCH_CAP), page_size))

    def fetch_page(off, size):
        # Raw text first: GBIF occasionally serves a page whose JSON is invalid (always at the same
        # byte, i.e. a bad record). Split such pages until the bad record is isolated and skipped.
        r = http.get(f"{api}/occurrence/search", params={**params, "limit": size, "offset": off})
        try:
            return r.json().get("results", [])
        except ValueError:
            if size == 1:
                log.warning("GBIF: skipping unparseable record at offset %d of %s", off, params)
                return []
            step = max(1, size // 10)
            return [rec for o in range(off, off + size, step) for rec in fetch_page(o, min(step, off + size - o))]

    def one(off):
        return fetch_page(off, page_size)

    with ThreadPoolExecutor(max_workers=1) as ex:  # GBIF rate-limits parallel pagers (429)
        rows = [rec for page in ex.map(one, offsets) for rec in page]
    df = pd.DataFrame(rows)
    if "issues" in df:
        df["issues"] = df["issues"].map(lambda v: ";".join(v) if isinstance(v, list) else v)
    return df.reindex(columns=FIELDS).astype("string").drop_duplicates("key")


def fetch(cfg, http, force: bool = False, name: str = "gbif") -> None:
    sc = cfg.source("gbif")
    api = sc["api"]
    root = cfg.raw_dir("gbif")
    # GBIF tolerates a few parallel requests; use a dedicated client with a shorter interval.
    gh = HttpClient({**cfg["http"], "min_interval_s": 0.35})
    creds = _creds()
    if creds:
        log.info("GBIF credentials found: using the download API")
    else:
        log.info("no GBIF credentials: paging the search API (set GBIF_USERNAME/PASSWORD/EMAIL to use downloads)")
    for sp in cfg.species_names:
        key = taxon_key(gh, api, sp)
        d = root / slug(sp)
        d.mkdir(exist_ok=True)
        if creds:
            _download_api(cfg, key, sp, d, creds, force)
        else:
            _search_api(gh, api, sc, key, sp, d, force)
    _fetch_datasets(cfg, gh, force)


def _count(http, api, params) -> int:
    return int(http.get_json(f"{api}/occurrence/search", params={**params, "limit": 0})["count"])


def _bisect(http, api, params, label, n, limit, axis="decimalLatitude", lo=-90.0, hi=90.0, depth=0):
    """Recursively halve a coordinate range until each piece holds <= limit records."""
    if n <= limit:
        yield params, label, n
        return
    if depth >= 12:
        if axis == "decimalLatitude":  # dense spot: switch to longitude
            yield from _bisect(http, api, params, label, n, limit, "decimalLongitude", -180.0, 180.0, 0)
            return
        log.warning("GBIF partition %s still has %d > %d records; truncating", label, n, limit)
        yield params, label, n
        return
    mid = round((lo + hi) / 2, 6)
    tag = "lat" if axis == "decimalLatitude" else "lon"
    # Ranges are inclusive at both ends; the rare boundary duplicate is removed by `key` later.
    for a, b in ((lo, mid), (mid, hi)):
        p = {**params, axis: f"{a},{b}"}
        c = _count(http, api, p)
        if c:
            yield from _bisect(http, api, p, f"{label}_{tag}{a:g}_{b:g}", c, limit, axis, a, b, depth + 1)


def partitions(http, api, base: dict, limit: int):
    """Yield (params, label, count) query partitions, each small enough for fast paging.

    GBIF's search API slows from <1 s to >2 min per page beyond offset ~10 000, so partitions are
    kept below `limit` (default 9 000): year groups -> single months -> latitude/longitude bisection.
    """
    years = _facet(http, api, base, "year")
    for y0, y1 in plan_partitions(years, limit):
        n = sum(c for y, c in years.items() if y0 <= y <= y1)
        if n <= limit:
            yield {**base, "year": f"{y0},{y1}"}, f"{y0}_{y1}", n
            continue
        months = _facet(http, api, {**base, "year": y0}, "month")
        if n - sum(months.values()) > 0:
            log.info("GBIF %s: %d records without month are not reachable by month partitions",
                     y0, n - sum(months.values()))
        for m, c in sorted(months.items()):
            yield from _bisect(http, api, {**base, "year": y0, "month": m}, f"{y0}_{m:02d}", c, limit)


def _search_api(http, api, sc, key, sp, d, force) -> None:
    base = {"taxonKey": key, "hasCoordinate": "true"}
    total = _count(http, api, base)
    cap = sc.get("max_records_per_species")
    limit = min(int(sc.get("partition_max", 9000)), 10_000)
    log.info("GBIF %s (taxonKey %s): %d records", sp, key, total)
    got = 0
    for params, label, n in partitions(http, api, base, limit):
        if cap and got >= cap:
            break
        out = d / f"{label}.parquet"
        if out.exists() and not force:
            got += n
            continue
        n = min(n, cap - got) if cap else n
        log.info("GBIF %s %s: %d", sp, label, n)
        df = _page_all(http, api, params, n, int(sc.get("page_size", 300)))
        df.to_parquet(out)
        got += len(df)
    write_manifest(d / "manifest.json", source="GBIF search API", taxonKey=key, query=base,
                   total_reported=total, total_fetched=got,
                   note="partitioned (year/month/lat-lon); records without a year are not fetched")


def _download_api(cfg, key, sp, d, creds, force) -> None:
    from pygbif import occurrences as occ
    out = d / "download.zip"
    if out.exists() and not force:
        return
    user, pwd, email = creds
    res = occ.download([f"taxonKey = {key}", "hasCoordinate = TRUE"], format="SIMPLE_CSV",
                       user=user, pwd=pwd, email=email)
    dl_key = res[0]
    log.info("GBIF download %s requested for %s", dl_key, sp)
    while True:
        meta = occ.download_meta(dl_key)
        if meta["status"] in ("SUCCEEDED", "FAILED", "KILLED", "CANCELLED"):
            break
        time.sleep(30)
    if meta["status"] != "SUCCEEDED":
        raise RuntimeError(f"GBIF download {dl_key} {meta['status']}")
    occ.download_get(dl_key, path=str(d))
    (d / f"{dl_key}.zip").replace(out)
    write_manifest(d / "manifest.json", source="GBIF download API", taxonKey=key,
                   download_key=dl_key, doi=meta.get("doi"), total_reported=meta.get("totalRecords"),
                   citation=f"GBIF.org ({meta.get('created', '')[:10]}) GBIF Occurrence Download "
                            f"https://doi.org/{meta.get('doi')}")


def _fetch_datasets(cfg, http, force) -> None:
    root = cfg.raw_dir("gbif")
    path = root / "datasets.json"
    known = json.loads(path.read_text(encoding="utf-8")) if path.exists() and not force else {}
    keys: set[str] = set()
    for p in root.glob("*/*.parquet"):
        keys |= set(pd.read_parquet(p, columns=["datasetKey"])["datasetKey"].dropna().unique())
    todo = sorted(keys - set(known))
    log.info("GBIF dataset metadata: %d new of %d", len(todo), len(keys))

    def one(k):
        try:
            r = http.get_json(f"{cfg.source('gbif')['api']}/dataset/{k}")
            return k, {"title": r.get("title"), "license": r.get("license"), "doi": r.get("doi"),
                       "citation": (r.get("citation") or {}).get("text"),
                       "publisher": r.get("publishingOrganizationKey"),
                       "identifiers": [i.get("identifier") for i in r.get("identifiers", [])],
                       "endpoints": [e.get("url") for e in r.get("endpoints", [])]}
        except Exception as e:
            log.warning("dataset %s: %s", k, e)
            return k, None

    with ThreadPoolExecutor(max_workers=2) as ex:
        for k, v in ex.map(one, todo):
            if v:
                known[k] = v
    path.write_text(json.dumps(known, indent=1), encoding="utf-8")
