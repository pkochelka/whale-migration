"""Showcase figures: one example per visualization technique (input: showcase/results/showcase.json).

Every function takes the whole showcase result and the output folder, like the other viz modules.
Maps use plain longitude/latitude axes (no projection library is installed); cell areas shrink
towards the poles, which matters for the density maps and is noted in the report.
"""
from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import BoundaryNorm, LinearSegmentedColormap, ListedColormap, LogNorm, to_rgb
from matplotlib.patches import PathPatch, Polygon, Rectangle
from matplotlib.path import Path as MplPath

from .style import (AXIS_GREY, BLUE, BLUE_RAMP, GREEN, GREY, INK, INK_MUTED, LAND, ORANGE, ORANGE_RAMP, SERIES,
                    SHORT_NAME, SPECIES_COLOR, SPECIES_ORDER, save)

BLUES = LinearSegmentedColormap.from_list("blues", BLUE_RAMP)
MONTHS = "JFMAMJJASOND"


# -- helpers ---------------------------------------------------------------------------------------
def draw_land(ax, land: list, shift: float = 0.0, **kw) -> None:
    """Coastline rings as filled polygons; `shift` moves them east/west by that many degrees."""
    for ring in land:
        xy = np.asarray(ring, float)
        ax.add_patch(Polygon(xy + [shift, 0], closed=True, facecolor=kw.get("color", LAND), edgecolor="none",
                             zorder=kw.get("zorder", 0)))


def map_axes(ax, extent, land, shifts=(0.0,), on_top: bool = False) -> None:
    """Blank map frame with land; `on_top` draws land above data cells (so islands stay visible)."""
    for s in shifts:
        draw_land(ax, land, s, zorder=3 if on_top else 0)
    ax.set(xlim=extent[:2], ylim=extent[2:], aspect="equal")
    ax.set_xticks([]), ax.set_yticks([])
    ax.grid(False)
    for side in ("top", "right", "bottom", "left"):
        ax.spines[side].set_visible(False)


def common_name(scientific_or_common: str) -> str:
    return SHORT_NAME.get(scientific_or_common, scientific_or_common.replace(" whale", ""))


def smooth(v, width: float = 1.5) -> np.ndarray:
    k = np.exp(-0.5 * (np.arange(-4, 5) / width) ** 2)
    return np.convolve(np.asarray(v, float), k / k.sum(), mode="same")


