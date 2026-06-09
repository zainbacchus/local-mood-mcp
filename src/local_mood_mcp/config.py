"""Configuration and constants.

Secrets are read from the environment (optionally a local .env). Nothing
sensitive is hard-coded. The client *secret* is optional: with PKCE a public
client needs only the client id. If a secret is present we use it (confidential
client), which is the stronger option for a server you control.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# Load a local .env if present (never committed). Safe no-op if missing.
try:  # pragma: no cover - trivial
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover
    pass


# --- Spotify endpoints (all non-deprecated as of 2026) ----------------------
ACCOUNTS_BASE = "https://accounts.spotify.com"
AUTHORIZE_URL = f"{ACCOUNTS_BASE}/authorize"
TOKEN_URL = f"{ACCOUNTS_BASE}/api/token"
API_BASE = "https://api.spotify.com/v1"

# Least-privilege scopes. Each is requested explicitly and justified, and each
# maps to an endpoint this server actually calls — nothing is requested "just in
# case":
#   user-top-read              -> /me/top/tracks
#   user-read-recently-played  -> /me/player/recently-played
#   user-library-read          -> /me/tracks (saved library)
#   playlist-modify-private    -> create private playlists by exact IDs
#   playlist-modify-public     -> create public playlists by exact IDs
#   user-read-playback-state   -> list devices / current playback
#   user-modify-playback-state -> play / pause / skip (Premium only)
SCOPES: tuple[str, ...] = (
    "user-top-read",
    "user-read-recently-played",
    "user-library-read",
    "playlist-modify-private",
    "playlist-modify-public",
    "user-read-playback-state",
    "user-modify-playback-state",
)


def _state_dir() -> Path:
    """Per-user state dir, override with LOCAL_MOOD_HOME. Created mode 0700."""
    raw = os.environ.get("LOCAL_MOOD_HOME")
    base = Path(raw).expanduser() if raw else Path.home() / ".local-mood-mcp"
    base.mkdir(parents=True, exist_ok=True)
    try:
        base.chmod(0o700)
    except OSError:  # pragma: no cover - non-posix
        pass
    return base


def _repo_checkout_root() -> Path | None:
    """Return the repo root IFF we're running from a source checkout.

    config.py lives at <root>/src/local_mood_mcp/config.py, so parents[2] is the
    repo root when developing. When pip-installed it points inside site-packages
    (no pyproject.toml there), in which case we return None so the drop folder
    falls back to the per-user state dir instead of polluting site-packages.
    """
    root = Path(__file__).resolve().parents[2]
    return root if (root / "pyproject.toml").exists() else None


def _history_dir() -> Path:
    """Folder where the user drops their Extended Streaming History export.

    Resolution order:
      1. LOCAL_MOOD_HISTORY_DIR if set.
      2. `<repo>/extended_history` when running from a source checkout.
      3. `<state_dir>/extended_history` otherwise (e.g. pip-installed).
    Created mode 0700 because the export contains personal data (IP addresses,
    timestamps). The repo folder is git-ignored; the state-dir folder lives
    entirely outside any repo.
    """
    raw = os.environ.get("LOCAL_MOOD_HISTORY_DIR")
    if raw:
        path = Path(raw).expanduser()
    elif (checkout := _repo_checkout_root()) is not None:
        path = checkout / "extended_history"
    else:
        path = _state_dir() / "extended_history"
    try:
        path.mkdir(parents=True, exist_ok=True)
        path.chmod(0o700)
    except OSError:  # pragma: no cover - non-posix
        pass
    return path


@dataclass(frozen=True)
class Settings:
    client_id: str
    client_secret: str | None
    redirect_uri: str
    redirect_host: str
    redirect_port: int
    state_dir: Path = field(default_factory=_state_dir)
    history_dir: Path = field(default_factory=_history_dir)

    @property
    def is_confidential(self) -> bool:
        return bool(self.client_secret)

    @property
    def library_path(self) -> Path:
        return self.state_dir / "library.json"

    @property
    def journal_path(self) -> Path:
        """Append-only log of observed plays — how memory accrues between
        (or without) Extended Streaming History exports."""
        return self.state_dir / "play_journal.jsonl"

    def has_dropped_history(self) -> bool:
        """True if any JSON files are present in the drop folder (recursively)."""
        return any(self.history_dir.rglob("*.json"))


def _parse_redirect(uri: str) -> tuple[str, int]:
    """Extract host/port from a loopback redirect URI.

    Spotify (post 2025-11-27) forbids `localhost`; only loopback IP literals
    (http://127.0.0.1:PORT or http://[::1]:PORT) are allowed for HTTP. We
    enforce that here so misconfiguration fails loudly and locally.
    """
    from urllib.parse import urlparse

    parsed = urlparse(uri)
    host = parsed.hostname or ""
    if host in ("localhost",):
        raise ValueError(
            "Spotify no longer accepts 'localhost' redirect URIs (OAuth migration "
            "2025-11-27). Use http://127.0.0.1:PORT/callback instead."
        )
    if parsed.scheme == "http" and host not in ("127.0.0.1", "::1"):
        raise ValueError(
            f"HTTP redirect URIs are only allowed for loopback IPs; got host={host!r}. "
            "Use http://127.0.0.1:PORT/callback or an https:// URI."
        )
    return host, parsed.port or (443 if parsed.scheme == "https" else 80)


def load_settings() -> Settings:
    client_id = os.environ.get("SPOTIFY_CLIENT_ID", "").strip()
    if not client_id:
        raise RuntimeError(
            "SPOTIFY_CLIENT_ID is not set. Create an app at "
            "https://developer.spotify.com/dashboard, then set SPOTIFY_CLIENT_ID "
            "(and optionally SPOTIFY_CLIENT_SECRET) in your environment or .env."
        )
    client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET", "").strip() or None
    redirect_uri = os.environ.get(
        "SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8888/callback"
    ).strip()
    host, port = _parse_redirect(redirect_uri)
    return Settings(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        redirect_host=host,
        redirect_port=port,
    )
