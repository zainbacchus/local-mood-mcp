"""Deterministic playlist selection + creation by exact track IDs.

Selection is a pure function of (library, mood, filters): score every track,
optionally blend in the user's own familiarity (play frequency), then sort by a
fully specified, tie-broken key. No randomness — re-running with the same
library and parameters yields the identical ordered list of track IDs.

Creation never interprets natural language: `create_playlist_from_ids` takes the
exact track IDs you pass (typically straight from `select_for_mood`) and writes
them, in order, to a new Spotify playlist.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from .models import Track
from .moods import MoodSpec, get_mood, score_track
from .spotify_client import SpotifyClient
from .store import Library

_ID_RE = re.compile(r"^[A-Za-z0-9]{22}$")  # Spotify base-62 IDs are 22 chars


@dataclass(frozen=True)
class Filters:
    min_popularity: int = 0
    max_popularity: int = 100
    min_year: int | None = None
    max_year: int | None = None
    exclude_explicit: bool = False
    require_genre_match: bool = False  # drop tracks with zero genre signal
    familiarity_weight: float = 0.25   # 0 = pure mood fit, 1 = pure play-frequency


@dataclass
class Selection:
    track: Track
    mood_score: float
    final_score: float
    components: dict[str, float]


def _familiarity_scores(tracks: list[Track]) -> dict[str, float]:
    max_pc = max((t.play_count for t in tracks), default=0)
    denom = math.log1p(max_pc) if max_pc > 0 else 1.0
    return {t.id: (math.log1p(t.play_count) / denom if denom else 0.0) for t in tracks}


def _passes_filters(t: Track, mood: MoodSpec, f: Filters) -> bool:
    if not (f.min_popularity <= t.popularity <= f.max_popularity):
        return False
    if f.exclude_explicit and t.explicit:
        return False
    if f.min_year is not None and (t.release_year is None or t.release_year < f.min_year):
        return False
    if f.max_year is not None and (t.release_year is None or t.release_year > f.max_year):
        return False
    if f.require_genre_match and not t.genres:
        return False
    return True


def select_for_mood(
    library: Library, mood_key: str, *, count: int = 25, filters: Filters | None = None
) -> list[Selection]:
    mood = get_mood(mood_key)
    f = filters or Filters()
    fam = _familiarity_scores(library.tracks)
    fam_w = max(0.0, min(1.0, f.familiarity_weight))

    scored: list[Selection] = []
    for t in library.tracks:
        if not _passes_filters(t, mood, f):
            continue
        ms, comps = score_track(t, mood)
        final = (1.0 - fam_w) * ms + fam_w * fam.get(t.id, 0.0)
        scored.append(Selection(track=t, mood_score=ms, final_score=final, components=comps))

    # Deterministic ordering. Round to avoid platform float jitter, then break
    # ties by play_count, popularity, and finally the stable track id.
    scored.sort(
        key=lambda s: (
            -round(s.final_score, 6),
            -s.track.play_count,
            -s.track.popularity,
            s.track.id,
        )
    )
    return scored[:count]


def selection_to_preview(selections: list[Selection]) -> list[dict]:
    return [
        {
            "id": s.track.id,
            "uri": s.track.uri,
            "name": s.track.name,
            "artists": s.track.artist_names,
            "genres": s.track.genres[:6],
            "popularity": s.track.popularity,
            "release_year": s.track.release_year,
            "play_count": s.track.play_count,
            "mood_score": round(s.mood_score, 4),
            "final_score": round(s.final_score, 4),
            "why": _why(s),
        }
        for s in selections
    ]


def _why(s: Selection) -> str:
    c = s.components
    parts = [f"{k}={v:.2f}" for k, v in c.items()]
    return (
        f"genre/pop/era/explicit -> {', '.join(parts)}; "
        f"play_count={s.track.play_count}; final={s.final_score:.3f}"
    )


def validate_track_ids(track_ids: list[str]) -> list[str]:
    cleaned: list[str] = []
    for raw in track_ids:
        tid = raw.strip()
        if tid.startswith("spotify:track:"):
            tid = tid.split(":")[-1]
        if "open.spotify.com/track/" in tid:
            tid = tid.rsplit("/", 1)[-1].split("?")[0]
        if not _ID_RE.match(tid):
            raise ValueError(f"Invalid Spotify track id: {raw!r}")
        cleaned.append(tid)
    # De-dupe preserving order.
    return list(dict.fromkeys(cleaned))


async def create_playlist_from_ids(
    client: SpotifyClient,
    *,
    name: str,
    track_ids: list[str],
    public: bool = False,
    description: str = "",
) -> dict:
    """Create a playlist and add the EXACT given track IDs, in order."""
    ids = validate_track_ids(track_ids)
    if not ids:
        raise ValueError("No valid track IDs provided.")
    me = await client.me()
    playlist = await client.create_playlist(
        me["id"], name, public=public, description=description
    )
    uris = [f"spotify:track:{tid}" for tid in ids]
    await client.add_tracks(playlist["id"], uris)
    return {
        "playlist_id": playlist["id"],
        "name": playlist.get("name", name),
        "url": playlist.get("external_urls", {}).get("spotify"),
        "public": public,
        "track_count": len(ids),
        "track_ids": ids,
    }
