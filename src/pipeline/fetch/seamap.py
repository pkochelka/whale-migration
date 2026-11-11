"""OBIS-SEAMAP datasets (incl. Happywhale photo-ID) as Darwin Core Archives from Duke's IPT.

The SEAMAP web portal is a JavaScript app with no stable download URLs, but every SEAMAP dataset
is published through the GBIF IPT at ipt.env.duke.edu, which exposes a JSON inventory and
DwC-A archives. Archives are cached as <resource>_<lastPublished>.zip, so a republished dataset
is fetched again while unchanged ones are not.
"""
from __future__ import annotations

import json
import logging

from ..http import write_manifest
from ..parallel import pmap

log = logging.getLogger(__name__)


def select_resources(inventory: list[dict], sc: dict) -> list[dict]:
    prefix = sc.get("title_prefix", "")
    kws = [k.lower() for k in sc.get("title_keywords", [])]
    extra = set(sc.get("extra_dataset_ids") or [])
    out = []
    for r in inventory:
        rid = r.get("dwca", "").split("r=")[-1]
        title = r.get("title", "")
        t = title.lower()
        hit = title.startswith(prefix) and any(k in t for k in kws) and "dolphin" not in t
        if (hit or rid in extra) and r.get("records", 0) > 0:
            out.append({**r, "resource": rid})
    return out


def fetch(cfg, http, force: bool = False, name: str = "seamap") -> None:
    sc = cfg.source("seamap")
    d = cfg.raw_dir("seamap")
    inv = http.get_json(f"{sc['ipt']}/inventory/dataset")["registeredResources"]
    (d / "inventory.json").write_text(json.dumps(inv, indent=1), encoding="utf-8")
    sel = select_resources(inv, sc)
    log.info("SEAMAP/IPT: %d of %d resources selected", len(sel), len(inv))

    def one(r):
        dest = d / f"{r['resource']}_{r.get('lastPublished', 'na')}.zip"
        http.download(r["dwca"], dest, force=force,
                      meta={"title": r["title"], "gbifKey": r.get("gbifKey"), "records": r.get("records"),
                            "eml": r.get("eml"), "lastPublished": r.get("lastPublished")})

    pmap(one, sel, int(sc.get("workers", 1)))
    write_manifest(d / "manifest.json", source="OBIS-SEAMAP via Duke IPT", ipt=sc["ipt"],
                   selected=[{k: r.get(k) for k in ("resource", "title", "records", "gbifKey", "lastPublished")}
                             for r in sel])