# -- 1 seasonal migration ----------------------------------------------------------------------------
def plot_migration(r: dict, out: Path) -> Path:
    """Latitude x month heatmap per humpback population; each month column sums to 100%."""
    m = r["migration"]
    pops = m["populations"]
    fig, axs = plt.subplots(2, len(pops), figsize=(15, 6.2), sharex=True,
                            gridspec_kw={"height_ratios": [1, 4.5], "hspace": 0.06, "wspace": 0.28})
    for k, p in enumerate(pops):
        cells = np.asarray(p["cells"])
        bands = np.arange(cells[:, 0].min(), cells[:, 0].max() + m["band_deg"], m["band_deg"])
        grid = np.zeros((len(bands), 12))
        for band, month, n in cells:
            grid[int((band - bands[0]) // m["band_deg"]), int(month) - 1] += n
        per_month = grid.sum(0)
        share = grid / np.where(per_month > 0, per_month, 1) * 100
        top, ax = axs[0, k], axs[1, k]
        top.bar(np.arange(1, 13), per_month / 1000, color=GREY, width=0.75)
        top.set_title(f"{p['population']}\n{p['records']:,} records", fontsize=9)
        top.grid(axis="x", visible=False)
        top.tick_params(labelsize=7)
        if k == 0:
            top.set_ylabel("records\n(thousands)", fontsize=7)
        im = ax.pcolormesh(np.arange(0.5, 13), np.append(bands, bands[-1] + m["band_deg"]), share,
                           cmap=BLUES, vmin=0, vmax=25, shading="flat")
        centre = bands + m["band_deg"] / 2
        median = [np.interp(0.5, np.cumsum(grid[:, j]) / per_month[j], centre) if per_month[j] else np.nan
                  for j in range(12)]
        ax.plot(np.arange(1, 13), median, color=INK, lw=1.4, marker="o", ms=3.5, mec="white", mew=0.8)
        ax.set_xticks(np.arange(1, 13), list(MONTHS))
        ax.grid(False)
        ax.set_ylim(bands[0], bands[-1] + m["band_deg"])
        ax.axhline(0, color=AXIS_GREY, lw=0.6, ls=":")
        if k == 0:
            ax.set_ylabel("latitude (°, south negative)")
    cb = fig.colorbar(im, ax=axs[1, :], fraction=0.015, pad=0.01)
    cb.set_label("share of that month's records (%)")
    axs[1, 0].plot([], [], color=INK, lw=1.4, marker="o", ms=3.5, label="median latitude")
    axs[1, 0].legend(loc="lower left", frameon=False, fontsize=7)
    fig.suptitle("Humpback whales move with the seasons: where each month's records fall in latitude",
                 x=0.01, ha="left", fontweight="bold", fontsize=11, y=1.02)
    return save(fig, out / "01_migration_latitude_month.png")


def plot_monthly_maps(r: dict, out: Path) -> Path:
    """Twelve small maps of North Pacific humpback records, one per month, shared colour scale."""
    m = r["monthly_map"]
    c = np.asarray(m["cells"])
    ext = m["extent"]
    ext = (-180, -105, 12, 64)
    fig, axs = plt.subplots(3, 4, figsize=(13, 7.6), gridspec_kw={"hspace": 0.18, "wspace": 0.05})
    norm = LogNorm(1, np.percentile(c[:, 3], 99))
    for month, ax in enumerate(axs.flat, start=1):
        map_axes(ax, ext, r["land"], on_top=True)
        for side in ax.spines.values():
            side.set_visible(True), side.set_color(LAND)
        g = c[c[:, 2] == month]
        for la, lo, _, n in g:
            ax.add_patch(Rectangle((lo, la), m["cell_deg"], m["cell_deg"], color=BLUES(norm(min(n, norm.vmax))),
                                   lw=0, zorder=1))
        ax.set_title(f"{['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][month - 1]}"
                     f"  ·  {int(g[:, 3].sum()):,}", fontsize=9)
    sm = plt.cm.ScalarMappable(norm=norm, cmap=BLUES)
    cb = fig.colorbar(sm, ax=axs, fraction=0.02, pad=0.01)
    cb.set_label("records per 2° cell (log scale)")
    fig.suptitle("North Pacific humpbacks by month, 1990–2025: winter off Hawaii and Mexico, summer in Alaska",
                 x=0.12, y=0.95, ha="left", fontweight="bold", fontsize=11)
    return save(fig, out / "02_migration_monthly_maps.png")


# -- 2-3 individuals ---------------------------------------------------------------------------------
def _pacific_lon(lon: float) -> float:
    return lon - 360 if lon > 100 else lon


# Hand-placed labels where regions sit close together (degrees; ha).
NAME_OFFSET = {"Gulf of Alaska & Bering": (-2.5, 2.0, "right"), "SE Alaska & BC": (2.2, 2.4, "left"),
               "US West Coast": (2.8, 0.5, "left")}
LABEL_T = {("Gulf of Alaska & Bering", "Hawaii"): 0.28, ("Hawaii", "SE Alaska & BC"): 0.38}


def plot_connectivity(r: dict, out: Path) -> Path:
    """Flow map: line width = photo-identified whales seen in both regions."""
    c = r["connectivity"]
    reg = {x["name"]: x for x in c["regions"]}
    fig, ax = plt.subplots(figsize=(15, 7.4))
    map_axes(ax, (-262, 98, -78, 82), r["land"], shifts=(0.0, -360.0))
    wmax = max(n for *_, n in c["links"])
    for a, b, n in sorted(c["links"], key=lambda x: x[2]):
        (x0, y0), (x1, y1) = [(_pacific_lon(reg[k]["lon"]), reg[k]["lat"]) for k in (a, b)]
        mx, my = (x0 + x1) / 2, (y0 + y1) / 2
        dx, dy = x1 - x0, y1 - y0
        bend = 0.18
        ctrl = (mx - dy * bend, my + dx * bend)
        path = MplPath([(x0, y0), ctrl, (x1, y1)], [MplPath.MOVETO, MplPath.CURVE3, MplPath.CURVE3])
        ax.add_patch(PathPatch(path, fill=False, edgecolor=BLUE, lw=0.6 + 7 * np.sqrt(n / wmax), alpha=0.75,
                               capstyle="round", zorder=2))
        if n >= 500:
            t = LABEL_T.get((a, b), 0.5)
            px = (1 - t) ** 2 * x0 + 2 * (1 - t) * t * ctrl[0] + t ** 2 * x1
            py = (1 - t) ** 2 * y0 + 2 * (1 - t) * t * ctrl[1] + t ** 2 * y1
            ax.text(px, py, f"{n:,}", fontsize=7.5, color=INK, ha="center", va="center", zorder=4,
                    bbox={"boxstyle": "round,pad=0.15", "fc": "white", "ec": "none", "alpha": 0.85})
    linked = {k for a, b, _ in c["links"] for k in (a, b)}
    for name, x in reg.items():
        lx = _pacific_lon(x["lon"])
        ax.scatter(lx, x["lat"], s=8 + x["whales"] / 120, color=INK if name in linked else GREY, zorder=3,
                   edgecolors="white", linewidths=0.6)
        dx, dy, ha = NAME_OFFSET.get(name, (2.2, 1.6, "left"))
        ax.text(lx + dx, x["lat"] + dy, name, fontsize=7, color=INK if name in linked else INK_MUTED, zorder=4, ha=ha)
    for n in (20, 200, 2000):
        ax.plot([], [], color=BLUE, lw=0.6 + 7 * np.sqrt(n / wmax), alpha=0.75, label=f"{n:,} whales")
    ax.legend(loc="lower left", frameon=False, title="seen in both regions", fontsize=8, title_fontsize=8)
    ax.set_title(f"Where photo-identified humpbacks travel: {c['whales_multi_region']:,} of "
                 f"{c['whales_with_region']:,} whales were photographed in more than one region\n"
                 f"(lines: region pairs shared by ≥{c['min_shared']} whales; dot size: whales photographed there; "
                 "map centred on the Pacific)", fontsize=10)
    return save(fig, out / "03_photo_id_connectivity.png")


def plot_life_histories(r: dict, out: Path) -> Path:
    """One row per whale: every sighting as a dot, coloured by feeding vs breeding latitude."""
    lh = r["life_histories"]
    whales = sorted(lh["whales"], key=lambda w: (w["first"], -w["years"]))
    fig, ax = plt.subplots(figsize=(12, 9))
    for i, w in enumerate(whales):
        s = np.asarray(w["sightings"])
        ax.plot([w["first"], s[:, 0].max()], [i, i], color=LAND, lw=3, solid_capstyle="round", zorder=1)
        for zone, color in ((1, BLUE), (0, ORANGE)):
            pts = s[s[:, 1] == zone]
            ax.scatter(pts[:, 0], np.full(len(pts), i), s=11, color=color, edgecolors="white", linewidths=0.4,
                       zorder=2)
    ax.set_yticks(range(len(whales)), [f"#{w['id']}  ({w['years']} yrs)" for w in whales], fontsize=6.5)
    ax.invert_yaxis()
    ax.grid(axis="y", visible=False)
    ax.set_xlabel("year")
    ax.scatter([], [], s=20, color=BLUE, label=f"feeding latitudes (|lat| ≥ {lh['feeding_lat']:.0f}°)")
    ax.scatter([], [], s=20, color=ORANGE, label="breeding latitudes (closer to the equator)")
    ax.legend(loc="upper right", frameon=False, fontsize=8, bbox_to_anchor=(1, 1.06), ncol=2)
    ax.set_title(f"Forty humpback lives: the whales photographed in the most different years (Happywhale IDs)\n"
                 f"{lh['followed_10y']:,} of {lh['photo_id_whales']:,} photo-identified humpbacks were followed "
                 "for 10 years or more", fontsize=10, pad=24)
    return save(fig, out / "04_life_histories.png")


# -- 4 tag tracks ------------------------------------------------------------------------------------
def plot_space_time_cube(r: dict, out: Path) -> Path:
    """Three tag tracks as a map and as a space-time cube (height = days since tagging)."""
    t = r["tracks"]
    colors = [BLUE, ORANGE, GREEN]
    ext = (-92, -48, -70, 12)
    fig = plt.figure(figsize=(14, 7.2))
    ax = fig.add_subplot(1, 2, 1)
    map_axes(ax, ext, r["land"])
    for tr, col in zip(t["tracks"], colors):
        a = np.asarray(tr["track"])
        ax.plot(a[:, 2], a[:, 1], color=col, lw=1.6, zorder=2)
        ax.scatter(a[0, 2], a[0, 1], s=30, color=col, marker="o", edgecolors="white", zorder=3)
        ax.scatter(a[-1, 2], a[-1, 1], s=40, color=col, marker="s", edgecolors="white", zorder=3)
        south = a[-1, 1] < -40   # this whale swam back to Antarctica: label it on the open-water side
        ax.text(a[-1, 2] + (1.5 if south else -1.2), a[-1, 1] + (1.5 if south else 0), f"tag {tr['tag']}",
                fontsize=7.5, color=INK, ha="left" if south else "right", va="center")
    ax.scatter([], [], s=30, color=INK_MUTED, marker="o", label="tagged (Feb 2013)")
    ax.scatter([], [], s=40, color=INK_MUTED, marker="s", label="last position")
    ax.legend(loc="upper left", bbox_to_anchor=(0.0, 0.0), frameon=False, fontsize=8, ncol=2)
    ax.set_title("Map view: Antarctic Peninsula → Pacific coast of Colombia (tag 123232 swam back)", fontsize=9)

    ax3 = fig.add_subplot(1, 2, 2, projection="3d")
    for ring in r["land"]:
        xy = np.asarray(ring, float)
        if (xy[:, 0].max() < ext[0] or xy[:, 0].min() > ext[1] or xy[:, 1].max() < ext[2]
                or xy[:, 1].min() > ext[3]):
            continue
        xy = xy[(xy[:, 0] >= ext[0]) & (xy[:, 0] <= ext[1]) & (xy[:, 1] >= ext[2]) & (xy[:, 1] <= ext[3])]
        if len(xy) > 2:
            ax3.plot(xy[:, 0], xy[:, 1], 0, color=GREY, lw=0.6)
    for tr, col in zip(t["tracks"], colors):
        a = np.asarray(tr["track"])
        ax3.plot(a[:, 2], a[:, 1], a[:, 0], color=col, lw=1.8)
        ax3.plot(a[:, 2], a[:, 1], 0, color=col, lw=0.7, alpha=0.35)
    ax3.set(xlim=ext[:2], ylim=ext[2:], zlim=(0, max(x["days"] for x in t["tracks"])),
            xlabel="longitude", ylabel="latitude", zlabel="days since tagging")
    ax3.view_init(elev=22, azim=-58)
    ax3.set_title("Space-time cube: steep segments = staying put, flat segments = travelling fast", fontsize=9)
    fig.suptitle(f"Three of {t['tagged_humpbacks']} tagged humpbacks crossed up to "
                 f"{max(x['lat_span'] for x in t['tracks']):.0f}° of latitude in 6–9 months",
                 x=0.01, ha="left", fontweight="bold", fontsize=11)
    return save(fig, out / "05_tag_tracks_space_time_cube.png")


# -- 5-6 whaling and record history ----------------------------------------------------------------------
def plot_whaling(r: dict, out: Path, min_days: int = 30) -> Path:
    """Sperm whale encounter rate per 100 vessel-days, 5-degree cells, four eras of sail whaling."""
    w = r["whaling"]
    c = w["cell_deg"]
    rates = np.concatenate([[100 * s / d for _, _, d, s in e["cells"] if d >= min_days] for e in w["eras"]])
    bounds = [0, 1, 2, 4, 8, 16, 32]
    cmap = ListedColormap(BLUE_RAMP[1::1][: len(bounds)])
    norm = BoundaryNorm(bounds + [max(100, rates.max())], cmap.N)
    fig, axs = plt.subplots(2, 2, figsize=(15, 6.6), gridspec_kw={"hspace": 0.12, "wspace": 0.04})
    for ax, e in zip(axs.flat, w["eras"]):
        map_axes(ax, (-180, 180, -70, 75), r["land"], on_top=True)
        for la, lo, d, s in e["cells"]:
            if d < min_days:
                ax.add_patch(Rectangle((lo, la), c, c, fc="none", ec=GREY, lw=0.4, zorder=1))
            else:
                ax.add_patch(Rectangle((lo, la), c, c, color=cmap(norm(100 * s / d)), lw=0, zorder=1))
        ax.set_title(f"{e['from']}–{e['to']}  ·  {e['vessel_days']:,} vessel-days, "
                     f"{e['sperm_days']:,} with sperm whales", fontsize=9)
    sm = plt.cm.ScalarMappable(norm=norm, cmap=cmap)
    cb = fig.colorbar(sm, ax=axs, fraction=0.02, pad=0.01, ticks=bounds)
    cb.set_label("days with sperm whales seen or struck, per 100 vessel-days")
    axs[0, 0].add_patch(Rectangle((-178, -66), c, c, fc="none", ec=GREY, lw=0.6))
    axs[0, 0].text(-171, -64, f"fewer than {min_days} vessel-days (not rated)", fontsize=7, color=INK_MUTED,
                   va="center")
    fig.suptitle("Where sail whalers met sperm whales, 1780–1920: encounters divided by effort (American logbooks)",
                 x=0.12, y=0.98, ha="left", fontweight="bold", fontsize=11)
    return save(fig, out / "06_whaling_encounter_rate.png")


KIND_LABEL = {"catch": "catch (whaling)", "sighting": "visual sighting", "acoustic": "acoustic detection",
              "encounter": "photo-ID encounter", "tag_fix": "satellite-tag fix"}
KIND_ORDER = ["catch", "sighting", "acoustic", "encounter", "tag_fix"]   # stack order = SERIES order


def plot_record_types(r: dict, out: Path) -> Path:
    """Share of records by kind per decade (stacked area) and total records per decade (log bars)."""
    rt = r["record_types"]
    rows = np.asarray(rt["rows"], float)
    rows = rows[rows[:, 0] >= 1780]
    dec = rows[:, 0]
    by = {k: rows[:, 1 + rt["kinds"].index(k)] for k in KIND_ORDER}
    total = sum(by.values())
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(12, 7), sharex=True, gridspec_kw={"height_ratios": [2.4, 1]})
    shares = [100 * by[k] / total for k in KIND_ORDER]
    a1.stackplot(dec + 5, shares, colors=SERIES[:5], edgecolor="white", linewidth=1.2)
    a1.set(ylim=(0, 100), xlim=(1780, 2030), ylabel="share of the decade's records (%)")
    a1.grid(False)
    label_at = {"catch": 1950, "sighting": 1880, "acoustic": 2015, "encounter": 2013, "tag_fix": 2008}
    for k, share in zip(KIND_ORDER, shares):
        x = label_at[k]
        j = int(np.argmin(np.abs(dec + 5 - x)))
        base = sum(s[j] for s in shares[:KIND_ORDER.index(k)])
        if share[j] > 4:
            a1.text(x, base + share[j] / 2, KIND_LABEL[k], fontsize=8, color=INK, ha="center", va="center",
                    bbox={"boxstyle": "round,pad=0.2", "fc": "white", "ec": "none", "alpha": 0.8})
    a1.legend([plt.Rectangle((0, 0), 1, 1, color=col) for col in SERIES[:5]], [KIND_LABEL[k] for k in KIND_ORDER],
              loc="lower left", bbox_to_anchor=(0, 1.0), frameon=False, ncol=5, fontsize=8)
    a2.bar(dec + 5, total, width=8, color=GREY)
    a2.set(yscale="log", ylabel="records per decade\n(log scale)", xlabel="decade")
    a2.grid(axis="x", visible=False)
    a1.set_title("From harpoons to cameras: what kind of record the data holds, decade by decade", fontsize=11,
                 pad=26)
    fig.tight_layout()
    return save(fig, out / "07_record_types_over_time.png")


# -- 7 environmental niche ----------------------------------------------------------------------------------
def plot_niche_ridgeline(r: dict, out: Path) -> Path:
    """Sea temperature at sightings: one smoothed distribution per species, sorted by median."""
    n = r["niche"]
    bins = np.asarray(n["sst_bins"])
    mid = (bins[:-1] + bins[1:]) / 2
    sp = sorted(n["species"], key=lambda s: s["sst_quantiles"][1])
    fig, ax = plt.subplots(figsize=(12, 6.5))
    for i, s in enumerate(sp):
        h = smooth(s["sst_hist"])
        h = h / h.max() * 1.25
        ax.fill_between(mid, i, i + h, color=BLUE, alpha=0.55, lw=0, zorder=len(sp) - i)
        ax.plot(mid, i + h, color=BLUE, lw=1.2, zorder=len(sp) - i)
        q10, q50, q90 = s["sst_quantiles"]
        ax.plot([q50, q50], [i, i + np.interp(q50, mid, h)], color=INK, lw=1.2, zorder=len(sp) + 1)
        ax.text(1.01, i + 0.15, f"median {q50:.0f} °C\n80% within {q10:.0f}–{q90:.0f} °C\nn={s['n_sst']:,}",
                fontsize=7.5, color=INK_MUTED, ha="left", va="bottom", transform=ax.get_yaxis_transform())
    ax.set_yticks(np.arange(len(sp)) + 0.3, [common_name(s["species"]) for s in sp])
    ax.set(xlim=(-2, 32), ylim=(-0.2, len(sp) + 0.6), xlabel="sea surface temperature at the sighting (°C, HadISST monthly, 1° cell)")
    ax.grid(axis="y", visible=False)
    ax.set_title("Temperature niche: the water each species is seen in (sightings 1998–2025, acoustic excluded)",
                 fontsize=10)
    return save(fig, out / "08_niche_temperature_ridgeline.png")


def plot_niche_joint(r: dict, out: Path) -> Path:
    """Temperature x chlorophyll density per species (small multiples), all species in grey behind."""
    n = r["niche"]
    xb, yb = np.asarray(n["joint_sst_bins"]), np.asarray(n["joint_log_chl_bins"])
    xm, ym = (xb[:-1] + xb[1:]) / 2, (yb[:-1] + yb[1:]) / 2
    by = {s["species"]: np.asarray(s["joint_hist"], float) for s in n["species"]}
    every = sum(h / h.sum() for h in by.values())
    fig, axs = plt.subplots(2, 3, figsize=(13, 7.5), sharex=True, sharey=True)
    for ax, sp in zip(axs.flat, SPECIES_ORDER):
        h = by[sp] / by[sp].sum()
        ax.contour(xm, ym, every.T / every.max(), levels=[0.02, 0.1, 0.3], colors=GREY, linewidths=0.7)
        ax.contourf(xm, ym, h.T / h.max(), levels=[0.05, 0.15, 0.35, 0.6, 1.01],
                    colors=BLUE_RAMP[2:7:1][:4], alpha=0.9)
        ax.set_title(f"{common_name(sp)}  ·  n={int(by[sp].sum()):,}", fontsize=9)
        ax.set_yticks([-1, 0, 1], ["0.1", "1", "10"])
    for ax in axs[1]:
        ax.set_xlabel("sea temperature (°C)")
    for ax in axs[:, 0]:
        ax.set_ylabel("chlorophyll-a (mg/m³, log)")
    axs[0, 0].plot([], [], color=GREY, lw=0.7, label="all six species")
    axs[0, 0].fill_between([], [], color=BLUE_RAMP[4], label="this species (darker = denser)")
    axs[0, 0].legend(loc="upper right", frameon=False, fontsize=7)
    fig.suptitle("Niche space: sea temperature and chlorophyll at sightings, 1998–2025 (monthly values at the 1° cell)",
                 x=0.01, ha="left", fontweight="bold", fontsize=11)
    fig.tight_layout()
    return save(fig, out / "09_niche_temperature_chlorophyll.png")


# -- 8-9 human pressure --------------------------------------------------------------------------------------
def _bivariate_colors() -> np.ndarray:
    """3x3 colours: rows = whale class (blue ramp), columns = ship class (orange ramp), multiplied."""
    b = [to_rgb(x) for x in ("#f3f3f1", BLUE_RAMP[2], BLUE_RAMP[5])]
    o = [to_rgb(x) for x in ("#f3f3f1", ORANGE_RAMP[1], ORANGE_RAMP[3])]
    return np.array([[np.multiply(bi, oj) for oj in o] for bi in b])


def plot_ship_strike(r: dict, out: Path) -> Path:
    """Bivariate map: right whale records x ship traffic, three classes each."""
    s = r["ship_strike"]
    c = np.asarray(s["cells"], float)
    ship_cut = np.quantile(c[:, 2], [1 / 3, 2 / 3])
    whale_cut = np.median(c[c[:, 3] > 0, 3])
    ship_cls = np.digitize(c[:, 2], ship_cut)
    whale_cls = np.where(c[:, 3] == 0, 0, np.where(c[:, 3] < whale_cut, 1, 2))
    col = _bivariate_colors()
    la0, la1, lo0, lo1 = s["box"]
    fig, ax = plt.subplots(figsize=(11, 9.5))
    map_axes(ax, (lo0, lo1, la0, la1), r["land_narw"], on_top=True)
    for (la, lo, _, _), wc, sc in zip(c, whale_cls, ship_cls):
        ax.add_patch(Rectangle((lo, la), s["cell_deg"], s["cell_deg"], color=col[wc, sc], lw=0, zorder=1))
    for ring in r["land_narw"]:
        ax.add_patch(Polygon(ring, closed=True, fc="none", ec=AXIS_GREY, lw=0.4, zorder=4))
    hot = c[(whale_cls == 2) & (ship_cls == 2), 3].sum()
    for name, (lo, la) in {"Gulf of Maine": (-68.8, 43.2), "Cape Cod Bay": (-70.2, 41.2),
                           "calving grounds\n(Georgia–Florida)": (-80.2, 29.3)}.items():
        ax.text(lo, la, name, fontsize=7.5, color=INK, zorder=5, ha="center",
                bbox={"boxstyle": "round,pad=0.15", "fc": "white", "ec": "none", "alpha": 0.8})
    inset = ax.inset_axes([1.03, 0.04, 0.24, 0.24])
    for i in range(3):
        for j in range(3):
            inset.add_patch(Rectangle((j, i), 0.94, 0.94, color=col[i, j]))
    inset.set(xlim=(0, 3), ylim=(0, 3), aspect="equal")
    inset.set_xticks([0.47, 1.47, 2.47], ["low", "mid", "high"], fontsize=7)
    inset.set_yticks([0.47, 1.47, 2.47], ["none", "some", "many"], fontsize=7)
    inset.set_xlabel("ship traffic →", fontsize=7.5)
    inset.set_ylabel("right whale records →", fontsize=7.5)
    inset.grid(False)
    for side in inset.spines.values():
        side.set_visible(False)
    ax.set_title(f"Ship-strike risk: where North Atlantic right whales and ships share water (0.5° cells)\n"
                 f"{int(hot):,} of {s['whale_records']:,} right whale records (2000–2025) fall in the darkest "
                 f"class: many whales and the busiest third of shipping", fontsize=10)
    return save(fig, out / "10_ship_strike_bivariate.png")


def plot_mpa(r: dict, out: Path) -> Path:
    """Waffle per species: 100 squares = sightings since 2000, split by protection status."""
    m = r["mpa"]
    status = [("no_take", BLUE_RAMP[6], "inside a fully no-take area"),
              ("other", BLUE_RAMP[3], "inside another protected area"),
              ("outside", LAND, "outside protected areas")]
    fig, axs = plt.subplots(1, len(m["species"]), figsize=(15, 3.4), gridspec_kw={"wspace": 0.25})
    for ax, s in zip(axs, m["species"]):
        counts = np.round([s[k] * 100 for k, *_ in status]).astype(int)
        counts[-1] = 100 - counts[:-1].sum()
        cells = np.concatenate([[i] * n for i, n in enumerate(counts)])
        for idx, kind in enumerate(cells):
            ax.add_patch(Rectangle((idx % 10, 9 - idx // 10), 0.86, 0.86, color=status[kind][1], lw=0))
        ax.set(xlim=(-0.2, 10), ylim=(-0.2, 10), aspect="equal")
        ax.set_axis_off()
        prot = 100 * (s["no_take"] + s["other"])
        ax.set_title(f"{common_name(s['species'])}\n{prot:.0f}% protected\n{100 * s['no_take']:.0f}% no-take",
                     fontsize=9)
    fig.legend([Rectangle((0, 0), 1, 1, color=c) for _, c, _ in status], [lab for *_, lab in status],
               loc="lower center", ncol=3, frameon=False, fontsize=8, bbox_to_anchor=(0.5, -0.02))
    fig.suptitle(f"How much of each species' sightings fall inside marine protected areas (sightings since 2000; "
                 f"{m['areas']:,} WDPA areas; one square = 1%)", x=0.12, y=1.08, ha="left", fontweight="bold",
                 fontsize=11)
    return save(fig, out / "11_protected_area_waffle.png")


# -- 10-11 provenance and coast ----------------------------------------------------------------------------
SOURCE_LABEL = {"obis": "OBIS", "gbif": "GBIF", "seamap": "OBIS-SEAMAP", "whalinghistory": "Whaling logbooks",
                "movebank": "Movebank"}


def plot_upset(r: dict, out: Path) -> Path:
    """UpSet plot: which combination of databases each final record was found in."""
    combos = r["provenance"]["combinations"]
    sources = ["obis", "gbif", "seamap", "whalinghistory"]
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(9, 6.5), sharex=True, gridspec_kw={"height_ratios": [2.4, 1.2]})
    x = np.arange(len(combos))
    vals = [n for _, n in combos]
    a1.bar(x, vals, color=BLUE, width=0.6)
    for xi, v in zip(x, vals):
        a1.text(xi, v, f"{v:,}", ha="center", va="bottom", fontsize=8, color=INK)
    a1.set(ylabel="final records")
    a1.grid(axis="x", visible=False)
    a1.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v / 1000:.0f}k"))
    for yi, src in enumerate(sources):
        a2.scatter(x, [yi] * len(x), s=60, color=LAND, zorder=1)
        on = [i for i, (s, _) in enumerate(combos) if src in s.split(",")]
        a2.scatter(on, [yi] * len(on), s=60, color=INK, zorder=2)
    for i, (s, _) in enumerate(combos):
        ys = [sources.index(k) for k in s.split(",")]
        a2.plot([i, i], [min(ys), max(ys)], color=INK, lw=2, zorder=1)
    a2.set_yticks(range(len(sources)), [SOURCE_LABEL[s] for s in sources])
    a2.set(ylim=(-0.6, len(sources) - 0.4))
    a2.invert_yaxis()
    a2.grid(False)
    a2.set_xticks([])
    for side in ("left", "bottom"):
        a2.spines[side].set_visible(False)
    a1.set_title("Where each record came from: most whale records are published in several databases at once\n"
                 "(UpSet plot; a filled dot = the record was found in that database)", fontsize=10)
    fig.tight_layout()
    return save(fig, out / "12_provenance_upset.png")


def _ribbon(ax, x0, x1, y0a, y0b, y1a, y1b, color, alpha=0.45):
    """S-shaped band from [y0a, y0b] at x0 to [y1a, y1b] at x1."""
    t = np.linspace(0, 1, 60)
    s = 3 * t ** 2 - 2 * t ** 3
    xs = x0 + (x1 - x0) * t
    ax.fill_between(xs, y0a + (y1a - y0a) * s, y0b + (y1b - y0b) * s, color=color, alpha=alpha, lw=0)


def plot_cleaning_flow(r: dict, out: Path) -> Path:
    """Sankey: raw rows per source -> kept or dropped in cleaning -> unique or duplicate after merging."""
    p = r["provenance"]
    cl, un = p["cleaning"], p["unify"]
    order = ["gbif", "obis", "seamap", "whalinghistory"]
    no_whale = "no_encounter_vessel_day (kept in effort table)"
    gap, w = 0.02, 0.05
    total = sum(cl[s]["rows_in"] for s in order)
    scale = 1 / (total * (1 + gap * 6))
    fig, ax = plt.subplots(figsize=(13, 7.5))
    ax.set_axis_off()
    # Column 1: sources.
    y = 1.0
    src_pos = {}
    for s in order:
        h = cl[s]["rows_in"] * scale
        src_pos[s] = (y - h, y)
        ax.add_patch(Rectangle((0, y - h), w, h, color=INK_MUTED))
        ax.text(-0.01, y - h / 2, f"{SOURCE_LABEL[s]}\n{cl[s]['rows_in']:,} rows", ha="right", va="center", fontsize=8.5)
        y -= h + gap
    # Column 2: kept (stacked by source), logbook days without whales, other drops.
    kept_total = sum(cl[s]["rows_out"] for s in order)
    nowhale = cl["whalinghistory"]["dropped"].get(no_whale, 0)
    other = sum(cl[s]["rows_in"] - cl[s]["rows_out"] for s in order) - nowhale
    x2 = 0.42
    kept_top, kept_bot = 1.0, 1.0 - kept_total * scale
    nw_top, nw_bot = kept_bot - gap * 2, kept_bot - gap * 2 - nowhale * scale
    ot_top, ot_bot = nw_bot - gap, nw_bot - gap - other * scale
    for (top, bot, color, label) in ((kept_top, kept_bot, BLUE, f"kept after cleaning\n{kept_total:,}"),
                                     (nw_top, nw_bot, GREY, f"logbook days without whales\n{nowhale:,} (kept as effort)"),
                                     (ot_top, ot_bot, ORANGE, f"dropped: wrong taxon, on land,\nbad date, rights reserved\n{other:,}")):
        ax.add_patch(Rectangle((x2, bot), w, top - bot, color=color))
        ax.text(x2 + w + 0.01, (top + bot) / 2, label, va="center", fontsize=8.5)
    ky, oy = kept_top, ot_top
    for s in order:
        a, b = src_pos[s]
        kept = cl[s]["rows_out"] * scale
        drop = (cl[s]["rows_in"] - cl[s]["rows_out"]) * scale
        _ribbon(ax, w, x2, b - kept, b, ky - kept, ky, BLUE)
        ky -= kept
        if s == "whalinghistory":
            _ribbon(ax, w, x2, a + (drop - nowhale * scale), a + drop, nw_bot, nw_top, GREY)
            rest = drop - nowhale * scale
        else:
            rest = drop
        if rest > 0:
            _ribbon(ax, w, x2, a, a + rest, oy - rest, oy, ORANGE)
            oy -= rest
    # Column 3: unique records vs duplicates.
    x3 = 0.78
    uq = un["records_out"] * scale
    dup = un["duplicates_removed"] * scale
    ax.add_patch(Rectangle((x3, kept_top - uq), w, uq, color=BLUE))
    ax.add_patch(Rectangle((x3, kept_top - uq - gap - dup), w, dup, color=GREY))
    _ribbon(ax, x2 + w, x3, kept_top - uq, kept_top, kept_top - uq, kept_top, BLUE)
    _ribbon(ax, x2 + w, x3, kept_bot, kept_top - uq, kept_top - uq - gap - dup, kept_top - uq - gap, GREY)
    ax.text(x3 + w + 0.01, kept_top - uq / 2, f"final dataset\n{un['records_out']:,} records", va="center",
            fontsize=9, fontweight="bold")
    ax.text(x3 + w + 0.01, kept_top - uq - gap - dup / 2,
            f"duplicates removed\n{un['duplicates_removed']:,}\n({un['exact_duplicates']:,} same ID,\n"
            f"{un['fuzzy_duplicates']:,} same day & place)", va="center", fontsize=8.5)
    for x, lab in ((0, "1 · raw rows"), (x2, "2 · cleaning"), (x3, "3 · merging")):
        ax.text(x + w / 2, 1.04, lab, ha="center", fontsize=9, color=INK_MUTED)
    ax.set(xlim=(-0.2, 1.05), ylim=(ot_bot - 0.03, 1.08))
    ax.set_title(f"From {total:,} raw rows to {un['records_out']:,} records: what cleaning and merging removed",
                 fontsize=11, loc="left")
    return save(fig, out / "13_cleaning_flow_sankey.png")


def plot_coast(r: dict, out: Path) -> Path:
    """Empirical CDF of distance to coast per species (log axis), medians marked."""
    c = r["coast"]
    grid = np.asarray(c["grid_km"])
    by = {s["species"]: s for s in c["species"]}
    fig, ax = plt.subplots(figsize=(10, 6))
    for sp in SPECIES_ORDER:
        s = by[sp]
        ax.plot(grid, s["cdf"], color=SPECIES_COLOR[sp], lw=1.8)
        ax.plot(s["median"], 0.5, "o", color=SPECIES_COLOR[sp], ms=6, mec="white", mew=1)
    ax.axhline(0.5, color=AXIS_GREY, lw=0.6, ls=":")
    ax.set(xscale="log", xlim=(0.1, 3000), ylim=(0, 1.02), xlabel="distance to the nearest coastline (km, log scale)",
           ylabel="share of sightings at most this far out")
    ax.set_xticks([0.1, 1, 10, 100, 1000], ["0.1", "1", "10", "100", "1,000"])
    ax.legend([plt.Line2D([], [], color=SPECIES_COLOR[sp], lw=1.8) for sp in SPECIES_ORDER],
              [f"{common_name(sp)} · median {by[sp]['median']:.0f} km · n={by[sp]['n']:,}" for sp in SPECIES_ORDER],
              loc="lower right", frameon=False, fontsize=8)
    ax.set_title("How far from shore each species is seen (sightings since 1990; dot = median)", fontsize=10)
    return save(fig, out / "14_distance_to_coast_ecdf.png")


FIGURES = [plot_migration, plot_monthly_maps, plot_connectivity, plot_life_histories, plot_space_time_cube,
           plot_whaling, plot_record_types, plot_niche_ridgeline, plot_niche_joint, plot_ship_strike, plot_mpa,
           plot_upset, plot_cleaning_flow, plot_coast]
