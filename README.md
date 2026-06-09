# spotify-mood-mcp

A **deterministic, mood-based Spotify playlist generator** and playback
controller, exposed as an [MCP](https://modelcontextprotocol.io) server so you
can drive it from Claude. Built clean and secure for the **post-2024 Spotify
Web API**.

> **Why this is built the way it is.** On **2024-11-27** Spotify deprecated the
> `audio-features`, `audio-analysis`, `recommendations`, and `related-artists`
> endpoints for all newly-created apps — they now return `403` with no official
> replacement. The classic "mood = valence/energy/danceability" trick is
> therefore **impossible** for any new app. This project does not call those
> endpoints. Instead, mood is inferred **deterministically** from signals that
> still exist: artist **genres**, track **popularity**, release **era**, the
> **explicit** flag, and *your own* play frequency.

## What it does

- **Analyzes your listening history** from every source the API still exposes:
  top tracks (short / medium / long term), recently played, and your full saved
  library — deduped and enriched with artist genres.
- **Deterministic mood mapping.** A fixed, transparent genre→mood table
  (`moods.py`) scores every track. Same library + same parameters → the
  identical ordered list of tracks, every time.
- **Explicit track-ID selection.** Generation returns **exact Spotify track
  IDs** with a per-track scoring rationale. Playlist creation only ever consumes
  explicit IDs — it never free-text "finds songs."
- **Playback control** from Claude: list devices, play exact tracks or a
  playlist, pause, skip, and see what's playing (requires Spotify Premium).
- **True lifetime history (optional):** import Spotify's official *Extended
  Streaming History* export for play counts the API can't give you.

## Security posture

- **Authorization Code + PKCE** only (Spotify ended the implicit grant flow on
  2025-11-27). CSRF-protected with a random `state` verified on callback.
- **Loopback redirect** `http://127.0.0.1:8888/callback`. `localhost` is
  rejected at config load (Spotify no longer accepts it); the loopback callback
  server binds to `127.0.0.1` only.
- **Least-privilege scopes**, each requested explicitly and justified in
  `config.py`.
- **Tokens stored securely:** OS keyring (macOS Keychain / libsecret / Windows
  Credential Locker) by default; encrypted-file fallback (Fernet, key in keyring,
  `0600`) only if no keyring is available — with a loud warning. Tokens are never
  logged.
- The client secret is **optional** (PKCE needs only the client id). Secrets and
  local state are `.gitignore`d.

## Setup

1. **Create a Spotify app** at <https://developer.spotify.com/dashboard>.
   Add this exact Redirect URI: `http://127.0.0.1:8888/callback`.

2. **Install** (Python ≥ 3.11):
   ```bash
   python3 -m venv .venv && source .venv/bin/activate
   pip install -e .
   ```

3. **Configure** — copy `.env.example` to `.env` and set `SPOTIFY_CLIENT_ID`
   (optionally `SPOTIFY_CLIENT_SECRET`).

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

## Tools exposed

| Tool | What it does |
|------|--------------|
| `spotify_auth_status` | Auth state + token expiry (no login side-effect) |
| `sync_listening_history` | Pull + analyze + cache your history |
| `import_extended_history` | Fold in lifetime play counts from the official export |
| `library_stats` | Track count, source breakdown, top genres |
| `list_moods` | The mood taxonomy and its deterministic rules |
| `generate_playlist` | **Deterministic** selection → preview of exact track IDs |
| `explain_track` | Why a track scores as it does for a mood |
| `create_playlist` | Create a playlist from **exact** track IDs |
| `create_mood_playlist` | Select for a mood and create, in one step (still ID-based) |
| `list_devices` | Your Spotify Connect devices |
| `play` / `pause` / `skip_next` / `now_playing` | Playback control (Premium) |

### Typical flow in Claude

1. *"Sync my Spotify history"* → `sync_listening_history`
2. *"Generate a 25-track focus playlist, nothing explicit"* → `generate_playlist`
   (returns exact IDs + rationale; nothing created yet)
3. *"Create that as a private playlist called Deep Work"* → `create_playlist`
   with the returned IDs
4. *"Play it on my laptop"* → `list_devices` then `play`

## Moods

`focus`, `chill`, `energetic`, `party`, `melancholy`, `uplifting`,
`aggressive`, `romantic`, `nostalgic`, `sleepy`. Each is a transparent rule set
in `moods.py` — edit the keywords/preferences to tune to your taste; behaviour
stays deterministic.

## Determinism & tuning knobs

`generate_playlist` accepts `count`, `min/max_popularity`, `min/max_year`,
`exclude_explicit`, `require_genre_match`, and `familiarity_weight` (0 = pure
mood fit, 1 = pure play-frequency). Ordering is fully specified: score, then
play count, popularity, and finally track id as a stable tiebreak.

## Limitations (by design / by API)

- **No true "entire" history via the API** — Spotify caps recently-played at
  ~50 and offers no full stream endpoint. `import_extended_history` is the only
  path to lifetime data.
- **No acoustic mood analysis** — `audio-features` is gone for new apps. Mood is
  a genre/metadata heuristic, deliberately transparent rather than a black box.
- **Playback requires Premium** and an active Connect device.

## Development

```bash
pip install -e ".[dev]"
pytest -q            # determinism + selection tests (no network)
```

## License

MIT
