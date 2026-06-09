"""Secure token storage.

Refresh tokens are long-lived credentials and are treated as secrets:

  * Primary backend: the OS keyring (macOS Keychain / libsecret / Windows
    Credential Locker) via `keyring`. Nothing touches the filesystem.
  * Fallback backend: an encrypted file. The token blob is sealed with
    Fernet (AES-128-CBC + HMAC). The key itself lives in the OS keyring when
    possible; only if the keyring is entirely unavailable does it fall back to
    a 0600 key file, and we warn loudly in that case.

Tokens are never written in plaintext and never logged.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import keyring
from keyring.errors import KeyringError
from cryptography.fernet import Fernet, InvalidToken

_SERVICE = "local-mood-mcp"
_ACCOUNT = "token-bundle"
_KEY_ACCOUNT = "file-encryption-key"


@dataclass
class TokenBundle:
    access_token: str
    refresh_token: str
    expires_at: float  # epoch seconds
    scope: str
    token_type: str = "Bearer"

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, raw: str) -> "TokenBundle":
        data = json.loads(raw)
        return cls(
            access_token=data["access_token"],
            refresh_token=data["refresh_token"],
            expires_at=float(data["expires_at"]),
            scope=data.get("scope", ""),
            token_type=data.get("token_type", "Bearer"),
        )


def _keyring_available() -> bool:
    try:
        keyring.get_password(_SERVICE, "__probe__")
        return True
    except KeyringError:
        return False
    except Exception:
        return False


class TokenStore:
    """Persists a single user's TokenBundle securely."""

    def __init__(self, state_dir: Path):
        self._state_dir = state_dir
        self._enc_path = state_dir / "tokens.enc"
        self._keyfile_path = state_dir / "fkey"  # only used as last resort

    # -- public API ---------------------------------------------------------
    def load(self) -> TokenBundle | None:
        if _keyring_available():
            raw = keyring.get_password(_SERVICE, _ACCOUNT)
            if raw:
                return TokenBundle.from_json(raw)
            # Fall through: maybe a prior run used the file backend.
        if self._enc_path.exists():
            try:
                blob = self._enc_path.read_bytes()
                raw = self._fernet().decrypt(blob).decode("utf-8")
                return TokenBundle.from_json(raw)
            except (InvalidToken, ValueError):
                return None
        return None

    def save(self, bundle: TokenBundle) -> None:
        payload = bundle.to_json()
        if _keyring_available():
            keyring.set_password(_SERVICE, _ACCOUNT, payload)
            # Remove any stale file-backend artifacts.
            self._enc_path.unlink(missing_ok=True)
            return
        # File fallback (encrypted).
        blob = self._fernet().encrypt(payload.encode("utf-8"))
        self._enc_path.write_bytes(blob)
        self._chmod_600(self._enc_path)

    def clear(self) -> None:
        try:
            keyring.delete_password(_SERVICE, _ACCOUNT)
        except Exception:
            pass
        self._enc_path.unlink(missing_ok=True)

    # -- encryption key management -----------------------------------------
    def _fernet(self) -> Fernet:
        return Fernet(self._encryption_key())

    def _encryption_key(self) -> bytes:
        # Prefer storing the file-encryption key in the keyring even when the
        # keyring can't hold the token blob directly (rare). Most setups never
        # reach the file backend at all.
        try:
            existing = keyring.get_password(_SERVICE, _KEY_ACCOUNT)
            if existing:
                return existing.encode("utf-8")
            key = Fernet.generate_key()
            keyring.set_password(_SERVICE, _KEY_ACCOUNT, key.decode("utf-8"))
            return key
        except Exception:
            pass
        # Absolute last resort: a 0600 key file. Warn so the user understands
        # the reduced security posture.
        if self._keyfile_path.exists():
            return self._keyfile_path.read_bytes()
        print(
            "[local-mood-mcp] WARNING: OS keyring unavailable; storing the "
            "token-encryption key in a 0600 file. This is less secure than a "
            "system keyring.",
            file=sys.stderr,
        )
        key = Fernet.generate_key()
        self._keyfile_path.write_bytes(key)
        self._chmod_600(self._keyfile_path)
        return key

    @staticmethod
    def _chmod_600(path: Path) -> None:
        try:
            os.chmod(path, 0o600)
        except OSError:  # pragma: no cover - non-posix
            pass
