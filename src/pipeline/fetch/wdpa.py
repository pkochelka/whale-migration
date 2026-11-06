"""Protected Planet WDPA/WD-OECM marine protected areas (monthly public release).

License: UNEP-WCMC terms allow non-commercial use only and forbid redistributing the data, so the
raw zip and derived files stay in data/ (git-ignored) and are never published.
"""
from __future__ import annotations

import logging
from datetime import date

import requests

log = logging.getLogger(__name__)


def candidate_months(today: date, n: int = 4) -> list[str]:
    out, y, m = [], today.year, today.month
    for _ in range(n):
        out.append(date(y, m, 1).strftime("%b%Y"))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return out


def fetch(cfg, http, force: bool = False, name: str = "mpa") -> None:
    sc = cfg.source("mpa")
    d = cfg.raw_dir("wdpa")
    existing = sorted(d.glob("WDPA_*_marine_shp.zip"))
    if existing and not force:
        log.info("cached: %s", existing[-1])
        return
    for mon in candidate_months(date.today()):
        url = sc["url"].format(MonYYYY=mon)
        try:
            http.session.head(url, timeout=30).raise_for_status()
        except requests.HTTPError:
            continue
        http.download(url, d / url.split("/")[-1], force=force,
                      meta={"license": sc["license"], "citation": sc["citation"].strip(), "release": mon})
        return
    raise RuntimeError("no WDPA marine release found for the last few months")
