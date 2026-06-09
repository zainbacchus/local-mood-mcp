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
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

from .models import (
    API_SOURCES,
    TIER_LONG,
    TIER_MEDIUM,
    TIER_SHORT,
    Track,
    parse_year,
)
from .moods import EMOTIONS, MOODS
from .spotify_client import SpotifyAPIError, SpotifyClient
from .store import Library, now_seconds

_DAY_MS = 86_400_000

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


async def build_library(
    client: SpotifyClient, *, saved_cap: int = 2000, recent: list[dict] | None = None
) -> Library:
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

    if recent is None:
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
def _iter_history_entries(path: Path, skipped: list[str] | None = None):
    # Accept a single file, or a folder (recursively) — so dropping the whole
    # unzipped "Spotify Extended Streaming History" folder just works. Files
    # that can't be parsed are recorded in `skipped` so dropped memory is
    # visible to the caller rather than silently lost.
    if path.is_dir():
        files = sorted(path.rglob("*.json"))
    else:
        files = [path]
    for fp in files:
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            if skipped is not None:
                skipped.append(fp.name)
            continue
        if isinstance(data, list):
            yield from data
        elif skipped is not None:
            skipped.append(fp.name)


def parse_extended_history(path: Path) -> tuple[dict[str, Track], list[str]]:
    """Aggregate the export into per-track behavioral Tracks.

    Timestamps are converted to the machine's LOCAL timezone so part-of-day and
    weekend splits reflect the listener's clock.

    Returns (tracks_by_id, skipped_file_names).
    """
    skipped: list[str] = []
    out: dict[str, Track] = {}
    for entry in _iter_history_entries(path, skipped):
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
    return out, skipped


async def merge_extended_history(
    client: SpotifyClient, library: Library, path: Path, *, top_n_unknown: int = 150
) -> tuple[Library, dict]:
    """Fold lifetime behavior into the library. Tracks already present gain their
    full behavioral profile; the most-played UNKNOWN tracks are added (with era/
    explicit/duration fetched individually, since batch /tracks is 403 for new
    apps)."""
    parsed, files_skipped = parse_extended_history(path)
    by_id = library.by_id()
    matched = 0

    for tid, beh in parsed.items():
        existing = by_id.get(tid)
        if existing is not None:
            _copy_behavior(beh, existing)
            if "extended_history" not in existing.sources:
                existing.sources.append("extended_history")
            matched += 1

    unknown_all = sorted(
        ((tid, beh) for tid, beh in parsed.items() if tid not in by_id),
        key=lambda kv: (-kv[1].lifetime_plays, kv[0]),
    )
    unknown = unknown_all[:top_n_unknown]

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

    export_max_ts = max((t.last_played_ms or 0 for t in parsed.values()), default=0)
    if export_max_ts:
        library.lifetime_through_ms = max(library.lifetime_through_ms or 0, export_max_ts)
        # Journal plays at or before the export snapshot are already inside the
        # export's aggregates (or were just overwritten by them) — never re-fold
        # them. Advancing this marker keeps folding exactly-once.
        library.journal_through_ms = max(
            library.journal_through_ms or 0, library.lifetime_through_ms
        )

    library.sources_summary["extended_history_unique_tracks"] = len(parsed)
    library.sources_summary["extended_history_matched"] = matched
    library.sources_summary["extended_history_added"] = added
    return library, {
        "unique_tracks_in_export": len(parsed),
        "matched_existing": matched,
        "added_from_export": added,
        "unknown_tracks_dropped": len(unknown_all) - added,
        "files_skipped": files_skipped,
        "total_streams_parsed": sum(t.lifetime_plays for t in parsed.values()),
    }


