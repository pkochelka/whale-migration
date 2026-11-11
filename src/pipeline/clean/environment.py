"""Environmental layers -> analysis-ready files in data/processed/env/.

All gridded outputs share one convention so they can be joined to occurrences directly:
monthly `time` stamped at the first of the month (UTC), `lat` ascending, `lon` in -180..180
ascending, CF attributes, zlib-compressed NetCDF.

  sst_monthly.nc                    HadISST 1 deg, 1870-present (sea-ice cells -> NaN + ice mask)
  chlorophyll_monthly.nc            ESA OC-CCI chlor_a, sampled 0.5 deg -> 1 deg nan-mean
  sea_ice_concentration_monthly.nc  NSIDC CDR v6, both hemispheres, binned to 1 deg mean
  sea_ice_extent_monthly.csv        NSIDC Sea Ice Index monthly extent/area per hemisphere
  marine_protected_areas.parquet    WDPA/WD-OECM marine polygons (GeoParquet, simplified)
  ship_density.nc                   IMF/World Bank AIS density summed to 0.1 deg
"""
from __future__ import annotations

import io
import logging
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

from . import SourceUnavailable, output_path

log = logging.getLogger(__name__)
ENC = {"zlib": True, "complevel": 4}


def _standardize(da: xr.DataArray) -> xr.DataArray:
    da = da.rename({k: v for k, v in {"latitude": "lat", "longitude": "lon"}.items() if k in da.dims})
    if "lon" in da.dims:
        da = da.assign_coords(lon=((da["lon"] + 180) % 360) - 180).sortby("lon")
    if "lat" in da.dims:
        da = da.sortby("lat")
    if "time" in da.dims:
        t = pd.DatetimeIndex(da["time"].values).to_period("M").to_timestamp()
        da = da.assign_coords(time=t)
    for extra in [d for d in da.dims if d not in ("time", "lat", "lon")]:
        da = da.isel({extra: 0}, drop=True)  # e.g. ERDDAP altitude/depth singleton
    return da


