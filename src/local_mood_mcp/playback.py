"""Playback control via the Spotify Connect API.

Playback requires Spotify **Premium** and an **active device** (the desktop/
mobile app, or a Web Playback SDK instance). This module surfaces clear,
actionable messages when either is missing rather than leaking raw 403/404s.
"""

from __future__ import annotations

from .playlists import validate_track_ids
from .spotify_client import SpotifyAPIError, SpotifyClient


class PlaybackError(RuntimeError):
    pass


async def list_devices(client: SpotifyClient) -> list[dict]:
    devices = await client.devices()
    return [
        {
            "id": d.get("id"),
            "name": d.get("name"),
            "type": d.get("type"),
            "is_active": d.get("is_active", False),
            "volume_percent": d.get("volume_percent"),
        }
        for d in devices
    ]


async def _resolve_device(client: SpotifyClient, device_id: str | None) -> str | None:
    if device_id:
        return device_id
    devices = await client.devices()
    if not devices:
        raise PlaybackError(
            "No active Spotify device found. Open Spotify on a device "
            "(desktop, phone, or web player) and start/queue anything once, "
            "then retry. Playback control also requires Spotify Premium."
        )
    active = next((d for d in devices if d.get("is_active")), None)
    return (active or devices[0]).get("id")


async def play(
    client: SpotifyClient,
    *,
    track_ids: list[str] | None = None,
    context_uri: str | None = None,
    device_id: str | None = None,
) -> dict:
    target = await _resolve_device(client, device_id)
    uris = None
    if track_ids:
        ids = validate_track_ids(track_ids)
        uris = [f"spotify:track:{t}" for t in ids]
    try:
        await client.play(device_id=target, uris=uris, context_uri=context_uri)
    except SpotifyAPIError as e:
        if e.status in (403, 404):
            raise PlaybackError(
                "Playback was rejected. This usually means the account is not "
                "Premium, or the chosen device is unavailable. "
                f"(Spotify said: {e})"
            ) from e
        raise
    return {"status": "playing", "device_id": target, "track_uris": uris, "context_uri": context_uri}


async def pause(client: SpotifyClient, *, device_id: str | None = None) -> dict:
    target = await _resolve_device(client, device_id)
    await client.pause(device_id=target)
    return {"status": "paused", "device_id": target}


async def skip_next(client: SpotifyClient, *, device_id: str | None = None) -> dict:
    target = await _resolve_device(client, device_id)
    await client.next_track(device_id=target)
    return {"status": "skipped", "device_id": target}


async def now_playing(client: SpotifyClient) -> dict:
    state = await client.playback_state()
    if not state:
        return {"is_playing": False, "message": "Nothing is currently playing."}
    item = state.get("item") or {}
    return {
        "is_playing": state.get("is_playing", False),
        "track": item.get("name"),
        "artists": [a.get("name") for a in item.get("artists", [])],
        "track_id": item.get("id"),
        "progress_ms": state.get("progress_ms"),
        "duration_ms": item.get("duration_ms"),
        "device": (state.get("device") or {}).get("name"),
    }
