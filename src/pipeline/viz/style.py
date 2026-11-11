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
# Slots 4-6 continue the same validated order. Only for lines and stacks (neighbours differ enough
# for colour-blind readers); maps and scatters stop at three colours. Label them directly.
YELLOW, MAGENTA, DARK_GREEN = "#eda100", "#e87ba4", "#008300"
SERIES = [BLUE, ORANGE, GREEN, YELLOW, MAGENTA, DARK_GREEN]
INK, INK_MUTED, LAND = "#0b0b0b", "#52514e", "#e6e6e3"
# One-hue ramps (light -> dark) for magnitudes; the second ramp is for a second magnitude.
BLUE_RAMP = ["#f0f6fe", "#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#2a78d6", "#1c5cab", "#104281", "#0d366b"]
ORANGE_RAMP = ["#fdf0ea", "#fbd9c8", "#f6b393", "#f08d63", "#eb6834", "#c4521f", "#8a3412"]

HEMI = {"N": "North", "S": "South"}
SHORT_NAME = {"Megaptera novaeangliae": "Humpback", "Physeter macrocephalus": "Sperm",
              "Balaenoptera musculus": "Blue", "Balaenoptera physalus": "Fin",
              "Eubalaena australis": "Southern right", "Eubalaena glacialis": "N. Atlantic right"}
# One colour per species wherever all six appear together (SERIES order, most records first).
SPECIES_ORDER = ["Humpback whale", "Fin whale", "Sperm whale", "Blue whale", "Southern right whale",
                 "North Atlantic right whale"]
SPECIES_COLOR = dict(zip(SPECIES_ORDER, SERIES))

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
