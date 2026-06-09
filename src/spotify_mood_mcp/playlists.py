"""Deterministic playlist selection + creation by exact track IDs.

Selection is a pure function of (library, mood, filters): build a normalization
context, score every track with the mood's behavioral scorer, optionally blend
in raw affinity (how much you listen to it), then sort by a fully specified,
tie-broken key. Re-running with the same library + params yields the identical
ordered list of Spotify track IDs.

Creation never interprets natural language: create_playlist_from_ids takes the
exact IDs you pass (typically from select_for_mood) and writes them in order.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .models import Track
from .moods import Context, MoodSpec, build_context, get_mood, score_track
from .spotify_client import SpotifyClient
from .store import Library

_ID_RE = re.compile(r"^[A-Za-z0-9]{22}$")  # Spotify base-62 IDs are 22 chars


class LifetimeRequiredError(RuntimeError):
    """A lifetime mood was requested but no Extended Streaming History is loaded."""


@dataclass(frozen=True)
class Filters:
    min_year: int | None = None
    max_year: int | None = None
    exclude_explicit: bool = False
    min_duration_ms: int | None = None
    max_duration_ms: int | None = None
    require_affinity: bool = False     # drop tracks you've never engaged with
    familiarity_weight: float = 0.25   # 0 = pure mood fit, 1 = pure listen-frequency


@dataclass
class Selection:
    track: Track
    mood_score: float
    final_score: float
    components: dict[str, float]


def _passes(t: Track, f: Filters) -> bool:
    if f.min_year is not None and (t.release_year is None or t.release_year < f.min_year):
        return False
    if f.max_year is not None and (t.release_year is None or t.release_year > f.max_year):
        return False
    if f.exclude_explicit and t.explicit:
        return False
    if f.min_duration_ms is not None and t.duration_ms < f.min_duration_ms:
        return False
    if f.max_duration_ms is not None and t.duration_ms > f.max_duration_ms:
        return False
    if f.require_affinity and t.affinity_plays <= 0:
        return False
    return True


def select_for_mood(
    library: Library, mood_key: str, *, count: int = 25, filters: Filters | None = None
) -> list[Selection]:
    mood: MoodSpec = get_mood(mood_key)
    f = filters or Filters()
    ctx: Context = build_context(library.tracks)

    if mood.requires_lifetime and not ctx.has_lifetime:
        raise LifetimeRequiredError(
            f"Mood {mood.key!r} needs your Extended Streaming History. "
            "Request it at Spotify → Account → Privacy → 'Extended streaming "
            "history', then run import_extended_history. Until then, use an "
            "instant mood (e.g. current_rotation, all_time_favorites, throwback)."
        )

    fam_w = max(0.0, min(1.0, f.familiarity_weight))
    scored: list[Selection] = []
    for t in library.tracks:
        if not _passes(t, f):
            continue
        ms, comps = score_track(t, mood, ctx)
        final = (1.0 - fam_w) * ms + fam_w * ctx.affinity_norm(t)
        scored.append(Selection(track=t, mood_score=ms, final_score=final, components=comps))

    scored.sort(
        key=lambda s: (
            -round(s.final_score, 6),
            -s.track.affinity_plays,
            -s.track.lifetime_plays,
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
            "release_year": s.track.release_year,
            "duration_ms": s.track.duration_ms,
            "explicit": s.track.explicit,
            "top_tiers": s.track.top_tiers,
            "affinity_plays": s.track.affinity_plays,
            "lifetime_plays": s.track.lifetime_plays,
            "mood_score": round(s.mood_score, 4),
            "final_score": round(s.final_score, 4),
            "why": {k: round(v, 4) for k, v in s.components.items()},
        }
        for s in selections
    ]


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
