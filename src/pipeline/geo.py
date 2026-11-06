"""Coordinate utilities: longitude conventions, distances, land mask, distance to coast."""
from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

EARTH_RADIUS_KM = 6371.0088
NE_URL = "https://naciscdn.org/naturalearth/{scale}/physical/ne_{scale}_{layer}.zip"


# -- longitude conventions ---------------------------------------------------------------------
def normalize_lon(lon):
    """Wrap longitudes to [-180, 180). Accepts scalars, arrays or Series."""
    wrapped = (np.asarray(lon, dtype="float64") + 180.0) % 360.0 - 180.0
    if isinstance(lon, pd.Series):
        return pd.Series(wrapped, index=lon.index, name=lon.name)
    return wrapped if np.ndim(wrapped) else float(wrapped)


def to_0_360(lon):
    """Convert longitudes from -180..180 to 0..360 (Pacific-centred maps, 0..360 grids)."""
    out = np.asarray(lon, dtype="float64") % 360.0
    if isinstance(lon, pd.Series):
        return pd.Series(out, index=lon.index, name=lon.name)
    return out if np.ndim(out) else float(out)


def lon_diff(a, b):
    """Smallest signed difference a-b in degrees, correct across the antimeridian."""
    return (np.asarray(a, dtype="float64") - np.asarray(b, dtype="float64") + 180.0) % 360.0 - 180.0


def haversine_km(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = (np.radians(np.asarray(x, dtype="float64")) for x in (lat1, lon1, lat2, lon2))
    a = (np.sin((lat2 - lat1) / 2) ** 2
         + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2)
    return 2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


# -- validity ----------------------------------------------------------------------------------
def coordinate_issues(lat: pd.Series, lon: pd.Series) -> pd.DataFrame:
    """Boolean issue columns for raw coordinates (before normalisation)."""
    lat = pd.to_numeric(lat, errors="coerce")
    lon = pd.to_numeric(lon, errors="coerce")
    return pd.DataFrame({
        "missing": lat.isna() | lon.isna(),
        "lat_out_of_range": lat.abs() > 90,
        # 180..360 is a legitimate convention and gets wrapped; beyond that is invalid.
        "lon_out_of_range": (lon < -180) | (lon > 360),
        "null_island": (lat == 0) & (lon == 0),
    })


# -- land mask / coastline ---------------------------------------------------------------------
def natural_earth_path(raw_dir: Path, layer: str = "land", scale: str = "10m", http=None) -> Path:
    dest = Path(raw_dir) / "naturalearth" / f"ne_{scale}_{layer}.zip"
    if not dest.exists():
        from .http import HttpClient
        (http or HttpClient()).download(NE_URL.format(scale=scale, layer=layer), dest,
                                        meta={"license": "Public domain (Natural Earth)"})
    return dest


@lru_cache(maxsize=4)
def _load_land(path: str):
    import geopandas as gpd
    land = gpd.read_file(f"zip://{path}")
    return land.to_crs(4326)


@lru_cache(maxsize=4)
def _land_tiles(path: str, cache_dir: str | None = None, tile_deg: float = 1.0):
    """Land polygons cut into tile_deg x tile_deg pieces (cached on disk next to the zip).

    Point-in-polygon against whole continents is very slow; small tiles make it ~100x faster.
    """
    import shapely
    cache = Path(cache_dir or Path(path).parent) / (Path(path).stem + f"_tiles{tile_deg:g}.wkb.npy")
    cache.parent.mkdir(parents=True, exist_ok=True)
    if cache.exists():
        return shapely.from_wkb(np.load(cache, allow_pickle=True))
    land = _load_land(path).geometry.values
    tree = shapely.STRtree(land)
    pieces = []
    for x in np.arange(-180, 180, tile_deg):
        for y in np.arange(-90, 90, tile_deg):
            box = shapely.box(x, y, x + tile_deg, y + tile_deg)
            for i in tree.query(box, predicate="intersects"):
                g = shapely.intersection(land[i], box)
                if not g.is_empty:
                    pieces.append(g)
    arr = np.array(pieces, dtype=object)
    shapely.prepare(arr)
    np.save(cache, shapely.to_wkb(arr), allow_pickle=True)
    return arr


def on_land(lat: pd.Series, lon: pd.Series, land_zip: Path, cache_dir: Path | None = None) -> pd.Series:
    """True where the point falls inside a Natural Earth land polygon."""
    import shapely
    tiles = _land_tiles(str(land_zip), str(cache_dir) if cache_dir else None)
    tree = shapely.STRtree(tiles)
    pts = shapely.points(np.asarray(lon, "float64"), np.asarray(lat, "float64"))
    hit = np.zeros(len(pts), dtype=bool)
    valid = ~(np.isnan(np.asarray(lat, "float64")) | np.isnan(np.asarray(lon, "float64")))
    if valid.any():
        idx_pts, _ = tree.query(pts[valid], predicate="intersects")
        sub = np.zeros(valid.sum(), dtype=bool)
        sub[np.unique(idx_pts)] = True
        hit[valid] = sub
    return pd.Series(hit, index=lat.index)


@lru_cache(maxsize=4)
def _coast_tree(path: str, spacing_km: float = 2.0):
    """KD-tree over densified coastline vertices in 3-D unit-sphere coordinates."""
    import shapely
    from scipy.spatial import cKDTree
    land = _load_land(path)
    boundary = shapely.boundary(land.geometry.values)
    seg = shapely.segmentize(boundary, max_segment_length=spacing_km / 111.0)
    coords = shapely.get_coordinates(seg)
    lon, lat = np.radians(coords[:, 0]), np.radians(coords[:, 1])
    xyz = np.column_stack([np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat)])
    return cKDTree(xyz)


def distance_to_coast_km(lat: pd.Series, lon: pd.Series, land_zip: Path) -> pd.Series:
    """Great-circle distance (km) to the nearest Natural Earth coastline vertex (~2 km spacing)."""
    tree = _coast_tree(str(land_zip))
    la, lo = np.radians(np.asarray(lat, "float64")), np.radians(np.asarray(lon, "float64"))
    out = np.full(len(la), np.nan)
    ok = ~(np.isnan(la) | np.isnan(lo))
    xyz = np.column_stack([np.cos(la[ok]) * np.cos(lo[ok]), np.cos(la[ok]) * np.sin(lo[ok]), np.sin(la[ok])])
    chord, _ = tree.query(xyz, workers=-1)
    out[ok] = 2 * EARTH_RADIUS_KM * np.arcsin(np.clip(chord / 2, 0, 1))
    return pd.Series(out, index=lat.index)

