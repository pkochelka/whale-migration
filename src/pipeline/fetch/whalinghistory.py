"""WhalingHistory.org: American Offshore Whaling Logbook (AOWL) + Voyages (AOWV) for vessel names."""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

VOYAGES_URL = "https://whalinghistory.org/aowv/AmericanOffshoreWhalingVoyages.zip"


def fetch(cfg, http, force: bool = False, name: str = "whalinghistory") -> None:
    sc = cfg.source("whalinghistory")
    d = cfg.raw_dir("whalinghistory")
    meta = {"license": sc["license"], "citation": sc["citation"].strip()}
    http.download(sc["url"], d / "AmericanOffshoreWhalingLogbookData.zip", force=force, meta=meta)
    # Voyages table supplies vessel name and rig for the `platform` column.
    http.download(VOYAGES_URL, d / "AmericanOffshoreWhalingVoyages.zip", force=force,
                  meta={"license": "CC-BY-4.0",
                        "citation": "American Offshore Whaling Voyages: a database, whalinghistory.org"})
