"""Build the behavioral listening library from Spotify-only data.

Two complementary sources, no external services:

  build_library(...)            -- API affinity, available instantly:
      * /me/top/tracks short/medium/long_term  -> affinity tiers
      * /me/player/recently-played             -> recency + recent play counts
      * /me/tracks                             -> saved flag
    Track objects from these carry release_date / explicit / duration, so era,
    explicit and length are captured here with no extra calls.

  merge_extended_history(...)   -- the deterministic backbone, once the user's
    "Extended Streaming History" export arrives. Parses every stream into
    per-track behavior: plays, ms played, completions, skips, deliberate
    starts, an hour-of-day histogram, weekend split, and first/last play.

Genre/popularity enrichment is intentionally gone: Spotify returns null for
those to new apps in 2026.
"""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

from .models import (
    TIER_LONG,
    TIER_MEDIUM,
    TIER_SHORT,
    Track,
    parse_year,
)
from .spotify_client import SpotifyAPIError, SpotifyClient
from .store import Library, now_seconds

_TIME_RANGES = (TIER_SHORT, TIER_MEDIUM, TIER_LONG)
_DELIBERATE_STARTS = {"clickrow", "playbtn"}
_SKIP_END = {"fwdbtn", "endplay"}
_MIN_MEANINGFUL_MS = 30_000  # Spotify's own ~30s "counts as a play" threshold


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
        duration_ms=int(raw.get("duration_ms", 0)),
        explicit=bool(raw.get("explicit", False)),
        release_year=parse_year(album.get("release_date")),
    )


def _get_or_add(into: dict[str, Track], track: Track) -> Track:
    existing = into.get(track.id)
    if existing is None:
        into[track.id] = track
        return track
    # Keep richer metadata.
    if not existing.release_year and track.release_year:
        existing.release_year = track.release_year
    if not existing.duration_ms and track.duration_ms:
        existing.duration_ms = track.duration_ms
    return existing


async def build_library(client: SpotifyClient, *, saved_cap: int = 2000) -> Library:
    merged: dict[str, Track] = {}
    summary: Counter[str] = Counter()

    me = await client.me()
    user_id = me.get("id", "")

    for time_range in _TIME_RANGES:
        items = await client.top_tracks(time_range)
        for raw in items:
            t = _track_from_api(raw)
            if not t:
                continue
            rec = _get_or_add(merged, t)
            if time_range not in rec.top_tiers:
                rec.top_tiers.append(time_range)
            if "top_" + time_range not in rec.sources:
                rec.sources.append("top_" + time_range)
        summary[time_range] = len(items)

    recent = await client.recently_played()
    for item in recent:
        t = _track_from_api(item.get("track", {}))
        if not t:
            continue
        rec = _get_or_add(merged, t)
        rec.api_recent_plays += 1
        if "recently_played" not in rec.sources:
            rec.sources.append("recently_played")
        ms = _iso_to_ms(item.get("played_at"))
        if ms and (rec.last_played_ms or 0) < ms:
            rec.last_played_ms = ms
    summary["recently_played"] = len(recent)

    saved = await client.saved_tracks(cap=saved_cap)
    for item in saved:
        t = _track_from_api(item.get("track", {}))
        if not t:
            continue
        rec = _get_or_add(merged, t)
        rec.in_saved = True
        if "saved" not in rec.sources:
            rec.sources.append("saved")
    summary["saved"] = len(saved)

    return Library(
        tracks=list(merged.values()),
        built_at=now_seconds(),
        sources_summary=dict(summary),
        user_id=user_id,
    )


def _iso_to_ms(ts: str | None) -> int | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return int(dt.timestamp() * 1000)
    except ValueError:
        return None


# --- Extended Streaming History --------------------------------------------
def _iter_history_entries(path: Path):
    # Accept a single file, or a folder (recursively) — so dropping the whole
    # unzipped "Spotify Extended Streaming History" folder just works.
    if path.is_dir():
        files = sorted(path.rglob("*.json"))
    else:
        files = [path]
    for fp in files:
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(data, list):
            yield from data


