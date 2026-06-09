"""MCP server exposing the Spotify mood toolkit.

Design principles encoded in the tool surface:
  * Mood -> tracks is DETERMINISTIC and returns EXACT Spotify track IDs.
  * Playlist creation only ever consumes explicit track IDs (no NL track-picking).
  * Only non-deprecated Spotify endpoints are touched.
  * Auth/token handling is delegated to the secure PKCE + keyring layer.

Run with: `local-mood-mcp` (stdio transport, for Claude Desktop / clients).
"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from . import history as history_mod
from . import moods as moods_mod
from . import playback as playback_mod
from . import playlists as playlists_mod
from .config import Settings, load_settings
from .spotify_client import SpotifyClient
from .store import Library, load_library, save_library
from .tokenstore import TokenStore

mcp = FastMCP("local-mood")


def _settings() -> Settings:
    return load_settings()


@asynccontextmanager
async def _client():
    settings = _settings()
    client = SpotifyClient(settings)
    try:
        yield settings, client
    finally:
        await client.aclose()


def _require_library(settings: Settings) -> Library:
    lib = load_library(settings.library_path)
    if lib is None or not lib.tracks:
        raise RuntimeError(
            "No analyzed library yet. Run the `sync_listening_history` tool first."
        )
    return lib


def _err(e: Exception) -> dict:
    return {"error": type(e).__name__, "message": str(e)}


# --- auth / status ----------------------------------------------------------
@mcp.tool()
def spotify_auth_status() -> dict:
    """Report whether the user is authenticated and how long the access token lasts.
    Does NOT trigger a login (login is a one-time terminal step: `local-mood-auth login`)."""
    try:
        settings = _settings()
    except Exception as e:
        return _err(e)
    bundle = TokenStore(settings.state_dir).load()
    if not bundle:
        return {
            "authenticated": False,
            "how_to_fix": "Run `local-mood-auth login` once in a terminal to grant access.",
        }
    return {
        "authenticated": True,
        "scopes": bundle.scope.split(),
        "access_token_expires_in_seconds": max(int(bundle.expires_at - time.time()), 0),
        "confidential_client": settings.is_confidential,
    }


# --- history ----------------------------------------------------------------
@mcp.tool()
async def sync_listening_history(saved_cap: int = 2000, auto_import_history: bool = True) -> dict:
    """Fetch and analyze the user's listening history from all available Spotify
    sources (top tracks across short/medium/long term, recently played, and the
    saved library), capture era/explicit/duration, dedupe, and cache it locally.
    If an Extended Streaming History export has been dropped into the
    `extended_history/` folder, it is auto-merged (unless auto_import_history is
    false), unlocking the lifetime moods. `saved_cap` bounds saved-track pulls."""
    try:
        async with _client() as (settings, client):
            library = await history_mod.build_library(client, saved_cap=saved_cap)
            result = {
                "tracks_analyzed": len(library.tracks),
                "sources": dict(library.sources_summary),
                "with_release_year": sum(1 for t in library.tracks if t.release_year),
                "cached_at": settings.library_path.as_posix(),
            }
            if auto_import_history and settings.has_dropped_history():
                library, report = await history_mod.merge_extended_history(
                    client, library, settings.history_dir
                )
                result["extended_history_auto_imported"] = report
                result["lifetime_moods_unlocked"] = True
            else:
                result["lifetime_moods_unlocked"] = any(t.has_lifetime for t in library.tracks)
                result["drop_folder"] = settings.history_dir.as_posix()
                result["hint"] = (
                    "Drop your Extended Streaming History JSON files into the folder "
                    "above (then re-run sync) to unlock lifetime moods like morning, "
                    "on_repeat, comfort."
                )
            save_library(settings.library_path, library)
            return result
    except Exception as e:
        return _err(e)


@mcp.tool()
async def import_extended_history(export_path: str | None = None, top_n_unknown: int = 150) -> dict:
    """Fold true lifetime listening behavior from a Spotify 'Extended Streaming
    History' export into the cached library, unlocking lifetime moods. With no
    `export_path`, reads the repo's `extended_history/` drop folder (recursively).
    Otherwise accepts a single JSON file or any folder. Run sync_listening_history
    first."""
    try:
        settings = _settings()
        library = _require_library(settings)
        path = Path(export_path).expanduser() if export_path else settings.history_dir
        if not path.exists():
            return {"error": "FileNotFound", "message": f"No such path: {path}"}
        if path.is_dir() and not any(path.rglob("*.json")):
            return {
                "error": "NoFilesFound",
                "message": f"No .json files in {path}. Drop your unzipped Extended "
                "Streaming History export there first.",
                "drop_folder": path.as_posix(),
            }
        async with _client() as (_s, client):
            library, report = await history_mod.merge_extended_history(
                client, library, path, top_n_unknown=top_n_unknown
            )
            save_library(settings.library_path, library)
            return {
                "tracks_in_library": len(library.tracks),
                "lifetime_moods_unlocked": any(t.has_lifetime for t in library.tracks),
                **report,
            }
    except Exception as e:
        return _err(e)


@mcp.tool()
def extended_history_status() -> dict:
    """Report the Extended Streaming History drop folder, what files are detected
    there, and whether lifetime behavioral data is loaded into the library."""
    try:
        settings = _settings()
        files = sorted(p.name for p in settings.history_dir.rglob("*.json"))
        lib = load_library(settings.library_path)
        loaded = bool(lib and any(t.has_lifetime for t in lib.tracks))
        return {
            "drop_folder": settings.history_dir.as_posix(),
            "json_files_detected": len(files),
            "files": files[:50],
            "lifetime_data_loaded_in_library": loaded,
            "next_step": (
                "Run sync_listening_history (auto-imports) or import_extended_history."
                if files and not loaded else
                "Drop your unzipped export's JSON files into the folder above."
                if not files else "Lifetime moods are ready."
            ),
        }
    except Exception as e:
        return _err(e)


@mcp.tool()
def library_stats() -> dict:
    """Summarize the locally cached, analyzed library: track count, affinity-tier
    and source breakdown, era distribution, and whether lifetime (Extended
    Streaming History) behavioral data is loaded."""
    try:
        settings = _settings()
        lib = load_library(settings.library_path)
        if lib is None:
            return {"exists": False, "how_to_fix": "Run sync_listening_history."}
        decades: dict[str, int] = {}
        for t in lib.tracks:
            if t.release_year:
                d = f"{(t.release_year // 10) * 10}s"
                decades[d] = decades.get(d, 0) + 1
        tier_counts = {
            "short_term": sum(1 for t in lib.tracks if "short_term" in t.top_tiers),
            "medium_term": sum(1 for t in lib.tracks if "medium_term" in t.top_tiers),
            "long_term": sum(1 for t in lib.tracks if "long_term" in t.top_tiers),
            "saved": sum(1 for t in lib.tracks if t.in_saved),
        }
        with_lifetime = sum(1 for t in lib.tracks if t.has_lifetime)
        return {
            "exists": True,
            "tracks": len(lib.tracks),
            "built_at_epoch": lib.built_at,
            "sources": lib.sources_summary,
            "affinity_tiers": tier_counts,
            "with_release_year": sum(1 for t in lib.tracks if t.release_year),
            "decades": dict(sorted(decades.items())),
            "lifetime_loaded": with_lifetime > 0,
            "tracks_with_lifetime_data": with_lifetime,
            "total_lifetime_plays": sum(t.lifetime_plays for t in lib.tracks),
        }
    except Exception as e:
        return _err(e)


# --- moods / generation -----------------------------------------------------
@mcp.tool()
def list_moods() -> list[dict]:
    """List the available behavioral moods. Each is marked whether it works now
    (instant, from API affinity) or needs the Extended Streaming History export
    (lifetime moods like morning/on_repeat/comfort)."""
    settings = _settings()
    lib = load_library(settings.library_path)
    has_lifetime = bool(lib and any(t.has_lifetime for t in lib.tracks))
    return moods_mod.list_moods(has_lifetime=has_lifetime)


def _build_filters(
    min_year: int | None,
    max_year: int | None,
    exclude_explicit: bool,
    min_duration_ms: int | None,
    max_duration_ms: int | None,
    require_affinity: bool,
    familiarity_weight: float,
) -> "playlists_mod.Filters":
    return playlists_mod.Filters(
        min_year=min_year,
        max_year=max_year,
        exclude_explicit=exclude_explicit,
        min_duration_ms=min_duration_ms,
        max_duration_ms=max_duration_ms,
        require_affinity=require_affinity,
        familiarity_weight=familiarity_weight,
    )


@mcp.tool()
def generate_playlist(
    mood: str,
    count: int = 25,
    min_year: int | None = None,
    max_year: int | None = None,
    exclude_explicit: bool = False,
    min_duration_ms: int | None = None,
    max_duration_ms: int | None = None,
    require_affinity: bool = False,
    familiarity_weight: float = 0.25,
) -> dict:
    """Deterministically select tracks from the cached library for a behavioral
    mood and return a PREVIEW of the exact track IDs (with per-track scoring
    rationale). This does NOT create anything on Spotify — pass the returned
    `track_ids` to create_playlist (or play) when you're happy. Re-running with
    identical parameters returns the identical ordered list. Lifetime moods
    (morning, on_repeat, comfort, ...) require import_extended_history first."""
    try:
        settings = _settings()
        library = _require_library(settings)
        filters = _build_filters(
            min_year, max_year, exclude_explicit,
            min_duration_ms, max_duration_ms, require_affinity, familiarity_weight,
        )
        sels = playlists_mod.select_for_mood(library, mood, count=count, filters=filters)
        preview = playlists_mod.selection_to_preview(sels)
        return {
            "mood": mood,
            "count": len(preview),
            "track_ids": [p["id"] for p in preview],
            "tracks": preview,
            "deterministic": True,
        }
    except Exception as e:
        return _err(e)


@mcp.tool()
def explain_track(track_id: str, mood: str) -> dict:
    """Explain why a specific track (must be in the cached library) scores the way
    it does for a given mood — the full component breakdown."""
    try:
        settings = _settings()
        library = _require_library(settings)
        track = library.by_id().get(track_id.strip())
        if not track:
            return {"error": "NotInLibrary", "message": f"{track_id} not in cached library."}
        spec = moods_mod.get_mood(mood)
        ctx = moods_mod.build_context(library.tracks)
        score, comps = moods_mod.score_track(track, spec, ctx)
        return {
            "track": track.name,
            "artists": track.artist_names,
            "release_year": track.release_year,
            "explicit": track.explicit,
            "duration_ms": track.duration_ms,
            "top_tiers": track.top_tiers,
            "affinity_plays": track.affinity_plays,
            "lifetime_plays": track.lifetime_plays,
            "part_of_day": track.part_of_day_shares(),
            "mood": spec.key,
            "requires_extended_history": spec.requires_lifetime,
            "score": round(score, 4),
            "components": {k: round(v, 4) for k, v in comps.items()},
        }
    except Exception as e:
        return _err(e)


# --- playlist creation (exact IDs only) ------------------------------------
@mcp.tool()
async def create_playlist(
    name: str,
    track_ids: list[str],
    public: bool = False,
    description: str = "",
) -> dict:
    """Create a new Spotify playlist from the EXACT given track IDs (or URIs/URLs),
    in order. This is the only creation path — it never interprets natural
    language. Typically you pass the `track_ids` from generate_playlist."""
    try:
        async with _client() as (_s, client):
            return await playlists_mod.create_playlist_from_ids(
                client, name=name, track_ids=track_ids, public=public, description=description
            )
    except Exception as e:
        return _err(e)


@mcp.tool()
async def create_mood_playlist(
    name: str,
    mood: str,
    count: int = 25,
    public: bool = False,
    description: str = "",
    min_year: int | None = None,
    max_year: int | None = None,
    exclude_explicit: bool = False,
    min_duration_ms: int | None = None,
    max_duration_ms: int | None = None,
    require_affinity: bool = False,
    familiarity_weight: float = 0.25,
) -> dict:
    """Convenience: deterministically select for `mood`, then create the playlist
    from those exact IDs in one step. Equivalent to generate_playlist followed by
    create_playlist with the returned IDs."""
    try:
        settings = _settings()
        library = _require_library(settings)
        filters = _build_filters(
            min_year, max_year, exclude_explicit,
            min_duration_ms, max_duration_ms, require_affinity, familiarity_weight,
        )
        sels = playlists_mod.select_for_mood(library, mood, count=count, filters=filters)
        ids = [s.track.id for s in sels]
        if not ids:
            return {"error": "NoMatches", "message": f"No tracks matched mood {mood!r} with those filters."}
        desc = description or f"Deterministic {mood} mix from your listening history (local-mood-mcp)."
        async with _client() as (_s, client):
            result = await playlists_mod.create_playlist_from_ids(
                client, name=name, track_ids=ids, public=public, description=desc
            )
        result["mood"] = mood
        result["selected_tracks"] = playlists_mod.selection_to_preview(sels)
        return result
    except Exception as e:
        return _err(e)


# --- playback ---------------------------------------------------------------
@mcp.tool()
async def list_devices() -> list[dict] | dict:
    """List the user's available Spotify Connect devices (id, name, type, active)."""
    try:
        async with _client() as (_s, client):
            return await playback_mod.list_devices(client)
    except Exception as e:
        return _err(e)


