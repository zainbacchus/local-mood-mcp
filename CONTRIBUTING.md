# Contributing to local-mood-mcp

Thanks for your interest! This project is small, dependency-light, and built
around one principle worth preserving: **mood → tracks is deterministic and
returns exact Spotify track IDs.** Contributions that keep that guarantee intact
are very welcome.

## Development setup

Python ≥ 3.11.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest -q          # 16 tests, no network, no Spotify account required
```

The test suite runs entirely on synthetic data — no auth, no API calls — so you
can iterate on the scoring/selection logic without a Spotify app.

## Project layout

| Path | Role |
|------|------|
| `src/local_mood_mcp/moods.py` | Mood taxonomy — each mood is a pure `(Track, Context) -> (score, components)` |
| `src/local_mood_mcp/playlists.py` | Deterministic selection + ID-only playlist creation |
| `src/local_mood_mcp/history.py` | Library building (API affinity + Extended Streaming History) |
| `src/local_mood_mcp/spotify_client.py` | Async client — only non-deprecated endpoints |
| `src/local_mood_mcp/auth.py` | OAuth (PKCE) + token refresh + login CLI |
| `src/local_mood_mcp/tokenstore.py` | Keyring / encrypted-file token storage |
| `src/local_mood_mcp/server.py` | MCP tool surface |

## Guidelines

- **Keep selection deterministic.** Any new or changed scorer must be a pure
  function of `(Track, Context)`. Time-dependent inputs go through
  `build_context` (which accepts a pinnable `now_ms`) so runs stay reproducible.
- **Add a test** for new scoring behavior or selection logic. Mirror the
  synthetic-library style in `tests/test_moods.py`.
- **Don't call deprecated endpoints.** Genres, popularity, audio-features,
  recommendations, related-artists, and batch reads are all `403`/`null` for new
  apps — see the inventory at the top of `spotify_client.py`.
- **Request only scopes you use.** Every scope in `config.py` must map to an
  endpoint the server actually calls.
- **Never log tokens or commit personal data.** The Extended Streaming History
  export contains IPs and timestamps; the drop folders are git-ignored — keep it
  that way.

## Pull requests

1. Fork and branch from `main`.
2. `pytest -q` must pass (CI runs it on 3.11–3.13).
3. Describe the change and, for scoring tweaks, why the new ranking is more
   faithful to listening behavior.
