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

from ..cache import fresh
from ..parallel import pmap

log = logging.getLogger(__name__)


def time_coverage(http, server: str, dataset_id: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    info = pd.read_csv(io.StringIO(http.get(f"{server}/info/{dataset_id}/index.csv").text))
    attr = info[(info["Row Type"] == "attribute") & (info["Variable Name"] == "NC_GLOBAL")]
    get = dict(zip(attr["Attribute Name"], attr["Value"]))
    return (pd.Timestamp(get["time_coverage_start"]).tz_localize(None),
            pd.Timestamp(get["time_coverage_end"]).tz_localize(None))


def build_urls(server: str, dataset_id: str, variable: str, ranges: list[tuple[str, str]],
               stride: int) -> list[str]:
    """One griddap .nc URL per (start, end) range; the dataset metadata is read only once."""
    from erddapy import ERDDAP
    e = ERDDAP(server=server, protocol="griddap")
    e.dataset_id = dataset_id
    e.griddap_initialize()
    e.variables = [variable]
    for ax in ("latitude", "longitude"):
        if f"{ax}_step" in e.constraints:
            e.constraints[f"{ax}_step"] = stride
    urls = []
    for start, end in ranges:
        e.constraints["time>="] = start
        e.constraints["time<="] = end
        urls.append(e.get_download_url(response="nc"))
    return urls


def fetch(cfg, http, force: bool = False, name: str = "sst") -> None:
    sc = cfg.source(name)
    ds = sc["dataset_id"]
    d = cfg.raw_dir(f"erddap_{ds}")
    cov0, cov1 = time_coverage(http, sc["server"], ds)
    start = max(pd.Timestamp(sc["start"]), cov0)
    end = min(pd.Timestamp(sc["end"]) if sc.get("end") else pd.Timestamp(date.today()), cov1)
    log.info("ERDDAP %s: coverage %s..%s, requesting %s..%s", ds, cov0.date(), cov1.date(), start.date(), end.date())
    # The final year may still grow: refresh it, unless it was fetched within the last day.
    years = [y for y in range(start.year, end.year + 1)
             if force or not (d / f"{ds}_{y}.nc").exists()
             or (y == end.year and not fresh(d / f"{ds}_{y}.nc", 86400))]
    if not years:
        return
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    ranges = [(max(start, pd.Timestamp(f"{y}-01-01")).strftime(fmt),
               min(end, pd.Timestamp(f"{y}-12-31T23:59:59")).strftime(fmt)) for y in years]
    urls = build_urls(sc["server"], ds, sc["variable"], ranges, int(sc.get("stride", 1)))
    meta = {"dataset_id": ds, "license": sc.get("license"), "citation": sc.get("citation", "").strip()}

    def one(job: tuple[int, str]) -> int | None:
        """Download one year; return the year if it is still missing."""
        year, url = job
        for attempt in range(4):
            try:
                http.download(url, d / f"{ds}_{year}.nc", force=True, meta={**meta, "year": year})
                return None
            except requests.HTTPError as e:
                if e.response is not None and e.response.status_code == 404 and attempt < 3:
                    log.warning("ERDDAP %s %s: 404 (dataset reloading?), retry in %ss", ds, year, 30 * (attempt + 1))
                    time.sleep(30 * (attempt + 1))
                    continue
                log.warning("ERDDAP %s %s: %s", ds, year, e)
                return year

    # Each year is a separate request that the server renders on the fly; overlap a few.
    missing = [y for y in pmap(one, zip(years, urls), int(sc.get("workers", 1))) if y]
    if missing:
        log.warning("ERDDAP %s: years still missing (re-run fetch later): %s", ds, missing)
