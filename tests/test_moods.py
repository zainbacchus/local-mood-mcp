"""Determinism and correctness of the behavioral mood + selection layer.

Synthetic tracks only — no network, no auth.
"""

import pytest

from spotify_mood_mcp.models import TIER_LONG, TIER_SHORT, TIER_MEDIUM, Track
from spotify_mood_mcp.moods import build_context, get_mood, list_moods, score_track
from spotify_mood_mcp.playlists import (
    Filters,
    LifetimeRequiredError,
    select_for_mood,
    validate_track_ids,
)
from spotify_mood_mcp.store import Library


def _t(id_, **kw):
    return Track(id=id_, name=f"track-{id_}", artist_names=["A"], **kw)


def _morning_hist():
    h = [0] * 24
    for hr in (6, 7, 8, 9):
        h[hr] = 5
    return h


def _night_hist():
    h = [0] * 24
    for hr in (23, 0, 1, 2):
        h[hr] = 5
    return h


def _instant_library():
    return Library(tracks=[
        _t("a" * 22, top_tiers=[TIER_SHORT], release_year=2024, duration_ms=180_000, api_recent_plays=3),
        _t("b" * 22, top_tiers=[TIER_LONG], release_year=1995, duration_ms=420_000),
        _t("c" * 22, top_tiers=[TIER_MEDIUM], release_year=2010, duration_ms=70_000, explicit=True),
        _t("d" * 22, top_tiers=[], release_year=2026, duration_ms=200_000, in_saved=True),
    ])


def _lifetime_library():
    return Library(tracks=[
        _t("a" * 22, lifetime_plays=50, completions=48, skips=1, deliberate_starts=40,
           hour_hist=_morning_hist(), weekday_plays=30, weekend_plays=2, first_play_ms=1),
        _t("b" * 22, lifetime_plays=40, completions=10, skips=25,
           hour_hist=_night_hist(), weekday_plays=5, weekend_plays=20, first_play_ms=1),
        _t("c" * 22, lifetime_plays=3, completions=3, skips=0,
           hour_hist=[1] * 24),
    ])


# --- instant moods ----------------------------------------------------------
def test_current_rotation_prefers_short_term():
    lib = _instant_library()
    ctx = build_context(lib.tracks)
    short = next(t for t in lib.tracks if t.id.startswith("a"))
    long_ = next(t for t in lib.tracks if t.id.startswith("b"))
    assert score_track(short, "current_rotation", ctx)[0] > score_track(long_, "current_rotation", ctx)[0]


def test_all_time_prefers_long_term():
    lib = _instant_library()
    ctx = build_context(lib.tracks)
    short = next(t for t in lib.tracks if t.id.startswith("a"))
    long_ = next(t for t in lib.tracks if t.id.startswith("b"))
    assert score_track(long_, "all_time_favorites", ctx)[0] > score_track(short, "all_time_favorites", ctx)[0]


def test_throwback_vs_fresh_era():
    lib = _instant_library()
    ctx = build_context(lib.tracks)
    old = next(t for t in lib.tracks if t.id.startswith("b"))   # 1995
    new = next(t for t in lib.tracks if t.id.startswith("d"))   # 2026
    assert score_track(old, "throwback", ctx)[0] > score_track(new, "throwback", ctx)[0]
    assert score_track(new, "fresh", ctx)[0] > score_track(old, "fresh", ctx)[0]


def test_long_form_vs_quick_hits_duration():
    lib = _instant_library()
    ctx = build_context(lib.tracks)
    long_ = next(t for t in lib.tracks if t.id.startswith("b"))  # 7 min
    short = next(t for t in lib.tracks if t.id.startswith("c"))  # 70 s
    assert score_track(long_, "long_form", ctx)[0] > score_track(short, "long_form", ctx)[0]
    assert score_track(short, "quick_hits", ctx)[0] > score_track(long_, "quick_hits", ctx)[0]


