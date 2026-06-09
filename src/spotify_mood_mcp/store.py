"""Local persistence of the analyzed listening library.

A single JSON document under the state dir holds the deduped, genre-enriched
Track set plus metadata about when/how it was built. Written atomically with
0600 permissions. This is *derived* data (not secrets), but we keep it private
to the user anyway.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from .models import Track


@dataclass
class Library:
    tracks: list[Track] = field(default_factory=list)
    built_at: float = 0.0
    sources_summary: dict[str, int] = field(default_factory=dict)
    user_id: str = ""

    def by_id(self) -> dict[str, Track]:
        return {t.id: t for t in self.tracks}

    def to_dict(self) -> dict:
        return {
            "version": 1,
            "built_at": self.built_at,
            "user_id": self.user_id,
            "sources_summary": self.sources_summary,
            "tracks": [t.to_dict() for t in self.tracks],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Library":
        return cls(
            tracks=[Track.from_dict(t) for t in d.get("tracks", [])],
            built_at=float(d.get("built_at", 0.0)),
            sources_summary=dict(d.get("sources_summary", {})),
            user_id=d.get("user_id", ""),
        )


def save_library(path: Path, library: Library) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(library.to_dict(), indent=2), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:  # pragma: no cover
        pass
    os.replace(tmp, path)


def load_library(path: Path) -> Library | None:
    if not path.exists():
        return None
    try:
        return Library.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, KeyError, ValueError):
        return None


def now_seconds() -> float:
    return time.time()
