"""Normalized track model — behavioral edition.

Spotify stripped genres and ALL popularity from the API for new apps in 2026
(verified live). What survives on track objects is era (album.release_date),
explicit, duration, names/ids/uris. "Mood" is therefore built from how the user
actually listens, not from sonic metadata:

  * Affinity tiers      — appearance in short/medium/long-term top tracks.
  * Recency             — recently-played timestamps.
  * Lifetime behavior   — from the Extended Streaming History export: play
                          counts, completions, skips, deliberate starts, an
                          hour-of-day histogram, weekend split, first/last play.

Every field here is derivable from Spotify-only data with no external services.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Affinity tier labels (also the source tags used during aggregation).
TIER_SHORT = "short_term"
TIER_MEDIUM = "medium_term"
TIER_LONG = "long_term"

# Source tags the live API window can contribute. Anything outside this set
# ("extended_history", "journal") exists only because of long-term memory.
API_SOURCES = frozenset(
    {"top_short_term", "top_medium_term", "top_long_term", "recently_played", "saved"}
)


def parse_year(release_date: str | None) -> int | None:
    if not release_date:
        return None
    head = release_date.split("-", 1)[0]
    return int(head) if head.isdigit() else None


def _zeros() -> list[int]:
    return [0] * 24


@dataclass
class Track:
    id: str
    name: str
    artist_ids: list[str] = field(default_factory=list)
    artist_names: list[str] = field(default_factory=list)

    # --- still-available Spotify metadata ---
    duration_ms: int = 0
    explicit: bool = False
    release_year: int | None = None

    # --- API affinity signals (available instantly) ---
    top_tiers: list[str] = field(default_factory=list)   # subset of TIER_*
    in_saved: bool = False
    api_recent_plays: int = 0                            # from recently-played
    last_played_ms: int | None = None                    # most recent ts seen

    # --- lifetime behavioral signals (from Extended Streaming History) ---
    lifetime_plays: int = 0
    lifetime_ms_played: int = 0
    completions: int = 0          # reason_end == "trackdone"
    skips: int = 0                # skipped flag / early fwdbtn
    deliberate_starts: int = 0    # reason_start in {clickrow, playbtn}
    hour_hist: list[int] = field(default_factory=_zeros)  # 24 buckets, local-naive
    weekday_plays: int = 0
    weekend_plays: int = 0
    first_play_ms: int | None = None

    sources: list[str] = field(default_factory=list)

    # -- convenience --------------------------------------------------------
    @property
    def uri(self) -> str:
        return f"spotify:track:{self.id}"

    @property
    def has_lifetime(self) -> bool:
        return self.lifetime_plays > 0

    @property
    def affinity_plays(self) -> int:
        """A robust 'how much do you listen to this' integer that works whether
        or not the export is present. Lifetime plays dominate when available;
        otherwise affinity tiers + recent plays + saves stand in."""
        if self.lifetime_plays > 0:
            return self.lifetime_plays
        return len(self.top_tiers) * 3 + self.api_recent_plays + (2 if self.in_saved else 0)

    @property
    def completion_ratio(self) -> float:
        return self.completions / self.lifetime_plays if self.lifetime_plays else 0.0

    @property
    def skip_ratio(self) -> float:
        return self.skips / self.lifetime_plays if self.lifetime_plays else 0.0

    @property
    def deliberate_ratio(self) -> float:
        return self.deliberate_starts / self.lifetime_plays if self.lifetime_plays else 0.0

    def part_of_day_shares(self) -> dict[str, float]:
        """Fraction of plays in each part of day. Empty -> all zeros."""
        total = sum(self.hour_hist) or 0
        if total == 0:
            return {"morning": 0.0, "afternoon": 0.0, "evening": 0.0, "night": 0.0}
        morning = sum(self.hour_hist[5:12])      # 05:00-11:59
        afternoon = sum(self.hour_hist[12:17])   # 12:00-16:59
        evening = sum(self.hour_hist[17:22])     # 17:00-21:59
        night = sum(self.hour_hist[22:24]) + sum(self.hour_hist[0:5])  # 22:00-04:59
        return {
            "morning": morning / total,
            "afternoon": afternoon / total,
            "evening": evening / total,
            "night": night / total,
        }

    def weekend_share(self) -> float:
        total = self.weekday_plays + self.weekend_plays
        return self.weekend_plays / total if total else 0.0

    # -- (de)serialization --------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "artist_ids": self.artist_ids,
            "artist_names": self.artist_names,
            "duration_ms": self.duration_ms,
            "explicit": self.explicit,
            "release_year": self.release_year,
            "top_tiers": self.top_tiers,
            "in_saved": self.in_saved,
            "api_recent_plays": self.api_recent_plays,
            "last_played_ms": self.last_played_ms,
            "lifetime_plays": self.lifetime_plays,
            "lifetime_ms_played": self.lifetime_ms_played,
            "completions": self.completions,
            "skips": self.skips,
            "deliberate_starts": self.deliberate_starts,
            "hour_hist": self.hour_hist,
            "weekday_plays": self.weekday_plays,
            "weekend_plays": self.weekend_plays,
            "first_play_ms": self.first_play_ms,
            "sources": self.sources,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Track":
        hh = list(d.get("hour_hist") or _zeros())
        if len(hh) != 24:
            hh = _zeros()
        return cls(
            id=d["id"],
            name=d.get("name", ""),
            artist_ids=list(d.get("artist_ids", [])),
            artist_names=list(d.get("artist_names", [])),
            duration_ms=int(d.get("duration_ms", 0)),
            explicit=bool(d.get("explicit", False)),
            release_year=d.get("release_year"),
            top_tiers=list(d.get("top_tiers", [])),
            in_saved=bool(d.get("in_saved", False)),
            api_recent_plays=int(d.get("api_recent_plays", 0)),
            last_played_ms=d.get("last_played_ms"),
            lifetime_plays=int(d.get("lifetime_plays", 0)),
            lifetime_ms_played=int(d.get("lifetime_ms_played", 0)),
            completions=int(d.get("completions", 0)),
            skips=int(d.get("skips", 0)),
            deliberate_starts=int(d.get("deliberate_starts", 0)),
            hour_hist=hh,
            weekday_plays=int(d.get("weekday_plays", 0)),
            weekend_plays=int(d.get("weekend_plays", 0)),
            first_play_ms=d.get("first_play_ms"),
            sources=list(d.get("sources", [])),
        )
