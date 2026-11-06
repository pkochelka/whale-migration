"""Track-based position checks shared by logbook voyages and telemetry.

A fix is a *spike* when the legs into and out of it both exceed the maximum plausible speed while
its two neighbours are mutually consistent. For logbooks we then try to explain the spike as a
transcription error: a flipped hemisphere sign, swapped adjacent digits, or one wrong digit in
the integer degrees (e.g. 61.25 W written for 51.25 W). A correction is applied only when exactly
one candidate makes both legs plausible; otherwise the fix stays flagged as uncorrectable.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..geo import haversine_km


def leg_speeds(df: pd.DataFrame, group: str, time_col: str, min_dt_days: float = 1.0) -> pd.DataFrame:
    """Add dist_prev_km, dist_next_km, speed_in, speed_out (km/day) and dist_skip_km per group.

    `df` must already be sorted by group then time.
    """
    g = df.groupby(group, sort=False)
    lat, lon, t = df["lat"], df["lon"], df[time_col]
    plat, plon, pt = g["lat"].shift(1), g["lon"].shift(1), g[time_col].shift(1)
    nlat, nlon, nt = g["lat"].shift(-1), g["lon"].shift(-1), g[time_col].shift(-1)
    days = lambda a, b: np.maximum((a - b).dt.total_seconds() / 86400.0, min_dt_days)  # noqa: E731
    out = df.copy()
    out["dist_prev_km"] = haversine_km(plat, plon, lat, lon)
    out["dist_next_km"] = haversine_km(lat, lon, nlat, nlon)
    out["speed_in"] = out["dist_prev_km"] / days(t, pt)
    out["speed_out"] = out["dist_next_km"] / days(nt, t)
    out["speed_skip"] = haversine_km(plat, plon, nlat, nlon) / days(nt, pt)
    return out


def find_spikes(df: pd.DataFrame, vmax: float) -> pd.Series:
    return ((df["speed_in"] > vmax) & (df["speed_out"] > vmax) & (df["speed_skip"] <= vmax)).fillna(False)


def _int_digit_variants(value: float) -> list[tuple[float, str]]:
    """Alternatives for |value| with one integer digit changed or two adjacent digits swapped."""
    sign = -1.0 if value < 0 else 1.0
    a = abs(value)
    whole = int(a)
    frac = a - whole
    s = str(whole)
    out: set[tuple[float, str]] = set()
    for i in range(len(s)):
        for dch in "0123456789":
            if dch != s[i]:
                t = s[:i] + dch + s[i + 1:]
                out.add((sign * (int(t) + frac), "digit"))
    for i in range(len(s) - 1):
        t = s[:i] + s[i + 1] + s[i] + s[i + 2:]
        if t != s:
            out.add((sign * (int(t) + frac), "transposed"))
    return list(out)


def candidate_positions(lat: float, lon: float) -> list[tuple[float, float, str]]:
    c = [(-lat, lon, "lat_hemisphere"), (lat, -lon, "lon_hemisphere"), (-lat, -lon, "both_hemispheres"),
         (lon, lat, "lat_lon_swapped")]
    c += [(v, lon, f"lat_{k}") for v, k in _int_digit_variants(lat)]
    c += [(lat, v, f"lon_{k}") for v, k in _int_digit_variants(lon)]
    return [(a, b, k) for a, b, k in c if -90 <= a <= 90 and -180 <= b <= 180]


def correct_spikes(df: pd.DataFrame, spikes: pd.Series, vmax: float, time_col: str, group: str
                   ) -> pd.DataFrame:
    """Try to correct each spike. Returns DataFrame indexed like spikes with
    columns new_lat, new_lon, kind (None if uncorrectable)."""
    res = []
    g = df.groupby(group, sort=False)
    prev = df[["lat", "lon", time_col]].groupby(df[group], sort=False).shift(1)
    nxt = df[["lat", "lon", time_col]].groupby(df[group], sort=False).shift(-1)
    del g
    for i in df.index[spikes]:
        lat, lon = df.at[i, "lat"], df.at[i, "lon"]
        p, n = prev.loc[i], nxt.loc[i]
        dt_in = max((df.at[i, time_col] - p[time_col]).total_seconds() / 86400, 1.0)
        dt_out = max((n[time_col] - df.at[i, time_col]).total_seconds() / 86400, 1.0)
        ok = []
        for clat, clon, kind in candidate_positions(lat, lon):
            v_in = haversine_km(p["lat"], p["lon"], clat, clon) / dt_in
            v_out = haversine_km(clat, clon, n["lat"], n["lon"]) / dt_out
            if v_in <= vmax and v_out <= vmax:
                ok.append((v_in + v_out, clat, clon, kind))
        ok.sort()
        # Unique fit, or one fit that is clearly better than the runner-up (< half the speed).
        if len(ok) == 1 or (len(ok) > 1 and ok[0][0] < 0.5 * ok[1][0]):
            res.append((i, ok[0][1], ok[0][2], ok[0][3]))
        else:
            res.append((i, np.nan, np.nan, None))
    return pd.DataFrame(res, columns=["idx", "new_lat", "new_lon", "kind"]).set_index("idx")
