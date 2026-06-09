# local-mood-mcp

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Python](https://img.shields.io/badge/python-%E2%89%A53.11-blue.svg)
![Tests](https://img.shields.io/badge/tests-passing-brightgreen.svg)

> Spotify's API will tell a new app the last **~50 songs** you played.
> Your Spotify export remembers **every song you've ever played**.
> This project exists to show what that difference is worth.

**local-mood-mcp** is an [MCP](https://modelcontextprotocol.io) server that
builds deterministic, behavior-based Spotify playlists — and controls playback —
from Claude or any MCP client. It was built to demonstrate one idea:
**quality is a function of memory.** The scoring code never
changes; the only variable is how much of your listening history it can see.
Given identical developer constraints, adding memory turns a 50-play window
into years of behavioral signal — unlocking playlist types and explanations
that are impossible without it.

## Why I built this

I wanted my team to feel, not just hear, why memory matters in our AI
workflows. Every signal here has an analogue in the systems we build: the
API's 50-play window is a context window; the export is long-term memory; the
emotional labels are semantic memory — the model's knowledge, written down
once instead of guessed at every time; the lifetime moods are the
capabilities that only exist once a system can remember. Better context leads
to better outcomes.

## The experiment

| | Working memory (API only) | Long-term memory (your export) |
|---|---|---|
| History depth | Last ~50 plays + 3×50 "top tracks" summaries | Every stream since your account was created |
| Signals | "You played this recently", coarse affinity | Play counts, completions, skips, deliberate starts, hour-of-day, weekday/weekend, first/last play |
| Moods available | 9 instant moods | + 7 lifetime moods |
| Best possible "why" | "It's in your top tracks" | "Completed 96% of 412 plays, deliberately started, mostly 6–9 am, loyal since 2019" |

Both tiers run the exact same selection algorithm
([`playlists.py`](src/local_mood_mcp/playlists.py)). The lifetime moods
(`morning`, `late_night`, `weekend`, `on_repeat`, `comfort`, `focus_flow`,
`deep_cuts`) aren't better-tuned versions of the instant ones — they are
**informationally impossible** without the export. No amount of cleverness
recovers a completion ratio or an hour-of-day histogram from a 50-play window.

You don't have to take the table's word for it — the experiment is a tool:

- **`compare_memory(mood)`** runs the identical selection twice, with memory
  and as if only the API window existed, and returns both lists plus the diff
  ("14 of 25 picks change when memory is removed" — or, for lifetime moods,
  "impossible without memory").
- **`library_stats`** quantifies the gap with your own numbers: streams
  remembered vs. the 50-play window (`memory_multiplier`), years of history
  covered, and how much of your library the API can't even see.

A real `why`, from `explain_track` (illustrative values — only possible with
the export loaded):

```json
{
  "mood": "comfort",
  "score": 0.91,
  "components": { "completion": 0.96, "non_skip": 0.97, "loyalty": 1.0, "plays": 0.74 },
  "lifetime_plays": 412,
  "part_of_day": { "morning": 0.62, "afternoon": 0.21, "evening": 0.12, "night": 0.05 }
}
```

**What this does *not* claim:** Spotify's own features (Daylist, Discover
Weekly) use your full history internally, plus collaborative data from hundreds
of millions of users — and this tool can't recommend music you've never played
(the recommendations endpoint is gone for new apps). What it demonstrates is
what *you* can build when given the same memory Spotify keeps for itself: full
control over the slice ("morning songs, nothing explicit, pre-2010"), a
transparent per-component `why` for every pick, and reproducible results
instead of a black box.

## Quickstart

```bash
# 1. Create an app at https://developer.spotify.com/dashboard
#    Add Redirect URI: http://127.0.0.1:8888/callback  (NOT localhost) · check "Web API"
# 2. Install (Python ≥ 3.11)
python3 -m venv .venv && source .venv/bin/activate && pip install -e .
# 3. Configure & authorize (opens your browser, one time)
cp .env.example .env   # then set SPOTIFY_CLIENT_ID
local-mood-auth login  # also: status | logout
```

Then register the server with your MCP client (e.g. Claude Desktop
`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "local-mood": {
      "command": "/absolute/path/to/.venv/bin/local-mood-mcp",
      "env": { "SPOTIFY_CLIENT_ID": "your_client_id" }
    }
  }
}
```

### Typical flow in Claude

1. *"Sync my Spotify history"* → `sync_listening_history`
2. *"Make me a 25-track throwback playlist, nothing explicit, before 2010"* →
   `generate_playlist` (returns exact IDs + rationale; nothing created yet)
3. *"Create that as a private playlist called Throwbacks"* → `create_playlist`
   with the returned IDs
4. *"Play it on my laptop"* → `list_devices` then `play`

## How "mood" is defined

Three tiers of moods, matching three tiers of memory — all deterministic:

**Instant moods** (working memory — work the moment you `sync_listening_history`,
from API affinity): `current_rotation`, `steady_favorites`,
`all_time_favorites`, `throwback`, `fresh`, `long_form`, `quick_hits`, `clean`,
`explicit`.

**Lifetime moods** (long-term memory — need your *Extended Streaming History*
export, see below): `morning`, `late_night`, `weekend`, `on_repeat`, `comfort`,
`focus_flow`, `deep_cuts`.

**Emotional moods** (semantic memory — need labels written via
`annotate_tracks`): `happy`, `energetic`, `motivated`, `sad`, `melancholy`,
`calm`. Spotify exposes no emotional signal, so these can't be measured — but
the model driving the MCP client *knows* these songs. Ask it to read your
library (`list_library_tracks`), judge the tracks it knows, and write the
labels back once. That's the model's world knowledge persisted as data:
subjective judgments, honestly marked as such — and once stored, selection
over them is exactly as deterministic and reproducible as everything else.
An emotional playlist is never padded with unlabeled tracks.

Each behavioral mood is a pure scoring function in
[`moods.py`](src/local_mood_mcp/moods.py) over signals like affinity tier,
release year, duration, recency, play count, completion/skip ratio, deliberate
starts, and an hour-of-day histogram. Every selection comes with a
per-component `why` breakdown.

> **On determinism:** selection is exactly reproducible for a fixed point in
> time. Moods that weight *recency* (`current_rotation`, `on_repeat`) naturally
> shift as the clock advances and as you keep listening; re-syncing changes the
> input library. For pinned, fully reproducible runs, `build_context` accepts an
> optional `now_ms`.

## Giving it long-term memory (the Extended Streaming History)

The Web API can't give true lifetime data, so lifetime moods read Spotify's
official export:

1. Go to <https://www.spotify.com/account/privacy/> →
   **"Extended streaming history"** → **Request data**.
2. Spotify emails a download link in **~5 days** (occasionally up to 30).
3. Unzip it and **drop the JSON files into the drop folder** (subfolders are
   scanned recursively):
   - Running from a clone of this repo → [`extended_history/`](extended_history/)
     (git-ignored).
   - Installed via pip → `~/.local-mood-mcp/extended_history/`.
   - Either way you can override with `LOCAL_MOOD_HISTORY_DIR`, or point at any
     path: `import_extended_history("/some/path")`.
4. Run `sync_listening_history` — it **auto-detects and merges** the drop folder
   and unlocks the lifetime moods. (`import_extended_history` with no argument
   does the same; `extended_history_status` shows what's detected.)

Once imported, the memory is durable: **lifetime behavior survives re-syncs**,
and the import report tells you if any files were skipped or tracks dropped, so
truncated memory is visible rather than silent.

Memory also **accrues without the export**: every sync journals the API's
~50-play window into a local, append-only play log
(`~/.local-mood-mcp/play_journal.jsonl`), and folds it into lifetime signals
exactly once — so the system keeps remembering from the day you install, and
never double-counts when an export lands. A small window is enough *if you
journal what passes through it.*

The export's personal data (IPs, timestamps) is created `0700` and is never
committed. Until the export arrives, instant moods work fully.

## Tools exposed

| Tool | What it does |
|------|--------------|
| `spotify_auth_status` | Auth state + token expiry (no login side-effect) |
| `sync_listening_history` | Pull + analyze + cache your history; journals plays and preserves lifetime data |
| `import_extended_history` | Fold in lifetime behavior from the official export (defaults to the drop folder) |
| `extended_history_status` | Show the drop folder, detected files, and whether lifetime data is loaded |
| `library_stats` | Track count, tiers, eras — plus `memory_impact` metrics (memory vs. the API window) |
| `list_moods` | All moods across the three memory tiers, each marked with what it needs |
| `list_library_tracks` | Page through the library (most-listened first) — how the model reads it for labeling |
| `annotate_tracks` | Write emotional labels (semantic memory): happy, energetic, motivated, sad, melancholy, calm |
| `generate_playlist` | **Deterministic** selection → preview of exact track IDs + rationale |
| `compare_memory` | **The experiment**: same mood with vs. without memory, plus the diff |
| `explain_track` | Why a track scores as it does for a mood |
| `create_playlist` | Create a playlist from **exact** track IDs |
| `create_mood_playlist` | Select for a mood and create, in one step (still ID-based) |
| `list_devices` | Your Spotify Connect devices |
| `play` / `pause` / `skip_next` / `now_playing` | Playback control (Premium) |

## Tuning knobs

`generate_playlist` / `create_mood_playlist` accept: `count`, `min_year`,
`max_year`, `exclude_explicit`, `min_duration_ms`, `max_duration_ms`,
`require_affinity`, and `familiarity_weight` (0 = pure mood fit, 1 = pure
listen-frequency). Ordering is fully specified: final score, then affinity
plays, lifetime plays, and finally track id as a stable tiebreak.

## Security posture

- **Authorization Code + PKCE** only (Spotify ended implicit grant 2025-11-27).
  CSRF-protected with a random `state` verified on callback.
- **Loopback redirect** `http://127.0.0.1:8888/callback`. `localhost` is rejected
  at config load (Spotify no longer accepts it); the callback server binds to
  `127.0.0.1` only and handles exactly one request.
- **Least-privilege scopes** — every scope in [`config.py`](src/local_mood_mcp/config.py)
  maps to an endpoint this server actually calls; nothing is requested "just in case".
- **Tokens stored securely:** OS keyring (macOS Keychain / libsecret / Windows
  Credential Locker) by default; encrypted-file fallback (Fernet, key in keyring,
  `0600`) only if no keyring exists — with a loud warning. Tokens are never logged.
- Client secret is **optional** (PKCE needs only the client id). Secrets and local
  state (`~/.local-mood-mcp`) are kept out of the repo.

## Why behavioral moods?

<details>
<summary><strong>Spotify has locked down the Web API for new apps (verified live, June 2026)</strong></summary>

We confirmed the following **against a live new app**, not just the docs:

| Signal | Status for new apps |
|--------|---------------------|
| `audio-features` / `audio-analysis` / `recommendations` / `related-artists` | `403` — deprecated 2024-11-27, no replacement |
| Artist **genres**, artist **popularity**, **followers** | returned as `null` |
| **Track popularity** | field no longer returned |
| `GET /artists?ids=` and `GET /tracks?ids=` (**batch** reads) | `403` |
| `GET /artists/{id}`, `GET /tracks/{id}` (**single** reads) | OK |
| Release **era**, **explicit**, **duration**, IDs/names/URIs | OK |
| Your **top tracks** (3 ranges), **recently-played**, **saved library** | OK |
| Raw play history | **capped at the last ~50 plays** — no paging further back |

The upshot: **there is no genre or audio signal available to a new app**, so the
usual "mood = valence/energy/genre" approach is impossible. Instead, mood is
defined by **how you actually listen** — affinity, recency, era, length, and
(with the export) time-of-day and completion behavior.

</details>

## Limitations (stated honestly)

- **No discovery** — this re-curates what you've already played; it cannot
  suggest unheard music (the recommendations endpoint is dead for new apps).
- **No genre or acoustic mood from Spotify** — the API exposes neither to new
  apps. Behavioral moods are temporal/era-based and deliberately transparent;
  emotional moods come from **model-written labels**, which are subjective
  judgments (re-runnable, versioned via `annotate_tracks`), not measurements.
- **No "entire" history via the API** — recently-played caps at ~50 and there's
  no full-stream endpoint. `import_extended_history` is the only lifetime path.
- **Hour-of-day buckets use your machine's current timezone** — plays made in
  other timezones (or across DST shifts) are bucketed by today's clock, not the
  clock where you listened.
- **Playback requires Premium** and an active Connect device.

## Development

```bash
pip install -e ".[dev]"
pytest -q            # determinism + selection tests, synthetic data, no network
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the contribution workflow.

## License

[MIT](LICENSE)
