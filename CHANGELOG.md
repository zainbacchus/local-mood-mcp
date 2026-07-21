# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- Catalog discovery, the first tools that reach beyond your own history:
  `discover` searches Spotify's catalog by genre / year / free text and
  subtracts everything already in your cached library + play journal (and,
  optionally, anything by an artist you already know), returning only tracks you
  have not heard. `search_catalog` is the raw passthrough; `create_discovery_playlist`
  does discover-then-create in one step and can `weave` familiar `seed_track_ids`
  (e.g. saved Larry June / Mac Miller) through the new finds. Non-deterministic
  by design: it hits the live catalog, the inverse of the behavioral moods.
  Verified live for this app: `/search` works but caps `limit` at 10 (we page in
  10s) and 403s on `market=from_token`; both quirks are documented in the client.
- Semantic memory tier: `annotate_tracks` persists emotional labels written
  by the MCP client (`happy`, `energetic`, `motivated`, `sad`, `melancholy`,
  `calm`), `list_library_tracks` pages the library for labeling, and six
  emotional moods select deterministically over the stored labels, never
  padded with unlabeled tracks. Labels survive re-syncs and are reported in
  `memory_impact` as their own tier (moods: 16 → 22).
- `compare_memory` tool: the README's experiment as one command, the same
  mood selected with long-term memory and as if only the API window existed,
  plus the diff (overlap, picks only memory finds, one-line verdict).
- `memory_impact` metrics in `library_stats`: streams remembered vs. the
  ~50-play API window (`memory_multiplier`), years of history, tracks with
  behavioral profiles, tracks invisible to the API window, moods unlocked.
- Incremental memory: every sync journals the API's recently-played window
  into a local append-only play log and folds it into lifetime signals
  exactly once, so memory accrues between (or without) exports and never
  double-counts when an export lands.

### Fixed
- Playlist writes migrated to the post-2026-02-11 routes (`POST /me/playlists`,
  `POST /playlists/{id}/items`). The legacy `/users/{id}/playlists` and
  `/playlists/{id}/tracks` return a generic 403 for apps created after the
  migration (verified live); only grandfathered apps keep them. Also saves an
  API round-trip (`/me` is no longer needed for creation).
- Lifetime behavior from an imported Extended Streaming History export is now
  **preserved across `sync_listening_history` runs**. Previously a re-sync
  rebuilt the library from the API window and silently wiped lifetime data
  unless the export happened to sit in the drop folder.
- `play` rejects being given both `track_ids` and `playlist_id` with a clear
  error instead of letting Spotify return a raw 400.
- `pause` / `skip_next` now translate Premium/device rejections (403/404) into
  the same friendly error `play` already used.
- `list_moods` returns a structured error object like every other tool instead
  of raising on configuration problems.
- Non-positive `count` values return an empty selection instead of mis-slicing.

### Changed
- Import reports now include `files_skipped` and `unknown_tracks_dropped`, so
  truncated memory is visible instead of silent.

## [0.1.0] - 2026-06-09

Initial release: deterministic behavioral moods (9 instant + 7 lifetime),
Extended Streaming History import, Authorization Code + PKCE auth with
keyring-backed token storage, playlist creation by exact track IDs, and
Spotify Connect playback control, all exposed as an MCP server for the 2026
Spotify Web API (no genres, popularity, or audio features for new apps).
