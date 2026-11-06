"""Normalise free-text / URL licenses to short SPDX-style identifiers."""
from __future__ import annotations

import re

import pandas as pd


def normalize_license(value) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)) or value is pd.NA:
        return None
    s = str(value).strip()
    if not s:
        return None
    low = s.lower().replace("_", "-")
    ver = re.search(r"(\d\.\d)", low)
    v = ver.group(1) if ver else "4.0"
    if "cc0" in low or "publicdomain/zero" in low or "public domain" in low or "zero/1.0" in low:
        return "CC0-1.0"
    nc = bool(re.search(r"by-nc|non-?commercial|noncommercial|\bnc\b", low))
    nd = bool(re.search(r"-nd\b|noderiv", low))
    sa = bool(re.search(r"-sa\b|sharealike|share-alike", low))
    by = bool(re.search(r"\bcc[- ]?by|licenses/by|attribution|creative commons", low))
    if by or nc:
        tag = "CC-BY" + ("-NC" if nc else "") + ("-ND" if nd else "") + ("-SA" if sa else "")
        return f"{tag}-{v}"
    return s[:120]


def normalize_series(s: pd.Series) -> pd.Series:
    uniq = {u: normalize_license(u) for u in s.dropna().unique()}
    return s.map(uniq).astype("string")
