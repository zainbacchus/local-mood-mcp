"""Deterministic mood taxonomy and scoring.

Because audio-features is gone, "mood" is inferred from signals that still
exist: artist **genres**, track **popularity**, release **era**, and the
**explicit** flag. The mapping is a fixed, transparent table — no model, no
randomness. The same track always scores the same against the same mood, so
playlists are reproducible.

`score_track(track, mood)` returns a float in [0, 1] plus a breakdown that
`explain` surfaces to the user, so every selection is auditable.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import Track


@dataclass(frozen=True)
class MoodSpec:
    key: str
    label: str
    description: str
    genre_keywords: tuple[str, ...]
    anti_keywords: tuple[str, ...] = ()
    pop_pref: str = "any"        # high | mid | low | any
    era: tuple[int, int] | None = None   # inclusive preferred release-year band
    explicit_pref: str = "any"   # avoid | any | prefer
    # Component weights (normalized at scoring time).
    w_genre: float = 0.60
    w_pop: float = 0.20
    w_era: float = 0.10
    w_explicit: float = 0.10


# The taxonomy. Keywords are matched as case-insensitive substrings against
# each artist genre string (Spotify genres are granular, e.g. "deep house",
# "indie folk", "lo-fi beats"). Edit freely — it is the single source of truth.
MOODS: dict[str, MoodSpec] = {
    "focus": MoodSpec(
        key="focus",
        label="Focus / Deep Work",
        description="Low-distraction, mostly instrumental, steady-state listening.",
        genre_keywords=(
            "ambient", "lo-fi", "lofi", "instrumental", "classical", "piano",
            "post-rock", "minimal", "downtempo", "study", "drone", "neoclassical",
        ),
        anti_keywords=("metal", "punk", "trap", "drill", "hardcore", "screamo"),
        pop_pref="any",
        explicit_pref="avoid",
    ),
    "chill": MoodSpec(
        key="chill",
        label="Chill / Relax",
        description="Easygoing, warm, low-intensity background listening.",
        genre_keywords=(
            "chill", "lo-fi", "lofi", "downtempo", "indie folk", "acoustic",
            "bedroom pop", "dream pop", "soul", "bossa nova", "jazz", "soft",
            "trip hop", "chillhop",
        ),
        anti_keywords=("hardcore", "thrash", "speed metal", "gabber"),
        pop_pref="any",
    ),
    "energetic": MoodSpec(
        key="energetic",
        label="Energetic / Workout",
        description="High-drive, up-tempo material for movement and workouts.",
        genre_keywords=(
            "edm", "house", "techno", "dance", "electro", "drum and bass",
            "dnb", "hip hop", "rap", "trap", "pop", "big room", "hardstyle",
            "phonk", "bass",
        ),
        anti_keywords=("ambient", "slowcore", "drone", "sleep"),
        pop_pref="high",
        explicit_pref="any",
    ),
    "party": MoodSpec(
        key="party",
        label="Party",
        description="Crowd-pleasing, high-popularity, danceable hits.",
        genre_keywords=(
            "pop", "dance", "house", "edm", "reggaeton", "latin", "afrobeats",
            "hip hop", "rap", "funk", "disco", "electro",
        ),
        pop_pref="high",
    ),
    "melancholy": MoodSpec(
        key="melancholy",
        label="Melancholy / Sad",
        description="Introspective, wistful, emotionally heavy tracks.",
        genre_keywords=(
            "sad", "emo", "slowcore", "shoegaze", "indie folk", "singer-songwriter",
            "ambient", "post-rock", "dream pop", "blues", "acoustic", "piano",
        ),
        anti_keywords=("party", "dance pop", "happy hardcore"),
        pop_pref="any",
    ),
    "uplifting": MoodSpec(
        key="uplifting",
        label="Uplifting / Happy",
        description="Bright, major-key, feel-good listening.",
        genre_keywords=(
            "pop", "indie pop", "funk", "soul", "disco", "afrobeats", "tropical",
            "synthpop", "dance pop", "motown", "gospel",
        ),
        anti_keywords=("doom", "black metal", "slowcore", "funeral"),
        pop_pref="high",
    ),
    "aggressive": MoodSpec(
        key="aggressive",
        label="Aggressive / Hype",
        description="Hard-hitting, intense, high-aggression material.",
        genre_keywords=(
            "metal", "hardcore", "punk", "drill", "trap metal", "rap metal",
            "thrash", "metalcore", "phonk", "industrial", "rage",
        ),
        pop_pref="any",
        explicit_pref="prefer",
    ),
    "romantic": MoodSpec(
        key="romantic",
        label="Romantic",
        description="Warm, intimate, slow-burn love songs.",
        genre_keywords=(
            "r&b", "rnb", "soul", "neo soul", "slow jam", "quiet storm",
            "bolero", "bachata", "love", "smooth", "jazz",
        ),
        pop_pref="any",
        explicit_pref="avoid",
    ),
    "nostalgic": MoodSpec(
        key="nostalgic",
        label="Nostalgic / Throwback",
        description="Older-era favourites that read as throwbacks.",
        genre_keywords=(
            "classic rock", "80s", "90s", "oldies", "new wave", "synthwave",
            "grunge", "britpop", "motown", "disco", "soul",
        ),
        era=(1960, 2009),
        pop_pref="any",
    ),
    "sleepy": MoodSpec(
        key="sleepy",
        label="Sleep / Calm",
        description="Very low-intensity, soothing, wind-down listening.",
        genre_keywords=(
            "ambient", "sleep", "piano", "neoclassical", "drone", "meditation",
            "new age", "soft", "lullaby", "downtempo",
        ),
        anti_keywords=("rap", "metal", "edm", "punk", "trap", "house"),
        pop_pref="any",
        explicit_pref="avoid",
    ),
}


def list_moods() -> list[dict]:
    return [
        {
            "key": m.key,
            "label": m.label,
            "description": m.description,
            "genre_keywords": list(m.genre_keywords),
            "popularity_preference": m.pop_pref,
            "era": list(m.era) if m.era else None,
            "explicit_preference": m.explicit_pref,
        }
        for m in MOODS.values()
    ]


# --- scoring components (all pure, deterministic) ---------------------------
def _genre_score(track: Track, mood: MoodSpec) -> float:
    if not track.genres:
        # Unknown genres: neutral-low so other signals decide rather than zeroing.
        return 0.15
    genres = [g.lower() for g in track.genres]
    hits = sum(1 for g in genres for kw in mood.genre_keywords if kw in g)
    anti = sum(1 for g in genres for kw in mood.anti_keywords if kw in g)
    base = min(1.0, hits / 2.0)               # 2+ keyword hits = full marks
    base -= min(0.5, 0.25 * anti)             # each anti-genre subtracts, capped
    return max(0.0, min(1.0, base))


def _pop_score(track: Track, mood: MoodSpec) -> float:
    p = max(0, min(100, track.popularity)) / 100.0
    if mood.pop_pref == "high":
        return p
    if mood.pop_pref == "low":
        return 1.0 - p
    if mood.pop_pref == "mid":
        return 1.0 - abs(p - 0.5) * 2.0
    return 0.5


def _era_score(track: Track, mood: MoodSpec) -> float:
    if mood.era is None:
        return 0.5
    if track.release_year is None:
        return 0.4  # slightly below neutral when era matters but is unknown
    lo, hi = mood.era
    if lo <= track.release_year <= hi:
        return 1.0
    dist = (lo - track.release_year) if track.release_year < lo else (track.release_year - hi)
    return max(0.0, 1.0 - dist / 20.0)  # linear falloff over ~2 decades


def _explicit_score(track: Track, mood: MoodSpec) -> float:
    if mood.explicit_pref == "avoid":
        return 0.0 if track.explicit else 1.0
    if mood.explicit_pref == "prefer":
        return 1.0 if track.explicit else 0.3
    return 0.5


def score_track(track: Track, mood: MoodSpec) -> tuple[float, dict[str, float]]:
    """Return (overall_score in [0,1], component breakdown)."""
    comps = {
        "genre": _genre_score(track, mood),
        "popularity": _pop_score(track, mood),
        "era": _era_score(track, mood),
        "explicit": _explicit_score(track, mood),
    }
    weights = {
        "genre": mood.w_genre,
        "popularity": mood.w_pop,
        "era": mood.w_era,
        "explicit": mood.w_explicit,
    }
    total_w = sum(weights.values()) or 1.0
    overall = sum(comps[k] * weights[k] for k in comps) / total_w
    return overall, comps


def get_mood(key: str) -> MoodSpec:
    norm = key.strip().lower()
    if norm not in MOODS:
        raise KeyError(
            f"Unknown mood {key!r}. Available: {', '.join(MOODS)}."
        )
    return MOODS[norm]
