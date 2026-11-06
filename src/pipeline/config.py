"""Configuration loading and project paths."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PROJECT_ROOT / "config.yaml"


@dataclass
class Config:
    data: dict[str, Any]
    root: Path = field(default=PROJECT_ROOT)

    def __getitem__(self, key: str) -> Any:
        return self.data[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    # -- paths ---------------------------------------------------------------------------------
    def _path(self, key: str) -> Path:
        p = Path(self.data["paths"][key])
        return p if p.is_absolute() else self.root / p

    @property
    def raw(self) -> Path:
        return self._path("raw")

    @property
    def interim(self) -> Path:
        return self._path("interim")

    @property
    def processed(self) -> Path:
        return self._path("processed")

    def raw_dir(self, source: str) -> Path:
        d = self.raw / source
        d.mkdir(parents=True, exist_ok=True)
        return d

    # -- convenience ---------------------------------------------------------------------------
    def source(self, name: str) -> dict[str, Any]:
        return self.data["sources"].get(name) or self.data["environment"].get(name) or {}

    @property
    def species(self) -> list[dict[str, Any]]:
        return self.data["species"]

    @property
    def species_names(self) -> list[str]:
        return [s["scientific"] for s in self.species]

    def common_name(self, scientific: str) -> str | None:
        for s in self.species:
            if s["scientific"] == scientific:
                return s.get("common")
        return None


def load_config(path: str | os.PathLike | None = None) -> Config:
    path = Path(path) if path else DEFAULT_CONFIG
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    root = path.resolve().parent
    return Config(data=data, root=root)
