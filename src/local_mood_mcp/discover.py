"""Catalog discovery — the one place the toolkit reaches BEYOND the user.

Every other module is built from how the user already listens (behavioral
memory). Discovery is the inverse: surface tracks the user has NOT heard. It is
therefore the only module that

  * touches the live catalog (so it is intentionally NON-deterministic), and
  * returns tracks that are deliberately outside the cached library.

Constraints baked in (all verified live against this app's token):
  * GET /search works; /recommendations and /artists/{id}/related-artists are
    403 for new apps — so there is no "tracks similar to artist X". Discovery is
    driven by genre / year / free-text search queries, not similarity seeds.
  * `market=from_token` 403s (needs user-read-private, which this app does not
    request) — the client never sends it.
  * this app's /search caps `limit` at 10 (verified live — 11+ returns 400
    "Invalid limit"), so we page through results in 10s via `offset`.
  * track/artist objects omit popularity and genres for new apps — we rank by
    Spotify's own search ordering, interleaved across queries for breadth.

Library-awareness: the cached library + the play journal define what the user
already knows. `filter_and_rank` removes those, so what comes back is new. The
pure helpers (query building, parsing, interleave, filter, blend) carry no I/O
and are unit-tested without a network; `search`/`discover` add the live calls
over a duck-typed client exposing `search_tracks(q, *, limit, offset)`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import parse_year

# This app's /search caps `limit` at 10 (verified live — 11+ -> 400 "Invalid
# limit"); offset goes up to 1000. So we page through results in 10s.
_PAGE = 10
_MAX_OFFSET = 1000


@dataclass
class DiscoveryCandidate:
    id: str
    name: str
    artist_names: list[str]
    release_year: int | None
    duration_ms: int
    explicit: bool

    @property
    def uri(self) -> str:
        return f"spotify:track:{self.id}"

    def to_preview(self) -> dict:
        return {
            "id": self.id,
            "uri": self.uri,
            "name": self.name,
            "artists": self.artist_names,
            "release_year": self.release_year,
            "duration_ms": self.duration_ms,
            "explicit": self.explicit,
            "source": "catalog_search",
        }


# --- pure helpers (no I/O) ---------------------------------------------------
def build_search_queries(
    genres: list[str] | None,
    query: str | None,
    min_year: int | None,
    max_year: int | None,
) -> list[str]:
    """Turn the discovery intent into one Spotify search query per genre (or a
    single text query). Year bounds become a `year:LO-HI` filter shared by every
    query. Returns [] when there's nothing to search for."""
    year_token = ""
    if min_year is not None or max_year is not None:
        lo = min_year if min_year is not None else 1900
        hi = max_year if max_year is not None else 2100
        year_token = f"year:{lo}-{hi}"

    text = (query or "").strip()
    extra = " ".join(p for p in (text, year_token) if p)

    queries: list[str] = []
    cleaned_genres = [g.strip() for g in (genres or []) if g and g.strip()]
    if cleaned_genres:
        for g in cleaned_genres:
            parts = [f'genre:"{g}"']
            if extra:
                parts.append(extra)
            queries.append(" ".join(parts))
    elif text:
        queries.append(" ".join(p for p in (text, year_token) if p))
    return queries


def track_from_search(item: dict | None) -> DiscoveryCandidate | None:
    """Normalize one /search track object into a DiscoveryCandidate (or None)."""
    if not item or not item.get("id"):
        return None
    album = item.get("album") or {}
    return DiscoveryCandidate(
        id=item["id"],
        name=item.get("name", ""),
        artist_names=[a.get("name", "") for a in (item.get("artists") or []) if a],
        release_year=parse_year(album.get("release_date")),
        duration_ms=int(item.get("duration_ms") or 0),
        explicit=bool(item.get("explicit", False)),
    )


def interleave(lists: list[list[Any]]) -> list[Any]:
    """Round-robin merge so multi-genre discovery doesn't front-load one genre:
    take the 1st of each list, then the 2nd of each, and so on."""
    out: list[Any] = []
    longest = max((len(lst) for lst in lists), default=0)
    for i in range(longest):
        for lst in lists:
            if i < len(lst):
                out.append(lst[i])
    return out