def _write(ds: xr.Dataset, path: Path, attrs: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ds.attrs.update({k: v for k, v in attrs.items() if v})
    ds.attrs["processing"] = "whale-pipeline clean stage; see README.md"
    tmp = path.with_suffix(".tmp.nc")
    ds.to_netcdf(tmp, encoding={v: ENC for v in ds.data_vars})
    tmp.replace(path)
    log.info("wrote %s (%s)", path, dict(ds.sizes))


def _concat_years(directory: Path, variable: str) -> xr.DataArray:
    files = sorted(directory.glob("*.nc"))
    if not files:
        raise SourceUnavailable(f"no files in {directory}")
    parts = []
    for f in files:
        with xr.open_dataset(f) as ds:
            parts.append(_standardize(ds[variable].load()))
    da = xr.concat(parts, dim="time")
    return da.sel(time=~da.get_index("time").duplicated())


def clean_sst(cfg) -> None:
    sc = cfg.source("sst")
    da = _concat_years(cfg.raw / f"erddap_{sc['dataset_id']}", sc["variable"])
    ice = da <= -999  # HadISST marks sea-ice-covered cells with -1000
    sst = da.where(~ice).astype("float32")
    sst.attrs = {"long_name": "sea surface temperature", "units": "degC", "standard_name": "sea_surface_temperature"}
    ds = xr.Dataset({"sst": sst, "ice_flag": ice.astype("int8").assign_attrs(
        long_name="1 where HadISST marks the cell as sea-ice covered (sst undefined)")})
    _write(ds, output_path(cfg, "sst"), {"source": f"NOAA ERDDAP {sc['dataset_id']}",
                                         "license": sc.get("license"), "citation": sc.get("citation", "").strip()})


def clean_chlorophyll(cfg) -> None:
    sc = cfg.source("chlorophyll")
    da = _concat_years(cfg.raw / f"erddap_{sc['dataset_id']}", sc["variable"])
    k = int(sc.get("coarsen", 1))
    if k > 1:
        da = da.coarsen(lat=k, lon=k, boundary="trim").mean()  # nan-aware by default
    chl = da.astype("float32")
    chl.attrs = {"long_name": "chlorophyll-a concentration", "units": "mg m-3",
                 "comment": f"sampled every {sc.get('stride')} native cells, then {k}x{k} nan-mean"}
    years = sorted({int(t[:4]) for t in pd.DatetimeIndex(chl.time.values).strftime("%Y")})
    gaps = sorted(set(range(years[0], years[-1] + 1)) - set(years))
    if gaps:
        log.warning("chlorophyll: years missing from raw cache (re-run fetch chlorophyll): %s", gaps)
    _write(xr.Dataset({"chlor_a": chl}), output_path(cfg, "chlorophyll"),
           {"source": f"NOAA ERDDAP {sc['dataset_id']}", "license": sc.get("license"),
            "citation": sc.get("citation", "").strip(), "missing_years": ",".join(map(str, gaps))})


def _bin_polar(path: Path, res: float = 1.0) -> xr.DataArray:
    """Polar-stereographic monthly SIC -> regular lat/lon grid by bin-averaging cell centres."""
    from pyproj import CRS, Transformer
    with xr.open_dataset(path) as ds:
        conc = ds["cdr_seaice_conc_monthly"].load()
        crs = CRS.from_wkt(ds["crs"].attrs["crs_wkt"])
        x, y = np.meshgrid(ds["x"].values, ds["y"].values)
    lon, lat = Transformer.from_crs(crs, 4326, always_xy=True).transform(x, y)
    nlat, nlon = int(180 / res), int(360 / res)
    li = np.clip(((lat + 90) / res).astype(int), 0, nlat - 1)
    lj = np.clip(((lon + 180) / res).astype(int), 0, nlon - 1)
    flat = (li * nlon + lj).ravel()
    vals = conc.values.reshape(conc.shape[0], -1).astype("float64")
    vals[(vals < 0) | (vals > 1.0001)] = np.nan       # flags (land, pole hole, coast) -> NaN
    out = np.full((conc.shape[0], nlat * nlon), np.nan, dtype="float32")
    for t in range(conc.shape[0]):
        v = vals[t]
        ok = ~np.isnan(v)
        s = np.bincount(flat[ok], weights=v[ok], minlength=nlat * nlon)
        n = np.bincount(flat[ok], minlength=nlat * nlon)
        with np.errstate(invalid="ignore", divide="ignore"):
            out[t] = np.where(n > 0, s / n, np.nan)
    lat_c = -90 + res / 2 + res * np.arange(nlat)
    lon_c = -180 + res / 2 + res * np.arange(nlon)
    t = pd.DatetimeIndex(conc["time"].values).to_period("M").to_timestamp()
    return xr.DataArray(out.reshape(-1, nlat, nlon), dims=("time", "lat", "lon"),
                        coords={"time": t, "lat": lat_c, "lon": lon_c})


def clean_sea_ice(cfg) -> None:
    from ..fetch.nsidc import local_aggregate  # file names change monthly; fetch knows the pattern
    sc = cfg.source("sea_ice")
    d = cfg.raw / "nsidc"
    files = [local_aggregate(d, hemi) for hemi in ("north", "south")]
    if not all(files):
        raise SourceUnavailable("NSIDC files missing; run `python -m pipeline fetch sea_ice`")
    grids = [_bin_polar(f) for f in files]
    # Hemispheres do not overlap (north grid >= ~31N, south <= ~-39S): combine.
    sic = grids[0].combine_first(grids[1]).astype("float32")
    sic.attrs = {"long_name": "sea ice concentration (fraction)", "units": "1",
                 "comment": "NSIDC CDR v6 monthly, 25 km polar stereographic cells bin-averaged to 1 deg"}
    _write(xr.Dataset({"sic": sic}), output_path(cfg, "sea_ice"),
           {"source": "NSIDC G02202 v6", "license": sc.get("license"), "citation": sc.get("citation", "").strip()})
    rows = []
    for f in sorted((d / "extent").glob("*_extent_v*.csv")):
        e = pd.read_csv(f, skipinitialspace=True)
        e.columns = [c.strip() for c in e.columns]
        rows.append(e)
    if rows:
        ext = pd.concat(rows).rename(columns={"mo": "month"})
        ext = ext.replace(-9999, np.nan).sort_values(["region", "year", "month"])
        ext.to_csv(output_path(cfg, "sea_ice").with_name("sea_ice_extent_monthly.csv"), index=False)


def clean_mpa(cfg) -> None:
    import geopandas as gpd
    sc = cfg.source("mpa")
    zips = sorted((cfg.raw / "wdpa").glob("WDPA_*_marine_shp.zip"))
    if not zips:
        raise SourceUnavailable("WDPA not fetched; run `python -m pipeline fetch mpa`")
    keep = ["WDPAID", "SITE_ID", "NAME", "NAME_ENG", "DESIG_ENG", "IUCN_CAT", "STATUS", "STATUS_YR",
            "MARINE", "REP_M_AREA", "GIS_M_AREA", "NO_TAKE", "ISO3", "PA_DEF", "REALM"]
    parts = []
    with zipfile.ZipFile(zips[-1]) as outer:
        for inner_name in [n for n in outer.namelist() if n.endswith(".zip")]:
            with zipfile.ZipFile(io.BytesIO(outer.read(inner_name))) as inner:
                for shp in [n for n in inner.namelist() if n.endswith(".shp") and "polygons" in n.lower()]:
                    tmp = cfg.interim / "cache" / "wdpa"
                    tmp.mkdir(parents=True, exist_ok=True)
                    stem = shp[:-4]
                    for ext in (".shp", ".shx", ".dbf", ".prj", ".cpg"):
                        if stem + ext in inner.namelist():
                            (tmp / Path(stem + ext).name).write_bytes(inner.read(stem + ext))
                    g = gpd.read_file(tmp / Path(shp).name)
                    parts.append(g[[c for c in keep if c in g.columns] + ["geometry"]])
    gdf = pd.concat(parts, ignore_index=True)
    gdf = gpd.GeoDataFrame(gdf, geometry="geometry", crs=parts[0].crs).to_crs(4326)
    n0 = len(gdf)
    if "MARINE" in gdf:
        gdf = gdf[gdf["MARINE"].astype(str).isin(["1", "2"])]       # 1 coastal, 2 marine
    if "STATUS" in gdf:
        gdf = gdf[~gdf["STATUS"].isin(["Proposed", "Not Reported"])]
    gdf["geometry"] = gdf.geometry.simplify(float(sc.get("simplify_deg", 0.01)), preserve_topology=True).make_valid()
    gdf = gdf[~gdf.geometry.is_empty]
    out = output_path(cfg, "mpa")
    out.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_parquet(out, index=False)
    log.info("MPA: %d polygons -> %d marine designated/inscribed/established; wrote %s", n0, len(gdf), out)


def clean_shipping(cfg) -> None:
    import rasterio
    from rasterio.enums import Resampling
    sc = cfg.source("shipping")
    z = cfg.raw / "shipping" / "shipdensity_global.zip"
    if not z.exists():
        raise SourceUnavailable("shipping density not fetched; run `python -m pipeline fetch shipping`")
    res = float(sc.get("target_resolution_deg", 0.1))
    with rasterio.open(f"/vsizip/{z.as_posix()}/shipdensity_global.tif") as src:
        left, bottom, right, top = src.bounds
        h, w = int(round((top - bottom) / res)), int(round((right - left) / res))
        fy, fx = src.height / h, src.width / w
        arr = src.read(1, out_shape=(h, w), resampling=Resampling.average, masked=True).astype("float64")
        arr = (arr * fx * fy).filled(np.nan)  # mean * cells per block = total AIS positions per block
    lat = top - res / 2 - res * np.arange(h)
    lon = left + res / 2 + res * np.arange(w)
    da = xr.DataArray(arr.astype("float32"), dims=("lat", "lon"), coords={"lat": lat, "lon": lon}).sortby("lat")
    da.attrs = {"long_name": "ship density (AIS positions per cell, 2015-2021 total)", "units": "count",
                "comment": f"native ~0.005 deg summed to {res} deg (decimated average x cells)"}
    _write(xr.Dataset({"ship_density": da}), output_path(cfg, "shipping"),
           {"source": "IMF / World Bank Global Shipping Traffic Density", "license": sc.get("license"),
            "citation": sc.get("citation", "").strip()})


FUNCS = {"sst": clean_sst, "chlorophyll": clean_chlorophyll, "sea_ice": clean_sea_ice, "mpa": clean_mpa,
         "shipping": clean_shipping}


def clean(cfg, name: str) -> None:
    FUNCS[name](cfg)
