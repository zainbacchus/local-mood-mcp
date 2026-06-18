"""Catalog discovery: query building, parsing, merge, library-aware filtering,
seed blending, and the async orchestration over a fake (no-network) client.
"""

import asyncio

import pytest

from local_mood_mcp.discover import (
    DiscoveryCandidate,
    blend_ids,
    build_search_queries,
    discover,
    filter_and_rank,
    interleave,
    search,
    track_from_search,
)


def _c(id_, artists=("A",), explicit=False, year=2024):
    return DiscoveryCandidate(
        id=id_,
        name=f"t-{id_}",
        artist_names=list(artists),
        release_year=year,
        duration_ms=200_000,
        explicit=explicit,
    )


def _item(id_, artists=("A",), explicit=False, release_date="2024-01-01"):
    return {
        "id": id_,
        "name": f"song-{id_}",
        "explicit": explicit,
        "duration_ms": 210_000,
        "artists": [{"name": a} for a in artists],
        "album": {"release_date": release_date},
    }


class _FakeClient:
    """Implements only the surface discover.py uses: search_tracks(q, limit, offset)."""

    def __init__(self, by_query: dict[str, list[dict]]):
        self.by_query = by_query
        self.calls: list[tuple[str, int, int]] = []

    async def search_tracks(self, q, *, limit=50, offset=0):
        self.calls.append((q, limit, offset))
        return self.by_query.get(q, [])[offset : offset + limit]


# --- query building ----------------------------------------------------------
def test_build_queries_genre_and_year():
    assert build_search_queries(["deep house", "melodic house"], None, 2023, 2026) == [
        'genre:"deep house" year:2023-2026',
        'genre:"melodic house" year:2023-2026',
    ]


def test_build_queries_genre_with_text_and_open_year():
    # only min_year -> upper bound defaults; text appended to each genre query
    assert build_search_queries(["deep house"], "sunset", 2023, None) == [
        'genre:"deep house" sunset year:2023-2100'
    ]


def test_build_queries_text_only():
    assert build_search_queries(None, "melodic house chill", None, None) == [
        "melodic house chill"
    ]


def test_build_queries_empty_when_nothing_usable():
    assert build_search_queries(None, None, None, None) == []
    assert build_search_queries(["  "], "", None, None) == []


# --- parsing -----------------------------------------------------------------
def test_track_from_search_parses_year_artists_explicit():
    c = track_from_search(_item("x" * 22, artists=["Lane 8", "Kasablanca"], explicit=True))
    assert c.id == "x" * 22
    assert c.artist_names == ["Lane 8", "Kasablanca"]
    assert c.release_year == 2024
    assert c.explicit is True


def test_track_from_search_handles_junk():
    assert track_from_search({}) is None
    assert track_from_search(None) is None
    bare = track_from_search({"id": "z", "artists": [], "album": {}})
    assert bare.release_year is None and bare.duration_ms == 0


# --- merge / filter / blend (pure) -------------------------------------------
def test_interleave_round_robins():
    a = [_c("a1"), _c("a2")]
    b = [_c("b1"), _c("b2"), _c("b3")]
    assert [c.id for c in interleave([a, b])] == ["a1", "b1", "a2", "b2", "b3"]


def test_filter_excludes_known_ids_and_dedupes():
    cands = [_c("id1"), _c("id1"), _c("id2"), _c("id3")]
    out = filter_and_rank(
        cands, known_ids={"id2"}, known_artists=set(),
        exclude_known=True, exclude_known_artists=False, exclude_explicit=False, count=10,
    )
    assert [c.id for c in out] == ["id1", "id3"]


def test_filter_excludes_known_artists_case_insensitive():
    cands = [
        _c("id1", artists=["Lane 8"]),
        _c("id2", artists=["New Artist"]),
        _c("id3", artists=["mac miller", "Guest"]),  # any known artist -> dropped
    ]
    out = filter_and_rank(
        cands, known_ids=set(), known_artists={"lane 8", "mac miller"},
        exclude_known=False, exclude_known_artists=True, exclude_explicit=False, count=10,
    )
    assert [c.id for c in out] == ["id2"]


def test_filter_excludes_explicit_and_respects_count():
    cands = [_c("id1", explicit=True), _c("id2"), _c("id3"), _c("id4")]
    out = filter_and_rank(
        cands, set(), set(),
        exclude_known=False, exclude_known_artists=False, exclude_explicit=True, count=2,
    )
    assert [c.id for c in out] == ["id2", "id3"]


def test_filter_count_zero_returns_empty():
    out = filter_and_rank(
        [_c("id1")], set(), set(),
        exclude_known=False, exclude_known_artists=False, exclude_explicit=False, count=0,
    )
    assert out == []


def test_blend_weaves_seeds_through_discovered_anchor_first():
    out = blend_ids(["s1", "s2"], ["d1", "d2", "d3", "d4"], weave=True)
    assert out[0] == "s1"                       # an anchor opens the mix
    assert set(out) == {"s1", "s2", "d1", "d2", "d3", "d4"}
    assert len(out) == 6                        # nothing dropped at this layer


def test_blend_prepend_when_not_weaving():
    assert blend_ids(["s1"], ["d1", "d2"], weave=False) == ["s1", "d1", "d2"]


def test_blend_no_seeds_or_no_discovered():
    assert blend_ids([], ["d1", "d2"]) == ["d1", "d2"]
    assert blend_ids(["s1", "s2"], []) == ["s1", "s2"]


# --- async orchestration (fake client) ---------------------------------------
def test_discover_merges_genres_and_filters_known():
    dh = [_item(f"dh{i}", artists=[f"Art{i}"]) for i in range(3)]
    dh[0]["id"] = "known_id"                    # already in library -> excluded
    mh = [_item("mh0", artists=["Lane 8"])]     # known artist -> excluded
    client = _FakeClient({
        'genre:"deep house" year:2023-2026': dh,
        'genre:"melodic house" year:2023-2026': mh,
    })
    out = asyncio.run(discover(
        client,
        genres=["deep house", "melodic house"],
        count=10, min_year=2023, max_year=2026,
        known_ids={"known_id"}, known_artists={"lane 8"},
        exclude_known=True, exclude_known_artists=True, per_query=50,
    ))
    ids = [c.id for c in out]
    assert "known_id" not in ids               # excluded by id
    assert "mh0" not in ids                     # excluded by known artist
    assert ids == ["dh1", "dh2"]                # remaining, interleave order


def test_discover_requires_genres_or_query():
    with pytest.raises(ValueError):
        asyncio.run(discover(_FakeClient({}), genres=None, query=None))


def test_search_raw_dedupes_and_drops_explicit():
    items = [
        _item("a", explicit=True),
        _item("b"),
        _item("b"),  # duplicate id
    ]
    client = _FakeClient({"q": items})
    out = asyncio.run(search(client, "q", limit=10, exclude_explicit=True))
    assert [c.id for c in out] == ["b"]
