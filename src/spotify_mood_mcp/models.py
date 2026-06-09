"""Normalized track model shared across history, moods, and playlists.

Every field here is derivable from *non-deprecated* endpoints. There is
deliberately no valence/energy/danceability/tempo — those came from the
audio-features endpoint, which is dead for new apps.
"""

from __future__ import annotations

from dataclasses import dataclass, field


def parse_year(release_date: str | None) -> int | None:
    if not release_date:
        return None
    head = release_date.split("-", 1)[0]
    return int(head) if head.isdigit() else None


@dataclass
class Track:
    id: str
    name: str
    artist_ids: list[str]
    artist_names: list[str]
    popularity: int = 0            # 0-100 (Spotify track popularity)
    duration_ms: int = 0
    explicit: bool = False
    release_year: int | None = None
    genres: list[str] = field(default_factory=list)   # flattened artist genres
    # Signals derived from the user's own history:
    play_count: int = 0            # times seen across history sources
    sources: list[str] = field(default_factory=list)  # e.g. ["top_short", "saved"]
    last_played_ms: int | None = None  # epoch ms of most recent play, if known

    @property
    def uri(self) -> str:
        return f"spotify:track:{self.id}"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "artist_ids": self.artist_ids,
            "artist_names": self.artist_names,
            "popularity": self.popularity,
            "duration_ms": self.duration_ms,
            "explicit": self.explicit,
            "release_year": self.release_year,
            "genres": self.genres,
            "play_count": self.play_count,
            "sources": self.sources,
            "last_played_ms": self.last_played_ms,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Track":
        return cls(
            id=d["id"],
            name=d["name"],
            artist_ids=list(d.get("artist_ids", [])),
            artist_names=list(d.get("artist_names", [])),
            popularity=int(d.get("popularity", 0)),
            duration_ms=int(d.get("duration_ms", 0)),
            explicit=bool(d.get("explicit", False)),
            release_year=d.get("release_year"),
            genres=list(d.get("genres", [])),
            play_count=int(d.get("play_count", 0)),
            sources=list(d.get("sources", [])),
            last_played_ms=d.get("last_played_ms"),
        )
