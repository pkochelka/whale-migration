"""Small shared helpers."""
from __future__ import annotations

import re


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(text).lower()).strip("_")