def filter_and_rank(
    candidates: list[DiscoveryCandidate],
    known_ids: set[str],
    known_artists: set[str],
    *,
    exclude_known: bool,
    exclude_known_artists: bool,
    exclude_explicit: bool,
    count: int,
) -> list[DiscoveryCandidate]:
    """Dedupe by id (order-preserving) and drop anything filtered out, keeping at
    most `count`. `known_artists` must already be lowercased/stripped."""
    if count <= 0:
        return []
    seen: set[str] = set()
    out: list[DiscoveryCandidate] = []
    for c in candidates:
        if not c or not c.id or c.id in seen:
            continue
        if exclude_explicit and c.explicit:
            continue
        if exclude_known and c.id in known_ids:
            continue
        if exclude_known_artists and any(
            a.strip().lower() in known_artists for a in c.artist_names
        ):
            continue
        seen.add(c.id)
        out.append(c)
        if len(out) >= count:
            break
    return out


def blend_ids(seeds: list[str], discovered: list[str], *, weave: bool = True) -> list[str]:
    """Combine familiar anchor IDs (e.g. saved Larry June / Mac Miller) with the
    freshly discovered IDs. weave=True spreads the seeds evenly through the
    discoveries (anchor first); weave=False simply puts the seeds up front.
    De-duplication is left to create_playlist_from_ids."""
    if not seeds:
        return list(discovered)
    if not discovered:
        return list(seeds)
    if not weave:
        return list(seeds) + list(discovered)
    gap = max(1, len(discovered) // len(seeds))
    out: list[str] = []
    si = 0
    for i, d in enumerate(discovered):
        if si < len(seeds) and i % gap == 0:
            out.append(seeds[si])
            si += 1
        out.append(d)
    out.extend(seeds[si:])
    return out


# --- live calls (over a duck-typed client) ----------------------------------
async def _search_paged(client: Any, q: str, want: int) -> list[dict]:
    """Page /search until we have `want` raw items (or the catalog runs dry)."""
    out: list[dict] = []
    offset = 0
    while len(out) < want and offset < _MAX_OFFSET:
        items = await client.search_tracks(q, limit=_PAGE, offset=offset)
        if not items:
            break
        out.extend(items)
        if len(items) < _PAGE:
            break
        offset += _PAGE
    return out[:want]


async def search(
    client: Any, query: str, *, limit: int = 20, exclude_explicit: bool = False
) -> list[DiscoveryCandidate]:
    """Raw catalog search: page up to `limit` results, normalized + de-duped. No
    library awareness (use discover() for that)."""
    want = max(1, int(limit))
    raw = await _search_paged(client, query, want)
    cands = [c for c in (track_from_search(it) for it in raw) if c]
    return filter_and_rank(
        cands,
        known_ids=set(),
        known_artists=set(),
        exclude_known=False,
        exclude_known_artists=False,
        exclude_explicit=exclude_explicit,
        count=want,
    )


async def discover(
    client: Any,
    *,
    genres: list[str] | None = None,
    query: str | None = None,
    count: int = 25,
    min_year: int | None = None,
    max_year: int | None = None,
    known_ids: set[str] | None = None,
    known_artists: set[str] | None = None,
    exclude_known: bool = True,
    exclude_known_artists: bool = False,
    exclude_explicit: bool = False,
    per_query: int = 100,
) -> list[DiscoveryCandidate]:
    """Search the catalog for `genres`/`query`, then return up to `count` tracks
    the user has not heard (filtered against known_ids/known_artists)."""
    queries = build_search_queries(genres, query, min_year, max_year)
    if not queries:
        raise ValueError("discover needs at least one of `genres` or `query`.")
    per_query = max(1, min(int(per_query), 200))

    per_query_results: list[list[DiscoveryCandidate]] = []
    for q in queries:
        raw = await _search_paged(client, q, per_query)
        per_query_results.append([c for c in (track_from_search(it) for it in raw) if c])

    merged = interleave(per_query_results)
    return filter_and_rank(
        merged,
        known_ids or set(),
        known_artists or set(),
        exclude_known=exclude_known,
        exclude_known_artists=exclude_known_artists,
        exclude_explicit=exclude_explicit,
        count=count,
    )
