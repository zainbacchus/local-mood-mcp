"""Deterministic, memory-based Spotify playlist generator + playback controller as an MCP server.

Designed for the 2026 Spotify Web API. Verified live against a new app: genres,
artist/track popularity, audio-features, recommendations, related-artists, and
all BATCH reads (/artists?ids=, /tracks?ids=) are unavailable (403 or null) to
new apps. Nothing here depends on them. "Mood" is derived deterministically
from three tiers of memory: API affinity (the working-memory window), the
Extended Streaming History export plus an accruing play journal (long-term
memory: recency, era, time-of-day, completion behavior), and emotional labels
the MCP client writes via annotate_tracks (semantic memory). See moods.py for
the full taxonomy.
"""

__version__ = "0.1.0"
