"""Memory persistence and export parsing — synthetic data, no network."""

import json

from local_mood_mcp.history import carry_over_lifetime, parse_extended_history
from local_mood_mcp.models import TIER_SHORT, Track
from local_mood_mcp.store import Library


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
