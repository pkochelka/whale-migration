"""Movebank public tracking studies via the direct-read REST API.

Requires a (free) Movebank account: credentials are read from MOVEBANK_USERNAME and
MOVEBANK_PASSWORD. Without them this source is stubbed: a STUB.json is written and the pipeline
continues. Only studies where the account has download access (`i_have_download_access`) and that
do not require accepting additional license terms are fetched; everything skipped is logged.
"""
from __future__ import annotations

import io
import logging
import os

import pandas as pd

from ..cache import write_text
from ..http import write_manifest

log = logging.getLogger(__name__)

EVENT_ATTRS = [
    "event_id", "timestamp", "location_lat", "location_long", "individual_local_identifier",
    "tag_local_identifier", "individual_taxon_canonical_name", "sensor_type_id", "argos_lc",
    "gps_hdop", "visible",
]


def credentials() -> tuple[str, str] | None:
    u, p = os.environ.get("MOVEBANK_USERNAME"), os.environ.get("MOVEBANK_PASSWORD")
    return (u, p) if u and p else None


def is_license_prompt(text: str) -> bool:
    """direct-read returns an HTML/text license page instead of CSV when terms must be accepted."""
    head = text[:2000].lower()
    return "license" in head and ("<html" in head or "terms" in head) and "timestamp" not in head


def fetch(cfg, http, force: bool = False, name: str = "movebank") -> None:
    sc = cfg.source("movebank")
    d = cfg.raw_dir("movebank")
    creds = credentials()
    if not creds:
        msg = "MOVEBANK_USERNAME / MOVEBANK_PASSWORD not set; Movebank skipped (stub)."
        log.warning(msg)
        write_manifest(d / "STUB.json", status="skipped", reason=msg)
        return
    (d / "STUB.json").unlink(missing_ok=True)
    api = sc["api"]
    r = http.get(api, params={"entity_type": "study"}, auth=creds)
    studies = pd.read_csv(io.StringIO(r.text), dtype=str)
    studies.to_csv(d / "studies.csv", index=False)
    targets = [s.lower() for s in cfg.species_names]
    tax = studies.get("taxon_ids", pd.Series("", index=studies.index)).fillna("").str.lower()
    relevant = studies[tax.apply(lambda t: any(s in t for s in targets))]
    log.info("Movebank: %d studies mention target taxa", len(relevant))
    fetched, skipped = [], []
    for _, s in relevant.iterrows():
        sid = s["id"]
        if str(s.get("i_have_download_access", "")).lower() != "true":
            skipped.append({"id": sid, "name": s.get("name"), "reason": "no download access"})
            continue
        out = d / f"study_{sid}_events.csv"
        if out.exists() and not force:
            fetched.append(sid)
            continue
        ev = http.get(api, params={"entity_type": "event", "study_id": sid,
                                   "attributes": ",".join(EVENT_ATTRS)}, auth=creds)
        if is_license_prompt(ev.text):
            skipped.append({"id": sid, "name": s.get("name"), "reason": "license terms must be accepted"})
            continue
        write_text(out, ev.text)
        fetched.append(sid)
    write_manifest(d / "manifest.json", source="Movebank", fetched=fetched, skipped=skipped,
                   studies_meta="studies.csv")
    for s in skipped:
        log.info("Movebank skipped study %s (%s): %s", s["id"], s["name"], s["reason"])
