"""The unified occurrence schema shared by every source."""
from __future__ import annotations

import pandas as pd

EVENT_TYPES = ("catch", "encounter", "sighting", "tag_fix")
DATE_PRECISIONS = ("day", "month", "year")
POSITION_QUALITIES = ("logbook", "interpolated", "gazetteer", "gps", "argos", "reported")

# Column -> pandas dtype. Order is the on-disk column order.
COLUMNS: dict[str, str] = {
    "record_id": "string",           # globally unique: "<source>:<source-local id>"
    "source": "string",              # whalinghistory | obis | gbif | seamap | movebank
    "dataset_id": "string",          # source dataset identifier
    "species_scientific": "string",  # WoRMS accepted name
    "species_common": "string",
    "worms_aphia_id": "Int64",
    "individual_id": "string",       # photo-ID / tag id, nullable
    "event_type": "string",          # see EVENT_TYPES
    "timestamp_utc": "datetime64[us, UTC]",  # start of the period given by date_precision
    "date_precision": "string",      # see DATE_PRECISIONS
    "lat": "float64",
    "lon": "float64",                # -180..180
    "position_quality": "string",    # see POSITION_QUALITIES
    "count": "Int64",
    "platform": "string",            # vessel / survey platform / tag type where known
    "license": "string",
    "citation": "string",
    "raw_ref": "string",             # pointer back to raw record: "<raw file>#<row key>"
    # --- extensions beyond the requested schema (all nullable) -------------------------------
    "occurrence_id": "string",       # original occurrenceID (used for cross-source dedup)
    "coord_uncertainty_m": "float64",
    "quality_flags": "string",       # ';'-separated flags; empty = clean
}


def conform(df: pd.DataFrame) -> pd.DataFrame:
    """Add missing columns, drop unknown ones, cast dtypes, order columns."""
    out = pd.DataFrame(index=df.index)
    for col, dtype in COLUMNS.items():
        s = df[col] if col in df.columns else pd.Series(pd.NA, index=df.index)
        if dtype.startswith("datetime64"):
            s = pd.to_datetime(s, utc=True).astype(dtype)
        elif dtype == "Int64":
            s = pd.to_numeric(s, errors="coerce").round().astype("Int64")
        elif dtype == "float64":
            s = pd.to_numeric(s, errors="coerce").astype("float64")
        else:
            s = s.astype("string")
        out[col] = s
    return out.reset_index(drop=True)


def validate(df: pd.DataFrame) -> list[str]:
    """Return a list of human-readable schema violations (empty = valid)."""
    problems = []
    missing = set(COLUMNS) - set(df.columns)
    if missing:
        problems.append(f"missing columns: {sorted(missing)}")
        return problems
    if df["record_id"].duplicated().any():
        problems.append(f"duplicate record_id: {int(df['record_id'].duplicated().sum())}")
    for col, allowed in (("event_type", EVENT_TYPES), ("date_precision", DATE_PRECISIONS),
                         ("position_quality", POSITION_QUALITIES)):
        bad = ~df[col].isna() & ~df[col].isin(allowed)
        if bad.any():
            problems.append(f"{col}: {int(bad.sum())} invalid values e.g. {df.loc[bad, col].iloc[0]!r}")
    if ((df["lat"].abs() > 90) | (df["lon"] < -180) | (df["lon"] > 180)).any():
        problems.append("coordinates out of range")
    for col in ("license", "citation", "source", "species_scientific"):
        if df[col].isna().any():
            problems.append(f"{col}: {int(df[col].isna().sum())} nulls")
    return problems
