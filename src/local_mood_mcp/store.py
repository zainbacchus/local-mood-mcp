"""Local persistence of the analyzed listening library.

A single JSON document under the state dir holds the deduped, behavioral Track
set (affinity tiers, recency, and — once imported — lifetime listening signals)
plus metadata about when/how it was built. Written atomically with 0600
permissions. This is *derived* data (not secrets), but we keep it private to the
user anyway.
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
    # Memory markers: how far the lifetime aggregates already cover, so journal
    # folding is exactly-once. lifetime_through_ms = newest play inside the
    # imported export; journal_through_ms = newest journaled play folded in.
    lifetime_through_ms: int | None = None
    journal_through_ms: int | None = None

    def by_id(self) -> dict[str, Track]:
        return {t.id: t for t in self.tracks}

    def to_dict(self) -> dict:
        return {
            "version": 1,
            "built_at": self.built_at,
            "user_id": self.user_id,
            "sources_summary": self.sources_summary,
            "lifetime_through_ms": self.lifetime_through_ms,
            "journal_through_ms": self.journal_through_ms,
            "tracks": [t.to_dict() for t in self.tracks],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Library":
        return cls(
            tracks=[Track.from_dict(t) for t in d.get("tracks", [])],
            built_at=float(d.get("built_at", 0.0)),
            sources_summary=dict(d.get("sources_summary", {})),
            user_id=d.get("user_id", ""),
            lifetime_through_ms=d.get("lifetime_through_ms"),
            journal_through_ms=d.get("journal_through_ms"),
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


# --- play journal ------------------------------------------------------------
# An append-only JSONL of observed plays ({"ts_ms", "track_id", "name",
# "artists"}). The API's recently-played window only holds ~50 plays, but a
# system that journals what passes through its window builds long-term memory
# anyway — this is that journal. Same 0600 privacy posture as the library.

def read_play_journal(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict) and entry.get("track_id") and isinstance(entry.get("ts_ms"), int):
            out.append(entry)
    return out


def append_play_journal(path: Path, plays: list[dict]) -> int:
    """Append observed plays, deduped by (ts_ms, track_id) against the existing
    journal and within the batch. Returns how many were newly recorded."""
    seen = {(e["ts_ms"], e["track_id"]) for e in read_play_journal(path)}
    fresh: dict[tuple[int, str], dict] = {}
    for p in plays:
        ts, tid = p.get("ts_ms"), p.get("track_id")
        if not tid or not isinstance(ts, int) or (ts, tid) in seen:
            continue
        fresh[(ts, tid)] = p
    if not fresh:
        return 0
    with path.open("a", encoding="utf-8") as f:
        for key in sorted(fresh):
            f.write(json.dumps(fresh[key]) + "\n")
    try:
        os.chmod(path, 0o600)
    except OSError:  # pragma: no cover - non-posix
        pass
    return len(fresh)
