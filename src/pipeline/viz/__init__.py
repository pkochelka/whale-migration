"""PNG figures made with matplotlib from the analysis results.

    python -m pipeline viz   ->  figures/*.png

Each module below reads one JSON results file and lists its plot functions in FIGURES. A plot function takes (data: dict, out_dir: Path) and returns the PNG path(s) it wrote.
To add a figure: write the function in the right module and append it to that module's FIGURES.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from . import climate, showcase, social

log = logging.getLogger(__name__)

# analysis result file -> (module with its figures, folder the figures go to); paths from the project root
SOURCES = {"results/social_network.json": (social, "figures"),
           "results/climate_latitude.json": (climate, "figures"),
           "showcase/results/showcase.json": (showcase, "showcase/figures")}


def build(cfg) -> list[Path]:
    written: list[Path] = []
    for filename, (module, folder) in SOURCES.items():
        path = cfg.root / filename
        if not path.exists():
            log.warning("%s missing; run `python -m pipeline analyze` first", path)
            continue
        out = cfg.root / folder
        out.mkdir(parents=True, exist_ok=True)
        data = json.loads(path.read_text(encoding="utf-8"))
        for plot in module.FIGURES:
            result = plot(data, out)
            written += result if isinstance(result, list) else [result]
    return written