def memory_impact(library: Library) -> dict:
    """Quantify what long-term memory adds over the live API window.

    Pure accounting over the cached library — this is what lets the README's
    thesis be stated with numbers: how much listening the API window exposes
    vs. how much the imported/journaled memory remembers.
    """
    tracks = library.tracks
    lifetime_tracks = [t for t in tracks if t.has_lifetime]
    invisible = [t for t in tracks if not any(s in API_SOURCES for s in t.sources)]
    streams_remembered = sum(t.lifetime_plays for t in lifetime_tracks)
    firsts = [t.first_play_ms for t in lifetime_tracks if t.first_play_ms]
    lasts = [t.last_played_ms for t in lifetime_tracks if t.last_played_ms]
    years = 0.0
    if firsts and lasts:
        span_ms = max(lasts) - min(firsts)
        if span_ms > 0:
            years = round(span_ms / (365.25 * _DAY_MS), 1)
    api_recent = library.sources_summary.get("recently_played", 0)
    lifetime_moods = sum(1 for m in MOODS.values() if m.requires_lifetime)
    emotional_moods = sum(1 for m in MOODS.values() if m.requires_annotations)
    instant_moods = len(MOODS) - lifetime_moods - emotional_moods
    loaded = bool(lifetime_tracks)
    labeled = [t for t in tracks if t.emotions]
    unlocked = (
        instant_moods
        + (lifetime_moods if loaded else 0)
        + (emotional_moods if labeled else 0)
    )
    return {
        "api_window": {
            "recently_played_streams_visible": api_recent,
            "recently_played_hard_cap": 50,
            "note": "The API exposes ~50 recent plays plus 3x50 unexplained "
                    "top-track summaries. Everything beyond that is memory.",
        },
        "long_term_memory": {
            "loaded": loaded,
            "streams_remembered": streams_remembered,
            "tracks_with_behavioral_profile": len(lifetime_tracks),
            "years_of_history": years,
            "tracks_invisible_to_api_window": len(invisible),
        },
        "semantic_memory": {
            "loaded": bool(labeled),
            "tracks_labeled": len(labeled),
            "labels_in_use": sorted({e for t in labeled for e in t.emotions}),
            "labeled_by": library.annotation_meta.get("labeled_by"),
        },
        "memory_multiplier": (
            round(streams_remembered / api_recent, 1) if loaded and api_recent else None
        ),
        "moods_unlocked": unlocked,
        "moods_total": len(MOODS),
    }


def carry_over_lifetime(previous: Library | None, fresh: Library) -> int:
    """Preserve memory across re-syncs — behavioral and semantic.

    build_library only sees the API window (~50 recent plays + top-track
    summaries); without this, lifetime signals imported from an export and
    emotional labels written by annotate_tracks would be wiped every time the
    user re-syncs. Copies behavioral fields and labels onto matching fresh
    tracks and re-appends remembered tracks the API window no longer surfaces.
    Returns the number of tracks whose lifetime data was preserved.
    """
    if previous is None:
        return 0
    fresh.lifetime_through_ms = previous.lifetime_through_ms
    fresh.journal_through_ms = previous.journal_through_ms
    fresh.annotation_meta = dict(previous.annotation_meta)
    by_id = fresh.by_id()
    preserved = 0
    for old in previous.tracks:
        if not old.has_lifetime and not old.emotions:
            continue
        current = by_id.get(old.id)
        if current is None:
            fresh.tracks.append(old)
            by_id[old.id] = old
            if old.has_lifetime:
                preserved += 1
            continue
        if old.emotions:
            merged = set(old.emotions) | set(current.emotions)
            current.emotions = [e for e in EMOTIONS if e in merged]
        if old.has_lifetime:
            _copy_behavior(old, current)
            if "extended_history" not in current.sources:
                current.sources.append("extended_history")
            preserved += 1
    return preserved


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


# --- semantic memory: labels written by the MCP client -----------------------
_TRACK_ID_RE = re.compile(r"^[A-Za-z0-9]{22}$")


def _normalize_track_id(raw: str) -> str | None:
    tid = raw.strip()
    if tid.startswith("spotify:track:"):
        tid = tid.split(":")[-1]
    if "open.spotify.com/track/" in tid:
        tid = tid.rsplit("/", 1)[-1].split("?")[0]
    return tid if _TRACK_ID_RE.match(tid) else None


