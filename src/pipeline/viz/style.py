"""Shared look for every figure: colours, matplotlib defaults, saving."""
from __future__ import annotations

import logging
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # render to files, no window
import matplotlib.pyplot as plt  # noqa: E402

log = logging.getLogger(__name__)

# Colour-blind-safe categorical colours (use in this order) + a neutral grey for "other".
BLUE, ORANGE, GREEN, GREY = "#2a78d6", "#eb6834", "#1baf7a", "#a9aeaf"
AXIS_GREY = "#6f7576"

HEMI = {"N": "North", "S": "South"}
SHORT_NAME = {"Megaptera novaeangliae": "Humpback", "Physeter macrocephalus": "Sperm",
              "Balaenoptera musculus": "Blue", "Balaenoptera physalus": "Fin",
              "Eubalaena australis": "Southern right", "Eubalaena glacialis": "N. Atlantic right"}

plt.rcParams.update({
    "figure.dpi": 150, "savefig.dpi": 150, "savefig.bbox": "tight", "font.size": 9,
    "axes.spines.top": False, "axes.spines.right": False,
    "axes.grid": True, "grid.color": "#e6e9e9", "grid.linewidth": 0.6, "axes.axisbelow": True,
    "axes.titlesize": 10, "axes.titleweight": "bold", "axes.titlelocation": "left",
})


def save(fig, path: Path) -> Path:
    """Save a figure as PNG and close it."""
    fig.savefig(path)
    plt.close(fig)
    log.info("wrote %s", path)
    return path
