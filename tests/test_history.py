"""Memory persistence, metrics, and export parsing — synthetic data, no network."""

import json

from local_mood_mcp.history import (
    carry_over_lifetime,
    fold_journal,
    memory_impact,
    observations_from_recent,
    parse_extended_history,
)
from local_mood_mcp.models import TIER_SHORT, Track
from local_mood_mcp.store import Library, append_play_journal, read_play_journal

_DAY_MS = 86_400_000


def _t(id_, **kw):
    return Track(id=id_, name=f"track-{id_}", artist_names=["A"], **kw)


def test_carry_over_preserves_lifetime_across_resync():
    previous = Library(tracks=[
        _t("a" * 22, lifetime_plays=50, completions=48, weekend_plays=7),
        _t("e" * 22, lifetime_plays=20, sources=["extended_history"]),  # export-only
        _t("n" * 22),  # no lifetime data -> nothing to preserve
    ])
    # A fresh sync only sees the API window: track a, without its history.
    fresh = Library(tracks=[_t("a" * 22, top_tiers=[TIER_SHORT])])

    preserved = carry_over_lifetime(previous, fresh)

    assert preserved == 2
    by_id = fresh.by_id()
    assert by_id["a" * 22].lifetime_plays == 50
    assert by_id["a" * 22].completions == 48
    assert by_id["a" * 22].top_tiers == [TIER_SHORT]  # fresh affinity kept
    assert ("e" * 22) in by_id  # export-only track re-appended
    assert ("n" * 22) not in by_id  # nothing worth preserving


def test_carry_over_with_no_previous_library():
    fresh = Library(tracks=[_t("a" * 22)])
    assert carry_over_lifetime(None, fresh) == 0
    assert len(fresh.tracks) == 1


def test_memory_impact_quantifies_the_delta():
    lib = Library(
        tracks=[
            _t("a" * 22, sources=["top_short_term", "extended_history"],
               lifetime_plays=400, first_play_ms=0,
               last_played_ms=int(2 * 365.25 * _DAY_MS)),  # 2 years of history
            _t("b" * 22, sources=["extended_history"], lifetime_plays=100,
               first_play_ms=_DAY_MS),  # invisible to the API window
            _t("c" * 22, sources=["saved"]),  # API-only, no behavior
        ],
        sources_summary={"recently_played": 50},
    )
    impact = memory_impact(lib)
    assert impact["long_term_memory"]["loaded"] is True
    assert impact["long_term_memory"]["streams_remembered"] == 500
    assert impact["long_term_memory"]["tracks_with_behavioral_profile"] == 2
    assert impact["long_term_memory"]["tracks_invisible_to_api_window"] == 1
    assert impact["long_term_memory"]["years_of_history"] == 2.0
    assert impact["memory_multiplier"] == 10.0  # 500 streams vs 50-play window
    assert impact["moods_unlocked"] == 16  # instant + lifetime; no labels yet
    assert impact["moods_total"] == 22
    assert impact["semantic_memory"]["loaded"] is False


def test_memory_impact_without_memory():
    lib = Library(
        tracks=[_t("a" * 22, sources=["top_short_term"])],
        sources_summary={"recently_played": 50},
    )
    impact = memory_impact(lib)
    assert impact["long_term_memory"]["loaded"] is False
    assert impact["long_term_memory"]["streams_remembered"] == 0
    assert impact["memory_multiplier"] is None
    assert impact["moods_unlocked"] == 9  # instant moods only


# --- play journal: memory that accrues --------------------------------------
def test_journal_append_dedupes(tmp_path):
    path = tmp_path / "play_journal.jsonl"
    plays = [
        {"ts_ms": 1_000, "track_id": "a" * 22, "name": "s", "artists": ["A"]},
        {"ts_ms": 2_000, "track_id": "a" * 22, "name": "s", "artists": ["A"]},
    ]
    assert append_play_journal(path, plays) == 2
    assert append_play_journal(path, plays) == 0  # next sync: same window, nothing new
    newer = plays + [{"ts_ms": 3_000, "track_id": "b" * 22, "name": "t", "artists": ["B"]}]
    assert append_play_journal(path, newer) == 1
    assert len(read_play_journal(path)) == 3


def test_fold_journal_exactly_once_and_respects_export_floor():
    lib = Library(tracks=[_t("a" * 22)], lifetime_through_ms=1_000_000)
    entries = [
        {"ts_ms": 999_999, "track_id": "a" * 22},  # already inside the export
        {"ts_ms": 2_000_000, "track_id": "a" * 22},
        {"ts_ms": 3_000_000, "track_id": "z" * 22, "name": "new", "artists": ["Z"]},
    ]
    assert fold_journal(lib, entries) == 2
    by_id = lib.by_id()
    assert by_id["a" * 22].lifetime_plays == 1  # the pre-export play didn't double
    assert ("z" * 22) in by_id  # the journal can introduce tracks
    assert by_id["z" * 22].sources == ["journal"]
    assert lib.journal_through_ms == 3_000_000
    assert fold_journal(lib, entries) == 0  # second sync folds nothing again


def test_journal_alone_accrues_lifetime_memory():
    lib = Library(tracks=[_t("a" * 22)])  # no export ever imported
    entries = [{"ts_ms": ts, "track_id": "a" * 22} for ts in (1_000, 2_000, 3_000)]
    assert fold_journal(lib, entries) == 3
    track = lib.by_id()["a" * 22]
    assert track.lifetime_plays == 3
    assert track.has_lifetime  # memory built purely from journaled API windows


def test_carry_over_preserves_memory_markers():
    previous = Library(tracks=[], lifetime_through_ms=5, journal_through_ms=9)
    fresh = Library(tracks=[])
    carry_over_lifetime(previous, fresh)
    assert fresh.lifetime_through_ms == 5
    assert fresh.journal_through_ms == 9


def test_observations_from_recent_extracts_plays():
    items = [
        {"track": {"id": "a" * 22, "name": "s", "artists": [{"name": "A"}]},
         "played_at": "2026-01-01T10:00:00Z"},
        {"track": {}, "played_at": "2026-01-01T11:00:00Z"},  # no id -> skipped
    ]
    obs = observations_from_recent(items)
    assert len(obs) == 1
    assert obs[0]["track_id"] == "a" * 22
    assert isinstance(obs[0]["ts_ms"], int)
    assert obs[0]["artists"] == ["A"]


def test_parse_aggregates_and_reports_skipped_files(tmp_path):
    entry = {
        "spotify_track_uri": "spotify:track:" + "a" * 22,
        "master_metadata_track_name": "Song",
        "master_metadata_album_artist_name": "Artist",
        "ms_played": 200_000,
        "ts": "2023-05-01T08:30:00Z",
        "reason_start": "clickrow",
        "reason_end": "trackdone",
    }
    (tmp_path / "good.json").write_text(json.dumps([entry, entry]), encoding="utf-8")
    (tmp_path / "corrupt.json").write_text("{this is not json", encoding="utf-8")
    (tmp_path / "not_a_list.json").write_text(json.dumps({"hello": 1}), encoding="utf-8")

    tracks, skipped = parse_extended_history(tmp_path)

    assert sorted(skipped) == ["corrupt.json", "not_a_list.json"]
    t = tracks["a" * 22]
    assert t.lifetime_plays == 2
    assert t.completions == 2
    assert t.deliberate_starts == 2
