"""The with/without-memory experiment — synthetic data, no network."""

from local_mood_mcp.models import TIER_LONG, TIER_SHORT, Track
from local_mood_mcp.playlists import compare_memory
from local_mood_mcp.store import Library


def _t(id_, **kw):
    return Track(id=id_, name=f"track-{id_}", artist_names=["A"], **kw)


def _hybrid_library():
    """API-visible tracks with and without behavioral profiles, plus one track
    only memory knows about."""
    return Library(
        tracks=[
            _t("a" * 22, top_tiers=[TIER_SHORT], sources=["top_short_term", "recently_played"],
               release_year=2024, duration_ms=200_000, api_recent_plays=3,
               lifetime_plays=10, completions=9, first_play_ms=1, last_played_ms=2),
            _t("b" * 22, top_tiers=[TIER_LONG], sources=["top_long_term"],
               release_year=1995, duration_ms=420_000,
               lifetime_plays=80, completions=70, first_play_ms=1),
            _t("x" * 22, sources=["extended_history"],
               lifetime_plays=120, completions=110, first_play_ms=1),  # memory-only
        ],
        sources_summary={"recently_played": 1},
    )


def test_compare_instant_mood_diffs_the_two_worlds():
    lib = _hybrid_library()
    result = compare_memory(lib, "all_time_favorites", count=3)

    with_ids = [t["id"] for t in result["with_memory"]["tracks"]]
    wo_ids = [t["id"] for t in result["without_memory"]["tracks"]]
    assert ("x" * 22) in with_ids       # memory surfaces the export-only track
    assert ("x" * 22) not in wo_ids     # the API window has never seen it
    assert result["without_memory"]["available"] is True
    only_with = {t["id"] for t in result["comparison"]["only_with_memory"]}
    assert ("x" * 22) in only_with
    assert "change when memory is removed" in result["comparison"]["summary"]


def test_compare_lifetime_mood_is_impossible_without_memory():
    lib = _hybrid_library()
    result = compare_memory(lib, "on_repeat", count=3)
    assert result["with_memory"]["count"] > 0
    assert result["without_memory"]["available"] is False
    assert "impossible without memory" in result["comparison"]["summary"]


def test_compare_is_deterministic():
    lib = _hybrid_library()
    assert compare_memory(lib, "all_time_favorites", count=3) == compare_memory(
        lib, "all_time_favorites", count=3
    )


def test_compare_without_any_memory_loaded_says_so():
    lib = Library(tracks=[
        _t("a" * 22, top_tiers=[TIER_SHORT], sources=["top_short_term"], release_year=2024),
    ])
    result = compare_memory(lib, "current_rotation", count=5)
    assert "No long-term memory is loaded" in result["comparison"]["summary"]
