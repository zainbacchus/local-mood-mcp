"""Deterministic, behavior-based Spotify playlist generator + playback controller as an MCP server.

Designed for the 2026 Spotify Web API. Verified live against a new app: genres,
artist/track popularity, audio-features, recommendations, related-artists, and
all BATCH reads (/artists?ids=, /tracks?ids=) are unavailable (403 or null) to
new apps. Nothing here depends on them. "Mood" is derived deterministically from
how the user listens — affinity tiers, recency, release era, duration, and
(from the Extended Streaming History export) time-of-day and completion
behavior. See moods.py for the full taxonomy.
"""

__version__ = "0.1.0"
