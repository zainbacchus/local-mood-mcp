"""Deterministic, mood-based Spotify playlist generator + playback controller as an MCP server.

Designed for the post-2024 Spotify Web API: the audio-features, audio-analysis,
recommendations, and related-artists endpoints were deprecated for new apps on
2024-11-27 and return 403. Nothing in this package calls them. Mood is derived
deterministically from artist genres, popularity, era, and the user's own play
history. See moods.py for the full taxonomy.
"""

__version__ = "0.1.0"
