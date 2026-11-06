"""Social network of photo-identified whales (Happywhale encounters).

Co-sighting definition (strict, no distance guessing): Happywhale stamps every whale identified in
one submission with the same timestamp (to the second) and position. Two individuals of the same
species with an identical timestamp and position (5 decimals) were photographed together.

* Groups larger than `max_group` are excluded: humpback groups (competitive groups, bubble-net
  feeding) rarely exceed ~20, so bigger "groups" are whole-trip uploads, not associations.
* Association strength is the half-weight index (Cairns & Schwager 1987), the standard metric in
  cetacean social analysis:  HWI = X / (X + 0.5 * (Ya + Yb))  where X = groups with both,
  Ya / Yb = groups with only A / only B.
* The network keeps only *repeated* associations (X >= `min_together`): a single shared photo can
  be chance proximity; meeting again is not.

Output: results/social_network.json (nodes with layout, edges, summaries).
"""
from __future__ import annotations

import json
import logging

import duckdb
import networkx as nx
import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

REGIONS = [  # (name, lat0, lat1, lon0, lon1) - first match wins; humpback feeding & breeding areas
    ("Hawaii", 15, 26, -162, -150),
    ("SE Alaska & BC", 47, 61, -142, -122),
    ("Gulf of Alaska & Bering", 50, 66, -180, -142),
    ("California – Mexico", 15, 47, -125, -100),
    ("Central America", 0, 15, -100, -75),
    ("NW Atlantic", 35, 70, -80, -40),
    ("Southern Hemisphere", -90, 0, -180, 180),
]
OTHER = "Other North Hemisphere"