@mcp.tool()
async def play(
    track_ids: list[str] | None = None,
    playlist_id: str | None = None,
    device_id: str | None = None,
) -> dict:
    """Start playback (Spotify Premium + an active device required). Provide either
    `track_ids` (exact IDs/URIs to play in order) or `playlist_id` (plays that
    playlist's context). If no device_id is given, the active or first device is used."""
    try:
        context_uri = f"spotify:playlist:{playlist_id}" if playlist_id else None
        async with _client() as (_s, client):
            return await playback_mod.play(
                client, track_ids=track_ids, context_uri=context_uri, device_id=device_id
            )
    except Exception as e:
        return _err(e)


@mcp.tool()
async def pause(device_id: str | None = None) -> dict:
    """Pause playback on the active (or specified) device."""
    try:
        async with _client() as (_s, client):
            return await playback_mod.pause(client, device_id=device_id)
    except Exception as e:
        return _err(e)


@mcp.tool()
async def skip_next(device_id: str | None = None) -> dict:
    """Skip to the next track on the active (or specified) device."""
    try:
        async with _client() as (_s, client):
            return await playback_mod.skip_next(client, device_id=device_id)
    except Exception as e:
        return _err(e)


@mcp.tool()
async def now_playing() -> dict:
    """Show what is currently playing (track, artists, device, progress)."""
    try:
        async with _client() as (_s, client):
            return await playback_mod.now_playing(client)
    except Exception as e:
        return _err(e)


def main() -> None:
    mcp.run()


if __name__ == "__main__":  # pragma: no cover
    main()
