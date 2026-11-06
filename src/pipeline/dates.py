"""Parse heterogeneous date strings into (UTC timestamp, precision, flags).

Rules
-----
* The timestamp is the *start* of the period the source actually states; `date_precision`
  ("day" | "month" | "year") says how much of it is real. We never invent a day or month.
* ISO 8601 forms (``YYYY``, ``YYYY-MM``, ``YYYY-MM-DD``, with optional time and offset) are
  parsed vectorised. Slashes are accepted as separators.
* Intervals ``A/B`` collapse to the coarsest precision that contains both ends
  (same day -> day, same month -> month, same year -> year); wider ranges are rejected.
* Non-ISO strings go through a strict fallback: ``DD/MM/YYYY``-style dates are only accepted
  when unambiguous (day > 12); ambiguous ones degrade to year precision and are flagged.
* A time without an offset is assumed UTC and flagged ``tz_assumed_utc``.
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

ISO = re.compile(
    r"^\s*(?P<y>\d{4})"
    r"(?:[-/](?P<m>\d{1,2})"
    r"(?:[-/](?P<d>\d{1,2})"
    r"(?:[T ](?P<H>\d{1,2}):(?P<M>\d{2})(?::(?P<S>\d{2})(?:\.\d+)?)?\s*(?P<tz>Z|[+-]\d{2}:?\d{2})?)?"
    r")?)?\s*$"
)
DMY = re.compile(r"^\s*(\d{1,2})[./-](\d{1,2})[./-](\d{4})\s*$")
MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
TEXTUAL = re.compile(r"^\s*(?:(\d{1,2})\s+)?([A-Za-z]{3,})\.?,?\s+(\d{4})\s*$")

MIN_YEAR = 1700
PREC_RANK = {"day": 0, "month": 1, "year": 2}


def _tz_minutes(tz: pd.Series) -> pd.Series:
    tz = tz.fillna("")
    sign = np.where(tz.str.startswith("-"), -1, 1)
    digits = tz.str.replace(r"[^0-9]", "", regex=True)
    hh = pd.to_numeric(digits.str[:2], errors="coerce").fillna(0)
    mm = pd.to_numeric(digits.str[2:4], errors="coerce").fillna(0)
    return pd.Series(sign * (hh * 60 + mm), index=tz.index)


def _assemble(y, m, d, H=None, M=None, S=None) -> pd.Series:
    """Vectorised timestamp assembly; invalid combos -> NaT. Works for years >= 1700."""
    idx = y.index
    parts = pd.DataFrame({
        "y": y, "m": m.fillna(1), "d": d.fillna(1),
        "H": (H if H is not None else pd.Series(0, index=idx)).fillna(0),
        "M": (M if M is not None else pd.Series(0, index=idx)).fillna(0),
        "S": (S if S is not None else pd.Series(0, index=idx)).fillna(0),
    }).astype("float64")
    ok = parts["y"].notna()
    s = pd.Series(pd.NaT, index=idx, dtype="datetime64[us, UTC]")
    if ok.any():
        p = parts[ok].astype("int64")
        txt = (p["y"].astype(str).str.zfill(4) + "-" + p["m"].astype(str).str.zfill(2) + "-"
               + p["d"].astype(str).str.zfill(2) + "T" + p["H"].astype(str).str.zfill(2) + ":"
               + p["M"].astype(str).str.zfill(2) + ":" + p["S"].astype(str).str.zfill(2))
        s.loc[ok] = pd.to_datetime(txt, format="%Y-%m-%dT%H:%M:%S", errors="coerce", utc=True
                                   ).astype("datetime64[us, UTC]")
    return s


def _parse_single(raw: pd.Series) -> pd.DataFrame:
    """Parse non-interval strings. Returns columns ts, precision, flags."""
    raw = raw.astype("string")
    out = pd.DataFrame(index=raw.index)
    out["ts"] = pd.Series(pd.NaT, index=raw.index, dtype="datetime64[us, UTC]")
    out["precision"] = pd.Series(pd.NA, index=raw.index, dtype="string")
    out["flags"] = pd.Series("", index=raw.index, dtype="string")

    g = raw.str.extract(ISO)
    num = {k: pd.to_numeric(g[k], errors="coerce") for k in "ymdHMS"}
    iso_ok = num["y"].notna()

    prec = pd.Series(pd.NA, index=raw.index, dtype="string")
    prec[iso_ok] = "year"
    prec[iso_ok & num["m"].notna()] = "month"
    prec[iso_ok & num["d"].notna()] = "day"

    ts = _assemble(num["y"], num["m"], num["d"], num["H"], num["M"], num["S"])
    has_time = num["H"].notna()
    offset = _tz_minutes(g["tz"])
    ts = ts - pd.to_timedelta(offset.where(has_time, 0), unit="min")

    # Invalid day (e.g. 31 Feb) -> fall back to month; invalid month -> year.
    bad = iso_ok & ts.isna()
    if bad.any():
        month_ok = num["m"][bad].between(1, 12)
        ts_m = _assemble(num["y"][bad], num["m"][bad].where(month_ok),
                         pd.Series(np.nan, index=num["y"][bad].index))
        ts.loc[bad] = ts_m
        prec.loc[bad] = np.where(month_ok, "month", "year")
        out.loc[bad, "flags"] = np.where(month_ok, "invalid_day", "invalid_month")

    out["ts"] = ts
    out["precision"] = prec
    tz_missing = iso_ok & has_time & g["tz"].isna()
    out.loc[tz_missing, "flags"] = (out.loc[tz_missing, "flags"] + ";tz_assumed_utc").str.lstrip(";")

    # Fallback for the (few) non-ISO strings.
    rest = raw.notna() & ~iso_ok & (raw.str.strip() != "")
    for i, v in raw[rest].items():
        ts_i, p_i, f_i = _parse_fallback(str(v))
        out.at[i, "ts"] = ts_i
        out.at[i, "precision"] = p_i
        out.at[i, "flags"] = f_i
    return out


def _parse_fallback(v: str) -> tuple[pd.Timestamp, str | None, str]:
    m = DMY.match(v)
    if m:
        a, b, y = (int(x) for x in m.groups())
        if a > 12 and b <= 12:      # unambiguous DD/MM/YYYY
            d, mo, flag = a, b, "date_dmy"
        elif b > 12 and a <= 12:    # unambiguous MM/DD/YYYY
            d, mo, flag = b, a, "date_mdy"
        elif a == b:
            d, mo, flag = a, a, "date_dmy"
        else:
            return _ts(y, 1, 1), "year", "date_ambiguous_dm"
        t = _ts(y, mo, d)
        return (t, "day", flag) if t is not pd.NaT else (_ts(y, 1, 1), "year", "invalid_day")
    m = TEXTUAL.match(v)
    if m:
        d, mon, y = m.groups()
        mo = MONTHS.get(mon[:3].lower())
        if mo:
            if d:
                t = _ts(int(y), mo, int(d))
                if t is not pd.NaT:
                    return t, "day", "date_textual"
            return _ts(int(y), mo, 1), "month", "date_textual"
    return pd.NaT, None, "unparseable_date"


def _ts(y: int, m: int, d: int) -> pd.Timestamp:
    try:
        return pd.Timestamp(year=y, month=m, day=d, tz="UTC").as_unit("us")
    except (ValueError, OverflowError):
        return pd.NaT


def parse_dates(values: pd.Series) -> pd.DataFrame:
    """Parse a Series of date strings. Returns DataFrame[ts, precision, flags] with same index."""
    values = values.astype("string").str.strip()
    values = values.mask(values == "")
    is_range = values.str.contains("/", regex=False, na=False) & values.str.match(
        r"^\s*\d{4}[-\d:T .+Z]*\s*/\s*\d{4}", na=False)
    out = _parse_single(values.where(~is_range))
    if is_range.any():
        parts = values[is_range].str.split("/", n=1, expand=True)
        a, b = _parse_single(parts[0]), _parse_single(parts[1])
        res = _combine_interval(a, b)
        out.loc[is_range, ["ts", "precision", "flags"]] = res[["ts", "precision", "flags"]]
    # Sanity range.
    too_old = out["ts"].notna() & (out["ts"].dt.year < MIN_YEAR)
    future = out["ts"].notna() & (out["ts"] > pd.Timestamp.now(tz="UTC") + pd.Timedelta(days=1))
    for mask, flag in ((too_old, "date_before_1700"), (future, "date_in_future")):
        out.loc[mask, "ts"] = pd.NaT
        out.loc[mask, "precision"] = pd.NA
        out.loc[mask, "flags"] = (out.loc[mask, "flags"] + ";" + flag).str.lstrip(";")
    out.loc[out["ts"].isna() & values.notna() & (out["flags"] == ""), "flags"] = "unparseable_date"
    return out


def _combine_interval(a: pd.DataFrame, b: pd.DataFrame) -> pd.DataFrame:
    res = pd.DataFrame(index=a.index)
    ta, tb = a["ts"], b["ts"]
    ok = ta.notna() & tb.notna() & (tb >= ta)
    same_day = ok & (ta.dt.floor("D") == tb.dt.floor("D"))
    same_month = ok & (ta.dt.year == tb.dt.year) & (ta.dt.month == tb.dt.month)
    same_year = ok & (ta.dt.year == tb.dt.year)
    prec = pd.Series(pd.NA, index=a.index, dtype="string")
    prec[same_year] = "year"
    prec[same_month] = "month"
    prec[same_day] = "day"
    # Respect the coarser of the two endpoint precisions.
    for side in (a, b):
        coarser = prec.notna() & side["precision"].notna() & (
            side["precision"].map(PREC_RANK) > prec.map(PREC_RANK))
        prec[coarser] = side["precision"][coarser]
    ts = ta.copy()
    ts[prec == "month"] = ta[prec == "month"].dt.tz_localize(None).dt.to_period("M").dt.start_time.dt.tz_localize("UTC")
    ts[prec == "year"] = ta[prec == "year"].dt.tz_localize(None).dt.to_period("Y").dt.start_time.dt.tz_localize("UTC")
    ts[prec.isna()] = pd.NaT
    res["ts"] = ts.astype("datetime64[us, UTC]")
    res["precision"] = prec
    flags = pd.Series("date_range", index=a.index, dtype="string")
    flags[prec.isna()] = "date_range_too_wide"
    res["flags"] = flags
    return res


def from_parts(year: pd.Series, month: pd.Series | None = None, day: pd.Series | None = None
               ) -> pd.DataFrame:
    """Build (ts, precision, flags) from separate Y/M/D columns (any may be null)."""
    y = pd.to_numeric(year, errors="coerce")
    m = pd.to_numeric(month, errors="coerce") if month is not None else pd.Series(np.nan, index=y.index)
    d = pd.to_numeric(day, errors="coerce") if day is not None else pd.Series(np.nan, index=y.index)
    m = m.where(m.between(1, 12))
    d = d.where(m.notna() & d.between(1, 31))
    s = pd.Series(pd.NA, index=y.index, dtype="string")
    txt = y.astype("Int64").astype("string")
    txt = txt.where(m.isna(), txt + "-" + m.astype("Int64").astype("string").str.zfill(2))
    txt = txt.where(d.isna(), txt + "-" + d.astype("Int64").astype("string").str.zfill(2))
    s[y.notna()] = txt[y.notna()]
    return parse_dates(s)