def apply_annotations(
    library: Library,
    labels: dict[str, list[str]],
    *,
    labeled_by: str = "model",
    replace: bool = False,
) -> dict:
    """Write emotional labels into the library — the semantic-memory tier.

    Labels are the model's world knowledge persisted as data: subjective
    judgments, recorded once, deterministic to select over thereafter. Unknown
    emotions are a hard error (so the caller corrects itself); unknown track
    ids are skipped and reported. With replace=False (default) new labels merge
    with existing ones; replace=True overwrites per track.
    """
    invalid = sorted({e for ems in labels.values() for e in ems if e not in EMOTIONS})
    if invalid:
        raise ValueError(
            f"Unknown emotion labels {invalid}. Valid labels: {', '.join(EMOTIONS)}."
        )
    by_id = library.by_id()
    labeled = 0
    skipped: list[str] = []
    for raw_id, ems in sorted(labels.items()):
        tid = _normalize_track_id(raw_id)
        track = by_id.get(tid) if tid else None
        if track is None:
            skipped.append(raw_id)
            continue
        wanted = set(ems) if replace else set(track.emotions) | set(ems)
        track.emotions = [e for e in EMOTIONS if e in wanted]  # canonical order
        labeled += 1
    tracks_with_labels = sum(1 for t in library.tracks if t.emotions)
    library.annotation_meta = {
        "labeled_by": labeled_by,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "tracks_labeled": tracks_with_labels,
    }
    label_counts = {
        e: sum(1 for t in library.tracks if e in t.emotions) for e in EMOTIONS
    }
    return {
        "tracks_labeled_this_call": labeled,
        "unknown_track_ids_skipped": skipped,
        "library_coverage": f"{tracks_with_labels}/{len(library.tracks)}",
        "label_counts": label_counts,
        "emotional_moods_unlocked": tracks_with_labels > 0,
    }


# --- play journal: memory that accrues between exports ----------------------
def observations_from_recent(recent_items: list[dict]) -> list[dict]:
    """Turn the API's recently-played items into journal observations."""
    out: list[dict] = []
    for item in recent_items:
        track = item.get("track") or {}
        tid = track.get("id")
        ts = _iso_to_ms(item.get("played_at"))
        if not tid or ts is None:
            continue
        out.append({
            "ts_ms": ts,
            "track_id": tid,
            "name": track.get("name", ""),
            "artists": [a.get("name", "") for a in track.get("artists", [])],
        })
    return out


def fold_journal(library: Library, entries: list[dict]) -> int:
    """Fold journaled plays into lifetime aggregates, exactly once.

    Only entries newer than both memory markers apply: anything at or before
    lifetime_through_ms is already inside the export's aggregates, and anything
    at or before journal_through_ms was folded by a previous sync. Journal
    plays carry when/what but not completion/skip (the API window doesn't
    expose those), so they strengthen play-count and time-of-day signals only.
    Returns the number of plays folded.
    """
    floor = max(library.journal_through_ms or 0, library.lifetime_through_ms or 0)
    by_id = library.by_id()
    folded = 0
    high = library.journal_through_ms or 0
    for entry in sorted(entries, key=lambda e: (e["ts_ms"], e["track_id"])):
        ts = int(entry["ts_ms"])
        if ts <= floor:
            continue
        t = by_id.get(entry["track_id"])
        if t is None:
            t = Track(
                id=entry["track_id"],
                name=entry.get("name", ""),
                artist_names=list(entry.get("artists") or [""]),
                sources=["journal"],
            )
            library.tracks.append(t)
            by_id[t.id] = t
        elif "journal" not in t.sources:
            t.sources.append("journal")
        t.lifetime_plays += 1
        try:
            local = datetime.fromtimestamp(ts / 1000)  # local tz, like the export
            t.hour_hist[local.hour] += 1
            if local.weekday() >= 5:
                t.weekend_plays += 1
            else:
                t.weekday_plays += 1
        except (OverflowError, OSError, ValueError):
            pass
        if t.first_play_ms is None or ts < t.first_play_ms:
            t.first_play_ms = ts
        if (t.last_played_ms or 0) < ts:
            t.last_played_ms = ts
        folded += 1
        high = max(high, ts)
    if folded:
        library.journal_through_ms = high
    return folded


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
