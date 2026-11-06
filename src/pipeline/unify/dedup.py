"""Cross-source de-duplication (GBIF / OBIS / OBIS-SEAMAP / ...).

The aggregators re-publish each other's datasets (SEAMAP's Happywhale archives appear in OBIS and
GBIF; many OBIS datasets are also in GBIF), so one sighting can appear up to three times.

Pass 1, exact: same species + same original occurrenceID + same UTC day, and within
`exact_max_km` of the group's best record. Applies within and across sources (it also removes a
dataset that was published twice).

Pass 2, fuzzy (only between *different* sources): same species, same day (day precision only),
within `fuzzy_max_km`, and individual IDs compatible (equal, or at least one missing). Pairs are
merged greedily by distance, and a cluster never absorbs two records from the same source, so
distinct records of one source are not chained together through a third.

In every cluster the most detailed record survives (`detail_score`); missing individual_id /
count / platform are back-filled from the duplicates, and `merged_sources` lists every source
the record was seen in.
"""
from __future__ import annotations

import logging

import duckdb
import numpy as np
import pandas as pd

from ..geo import haversine_km

log = logging.getLogger(__name__)

PRECISION_SCORE = {"day": 2.0, "month": 1.0, "year": 0.0}


def detail_score(df: pd.DataFrame, source_priority: list[str]) -> pd.Series:
    ts = df["timestamp_utc"]
    has_time = ts.notna() & ((ts.dt.hour != 0) | (ts.dt.minute != 0) | (ts.dt.second != 0))
    unc = pd.to_numeric(df.get("coord_uncertainty_m"), errors="coerce")
    rank = {s: i for i, s in enumerate(source_priority)}
    prio = df["source"].map(rank).fillna(len(rank)).astype(float)
    return (4.0 * df["individual_id"].notna()
            + df["date_precision"].map(PRECISION_SCORE).fillna(0.0)
            + 1.0 * has_time
            + 1.0 * df["count"].notna()
            + 1.0 * (unc <= 1000).fillna(False)
            + 0.5 * df["occurrence_id"].notna()
            + 0.5 * df["platform"].notna()
            - 0.01 * prio)  # tie-break: preferred source


def normalize_occurrence_id(s: pd.Series) -> pd.Series:
    s = s.astype("string").str.strip().str.lower()
    s = s.str.replace(r"^urn:catalog:", "", regex=True)
    return s.mask(s.isin(["", "nan", "none", "null", "na", "-"]))


def exact_pass(df: pd.DataFrame, max_km: float) -> pd.DataFrame:
    """Return DataFrame[dup_idx, keep_idx, distance_km] for exact-ID duplicates."""
    oid = normalize_occurrence_id(df["occurrence_id"])
    day = df["timestamp_utc"].dt.floor("D")
    cand = df[oid.notna()].assign(_oid=oid, _day=day)
    keys = ["species_scientific", "_oid", "_day"]
    size = cand.groupby(keys, dropna=False)["_score"].transform("size")
    cand = cand[size > 1]
    if cand.empty:
        return pd.DataFrame(columns=["dup_idx", "keep_idx", "distance_km"])
    anchor = cand.groupby(keys, dropna=False)["_score"].transform("idxmax")
    alat, alon = df.loc[anchor, "lat"].values, df.loc[anchor, "lon"].values
    aind = df.loc[anchor, "individual_id"].values
    dist = haversine_km(cand["lat"].values, cand["lon"].values, alat, alon)
    ind = cand["individual_id"].values
    compatible = pd.isna(ind) | pd.isna(aind) | (ind == aind)
    is_dup = (cand.index.values != anchor.values) & (dist <= max_km) & compatible
    return pd.DataFrame({"dup_idx": cand.index.values[is_dup], "keep_idx": anchor.values[is_dup],
                         "distance_km": dist[is_dup]})


def fuzzy_pairs(df: pd.DataFrame, max_km: float) -> pd.DataFrame:
    """Candidate cross-source pairs (i, j, distance_km) via a DuckDB band join."""
    t = df[df["date_precision"] == "day"][["source", "species_scientific", "lat", "lon", "individual_id"]].copy()
    t["day"] = df.loc[t.index, "timestamp_utc"].dt.tz_convert("UTC").dt.floor("D").dt.tz_localize(None)
    t["i"] = t.index.values
    if len(t) < 2:
        return pd.DataFrame(columns=["i", "j", "distance_km"])
    dlat = max_km / 111.0
    con = duckdb.connect()
    con.register("t", t)
    q = f"""
        WITH p AS (
          SELECT a.i AS i, b.i AS j, a.lat AS la, a.lon AS loa, b.lat AS lb, b.lon AS lob
          FROM t a JOIN t b
            ON a.species_scientific = b.species_scientific AND a.day = b.day
           AND a.source < b.source
           AND b.lat BETWEEN a.lat - {dlat} AND a.lat + {dlat}
          WHERE (a.individual_id IS NULL OR b.individual_id IS NULL OR a.individual_id = b.individual_id)
        )
        SELECT i, j,
          2 * 6371.0088 * asin(sqrt(
            pow(sin(radians(lb - la) / 2), 2) +
            cos(radians(la)) * cos(radians(lb)) * pow(sin(radians(lob - loa) / 2), 2))) AS distance_km
        FROM p
    """
    res = con.execute(q).df()
    con.close()
    return res[res["distance_km"] <= max_km].sort_values("distance_km").reset_index(drop=True)