def region_of(lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    out = np.full(len(lat), OTHER, dtype=object)
    done = np.zeros(len(lat), bool)
    for name, a, b, c, d in REGIONS:
        m = ~done & (lat >= a) & (lat < b) & (lon >= c) & (lon < d)
        out[m] = name
        done |= m
    return out


def load_groups(db: str, max_group: int) -> tuple[pd.DataFrame, dict]:
    con = duckdb.connect(db, read_only=True)
    g = con.execute("""
        SELECT species_scientific AS sp, timestamp_utc AS ts, round(lat, 5) AS lat, round(lon, 5) AS lon,
               list(DISTINCT individual_id) AS inds, count(DISTINCT individual_id) AS n
        FROM occurrences
        WHERE individual_id LIKE 'happywhale:%'
        GROUP BY ALL
    """).df()
    con.close()
    stats = {"groups_total": int(len(g)), "groups_multi": int((g.n > 1).sum()),
             "groups_excluded_too_large": int((g.n > max_group).sum()),
             "sightings_in_excluded_groups": int(g.loc[g.n > max_group, "n"].sum())}
    return g[g.n <= max_group].reset_index(drop=True), stats


def pair_table(groups: pd.DataFrame) -> pd.DataFrame:
    rows = []
    multi = groups[groups.n > 1]
    for sp, inds, ts in zip(multi.sp, multi.inds, multi.ts):
        s = sorted(inds)
        y = ts.year
        for i in range(len(s)):
            for j in range(i + 1, len(s)):
                rows.append((sp, s[i], s[j], y))
    p = pd.DataFrame(rows, columns=["sp", "a", "b", "year"])
    return (p.groupby(["sp", "a", "b"]).agg(together=("year", "size"), years=("year", "nunique"),
                                            first=("year", "min"), last=("year", "max")).reset_index())


def build(cfg, species: str = "Megaptera novaeangliae", max_group: int = 20, min_together: int = 2) -> dict:
    db = str(cfg.processed / "whales.duckdb")
    groups, stats = load_groups(db, max_group)
    # Sightings per individual (sampling periods = groups) and home region (modal).
    ex = groups[["sp", "inds", "lat", "lon", "ts"]].explode("inds").rename(columns={"inds": "ind"})
    ex["region"] = region_of(ex["lat"].to_numpy(float), ex["lon"].to_numpy(float))
    n_ind = ex.groupby("ind").size()
    home = ex.groupby("ind")["region"].agg(lambda s: s.value_counts().index[0])
    pairs = pair_table(groups)
    pairs["hwi"] = pairs["together"] / (pairs["together"] + 0.5 * (
        (pairs["a"].map(n_ind) - pairs["together"]) + (pairs["b"].map(n_ind) - pairs["together"])))

    # Species comparison: how social does each species look in this data?
    per_sp = []
    for sp, gsp in groups.groupby("sp"):
        psp = pairs[pairs.sp == sp]
        per_sp.append({"species": sp, "groups": int(len(gsp)), "share_in_groups": float((gsp.n > 1).mean()),
                       "individuals": int(ex.loc[ex.sp == sp, "ind"].nunique()),
                       "pairs": int(len(psp)), "repeat_pairs": int((psp.together >= min_together).sum()),
                       "multi_year_pairs": int((psp.years >= 2).sum())})
    per_sp.sort(key=lambda r: -r["groups"])

    # Group-size distribution for the focal species.
    gs = groups[groups.sp == species].n.clip(upper=max_group).value_counts().sort_index()

    # Network of repeated associations for the focal species.
    e = pairs[(pairs.sp == species) & (pairs.together >= min_together)].copy()
    G = nx.Graph()
    for r in e.itertuples(index=False):
        G.add_edge(r.a, r.b, weight=float(r.hwi), together=int(r.together), years=int(r.years))
    comms = nx.community.louvain_communities(G, weight="weight", seed=7)
    comm_of = {n: i for i, c in enumerate(sorted(comms, key=len, reverse=True)) for n in c}
    comps = sorted(nx.connected_components(G), key=len, reverse=True)
    log.info("network: %d nodes, %d edges, %d components, %d communities",
             G.number_of_nodes(), G.number_of_edges(), len(comps), len(comms))

    # Layout: each connected component laid out on its own, then packed in a spiral by size.
    pos = {}
    cursor = 0
    for k, comp in enumerate(comps):
        sub = G.subgraph(comp)
        p = nx.spring_layout(sub, weight="weight", seed=k, iterations=80 if len(comp) < 400 else 40)
        arr = np.array(list(p.values()))
        arr = arr - arr.mean(0)
        scale = max(np.abs(arr).max(), 1e-9)
        radius = np.sqrt(len(comp))
        arr = arr / scale * radius
        ang, dist = cursor * 2.39996, 0 if k == 0 else 6 * np.sqrt(cursor + 4)
        cursor += max(1, len(comp) / 3)
        off = np.array([np.cos(ang), np.sin(ang)]) * dist
        for node, xy in zip(p.keys(), arr + off):
            pos[node] = xy
    xy = np.array(list(pos.values()))
    lo, hi = xy.min(0), xy.max(0)

    def norm(v):
        return ((v - lo) / (hi - lo)).round(4).tolist()

    first_seen = ex.groupby("ind")["ts"].min().dt.year
    last_seen = ex.groupby("ind")["ts"].max().dt.year
    nodes = [{"id": n.split(":")[1], "x": norm(pos[n])[0], "y": norm(pos[n])[1], "deg": G.degree(n),
              "strength": round(G.degree(n, weight="weight"), 3), "sightings": int(n_ind[n]),
              "region": home[n], "community": comm_of[n], "first": int(first_seen[n]), "last": int(last_seen[n])}
             for n in G.nodes]
    idx = {n: i for i, n in enumerate(G.nodes)}
    edges = [[idx[a], idx[b], round(d["weight"], 3), d["together"], d["years"]] for a, b, d in G.edges(data=True)]

    region_counts = pd.Series([n["region"] for n in nodes]).value_counts()
    top_pairs = (e.sort_values(["together", "hwi"], ascending=False).head(12)
                 .assign(a=lambda d: d.a.str.split(":").str[1], b=lambda d: d.b.str.split(":").str[1])
                 [["a", "b", "together", "years", "first", "last", "hwi"]].round({"hwi": 2}))
    top_pairs["region"] = [home[f"happywhale:{a}"] for a in top_pairs.a]
    return {
        "species": species, "max_group": max_group, "min_together": min_together,
        "stats": {**stats, "nodes": G.number_of_nodes(), "edges": G.number_of_edges(),
                  "components": len(comps), "largest_component": len(comps[0]) if comps else 0,
                  "communities": len(comms), "pairs_all": int((pairs.sp == species).sum()),
                  "pairs_multi_year": int(((pairs.sp == species) & (pairs.years >= 2)).sum()),
                  "modularity": round(nx.community.modularity(G, comms, weight="weight"), 3)},
        "per_species": per_sp,
        "group_sizes": [[int(k), int(v)] for k, v in gs.items()],
        "regions": [[k, int(v)] for k, v in region_counts.items()],
        "together_hist": [[int(k), int(v)] for k, v in e.together.clip(upper=8).value_counts().sort_index().items()],
        "top_pairs": top_pairs.to_dict("records"),
        "nodes": nodes, "edges": edges,
    }


def run(cfg) -> None:
    out = cfg.root / "results"
    out.mkdir(parents=True, exist_ok=True)
    res = build(cfg)
    (out / "social_network.json").write_text(json.dumps(res, separators=(",", ":"), default=str), encoding="utf-8")
    log.info("wrote %s", out / "social_network.json")
