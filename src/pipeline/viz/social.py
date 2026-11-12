"""Figures for the humpback social network (input: analysis/social_network.json)."""
from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.collections import LineCollection

from .style import BLUE, GREEN, GREY, ORANGE, SHORT_NAME, save

# Home regions are folded into three colours + grey (more colours become hard to tell apart).
REGION_GROUPS = [
    ("Alaska & British Columbia", ("SE Alaska & BC", "Gulf of Alaska & Bering"), BLUE),
    ("California – Mexico", ("California – Mexico", "Central America"), ORANGE),
    ("Hawaii", ("Hawaii",), GREEN),
    ("Elsewhere", (), GREY),
]


def region_group(region: str) -> int:
    for i, (_, regions, _) in enumerate(REGION_GROUPS[:-1]):
        if region in regions:
            return i
    return len(REGION_GROUPS) - 1


def grid_layout(n_nodes: int, edges: list, aspect: float = 16 / 9, pad: float = 1.7) -> np.ndarray:
    """Lay out each connected group on its own tile and pack the tiles in rows, largest first.

    Inside a group: spring layout, rotated so its long axis is horizontal, scaled so that its area
    grows with the number of whales. Tiles are packed left to right into rows of a fixed width,
    chosen so the whole picture fills a canvas of the given aspect ratio. Position carries no
    geographic meaning; only lines do.
    """
    import networkx as nx

    G = nx.Graph()
    G.add_nodes_from(range(n_nodes))
    G.add_weighted_edges_from((a, b, w) for a, b, w, *_ in edges)
    comps = sorted(nx.connected_components(G), key=lambda c: (-len(c), min(c)))

    tiles = []                                       # (nodes, local xy, width, height)
    for k, comp in enumerate(comps):
        comp = sorted(comp)
        if len(comp) == 2:
            p = np.array([[-0.5, 0.0], [0.5, 0.0]])
        else:
            pos = nx.kamada_kawai_layout(G.subgraph(comp)) if len(comp) <= 60 else \
                nx.spring_layout(G.subgraph(comp), weight="weight", seed=k, iterations=200)
            p = np.array([pos[n] for n in comp])
            p -= p.mean(0)
            _, _, vt = np.linalg.svd(p, full_matrices=False)   # rotate: long axis horizontal
            p = p @ vt.T
            # Scale so the typical distance between linked whales is ~1 unit in every group.
            idx = {n: i for i, n in enumerate(comp)}
            el = [np.linalg.norm(p[idx[a]] - p[idx[b]]) for a, b in G.subgraph(comp).edges()]
            p /= max(np.median(el), 1e-9)
        p -= p.min(0)
        w, h = p.max(0)
        tiles.append((comp, p, w, h))

    # Skyline packing: the canvas width is split into narrow columns; each tile (largest first)
    # drops into the horizontal position where it can sit highest, so small groups fill the gaps
    # beside and below large ones. The width is chosen by trial to match the target aspect.
    res = 0.25

    def pack(canvas_w):
        ncols = int(np.ceil(canvas_w / res))
        sky = np.zeros(ncols)                          # depth already used from the top, per column
        placed = []
        for comp, p, w, h in tiles:
            wc = min(ncols, int(np.ceil((w + pad) / res)))
            tops = np.lib.stride_tricks.sliding_window_view(sky, wc).max(1)
            x = int(np.argmin(tops))                   # first (leftmost) of the highest spots
            top = tops[x]
            placed.append((comp, p + [x * res, -top - h]))
            sky[x:x + wc] = top + h + 0.8 * pad
        used_w = max(q[:, 0].max() for _, q in placed)
        height = max(-q[:, 1].min() for _, q in placed)
        return placed, used_w, height

    widest = max(t[2] for t in tiles) + pad
    best = None
    for canvas_w in np.linspace(widest, widest * 6, 80):
        placed, used_w, height = pack(canvas_w)
        score = abs(np.log((used_w / height) / aspect))
        if best is None or score < best[0]:
            best = (score, placed)

    xy = np.zeros((n_nodes, 2))
    for comp, q in best[1]:
        xy[comp] = q
    return xy