def fuzzy_pass(df: pd.DataFrame, max_km: float) -> pd.DataFrame:
    pairs = fuzzy_pairs(df, max_km)
    if pairs.empty:
        return pd.DataFrame(columns=["dup_idx", "keep_idx", "distance_km"])
    parent: dict[int, int] = {}
    members: dict[int, set] = {}
    srcs: dict[int, set] = {}
    source = df["source"]

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    dist_of: dict[int, float] = {}
    for i, j, d in pairs[["i", "j", "distance_km"]].itertuples(index=False):
        i, j = int(i), int(j)
        ri, rj = find(i), find(j)
        if ri == rj:
            continue
        si = srcs.setdefault(ri, {source.at[i]})
        sj = srcs.setdefault(rj, {source.at[j]})
        if si & sj:  # would put two records of one source in a cluster
            continue
        parent[rj] = ri
        members.setdefault(ri, {ri}).update(members.pop(rj, {rj}))
        srcs[ri] = si | sj
        srcs.pop(rj, None)
        dist_of[i] = max(dist_of.get(i, 0), d)
        dist_of[j] = max(dist_of.get(j, 0), d)
    rows = []
    score = df["_score"]
    for root, mem in members.items():
        mem = list(mem)
        keep = max(mem, key=lambda k: score.at[k])
        rows += [(m, keep, dist_of.get(m, np.nan)) for m in mem if m != keep]
    return pd.DataFrame(rows, columns=["dup_idx", "keep_idx", "distance_km"])


def deduplicate(df: pd.DataFrame, cfg) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (deduplicated occurrences, duplicates log)."""
    dc = cfg["dedup"]
    df = df.reset_index(drop=True).copy()
    df["_score"] = detail_score(df, dc.get("source_priority", []))

    ex = exact_pass(df, float(dc.get("exact_max_km", 25)))
    ex["pass"] = "exact"
    remaining = df.drop(index=ex["dup_idx"])
    fz = fuzzy_pass(remaining, float(dc.get("fuzzy_max_km", 2.0)))
    fz["pass"] = "fuzzy"
    log.info("dedup: %d exact-ID duplicates, %d fuzzy duplicates", len(ex), len(fz))

    # Resolve chains: an exact duplicate's keeper may itself be absorbed in the fuzzy pass.
    keep_of = dict(zip(fz["dup_idx"], fz["keep_idx"]))
    ex["keep_idx"] = ex["keep_idx"].map(lambda k: keep_of.get(k, k))
    dups = pd.concat([ex, fz], ignore_index=True)

    out = df.drop(index=dups["dup_idx"]).copy()
    # Back-fill detail from duplicates, record provenance.
    g = df.loc[dups["dup_idx"]].assign(keep_idx=dups["keep_idx"].values)
    for col in ("individual_id", "count", "platform", "coord_uncertainty_m"):
        fill = g.dropna(subset=[col]).groupby("keep_idx")[col].first()
        if col == "coord_uncertainty_m":
            fill = g.dropna(subset=[col]).groupby("keep_idx")[col].min()
            cur = out.loc[out.index.intersection(fill.index), col]
            better = fill.reindex(cur.index)
            out.loc[cur.index, col] = np.where(cur.isna(), better, cur)
            continue
        idx = out.index.intersection(fill.index)
        out.loc[idx, col] = out.loc[idx, col].fillna(fill.reindex(idx))
    prov = pd.concat([pd.DataFrame({"k": out.index, "s": out["source"].values}),
                      pd.DataFrame({"k": g["keep_idx"].values, "s": g["source"].values})])
    merged = prov.drop_duplicates().sort_values("s").groupby("k")["s"].agg(",".join)
    out["merged_sources"] = merged.reindex(out.index).astype("string")
    out["n_duplicates"] = g.groupby("keep_idx").size().reindex(out.index).fillna(0).astype("int32")

    log_df = pd.DataFrame({
        "dropped_record_id": df.loc[dups["dup_idx"], "record_id"].values,
        "kept_record_id": df.loc[dups["keep_idx"], "record_id"].values,
        "dropped_source": df.loc[dups["dup_idx"], "source"].values,
        "kept_source": df.loc[dups["keep_idx"], "source"].values,
        "pass": dups["pass"].values, "distance_km": dups["distance_km"].astype(float).values,
    })
    return out.drop(columns="_score").reset_index(drop=True), log_df
