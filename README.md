# spotify-mood-mcp

A **deterministic, behavior-based Spotify playlist generator** and playback
controller, exposed as an [MCP](https://modelcontextprotocol.io) server so you
can drive it from Claude. Built clean and secure for the **2026 Spotify Web
API** — Spotify-only, no external services.

> ### Why "mood" works the way it does here (verified live, June 2026)
> Spotify has progressively locked down its Web API for newly-created apps. We
> confirmed the following **against a live new app**, not just the docs:
>
> | Signal | Status for new apps |
> |--------|---------------------|
> | `audio-features` / `audio-analysis` / `recommendations` / `related-artists` | `403` — deprecated 2024-11-27, no replacement |
> | Artist **genres**, artist **popularity**, **followers** | returned as `null` |
> | **Track popularity** | field no longer returned |
> | `GET /artists?ids=` and `GET /tracks?ids=` (**batch** reads) | `403` |
> | `GET /artists/{id}`, `GET /tracks/{id}` (**single** reads) | OK |
> | Release **era**, **explicit**, **duration**, IDs/names/URIs | OK |
> | Your **top tracks** (3 ranges), **recently-played**, **saved library** | OK |
>
> The upshot: **there is no genre or audio signal available to a new app**, so
> the usual "mood = valence/energy/genre" approach is impossible. Instead, mood
> is defined by **how you actually listen** — affinity, recency, era, length,
> and (with the export) time-of-day and completion behavior. Same library +
> same parameters → the identical ordered list of exact track IDs, every time.

## How "mood" is defined

Two tiers of moods, all deterministic:

**Instant moods** (work the moment you `sync_listening_history`, from API affinity):
`current_rotation`, `steady_favorites`, `all_time_favorites`, `throwback`,
`fresh`, `long_form`, `quick_hits`, `clean`, `explicit`.

**Lifetime moods** (need your *Extended Streaming History* export — see below):
`morning`, `late_night`, `weekend`, `on_repeat`, `comfort`, `focus_flow`,
`deep_cuts`.

Each mood is a pure scoring function in [`moods.py`](src/spotify_mood_mcp/moods.py)
over signals like affinity tier, release year, duration, recency, play count,
completion/skip ratio, deliberate starts, and an hour-of-day histogram. Every
selection comes with a per-component `why` breakdown.

## Security posture

- **Authorization Code + PKCE** only (Spotify ended implicit grant 2025-11-27).
  CSRF-protected with a random `state` verified on callback.
- **Loopback redirect** `http://127.0.0.1:8888/callback`. `localhost` is rejected
  at config load (Spotify no longer accepts it); the callback server binds to
  `127.0.0.1` only and handles exactly one request.
- **Least-privilege scopes**, each requested explicitly and justified in
  [`config.py`](src/spotify_mood_mcp/config.py).
- **Tokens stored securely:** OS keyring (macOS Keychain / libsecret / Windows
  Credential Locker) by default; encrypted-file fallback (Fernet, key in keyring,
  `0600`) only if no keyring exists — with a loud warning. Tokens are never logged.
- Client secret is **optional** (PKCE needs only the client id). Secrets and local
  state (`~/.spotify-mood-mcp`) are kept out of the repo.

## Setup

1. **Create a Spotify app** at <https://developer.spotify.com/dashboard>.
   Add this exact Redirect URI: `http://127.0.0.1:8888/callback`
   (not `localhost`). Check **Web API**.

2. **Install** (Python ≥ 3.11):
   ```bash
   python3 -m venv .venv && source .venv/bin/activate
   pip install -e .
   ```

3. **Configure** — `cp .env.example .env`, then set `SPOTIFY_CLIENT_ID`.

4. **Authorize once** (opens your browser):
   ```bash
   spotify-mood-auth login      # also: status | logout
   ```

5. **Register the MCP server** with your client (e.g. Claude Desktop
   `claude_desktop_config.json`):
   ```json
   {
     "mcpServers": {
       "spotify-mood": {
         "command": "/absolute/path/to/.venv/bin/spotify-mood-mcp",
         "env": { "SPOTIFY_CLIENT_ID": "your_client_id" }
       }
     }
   }
   ```

## Getting your Extended Streaming History (unlocks lifetime moods)

The Web API can't give true lifetime data, so lifetime moods read Spotify's
official export:

1. Go to <https://www.spotify.com/account/privacy/> →
   **"Extended streaming history"** → **Request data**.
2. Spotify emails a download link in **~5 days** (occasionally up to 30).
3. Unzip it and **drop the JSON files into the [`extended_history/`](extended_history/)
   folder** in this repo (subfolders are scanned recursively).
4. Run `sync_listening_history` — it **auto-detects and merges** the drop folder
   and unlocks the lifetime moods. (`import_extended_history` with no argument
   does the same; `extended_history_status` shows what's detected.)

The drop folder is **git-ignored** — the export's personal data (IPs,
timestamps) can never be committed. Until the export arrives, instant moods work
fully. You can also point at any path: `import_extended_history("/some/path")`.

## Tools exposed

| Tool | What it does |
|------|--------------|
| `spotify_auth_status` | Auth state + token expiry (no login side-effect) |
| `sync_listening_history` | Pull + analyze + cache your history (instant signals) |
| `import_extended_history` | Fold in lifetime behavior from the official export (defaults to the drop folder) |
| `extended_history_status` | Show the drop folder, detected files, and whether lifetime data is loaded |
| `library_stats` | Track count, affinity tiers, era distribution, lifetime status |
| `list_moods` | The moods, each marked instant vs. needs-export |
| `generate_playlist` | **Deterministic** selection → preview of exact track IDs + rationale |
| `explain_track` | Why a track scores as it does for a mood |
| `create_playlist` | Create a playlist from **exact** track IDs |
| `create_mood_playlist` | Select for a mood and create, in one step (still ID-based) |
| `list_devices` | Your Spotify Connect devices |
| `play` / `pause` / `skip_next` / `now_playing` | Playback control (Premium) |

### Typical flow in Claude

1. *"Sync my Spotify history"* → `sync_listening_history`
2. *"Make me a 25-track throwback playlist, nothing explicit, before 2010"* →
   `generate_playlist` (returns exact IDs + rationale; nothing created yet)
3. *"Create that as a private playlist called Throwbacks"* → `create_playlist`
   with the returned IDs
4. *"Play it on my laptop"* → `list_devices` then `play`

## Tuning knobs

`generate_playlist` / `create_mood_playlist` accept: `count`, `min_year`,
`max_year`, `exclude_explicit`, `min_duration_ms`, `max_duration_ms`,
`require_affinity`, and `familiarity_weight` (0 = pure mood fit, 1 = pure
listen-frequency). Ordering is fully specified: final score, then affinity
plays, lifetime plays, and finally track id as a stable tiebreak.

## Limitations (by API, stated honestly)

- **No genre or acoustic mood** — Spotify exposes neither to new apps. Mood is
  behavioral/temporal/era-based, deliberately transparent rather than a black box.
- **No "entire" history via the API** — recently-played caps at ~50 and there's
  no full-stream endpoint. `import_extended_history` is the only lifetime path.
- **Playback requires Premium** and an active Connect device.

## Development

```bash
pip install -e ".[dev]"
pytest -q            # 15 determinism + selection tests, no network
```

## License

MIT