def plot_network(d: dict, out: Path) -> Path:
    """Each dot is a whale, each line a pair photographed together at least twice."""
    nodes, edges = d["nodes"], d["edges"]           # edge = [node_a, node_b, hwi, times_together, years]
    fig = plt.figure(figsize=(16, 9))
    # The axes span the full figure width, with the title above and the legend below.
    box = [0.005, 0.05, 0.99, 0.86]
    ax = fig.add_axes(box)
    xy = grid_layout(len(nodes), edges, aspect=(box[2] * 16) / (box[3] * 9))
    group = np.array([region_group(n["region"]) for n in nodes])
    deg = np.array([n["deg"] for n in nodes])
    size = 22 + 10 * np.sqrt(deg)

    lines = [(xy[a], xy[b]) for a, b, *_ in edges]
    widths = [1.0 + min(3.0, (together - 2) * 0.6) for _, _, _, together, _ in edges]
    ax.add_collection(LineCollection(lines, colors="#3d454c", linewidths=widths, alpha=0.55, zorder=1,
                                     capstyle="round"))
    for k, (label, _, color) in enumerate(REGION_GROUPS):
        m = group == k
        ax.scatter(xy[m, 0], xy[m, 1], s=size[m], c=color, edgecolors="white", linewidths=0.5, zorder=2,
                   label=f"{label} ({m.sum():,})")
    s = d["stats"]
    ax.set_title(f"Humpback whales photographed together at least twice: {s['nodes']:,} whales, "
                 f"{s['edges']:,} repeated pairs, {s['components']:,} separate groups\n"
                 "Colour = home region (where the whale is photographed most often). Line width = times "
                 "seen together. Layout is not geographic.", fontsize=14)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, 0.0), ncol=len(REGION_GROUPS), frameon=False,
              markerscale=1.6, fontsize=13, handletextpad=0.2, columnspacing=1.8)
    ax.margins(0.005)
    ax.set_aspect("equal", adjustable="datalim")   # keep the axes box full-size; pad the data range instead
    ax.autoscale_view()
    ax.set_axis_off()
    return save(fig, out / "social_network.png")


def plot_summary(d: dict, out: Path) -> Path:
    """Three bar charts: group share per species, group sizes, repeat meetings."""
    fig, (a1, a2, a3) = plt.subplots(1, 3, figsize=(13, 3.8))

    species = [r for r in d["per_species"] if r["groups"] >= 100][::-1]   # skip species with too few photos
    share = [100 * r["share_in_groups"] for r in species]
    a1.barh([SHORT_NAME[r["species"]] for r in species], share, color=BLUE, height=0.6)
    for i, (r, v) in enumerate(zip(species, share)):
        a1.text(v + 0.15, i, f"{v:.1f}%  (n={r['groups']:,})", va="center", fontsize=8)
    a1.set(xlabel="% of encounters with 2+ identified whales", xlim=(0, max(share) * 1.6),
           title="How often are whales photographed in groups?")
    a1.grid(axis="y", visible=False)

    sizes = [(k, v) for k, v in d["group_sizes"] if k >= 2]
    a2.bar([k for k, _ in sizes], [v for _, v in sizes], color=BLUE, width=0.8)
    a2.set(yscale="log", xticks=[2, 5, 10, 15, 20], xlabel="identified whales in the encounter",
           ylabel="encounters (log scale)", title="Humpback group size")
    a2.grid(axis="x", visible=False)

    together = d["together_hist"]
    a3.bar([str(k) if k < 8 else "8+" for k, _ in together], [v for _, v in together], color=BLUE, width=0.7)
    a3.set(xlabel="times photographed together", ylabel="pairs", title="How often do companions meet again?")
    a3.grid(axis="x", visible=False)

    fig.tight_layout()
    return save(fig, out / "social_summary.png")


FIGURES = [plot_network, plot_summary]