def parse_extended_history(path: Path) -> dict[str, Track]:
    """Aggregate the export into per-track behavioral Tracks.

    Timestamps are converted to the machine's LOCAL timezone so part-of-day and
    weekend splits reflect the listener's clock.
    """
    out: dict[str, Track] = {}
    for entry in _iter_history_entries(path):
        if not isinstance(entry, dict):
            continue
        uri = entry.get("spotify_track_uri")
        if not uri or not uri.startswith("spotify:track:"):
            continue
        tid = uri.split(":")[-1]
        t = out.get(tid)
        if t is None:
            t = Track(
                id=tid,
                name=entry.get("master_metadata_track_name") or "",
                artist_names=[entry.get("master_metadata_album_artist_name") or ""],
                sources=["extended_history"],
            )
            out[tid] = t

        ms_played = int(entry.get("ms_played") or 0)
        t.lifetime_plays += 1
        t.lifetime_ms_played += ms_played

        if entry.get("reason_end") == "trackdone":
            t.completions += 1
        if entry.get("skipped") is True or (
            entry.get("reason_end") in _SKIP_END and ms_played < _MIN_MEANINGFUL_MS
        ):
            t.skips += 1
        if entry.get("reason_start") in _DELIBERATE_STARTS:
            t.deliberate_starts += 1

        ms = _iso_to_ms(entry.get("ts"))
        if ms is not None:
            try:
                local = datetime.fromtimestamp(ms / 1000)  # local tz
                t.hour_hist[local.hour] += 1
                if local.weekday() >= 5:
                    t.weekend_plays += 1
                else:
                    t.weekday_plays += 1
            except (OverflowError, OSError, ValueError):
                pass
            if t.first_play_ms is None or ms < t.first_play_ms:
                t.first_play_ms = ms
            if (t.last_played_ms or 0) < ms:
                t.last_played_ms = ms
    return out


async def merge_extended_history(
    client: SpotifyClient, library: Library, path: Path, *, top_n_unknown: int = 150
) -> tuple[Library, dict]:
    """Fold lifetime behavior into the library. Tracks already present gain their
    full behavioral profile; the most-played UNKNOWN tracks are added (with era/
    explicit/duration fetched individually, since batch /tracks is 403 for new
    apps)."""
    parsed = parse_extended_history(path)
    by_id = library.by_id()
    matched = 0

    for tid, beh in parsed.items():
        existing = by_id.get(tid)
        if existing is not None:
            _copy_behavior(beh, existing)
            if "extended_history" not in existing.sources:
                existing.sources.append("extended_history")
            matched += 1

    unknown = sorted(
        ((tid, beh) for tid, beh in parsed.items() if tid not in by_id),
        key=lambda kv: (-kv[1].lifetime_plays, kv[0]),
    )[:top_n_unknown]

    added = 0
    if unknown:
        metas = await _fetch_track_metas(client, [tid for tid, _ in unknown])
        for tid, beh in unknown:
            meta = metas.get(tid)
            if meta is not None:
                _copy_behavior(beh, meta)
                library.tracks.append(meta)
            else:
                library.tracks.append(beh)  # keep behavior even without metadata
            added += 1

    library.sources_summary["extended_history_unique_tracks"] = len(parsed)
    library.sources_summary["extended_history_matched"] = matched
    library.sources_summary["extended_history_added"] = added
    return library, {
        "unique_tracks_in_export": len(parsed),
        "matched_existing": matched,
        "added_from_export": added,
        "total_streams_parsed": sum(t.lifetime_plays for t in parsed.values()),
    }


def _copy_behavior(src: Track, dst: Track) -> None:
    dst.lifetime_plays = src.lifetime_plays
    dst.lifetime_ms_played = src.lifetime_ms_played
    dst.completions = src.completions
    dst.skips = src.skips
    dst.deliberate_starts = src.deliberate_starts
    dst.hour_hist = list(src.hour_hist)
    dst.weekday_plays = src.weekday_plays
    dst.weekend_plays = src.weekend_plays
    dst.first_play_ms = src.first_play_ms
    if src.last_played_ms and (dst.last_played_ms or 0) < src.last_played_ms:
        dst.last_played_ms = src.last_played_ms
    if not dst.name and src.name:
        dst.name = src.name
    if not dst.artist_names or dst.artist_names == [""]:
        dst.artist_names = src.artist_names


async def _fetch_track_metas(client: SpotifyClient, track_ids: list[str]) -> dict[str, Track]:
    """Fetch era/explicit/duration per track via the single-object endpoint
    (batch /tracks is 403 for new apps). Bounded concurrency, failures skipped."""
    sem = asyncio.Semaphore(8)
    result: dict[str, Track] = {}

    async def one(tid: str) -> None:
        async with sem:
            try:
                raw = await client.get(f"/tracks/{tid}")
            except SpotifyAPIError:
                return
        t = _track_from_api(raw)
        if t:
            result[tid] = t

    await asyncio.gather(*(one(t) for t in track_ids))
    return result
