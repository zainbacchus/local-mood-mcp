# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Fixed
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
Spotify Connect playback control — exposed as an MCP server for the 2026
Spotify Web API (no genres, popularity, or audio features for new apps).
