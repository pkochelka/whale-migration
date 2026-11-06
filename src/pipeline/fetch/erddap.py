"""Monthly gridded SST and chlorophyll-a from NOAA ERDDAP via erddapy, one NetCDF per year.

Requests are clamped to the dataset's advertised time coverage (ERDDAP answers 404 for
constraints outside it). ERDDAP also answers 404 "Currently unknown datasetID" while a dataset is
being reloaded; those years are retried with backoff and otherwise left for the next run.
"""
from __future__ import annotations

import io
import logging
import time
from datetime import date

import pandas as pd
import requests

log = logging.getLogger(__name__)


def time_coverage(http, server: str, dataset_id: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    info = pd.read_csv(io.StringIO(http.get(f"{server}/info/{dataset_id}/index.csv").text))
    attr = info[(info["Row Type"] == "attribute") & (info["Variable Name"] == "NC_GLOBAL")]
    get = dict(zip(attr["Attribute Name"], attr["Value"]))
    return (pd.Timestamp(get["time_coverage_start"]).tz_localize(None),
            pd.Timestamp(get["time_coverage_end"]).tz_localize(None))


def build_url(server: str, dataset_id: str, variable: str, start: str, end: str, stride: int) -> str:
    from erddapy import ERDDAP
    e = ERDDAP(server=server, protocol="griddap")
    e.dataset_id = dataset_id
    e.griddap_initialize()
    e.variables = [variable]
    e.constraints["time>="] = start
    e.constraints["time<="] = end
    for ax in ("latitude", "longitude"):
        if f"{ax}_step" in e.constraints:
            e.constraints[f"{ax}_step"] = stride
    return e.get_download_url(response="nc")


def fetch(cfg, http, force: bool = False, name: str = "sst") -> None:
    sc = cfg.source(name)
    ds = sc["dataset_id"]
    d = cfg.raw_dir(f"erddap_{ds}")
    cov0, cov1 = time_coverage(http, sc["server"], ds)
    start = max(pd.Timestamp(sc["start"]), cov0)
    end = min(pd.Timestamp(sc["end"]) if sc.get("end") else pd.Timestamp(date.today()), cov1)
    log.info("ERDDAP %s: coverage %s..%s, requesting %s..%s", ds, cov0.date(), cov1.date(), start.date(), end.date())
    missing = []
    for year in range(start.year, end.year + 1):
        out = d / f"{ds}_{year}.nc"
        last = year == end.year  # the final year may still grow: always refresh it
        if out.exists() and not force and not last:
            continue
        y0 = max(start, pd.Timestamp(f"{year}-01-01")).strftime("%Y-%m-%dT%H:%M:%SZ")
        y1 = min(end, pd.Timestamp(f"{year}-12-31T23:59:59")).strftime("%Y-%m-%dT%H:%M:%SZ")
        url = build_url(sc["server"], ds, sc["variable"], y0, y1, int(sc.get("stride", 1)))
        meta = {"dataset_id": ds, "license": sc.get("license"), "citation": sc.get("citation", "").strip(),
                "year": year}
        for attempt in range(4):
            try:
                http.download(url, out, force=True, meta=meta)
                break
            except requests.HTTPError as e:
                if e.response is not None and e.response.status_code == 404 and attempt < 3:
                    log.warning("ERDDAP %s %s: 404 (dataset reloading?), retry in %ss", ds, year, 30 * (attempt + 1))
                    time.sleep(30 * (attempt + 1))
                    continue
                log.warning("ERDDAP %s %s: %s", ds, year, e)
                missing.append(year)
                break
    if missing:
        log.warning("ERDDAP %s: years still missing (re-run fetch later): %s", ds, missing)
