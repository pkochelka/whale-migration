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


def plot_network(d: dict, out: Path) -> Path:
    """Each dot is a whale, each line a pair photographed together at least twice."""
    nodes, edges = d["nodes"], d["edges"]           # edge = [node_a, node_b, hwi, times_together, years]
    xy = np.array([[n["x"], 1 - n["y"]] for n in nodes])
    group = np.array([region_group(n["region"]) for n in nodes])
    size = 4 + 3 * np.sqrt([n["deg"] for n in nodes])

    fig, ax = plt.subplots(figsize=(10, 7.5))
    lines = [(xy[a], xy[b]) for a, b, *_ in edges]
    widths = [0.3 + min(1.5, together / 4) for _, _, _, together, _ in edges]
    ax.add_collection(LineCollection(lines, colors="#55606a", linewidths=widths, alpha=0.35, zorder=1))
    for k, (label, _, color) in enumerate(REGION_GROUPS):
        m = group == k
        ax.scatter(xy[m, 0], xy[m, 1], s=size[m], c=color, edgecolors="white", linewidths=0.3, zorder=2,
                   label=f"{label} ({m.sum():,})")
    s = d["stats"]
    ax.set_title("Humpback whales photographed together at least twice\n"
                 f"{s['nodes']:,} whales, {s['edges']:,} repeated pairs, largest cluster "
                 f"{s['largest_component']:,} whales (colour = home region; layout is not geographic)")
    ax.legend(loc="upper left", bbox_to_anchor=(1.0, 0.95), frameon=False, markerscale=1.6, title="Home region")
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
