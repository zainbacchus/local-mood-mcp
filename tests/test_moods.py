"""Determinism and correctness of the mood scoring + selection layer.

These tests use synthetic tracks only — no network, no auth.
"""

from spotify_mood_mcp.models import Track
from spotify_mood_mcp.moods import get_mood, score_track, list_moods
from spotify_mood_mcp.playlists import Filters, select_for_mood, validate_track_ids
from spotify_mood_mcp.store import Library


def _t(id_, genres, pop=50, year=2020, explicit=False, plays=1):
    return Track(
        id=id_,
        name=f"track-{id_}",
        artist_ids=["a"],
        artist_names=["Artist"],
        popularity=pop,
        release_year=year,
        explicit=explicit,
        genres=genres,
        play_count=plays,
    )


def _library():
    return Library(
        tracks=[
            _t("a" * 22, ["ambient", "drone"], pop=20, plays=5),
            _t("b" * 22, ["edm", "big room house"], pop=90, plays=2),
            _t("c" * 22, ["indie folk", "acoustic"], pop=40, plays=10),
            _t("d" * 22, ["metalcore", "hardcore"], pop=55, explicit=True, plays=1),
            _t("e" * 22, [], pop=60, plays=3),  # no genre signal
        ]
    )


def test_focus_prefers_ambient_over_edm():
    lib = _library()
    focus = get_mood("focus")
    ambient = next(t for t in lib.tracks if t.id.startswith("a"))
    edm = next(t for t in lib.tracks if t.id.startswith("b"))
    assert score_track(ambient, focus)[0] > score_track(edm, focus)[0]


def test_energetic_prefers_edm_over_ambient():
    lib = _library()
    energetic = get_mood("energetic")
    ambient = next(t for t in lib.tracks if t.id.startswith("a"))
    edm = next(t for t in lib.tracks if t.id.startswith("b"))
    assert score_track(edm, energetic)[0] > score_track(ambient, energetic)[0]


def test_scores_bounded_0_1():
    lib = _library()
    for mood_key in (m["key"] for m in list_moods()):
        spec = get_mood(mood_key)
        for t in lib.tracks:
            s, _ = score_track(t, spec)
            assert 0.0 <= s <= 1.0


def test_selection_is_deterministic():
    lib = _library()
    f = Filters(familiarity_weight=0.3)
    first = [s.track.id for s in select_for_mood(lib, "chill", count=5, filters=f)]
    second = [s.track.id for s in select_for_mood(lib, "chill", count=5, filters=f)]
    assert first == second
    # And stable regardless of input ordering.
    import random

    shuffled = Library(tracks=list(lib.tracks))
    random.Random(123).shuffle(shuffled.tracks)
    third = [s.track.id for s in select_for_mood(shuffled, "chill", count=5, filters=f)]
    assert first == third


def test_explicit_filter_and_avoid_pref():
    lib = _library()
    # focus avoids explicit -> the explicit metalcore track scores 0 on explicit
    focus = get_mood("focus")
    metal = next(t for t in lib.tracks if t.id.startswith("d"))
    _, comps = score_track(metal, focus)
    assert comps["explicit"] == 0.0
    # exclude_explicit filter drops it entirely
    f = Filters(exclude_explicit=True)
    ids = [s.track.id for s in select_for_mood(lib, "focus", count=10, filters=f)]
    assert metal.id not in ids


def test_require_genre_match_drops_unknown():
    lib = _library()
    f = Filters(require_genre_match=True)
    ids = [s.track.id for s in select_for_mood(lib, "chill", count=10, filters=f)]
    assert ("e" * 22) not in ids  # the no-genre track


def test_validate_track_ids_accepts_uri_url_and_id():
    raw = [
        "1" * 22,
        "spotify:track:" + "2" * 22,
        "https://open.spotify.com/track/" + "3" * 22 + "?si=abc",
        "1" * 22,  # duplicate -> deduped
    ]
    out = validate_track_ids(raw)
    assert out == ["1" * 22, "2" * 22, "3" * 22]


def test_validate_rejects_garbage():
    import pytest

    with pytest.raises(ValueError):
        validate_track_ids(["not-a-valid-id"])
