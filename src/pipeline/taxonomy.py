"""Taxonomic normalisation against WoRMS (via pyworms), with a persistent JSON cache.

Every raw name is classified as one of:
  target       accepted species (after synonym resolution / subspecies roll-up) is configured
  higher_taxon resolves only above species rank (e.g. "Cetacea", "Eubalaena", "whale")
  non_target   a valid species we were not asked for (e.g. Balaena mysticetus)
  unresolved   WoRMS has no match
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Callable

import pandas as pd

log = logging.getLogger(__name__)

SPECIES_RANKS = {"Species"}
BELOW_SPECIES = {"Subspecies", "Variety", "Forma", "Natio"}
# Vernacular / sloppy names that WoRMS will not match but are obviously higher taxa.
VAGUE_NAMES = {
    "whale": "Cetacea", "whales": "Cetacea", "cetacean": "Cetacea", "cetacea": "Cetacea",
    "unidentified whale": "Cetacea", "large whale": "Cetacea", "baleen whale": "Mysticeti",
    "mysticeti": "Mysticeti", "rorqual": "Balaenopteridae", "right whale": "Eubalaena",
}

Resolver = Callable[[str], dict | None]


def clean_name(name: str) -> str:
    """Strip authorship, qualifiers ("cf.", "sp.", "?") and extra whitespace."""
    n = re.sub(r"\(.*?\)|\b(cf|aff|sp|spp|ssp|var)\.?(?=\s|$)|\?", " ", str(name))
    n = re.sub(r"\s[A-Z][a-z]+,?\s*\d{4}.*$", "", n)          # "Linnaeus, 1758"
    n = re.sub(r"\s+", " ", n).strip()
    return n


def worms_resolver(name: str) -> dict | None:
    """Look a name up in WoRMS. Returns the accepted record or None."""
    import pyworms
    try:
        recs = pyworms.aphiaRecordsByName(name, like=False, marine_only=False) or []
    except Exception as e:  # network / API errors
        log.warning("WoRMS lookup failed for %r: %s", name, e)
        raise
    recs = [r for r in recs if r and r.get("valid_name")]
    if not recs:
        fuzzy = pyworms.aphiaRecordsByMatchNames([name], marine_only=False) or [[]]
        recs = [r for r in (fuzzy[0] or []) if r and r.get("valid_name")
                and r.get("match_type") in ("exact", "phonetic", "near_1")]
    if not recs:
        return None
    accepted = [r for r in recs if r.get("status") == "accepted"]
    if not accepted and len({r["valid_name"] for r in recs}) > 1:
        # Homonyms (same name, different authors) pointing at different accepted species, e.g.
        # "Balaena glacialis" -> E. glacialis or E. australis. Never guess; see config overrides.
        return {"ambiguous": sorted({r["valid_name"] for r in recs})}
    rec = accepted[0] if accepted else recs[0]
    valid_rank = rec.get("rank")
    if rec.get("valid_AphiaID") not in (None, rec.get("AphiaID")):
        valid = pyworms.aphiaRecordByAphiaID(rec["valid_AphiaID"]) or {}
        valid_rank = valid.get("rank", valid_rank)
    return {"aphia_id": rec.get("valid_AphiaID") or rec.get("AphiaID"),
            "valid_name": rec.get("valid_name"), "rank": valid_rank}


class Taxonomy:
    def __init__(self, cfg, cache_path: Path | None = None, resolver: Resolver | None = None):
        self.cfg = cfg
        self.targets = {s["scientific"]: s for s in cfg.species}
        self.policy = cfg["taxonomy"].get("higher_taxa", "drop")
        self.rollup = cfg["taxonomy"].get("rollup_subspecies", True)
        self.overrides = cfg["taxonomy"].get("overrides") or {}
        self.cache_path = cache_path
        self.resolver = resolver or worms_resolver
        self.cache: dict[str, dict | None] = {}
        if cache_path and Path(cache_path).exists():
            self.cache = json.loads(Path(cache_path).read_text(encoding="utf-8"))
        self.offline = False

    def save(self) -> None:
        if self.cache_path:
            Path(self.cache_path).parent.mkdir(parents=True, exist_ok=True)
            Path(self.cache_path).write_text(json.dumps(self.cache, indent=1, sort_keys=True), encoding="utf-8")

    def _lookup(self, name: str) -> dict | None:
        if name in self.cache:
            return self.cache[name]
        if name in self.targets:   # configured names never need the network
            t = self.targets[name]
            rec = {"aphia_id": t.get("aphia_id"), "valid_name": name, "rank": "Species"}
            if not self.offline:
                try:
                    live = self.resolver(name)
                    if live and live.get("valid_name") == name:
                        rec = live
                except Exception:
                    self.offline = True
            self.cache[name] = rec
            return rec
        if self.offline:
            return {"offline": True}
        try:
            rec = self.resolver(name)
        except Exception:
            self.offline = True
            log.warning("WoRMS unreachable; remaining names classified 'unresolved' (not cached)")
            return {"offline": True}
        self.cache[name] = rec
        return rec

    def classify(self, raw_name: str) -> dict:
        name = clean_name(raw_name) if raw_name and not pd.isna(raw_name) else ""
        if not name:
            return {"status": "unresolved", "species_scientific": None, "worms_aphia_id": None}
        vague = VAGUE_NAMES.get(name.lower())
        if vague:
            rec = self._lookup(vague) or {}
            return {"status": "higher_taxon", "species_scientific": rec.get("valid_name", vague),
                    "worms_aphia_id": rec.get("aphia_id")}
        override = self.overrides.get(name)
        if override:
            name = override
        rec = self._lookup(name)
        if rec and rec.get("ambiguous"):
            log.warning("ambiguous WoRMS name %r -> %s (add to taxonomy.overrides)", name, rec["ambiguous"])
            return {"status": "unresolved", "species_scientific": None, "worms_aphia_id": None}
        if not rec or rec.get("offline"):
            parts = name.split()
            if self.rollup and len(parts) >= 3 and not (rec or {}).get("offline"):
                # Trinomial WoRMS does not list (e.g. "Megaptera novaeangliae kuzira"): fall back
                # to its binomial when that is a valid species.
                sp = self.classify(" ".join(parts[:2]))
                if sp["status"] in ("target", "non_target"):
                    return {**sp, "rolled_up": True}
            return {"status": "unresolved", "species_scientific": None, "worms_aphia_id": None}
        valid, rank, aphia = rec.get("valid_name"), rec.get("rank"), rec.get("aphia_id")
        if rank in BELOW_SPECIES and self.rollup and valid:
            sp = " ".join(valid.split()[:2])
            sp_rec = self._lookup(sp) or {}
            valid, rank, aphia = sp, "Species", sp_rec.get("aphia_id", aphia)
        if rank in SPECIES_RANKS or rank in BELOW_SPECIES:
            status = "target" if valid in self.targets else "non_target"
        else:
            status = "higher_taxon"
        return {"status": status, "species_scientific": valid, "worms_aphia_id": aphia}

    def classify_series(self, names: pd.Series) -> pd.DataFrame:
        uniq = pd.Series(names.dropna().unique())
        table = {n: self.classify(n) for n in uniq}
        self.save()
        res = pd.DataFrame([table.get(n, {"status": "unresolved", "species_scientific": None,
                                          "worms_aphia_id": None}) if not pd.isna(n) else
                            {"status": "unresolved", "species_scientific": None, "worms_aphia_id": None}
                            for n in names], index=names.index)
        res["species_common"] = res["species_scientific"].map(
            lambda s: self.targets.get(s, {}).get("common") if isinstance(s, str) else None)
        return res

    def apply(self, df: pd.DataFrame, name_col: str, report) -> pd.DataFrame:
        """Classify `name_col`, attach taxonomy columns, drop per policy and log counts."""
        tx = self.classify_series(df[name_col])
        df = df.copy()
        df["species_scientific"] = tx["species_scientific"].values
        df["worms_aphia_id"] = tx["worms_aphia_id"].values
        df["species_common"] = tx["species_common"].values
        status = pd.Series(tx["status"].values, index=df.index)
        for st in ("unresolved", "non_target"):
            sub = df.loc[status == st, name_col].value_counts()
            if len(sub):
                report.note(f"{st} names (top): " + ", ".join(f"{k} ({v})" for k, v in sub.head(8).items()))
        if self.policy == "drop":
            sub = df.loc[status == "higher_taxon", name_col].value_counts()
            if len(sub):
                report.note("higher-taxon names dropped: " + ", ".join(f"{k} ({v})" for k, v in sub.head(8).items()))
            df = report.drop(df, status == "higher_taxon", "taxon_above_species")
        else:
            df = report.flag(df, status == "higher_taxon", "higher_taxon")
        df = report.drop(df, status.reindex(df.index) == "non_target", "non_target_species")
        df = report.drop(df, status.reindex(df.index) == "unresolved", "taxon_unresolved")
        return df
