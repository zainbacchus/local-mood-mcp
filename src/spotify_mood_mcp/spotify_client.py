"""Thin async Spotify Web API client.

Scope of endpoints (all confirmed available to new apps as of 2026):
  GET  /me
  GET  /me/top/tracks
  GET  /me/player/recently-played
  GET  /me/tracks                   (saved library)
  GET  /tracks/{id}                 (single-track era/explicit/duration)
  GET  /me/playlists
  POST /users/{id}/playlists        POST /playlists/{id}/tracks
  GET  /me/player/devices
  GET  /me/player                   PUT /me/player/play  PUT /me/player/pause
  POST /me/player/next

VERIFIED 403 FOR NEW APPS (do not use):
  /audio-features  /audio-analysis  /recommendations  /artists/{id}/related-artists
  /browse/featured-playlists  /browse/categories/*/playlists
  /artists?ids=...  /tracks?ids=...   (BATCH gets are 403; single gets work)
  Also: artist objects return null genres/popularity; track objects omit
  popularity. So genre/popularity-based logic is impossible — we use behavior.

The client handles 401 (refresh once + retry), 429 (honour Retry-After), and
transient 5xx (bounded backoff). It never logs tokens.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from .auth import AuthError, ensure_access_token
from .config import API_BASE, Settings
from .tokenstore import TokenStore

_MAX_RETRIES = 5


class SpotifyAPIError(RuntimeError):
    def __init__(self, status: int, message: str):
        self.status = status
        super().__init__(f"Spotify API {status}: {message}")


class SpotifyClient:
    def __init__(self, settings: Settings, store: TokenStore | None = None):
        self._settings = settings
        self._store = store or TokenStore(settings.state_dir)
        self._client = httpx.AsyncClient(base_url=API_BASE, timeout=20.0)

    async def __aenter__(self) -> "SpotifyClient":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    # -- low level ----------------------------------------------------------
    def _auth_header(self) -> dict[str, str]:
        bundle = ensure_access_token(self._settings, self._store)
        return {"Authorization": f"{bundle.token_type} {bundle.access_token}"}

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any | None = None,
    ) -> Any:
        refreshed = False
        for attempt in range(_MAX_RETRIES):
            headers = self._auth_header()
            resp = await self._client.request(
                method, path, params=params, json=json, headers=headers
            )
            if resp.status_code == 401 and not refreshed:
                # Force a refresh by clearing expiry locally, then retry once.
                refreshed = True
                bundle = self._store.load()
                if bundle and bundle.refresh_token:
                    from .auth import refresh as _refresh

                    self._store.save(_refresh(self._settings, bundle.refresh_token))
                    continue
                raise AuthError("Unauthorized and unable to refresh; please log in again.")
            if resp.status_code == 429:
                retry_after = float(resp.headers.get("Retry-After", "1"))
                await asyncio.sleep(min(retry_after, 30) + 0.1)
                continue
            if resp.status_code in (500, 502, 503, 504):
                await asyncio.sleep(min(2**attempt, 8))
                continue
            if resp.status_code == 403:
                raise SpotifyAPIError(
                    403,
                    f"{resp.text[:200]} (note: audio-features/recommendations/related-"
                    "artists are deprecated for new apps and always 403 — this client "
                    "never calls them, so a 403 here is likely a scope/Premium issue).",
                )
            if resp.status_code >= 400:
                raise SpotifyAPIError(resp.status_code, resp.text[:300])
            if resp.status_code == 204 or not resp.content:
                return None
            return resp.json()
        raise SpotifyAPIError(429, "Exhausted retries (rate limited or server errors).")

    async def get(self, path: str, **params: Any) -> Any:
        clean = {k: v for k, v in params.items() if v is not None}
        return await self.request("GET", path, params=clean or None)

    async def get_all(
        self, path: str, *, item_key: str | None = None, limit: int = 50, cap: int = 2000, **params: Any
    ) -> list[dict]:
        """Follow `next` cursors. Works for offset-paged (`items`) responses and
        cursor-paged responses that nest under `item_key` (e.g. recently-played
        nests playables under 'items')."""
        out: list[dict] = []
        page = await self.get(path, limit=limit, **params)
        while page is not None:
            container = page[item_key] if item_key and item_key in page else page
            items = container.get("items", []) if isinstance(container, dict) else []
            out.extend(items)
            if len(out) >= cap:
                return out[:cap]
            nxt = container.get("next") if isinstance(container, dict) else None
            if not nxt:
                break
            # `next` is a full URL; strip base so httpx base_url applies.
            page = await self.request("GET", nxt.replace(API_BASE, ""))
        return out

    # -- typed helpers ------------------------------------------------------
    async def me(self) -> dict:
        return await self.get("/me")

    async def top_tracks(self, time_range: str, limit: int = 50) -> list[dict]:
        data = await self.get("/me/top/tracks", time_range=time_range, limit=limit)
        return data.get("items", []) if data else []

    async def recently_played(self, limit: int = 50) -> list[dict]:
        data = await self.get("/me/player/recently-played", limit=limit)
        return data.get("items", []) if data else []

    async def saved_tracks(self, cap: int = 2000) -> list[dict]:
        return await self.get_all("/me/tracks", cap=cap)

    async def my_playlists(self, cap: int = 200) -> list[dict]:
        return await self.get_all("/me/playlists", cap=cap)

    async def create_playlist(
        self, user_id: str, name: str, *, public: bool, description: str
    ) -> dict:
        return await self.request(
            "POST",
            f"/users/{user_id}/playlists",
            json={"name": name, "public": public, "description": description},
        )

    async def add_tracks(self, playlist_id: str, uris: list[str]) -> None:
        # Spotify caps add-items at 100 per request.
        for i in range(0, len(uris), 100):
            await self.request(
                "POST", f"/playlists/{playlist_id}/tracks", json={"uris": uris[i : i + 100]}
            )

    # -- playback -----------------------------------------------------------
    async def devices(self) -> list[dict]:
        data = await self.get("/me/player/devices")
        return (data or {}).get("devices", [])

    async def playback_state(self) -> dict | None:
        return await self.get("/me/player")

    async def play(
        self, *, device_id: str | None = None, uris: list[str] | None = None, context_uri: str | None = None
    ) -> None:
        body: dict[str, Any] = {}
        if uris:
            body["uris"] = uris
        if context_uri:
            body["context_uri"] = context_uri
        params = {"device_id": device_id} if device_id else None
        await self.request("PUT", "/me/player/play", params=params, json=body or None)

    async def pause(self, device_id: str | None = None) -> None:
        params = {"device_id": device_id} if device_id else None
        await self.request("PUT", "/me/player/pause", params=params)

    async def next_track(self, device_id: str | None = None) -> None:
        params = {"device_id": device_id} if device_id else None
        await self.request("POST", "/me/player/next", params=params)
