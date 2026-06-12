"""Playback input validation — no network."""

import asyncio

import pytest

from local_mood_mcp.playback import PlaybackError, play


def test_play_rejects_both_track_ids_and_playlist():
    # The guard fires before any client call, so client=None is safe here.
    with pytest.raises(PlaybackError, match="not both"):
        asyncio.run(play(None, track_ids=["1" * 22], context_uri="spotify:playlist:x"))
