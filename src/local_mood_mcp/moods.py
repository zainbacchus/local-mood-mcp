"""Deterministic, behavioral mood taxonomy.

Spotify gives new apps no genres, no popularity, and no audio features in 2026
(all verified live). So a "mood" here is a reproducible function of how you
actually listen plus the little metadata that survives (era, explicit, length):

  INSTANT moods (work from API affinity the moment you sync):
    current_rotation, steady_favorites, all_time_favorites,
    throwback, fresh, long_form, quick_hits, clean, explicit

  LIFETIME moods (need the Extended Streaming History export):
    morning, late_night, weekend, on_repeat, comfort, focus_flow, deep_cuts

Every scorer is a pure function of (Track, Context) returning a value in [0, 1]
plus a component breakdown, so selections are auditable and identical on every
run with the same library.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from .models import TIER_LONG, TIER_MEDIUM, TIER_SHORT, Track

_DAY_MS = 86_400_000


def _clamp01(x: float) -> float:
    return 0.0 if x < 0 else 1.0 if x > 1 else x


@dataclass
class Context:
    """Library-relative normalizers, computed once per scoring run."""

    now_ms: int
    now_year: int
    max_lifetime_plays: int
    max_affinity: int
    has_lifetime: bool

    def affinity_norm(self, t: Track) -> float:
        if self.max_affinity <= 0:
            return 0.0
        return _clamp01(math.log1p(t.affinity_plays) / math.log1p(self.max_affinity))

    def plays_norm(self, t: Track) -> float:
        if self.max_lifetime_plays <= 0:
            return 0.0
        return _clamp01(math.log1p(t.lifetime_plays) / math.log1p(self.max_lifetime_plays))

    def recency(self, t: Track, *, window_days: int = 30) -> float:
        if not t.last_played_ms:
            return 0.0
        days = (self.now_ms - t.last_played_ms) / _DAY_MS
        return _clamp01(1.0 - days / window_days)

    def loyalty(self, t: Track, *, years: float = 3.0) -> float:
        if not t.first_play_ms:
            return 0.0
        age_days = (self.now_ms - t.first_play_ms) / _DAY_MS
        return _clamp01(age_days / (365.0 * years))


def build_context(tracks: list[Track], *, now_ms: int | None = None) -> Context:
    """Build the per-run normalization context.

    `now_ms` defaults to the wall clock; pass a fixed value to make recency- and
    loyalty-based moods (current_rotation, on_repeat, ...) reproducible at a
    chosen instant — selection is otherwise time-dependent for those moods.
    """
    now = datetime.fromtimestamp(now_ms / 1000) if now_ms is not None else datetime.now()
    return Context(
        now_ms=int(now.timestamp() * 1000),
        now_year=now.year,
        max_lifetime_plays=max((t.lifetime_plays for t in tracks), default=0),
        max_affinity=max((t.affinity_plays for t in tracks), default=0),
        has_lifetime=any(t.has_lifetime for t in tracks),
    )


# --- scoring primitives -----------------------------------------------------
def _blend(comps: dict[str, float], weights: dict[str, float]) -> float:
    tot = sum(weights.values()) or 1.0
    return _clamp01(sum(comps[k] * weights[k] for k in comps) / tot)


# --- instant-mode scorers ---------------------------------------------------
def _current_rotation(t: Track, ctx: Context) -> tuple[float, dict]:
    c = {
        "short_term_tier": 1.0 if TIER_SHORT in t.top_tiers else (0.5 if TIER_MEDIUM in t.top_tiers else 0.0),
        "recency": ctx.recency(t, window_days=30),
        "recent_plays": _clamp01(t.api_recent_plays / 5.0),
    }
    return _blend(c, {"short_term_tier": 0.5, "recency": 0.3, "recent_plays": 0.2}), c


def _steady_favorites(t: Track, ctx: Context) -> tuple[float, dict]:
    c = {
        "medium_term_tier": 1.0 if TIER_MEDIUM in t.top_tiers else 0.0,
        "affinity": ctx.affinity_norm(t),
    }
    return _blend(c, {"medium_term_tier": 0.7, "affinity": 0.3}), c


def _all_time_favorites(t: Track, ctx: Context) -> tuple[float, dict]:
    c = {
        "long_term_tier": 1.0 if TIER_LONG in t.top_tiers else (0.4 if TIER_MEDIUM in t.top_tiers else 0.0),
        "affinity": ctx.affinity_norm(t),
    }
    return _blend(c, {"long_term_tier": 0.6, "affinity": 0.4}), c


def _throwback(t: Track, ctx: Context) -> tuple[float, dict]:
    if t.release_year is None:
        return 0.0, {"era": 0.0, "affinity": ctx.affinity_norm(t)}
    age = ctx.now_year - t.release_year
    era = _clamp01((age - 8) / 20.0)  # ramps 8->28 years old
    c = {"era": era, "affinity": ctx.affinity_norm(t)}
    return _blend(c, {"era": 0.7, "affinity": 0.3}), c


def _fresh(t: Track, ctx: Context) -> tuple[float, dict]:
    if t.release_year is None:
        return 0.0, {"era": 0.0, "affinity": ctx.affinity_norm(t)}
    age = ctx.now_year - t.release_year
    era = _clamp01((3 - age) / 3.0)  # age 0 -> 1.0, age 3+ -> 0
    c = {"era": era, "affinity": ctx.affinity_norm(t)}
    return _blend(c, {"era": 0.8, "affinity": 0.2}), c


def _long_form(t: Track, ctx: Context) -> tuple[float, dict]:
    d = _clamp01((t.duration_ms - 240_000) / 180_000)  # 4min->0, 7min->1
    c = {"duration": d, "affinity": ctx.affinity_norm(t)}
    return _blend(c, {"duration": 0.8, "affinity": 0.2}), c


def _quick_hits(t: Track, ctx: Context) -> tuple[float, dict]:
    d = _clamp01((150_000 - t.duration_ms) / 90_000)  # 2.5min->0, 1min->1
    c = {"shortness": d, "affinity": ctx.affinity_norm(t)}
    return _blend(c, {"shortness": 0.8, "affinity": 0.2}), c


def _clean(t: Track, ctx: Context) -> tuple[float, dict]:
    if t.explicit:
        return 0.0, {"clean": 0.0, "affinity": ctx.affinity_norm(t)}
    c = {"clean": 1.0, "affinity": ctx.affinity_norm(t)}
    return _blend(c, {"clean": 0.7, "affinity": 0.3}), c


def _explicit(t: Track, ctx: Context) -> tuple[float, dict]:
    if not t.explicit:
        return 0.0, {"explicit": 0.0, "affinity": ctx.affinity_norm(t)}
    c = {"explicit": 1.0, "affinity": ctx.affinity_norm(t)}
    return _blend(c, {"explicit": 0.7, "affinity": 0.3}), c


# --- lifetime (export-required) scorers ------------------------------------
def _morning(t: Track, ctx: Context) -> tuple[float, dict]:
    s = t.part_of_day_shares()
    conf = _clamp01(t.lifetime_plays / 5.0)
    c = {"morning_share": s["morning"], "confidence": conf}
    return _blend(c, {"morning_share": 0.8, "confidence": 0.2}), c


def _late_night(t: Track, ctx: Context) -> tuple[float, dict]:
    s = t.part_of_day_shares()
    conf = _clamp01(t.lifetime_plays / 5.0)
    c = {"night_share": s["night"], "confidence": conf}
    return _blend(c, {"night_share": 0.8, "confidence": 0.2}), c


def _weekend(t: Track, ctx: Context) -> tuple[float, dict]:
    conf = _clamp01(t.lifetime_plays / 5.0)
    c = {"weekend_share": t.weekend_share(), "confidence": conf}
    return _blend(c, {"weekend_share": 0.8, "confidence": 0.2}), c


def _on_repeat(t: Track, ctx: Context) -> tuple[float, dict]:
    c = {"plays": ctx.plays_norm(t), "recency": ctx.recency(t, window_days=60)}
    return _blend(c, {"plays": 0.7, "recency": 0.3}), c


def _comfort(t: Track, ctx: Context) -> tuple[float, dict]:
    c = {
        "completion": t.completion_ratio,
        "non_skip": 1.0 - t.skip_ratio,
        "loyalty": ctx.loyalty(t),
        "plays": ctx.plays_norm(t),
    }
    return _blend(c, {"completion": 0.35, "non_skip": 0.25, "loyalty": 0.2, "plays": 0.2}), c


def _focus_flow(t: Track, ctx: Context) -> tuple[float, dict]:
    c = {
        "completion": t.completion_ratio,
        "deliberate": t.deliberate_ratio,
        "non_skip": 1.0 - t.skip_ratio,
    }
    return _blend(c, {"completion": 0.4, "deliberate": 0.3, "non_skip": 0.3}), c


def _deep_cuts(t: Track, ctx: Context) -> tuple[float, dict]:
    if t.lifetime_plays < 2:
        return 0.0, {"under_played": 0.0, "quality": 0.0}
    c = {
        "under_played": 1.0 - ctx.plays_norm(t),
        "quality": t.completion_ratio * (1.0 - t.skip_ratio),
    }
    return _blend(c, {"under_played": 0.5, "quality": 0.5}), c


@dataclass(frozen=True)
class MoodSpec:
    key: str
    label: str
    description: str
    requires_lifetime: bool
    scorer: Callable[[Track, Context], tuple[float, dict]]


MOODS: dict[str, MoodSpec] = {
    "current_rotation": MoodSpec("current_rotation", "Current Rotation",
        "What you're into right now — short-term top tracks, recently played.", False, _current_rotation),
    "steady_favorites": MoodSpec("steady_favorites", "Steady Favorites",
        "Your stable mid-term favorites (≈6 months).", False, _steady_favorites),
    "all_time_favorites": MoodSpec("all_time_favorites", "All-Time Favorites",
        "Long-term, enduring top tracks.", False, _all_time_favorites),
    "throwback": MoodSpec("throwback", "Throwback",
        "Older-era tracks (≈8+ years) you still hold onto.", False, _throwback),
    "fresh": MoodSpec("fresh", "Fresh Releases",
        "Recently released music (last ~2 years) in your library.", False, _fresh),
    "long_form": MoodSpec("long_form", "Long-Form / Immersive",
        "Longer tracks (5+ min) — slower, immersive listening.", False, _long_form),
    "quick_hits": MoodSpec("quick_hits", "Quick Hits",
        "Short tracks (≤~2.5 min) — punchy and brief.", False, _quick_hits),
    "clean": MoodSpec("clean", "Clean",
        "Non-explicit tracks you like.", False, _clean),
    "explicit": MoodSpec("explicit", "Explicit",
        "Explicit tracks you like.", False, _explicit),
    # lifetime
    "morning": MoodSpec("morning", "Morning",
        "Tracks you disproportionately play in the morning (05:00–11:59).", True, _morning),
    "late_night": MoodSpec("late_night", "Late Night",
        "Tracks you play late at night (22:00–04:59).", True, _late_night),
    "weekend": MoodSpec("weekend", "Weekend",
        "Tracks skewed toward Saturday/Sunday listening.", True, _weekend),
    "on_repeat": MoodSpec("on_repeat", "On Repeat",
        "Your most-played tracks of all time, weighted to recent.", True, _on_repeat),
    "comfort": MoodSpec("comfort", "Comfort",
        "Long-loved tracks you finish and rarely skip.", True, _comfort),
    "focus_flow": MoodSpec("focus_flow", "Focus Flow",
        "Deliberately chosen tracks you play through without skipping.", True, _focus_flow),
    "deep_cuts": MoodSpec("deep_cuts", "Deep Cuts",
        "Under-played gems you finish when they come on.", True, _deep_cuts),
}


def list_moods(*, has_lifetime: bool | None = None) -> list[dict]:
    out = []
    for m in MOODS.values():
        out.append({
            "key": m.key,
            "label": m.label,
            "description": m.description,
            "requires_extended_history": m.requires_lifetime,
            "available_now": (not m.requires_lifetime) or bool(has_lifetime),
        })
    return out


def get_mood(key: str) -> MoodSpec:
    norm = key.strip().lower()
    if norm not in MOODS:
        raise KeyError(f"Unknown mood {key!r}. Available: {', '.join(MOODS)}.")
    return MOODS[norm]


def score_track(track: Track, mood: MoodSpec | str, ctx: Context) -> tuple[float, dict]:
    spec = mood if isinstance(mood, MoodSpec) else get_mood(mood)
    return spec.scorer(track, ctx)