def test_clean_vs_explicit():
    lib = _instant_library()
    ctx = build_context(lib.tracks)
    explicit = next(t for t in lib.tracks if t.id.startswith("c"))
    clean = next(t for t in lib.tracks if t.id.startswith("a"))
    assert score_track(explicit, "explicit", ctx)[0] > 0
    assert score_track(explicit, "clean", ctx)[0] == 0.0
    assert score_track(clean, "clean", ctx)[0] > 0


# --- lifetime moods ---------------------------------------------------------
def test_morning_and_night_separation():
    lib = _lifetime_library()
    ctx = build_context(lib.tracks)
    morning = next(t for t in lib.tracks if t.id.startswith("a"))
    night = next(t for t in lib.tracks if t.id.startswith("b"))
    assert score_track(morning, "morning", ctx)[0] > score_track(night, "morning", ctx)[0]
    assert score_track(night, "late_night", ctx)[0] > score_track(morning, "late_night", ctx)[0]


def test_on_repeat_prefers_high_plays():
    lib = _lifetime_library()
    ctx = build_context(lib.tracks)
    heavy = next(t for t in lib.tracks if t.id.startswith("a"))  # 50 plays
    light = next(t for t in lib.tracks if t.id.startswith("c"))  # 3 plays
    assert score_track(heavy, "on_repeat", ctx)[0] > score_track(light, "on_repeat", ctx)[0]


def test_comfort_rewards_completion_over_skips():
    lib = _lifetime_library()
    ctx = build_context(lib.tracks)
    loved = next(t for t in lib.tracks if t.id.startswith("a"))   # completes, rarely skips
    skipped = next(t for t in lib.tracks if t.id.startswith("b"))  # high skip
    assert score_track(loved, "comfort", ctx)[0] > score_track(skipped, "comfort", ctx)[0]


def test_lifetime_mood_without_export_raises():
    lib = _instant_library()  # no lifetime data
    with pytest.raises(LifetimeRequiredError):
        select_for_mood(lib, "morning", count=5)


# --- determinism & filters --------------------------------------------------
def test_selection_is_deterministic_and_order_invariant():
    import random

    lib = _instant_library()
    f = Filters(familiarity_weight=0.3)
    first = [s.track.id for s in select_for_mood(lib, "all_time_favorites", count=4, filters=f)]
    second = [s.track.id for s in select_for_mood(lib, "all_time_favorites", count=4, filters=f)]
    assert first == second
    shuffled = Library(tracks=list(lib.tracks))
    random.Random(7).shuffle(shuffled.tracks)
    third = [s.track.id for s in select_for_mood(shuffled, "all_time_favorites", count=4, filters=f)]
    assert first == third


def test_filters_year_and_explicit_and_duration():
    lib = _instant_library()
    ids = [s.track.id for s in select_for_mood(
        lib, "all_time_favorites", count=10,
        filters=Filters(exclude_explicit=True, min_year=2000, max_duration_ms=300_000),
    )]
    # c is explicit -> dropped; b is 1995 -> dropped by min_year; b is 7min -> also dropped
    assert ("c" * 22) not in ids
    assert ("b" * 22) not in ids


def test_part_of_day_shares_sum_to_one():
    t = _t("a" * 22, hour_hist=_morning_hist(), lifetime_plays=20)
    shares = t.part_of_day_shares()
    assert abs(sum(shares.values()) - 1.0) < 1e-9
    assert shares["morning"] == 1.0


def test_list_moods_marks_lifetime_availability():
    moods = list_moods(has_lifetime=False)
    by_key = {m["key"]: m for m in moods}
    assert by_key["current_rotation"]["available_now"] is True
    assert by_key["morning"]["available_now"] is False
    assert by_key["morning"]["requires_extended_history"] is True


def test_validate_track_ids_accepts_uri_url_and_dedupes():
    out = validate_track_ids([
        "1" * 22,
        "spotify:track:" + "2" * 22,
        "https://open.spotify.com/track/" + "3" * 22 + "?si=abc",
        "1" * 22,
    ])
    assert out == ["1" * 22, "2" * 22, "3" * 22]


def test_validate_rejects_garbage():
    with pytest.raises(ValueError):
        validate_track_ids(["not-a-valid-id"])
