"""Semantic memory: emotional labels and the moods built on them — no network."""

import pytest

from local_mood_mcp.history import apply_annotations, carry_over_lifetime, memory_impact
from local_mood_mcp.models import TIER_SHORT, Track
from local_mood_mcp.moods import EMOTIONS, list_moods
from local_mood_mcp.playlists import (
    AnnotationsRequiredError,
    compare_memory,
    select_for_mood,
)
from local_mood_mcp.store import Library


def _t(id_, **kw):
    return Track(id=id_, name=f"track-{id_}", artist_names=["A"], **kw)


def _labeled_library():
    return Library(tracks=[
        _t("a" * 22, top_tiers=[TIER_SHORT], sources=["top_short_term"],
           emotions=["sad", "melancholy"]),
        _t("b" * 22, sources=["saved"], in_saved=True, emotions=["happy", "energetic"]),
        _t("c" * 22, sources=["saved"], in_saved=True),  # unlabeled, high affinity
    ])


# --- apply_annotations --------------------------------------------------------
def test_apply_rejects_unknown_emotions():
    lib = _labeled_library()
    with pytest.raises(ValueError, match="Unknown emotion"):
        apply_annotations(lib, {"a" * 22: ["chill"]})  # chill folded into calm


def test_apply_merges_replaces_and_normalizes():
    lib = Library(tracks=[_t("a" * 22)])
    report = apply_annotations(
        lib, {"spotify:track:" + "a" * 22: ["calm", "happy"]}
    )
    assert report["tracks_labeled_this_call"] == 1
    assert lib.by_id()["a" * 22].emotions == ["happy", "calm"]  # canonical order

    apply_annotations(lib, {"a" * 22: ["sad"]})  # merge by default
    assert lib.by_id()["a" * 22].emotions == ["happy", "sad", "calm"]

    apply_annotations(lib, {"a" * 22: ["motivated"]}, replace=True)
    assert lib.by_id()["a" * 22].emotions == ["motivated"]


def test_apply_skips_unknown_tracks_and_reports_coverage():
    lib = _labeled_library()
    report = apply_annotations(lib, {"z" * 22: ["happy"], "not-an-id": ["sad"]})
    assert report["tracks_labeled_this_call"] == 0
    assert sorted(report["unknown_track_ids_skipped"]) == ["not-an-id", "z" * 22]
    assert report["library_coverage"] == "2/3"
    assert report["label_counts"]["sad"] == 1


# --- emotional moods ------------------------------------------------------------
def test_emotional_mood_requires_labels():
    lib = Library(tracks=[_t("a" * 22, sources=["saved"], in_saved=True)])
    with pytest.raises(AnnotationsRequiredError):
        select_for_mood(lib, "sad", count=5)


def test_emotional_mood_never_pads_with_unlabeled_tracks():
    lib = _labeled_library()
    sels = select_for_mood(lib, "sad", count=10)
    assert [s.track.id for s in sels] == ["a" * 22]  # only the sad-labeled track
    assert sels[0].components["emotion_match"] == 1.0

    happy = select_for_mood(lib, "happy", count=10)
    assert [s.track.id for s in happy] == ["b" * 22]


def test_all_emotions_have_moods():
    keys = {m["key"] for m in list_moods(has_annotations=True)}
    assert set(EMOTIONS) <= keys
    assert "chill" not in keys


def test_list_moods_marks_annotation_availability():
    by_key = {m["key"]: m for m in list_moods(has_lifetime=True, has_annotations=False)}
    assert by_key["sad"]["requires_annotations"] is True
    assert by_key["sad"]["available_now"] is False
    assert by_key["comfort"]["available_now"] is True
    by_key = {m["key"]: m for m in list_moods(has_lifetime=False, has_annotations=True)}
    assert by_key["sad"]["available_now"] is True


# --- persistence and the memory story -----------------------------------------
def test_labels_survive_resync():
    previous = _labeled_library()
    previous.annotation_meta = {"labeled_by": "model", "updated_at": "2026-06-09"}
    # Fresh sync sees track a (no labels) but track b fell out of the API window.
    fresh = Library(tracks=[_t("a" * 22, top_tiers=[TIER_SHORT], sources=["top_short_term"])])
    carry_over_lifetime(previous, fresh)
    by_id = fresh.by_id()
    assert by_id["a" * 22].emotions == ["sad", "melancholy"]
    assert ("b" * 22) in by_id  # labeled track re-appended, not forgotten
    assert fresh.annotation_meta["labeled_by"] == "model"


def test_compare_memory_emotional_mood_impossible_without_memory():
    result = compare_memory(_labeled_library(), "sad", count=5)
    assert result["with_memory"]["count"] == 1
    assert result["without_memory"]["available"] is False
    assert "semantic memory" in result["without_memory"]["reason"]


def test_memory_impact_reports_semantic_tier():
    impact = memory_impact(_labeled_library())
    assert impact["semantic_memory"]["loaded"] is True
    assert impact["semantic_memory"]["tracks_labeled"] == 2
    assert impact["semantic_memory"]["labels_in_use"] == [
        "energetic", "happy", "melancholy", "sad"
    ]
    assert impact["moods_unlocked"] == 9 + 6  # instant + emotional, no lifetime
    assert impact["moods_total"] == 22
