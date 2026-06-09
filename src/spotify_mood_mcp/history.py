"""Aggregate the user's listening history into a deduped, enriched Track set.

Reality check on "entire history": the Web API does not expose a user's full
lifetime stream. We aggregate everything it *does* expose:

  * /me/top/tracks for short_term (~4 weeks), medium_term (~6 months),
    long_term (~1 year / all-time-ish)
  * /me/player/recently-played (last ~50 plays)
  * /me/tracks (the entire saved library, paginated)

Each track is tagged with which sources surfaced it and a play_count (number of
sources + recently-played repeats). Artist genres are then batch-fetched and
flattened onto each track.

For *true* lifetime history, `tracks_from_extended_history` parses the official
"Extended Streaming History" JSON export (Account → Privacy → Download your
data). That export contains every stream with a `spotify_track_uri`, which we
count and intersect with API metadata.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from pathlib import Path

from .models import Track, parse_year
from .spotify_client import SpotifyClient
from .store import Library, now_seconds

_TIME_RANGES = {
    "short_term": "top_short",
    "medium_term": "top_medium",
    "long_term": "top_long",
}


def _track_from_api(raw: dict) -> Track | None:
    if not raw or not raw.get("id"):
        return None
    artists = raw.get("artists", []) or []
    album = raw.get("album", {}) or {}
    return Track(
        id=raw["id"],
        name=raw.get("name", ""),
        artist_ids=[a["id"] for a in artists if a.get("id")],
        artist_names=[a.get("name", "") for a in artists],
        popularity=int(raw.get("popularity", 0)),
        duration_ms=int(raw.get("duration_ms", 0)),
        explicit=bool(raw.get("explicit", False)),
        release_year=parse_year(album.get("release_date")),
    )


def _merge(into: dict[str, Track], track: Track, source: str, *, last_played_ms: int | None = None) -> None:
    existing = into.get(track.id)
    if existing is None:
        track.sources = [source]
        track.play_count = 1
        track.last_played_ms = last_played_ms
        into[track.id] = track
        return
    if source not in existing.sources:
        existing.sources.append(source)
    existing.play_count += 1
    # Keep richer metadata if the new copy has more.
    if not existing.release_year and track.release_year:
        existing.release_year = track.release_year
    if last_played_ms and (existing.last_played_ms or 0) < last_played_ms:
        existing.last_played_ms = last_played_ms


async def build_library(client: SpotifyClient, *, saved_cap: int = 2000) -> Library:
    merged: dict[str, Track] = {}
    summary: Counter[str] = Counter()

    me = await client.me()
    user_id = me.get("id", "")

    for time_range, label in _TIME_RANGES.items():
        items = await client.top_tracks(time_range)
        for raw in items:
            t = _track_from_api(raw)
            if t:
                _merge(merged, t, label)
        summary[label] = len(items)

    recent = await client.recently_played()
    for item in recent:
        t = _track_from_api(item.get("track", {}))
        if t:
            played_at = item.get("played_at")
            ms = None
            if played_at:
                try:
                    ms = int(
                        datetime.fromisoformat(played_at.replace("Z", "+00:00")).timestamp() * 1000
                    )
                except ValueError:
                    ms = None
            _merge(merged, t, "recently_played", last_played_ms=ms)
    summary["recently_played"] = len(recent)

    saved = await client.saved_tracks(cap=saved_cap)
    for item in saved:
        t = _track_from_api(item.get("track", {}))
        if t:
            _merge(merged, t, "saved")
    summary["saved"] = len(saved)

    await _enrich_genres(client, merged)

    return Library(
        tracks=list(merged.values()),
        built_at=now_seconds(),
        sources_summary=dict(summary),
        user_id=user_id,
    )


async def _enrich_genres(client: SpotifyClient, tracks: dict[str, Track]) -> None:
    artist_ids: list[str] = []
    for t in tracks.values():
        artist_ids.extend(t.artist_ids)
    artists = await client.artists(artist_ids)
    for t in tracks.values():
        genres: list[str] = []
        for aid in t.artist_ids:
            genres.extend(artists.get(aid, {}).get("genres", []) or [])
        # Dedup preserving order, deterministic.
        t.genres = list(dict.fromkeys(genres))


# --- Extended Streaming History import -------------------------------------
def parse_extended_history_counts(path: Path) -> dict[str, int]:
    """Return {track_id: play_count} from an Extended Streaming History export.

    Accepts either a single JSON file or a directory of the export's
    `Streaming_History_Audio_*.json` files. Counts each entry whose
    spotify_track_uri is present.
    """
    files: list[Path]
    if path.is_dir():
        files = sorted(path.glob("*.json"))
    else:
        files = [path]
    counts: Counter[str] = Counter()
    for fp in files:
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(data, list):
            continue
        for entry in data:
            uri = entry.get("spotify_track_uri") if isinstance(entry, dict) else None
            if uri and uri.startswith("spotify:track:"):
                counts[uri.split(":")[-1]] += 1
    return dict(counts)


async def merge_extended_history(
    client: SpotifyClient, library: Library, path: Path, *, top_n_unknown: int = 200
) -> tuple[Library, dict]:
    """Fold lifetime play counts into the library.

    Tracks already in the library get their play_count increased and an
    'extended_history' source tag. The most-played tracks NOT yet in the
    library are fetched (metadata + genres) and added, so lifetime favourites
    that fell out of the recent windows still appear.
    """
    counts = parse_extended_history_counts(path)
    by_id = library.by_id()
    added = 0

    for tid, c in counts.items():
        t = by_id.get(tid)
        if t is not None:
            t.play_count += c
            if "extended_history" not in t.sources:
                t.sources.append("extended_history")

    # Bring in the top unknown tracks by lifetime plays.
    unknown = sorted(
        ((tid, c) for tid, c in counts.items() if tid not in by_id),
        key=lambda kv: (-kv[1], kv[0]),
    )[:top_n_unknown]
    if unknown:
        new_tracks = await _fetch_tracks(client, [tid for tid, _ in unknown])
        count_map = dict(unknown)
        new_map: dict[str, Track] = {t.id: t for t in new_tracks}
        await _enrich_genres(client, new_map)
        for tid, t in new_map.items():
            t.sources = ["extended_history"]
            t.play_count = count_map.get(tid, 1)
            library.tracks.append(t)
            added += 1

    library.sources_summary["extended_history_unique_tracks"] = len(counts)
    library.sources_summary["extended_history_added"] = added
    return library, {
        "unique_tracks_in_export": len(counts),
        "matched_existing": len(counts) - len(unknown),
        "added_from_export": added,
    }


async def _fetch_tracks(client: SpotifyClient, track_ids: list[str]) -> list[Track]:
    out: list[Track] = []
    for i in range(0, len(track_ids), 50):
        chunk = track_ids[i : i + 50]
        data = await client.get("/tracks", ids=",".join(chunk))
        for raw in (data or {}).get("tracks", []):
            t = _track_from_api(raw)
            if t:
                out.append(t)
    return out
