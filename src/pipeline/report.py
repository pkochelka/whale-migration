"""Per-source cleaning report: rows in, dropped, flagged, and why."""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


@dataclass
class CleaningReport:
    source: str
    rows_in: int = 0
    rows_out: int = 0
    dropped: Counter = field(default_factory=Counter)
    flagged: Counter = field(default_factory=Counter)
    notes: list[str] = field(default_factory=list)

    def drop(self, df: pd.DataFrame, mask: pd.Series, reason: str) -> pd.DataFrame:
        """Remove rows where mask is True and record why."""
        mask = mask.fillna(False).astype(bool)
        n = int(mask.sum())
        if n:
            self.dropped[reason] += n
        return df.loc[~mask]

    def flag(self, df: pd.DataFrame, mask: pd.Series, flag: str) -> pd.DataFrame:
        """Append `flag` to quality_flags where mask is True."""
        mask = mask.fillna(False).astype(bool)
        n = int(mask.sum())
        if n:
            self.flagged[flag] += n
            df = df.copy()
            if "quality_flags" not in df:
                df["quality_flags"] = pd.Series("", index=df.index, dtype="string")
            qf = df["quality_flags"].fillna("").astype("string")
            df.loc[mask, "quality_flags"] = (qf[mask] + ";" + flag).str.lstrip(";")
        return df

    def note(self, msg: str) -> None:
        self.notes.append(msg)

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "rows_in": self.rows_in,
            "rows_out": self.rows_out,
            "dropped_total": sum(self.dropped.values()),
            "dropped": dict(self.dropped.most_common()),
            "flagged": dict(self.flagged.most_common()),
            "notes": self.notes,
        }

    def save(self, directory: Path) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        p = directory / f"{self.source}.json"
        p.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        return p


def load_reports(directory: Path) -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(directory.glob("*.json"))]


def reports_markdown(reports: list[dict]) -> str:
    lines = ["| source | rows in | dropped | kept | top drop reasons | top flags |",
             "|---|---:|---:|---:|---|---|"]
    for r in reports:
        drops = ", ".join(f"{k} ({v:,})" for k, v in list(r["dropped"].items())[:4]) or "—"
        flags = ", ".join(f"{k} ({v:,})" for k, v in list(r["flagged"].items())[:4]) or "—"
        lines.append(f"| {r['source']} | {r['rows_in']:,} | {r['dropped_total']:,} | "
                     f"{r['rows_out']:,} | {drops} | {flags} |")
    return "\n".join(lines)
