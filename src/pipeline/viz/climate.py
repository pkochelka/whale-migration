"""Figures for the poleward-shift test (input: analysis/climate_latitude.json)."""
from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

from .style import AXIS_GREY, BLUE, GREEN, GREY, HEMI, ORANGE, save

# Row labels for the sensitivity "ladder" (same order as panel["ladder"]).
LADDER_LABELS = ["All records (raw)", "≥25% of years, 1985+", "≥50% of years, 1995+", "≥60% of years, 2000+",
                 "≥75% of years, 2000+ (strict)", "≥80% of years, 2005+"]
X_LABEL = "latitude trend, km/decade  (← equator | pole →)"


def km(v: float) -> str:
    """Signed, rounded km value without a '-0'."""
    r = round(v)
    return "0" if r == 0 else f"{r:+d}"


def plot_sensitivity(d: dict, out: Path) -> Path:
    """One panel per species/hemisphere: the latitude trend measured six ways (dot = trend, line = 95% CI)."""
    panels = d["panels"]
    strict_row = d["strict_index"] + 1          # ladder[0] is the raw row
    ncol = 4
    nrow = int(np.ceil(len(panels) / ncol))
    fig, axs = plt.subplots(nrow, ncol, figsize=(15, 3.5 * nrow), sharex=True, sharey=True, squeeze=False)
    y = np.arange(len(LADDER_LABELS))[::-1]

    for ax, p in zip(axs.flat, panels):
        ax.axvline(0, color=AXIS_GREY, lw=0.8)
        ax.axhspan(y[strict_row] - 0.45, y[strict_row] + 0.45, color=BLUE, alpha=0.08, lw=0)
        for yi, r in zip(y, p["ladder"]):
            if r.get("slope") is None:
                ax.text(0, yi, "too few cells", ha="center", va="center", fontsize=7, color=AXIS_GREY)
                continue
            ax.plot([r["lo"], r["hi"]], [yi, yi], color=BLUE, lw=1.6, solid_capstyle="round")
            ax.plot(r["slope"], yi, "o", color=BLUE, ms=5, mec="white", mew=1)
        title = f"{p['common']} · {HEMI[p['hemisphere']]}"
        if p.get("sst_trend"):
            title += f"\nSST in these waters {p['sst_trend']['slope']:+.1f} °C/decade"
        ax.set_title(title, fontsize=9)
        ax.set_yticks(y, LADDER_LABELS)
        ax.grid(axis="y", visible=False)
        ax.tick_params(labelbottom=True)
        ax.set_xlabel(X_LABEL)
    for ax in axs.flat[len(panels):]:
        ax.set_visible(False)
    axs.flat[0].set_xlim(-650, 350)
    fig.suptitle("Poleward shift? Theil–Sen trend with 95% CI, under increasingly strict sampling control "
                 "(rows 2–6 keep only 1° cells observed in a given share of years)",
                 x=0.01, ha="left", fontweight="bold", fontsize=11)
    fig.tight_layout()
    return save(fig, out / "climate_sensitivity.png")


def plot_timeseries(d: dict, out: Path) -> list[Path]:
    """Per species: latitude by method, records per year, and sea temperature (three stacked plots)."""
    strict_row = d["strict_index"] + 1
    files = []
    for p in d["panels"]:
        rows = p["series"]
        years = np.array([r["year"] for r in rows])

        def col(key):
            return np.array([np.nan if r[key] is None else r[key] for r in rows], float)

        fig, (a1, a2, a3) = plt.subplots(3, 1, figsize=(9, 7.5), sharex=True,
                                         gridspec_kw={"height_ratios": [2.2, 1, 1.1]})
        a1.plot(years, col("raw"), color=ORANGE, lw=1.6, label="Raw median of all records")
        a1.plot(years, col("loose"), color=GREEN, lw=1.6, label="Loose footprint (cells seen ≥25% of years)")
        a1.plot(years, col("strict"), color=BLUE, lw=2, label="Strict footprint (≥75% of years, from 2000)")
        st = p["ladder"][strict_row]
        trend = f"   strict trend {km(st['slope'])} km/decade [{km(st['lo'])}, {km(st['hi'])}]" if st.get("slope") is not None else ""
        a1.set(ylabel="distance from equator (° lat)", title=f"{p['common']} · {HEMI[p['hemisphere']]}ern hemisphere{trend}")
        a1.legend(frameon=False, fontsize=8)

        a2.bar(years, [r["n"] for r in rows], color=GREY, width=0.8)
        a2.set(ylabel="records / year")
        a2.grid(axis="x", visible=False)

        a3.plot(years, col("sst"), color=BLUE, lw=1.6)
        a3.set(ylabel="SST in strict\nfootprint (°C)", xlabel="year")

        fig.tight_layout()
        name = f"climate_timeseries_{p['common'].lower().replace(' ', '_')}_{p['hemisphere']}.png"
        files.append(save(fig, out / name))
    return files


FIGURES = [plot_sensitivity, plot_timeseries]
