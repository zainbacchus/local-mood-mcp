"""OAuth: Authorization Code flow with PKCE + token refresh.

Why PKCE: Spotify ended the implicit grant flow on 2025-11-27 and recommends
Authorization Code + PKCE for everything. PKCE also protects the authorization
code in transit even on a loopback redirect. We additionally:

  * use a cryptographically random `state` and verify it on the callback (CSRF),
  * bind the callback to 127.0.0.1 only,
  * never print tokens.

This module exposes:
  * `build_authorize_url` / `exchange_code` / `refresh` — the protocol steps.
  * `ensure_access_token` — used by the API client; refreshes if near expiry.
  * `login()` and `cli_main()` — an interactive, one-time browser login that
    runs a single-request loopback server to capture the auth code.
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import secrets
import threading
import time
import urllib.parse
import webbrowser
from dataclasses import dataclass

import httpx

from .config import AUTHORIZE_URL, SCOPES, TOKEN_URL, Settings, load_settings
from .tokenstore import TokenBundle, TokenStore

# Refresh a little before the real expiry to avoid mid-request 401s.
_EXPIRY_SKEW_SECONDS = 60


class AuthError(RuntimeError):
    """Raised when the user is not authenticated or a token op fails."""


# --- PKCE primitives --------------------------------------------------------
def _generate_code_verifier(n_bytes: int = 64) -> str:
    # token_urlsafe gives [43, 128] chars for n_bytes in [32, 96]; spec-compliant.
    return secrets.token_urlsafe(n_bytes)


def _code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


@dataclass
class PendingAuth:
    state: str
    code_verifier: str
    authorize_url: str


def build_authorize_url(settings: Settings) -> PendingAuth:
    verifier = _generate_code_verifier()
    state = secrets.token_urlsafe(32)
    params = {
        "client_id": settings.client_id,
        "response_type": "code",
        "redirect_uri": settings.redirect_uri,
        "code_challenge_method": "S256",
        "code_challenge": _code_challenge(verifier),
        "state": state,
        "scope": " ".join(SCOPES),
    }
    url = f"{AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"
    return PendingAuth(state=state, code_verifier=verifier, authorize_url=url)


def _token_request(settings: Settings, data: dict[str, str]) -> TokenBundle:
    # Confidential clients authenticate with HTTP Basic; public clients send
    # client_id in the body. Either way PKCE params are required for the code
    # exchange.
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    auth = None
    if settings.is_confidential:
        auth = (settings.client_id, settings.client_secret or "")
    else:
        data = {**data, "client_id": settings.client_id}
    with httpx.Client(timeout=15.0) as client:
        resp = client.post(TOKEN_URL, data=data, headers=headers, auth=auth)
    if resp.status_code != 200:
        # Do not leak the body verbatim if it could contain sensitive echoes;
        # Spotify's token errors are safe and useful, so include the summary.
        raise AuthError(
            f"Token endpoint returned {resp.status_code}: {resp.text[:300]}"
        )
    payload = resp.json()
    now = time.time()
    return TokenBundle(
        access_token=payload["access_token"],
        refresh_token=payload.get("refresh_token", ""),
        expires_at=now + float(payload.get("expires_in", 3600)),
        scope=payload.get("scope", " ".join(SCOPES)),
        token_type=payload.get("token_type", "Bearer"),
    )


def exchange_code(settings: Settings, code: str, verifier: str) -> TokenBundle:
    return _token_request(
        settings,
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": settings.redirect_uri,
            "code_verifier": verifier,
        },
    )


def refresh(settings: Settings, refresh_token: str) -> TokenBundle:
    bundle = _token_request(
        settings,
        {"grant_type": "refresh_token", "refresh_token": refresh_token},
    )
    # Spotify often omits a new refresh token; keep the existing one.
    if not bundle.refresh_token:
        bundle.refresh_token = refresh_token
    return bundle


def ensure_access_token(settings: Settings, store: TokenStore) -> TokenBundle:
    """Return a valid bundle, refreshing if expired/near expiry. Raises AuthError
    if the user has never logged in."""
    bundle = store.load()
    if bundle is None:
        raise AuthError(
            "Not authenticated. Run `local-mood-auth login` "
            "(or `python -m local_mood_mcp.auth login`) once to grant access."
        )
    if time.time() >= bundle.expires_at - _EXPIRY_SKEW_SECONDS:
        if not bundle.refresh_token:
            raise AuthError("Access token expired and no refresh token is stored.")
        bundle = refresh(settings, bundle.refresh_token)
        store.save(bundle)
    return bundle


# --- Interactive login ------------------------------------------------------
class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    # Populated by the server instance.
    expected_state: str = ""
    result: dict[str, str] = {}

    def do_GET(self) -> None:  # noqa: N802 (stdlib naming)
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path.rstrip("/") not in ("/callback", ""):
            self.send_response(404)
            self.end_headers()
            return
        qs = urllib.parse.parse_qs(parsed.query)
        state = (qs.get("state") or [""])[0]
        code = (qs.get("code") or [""])[0]
        error = (qs.get("error") or [""])[0]

        if error:
            self._respond(f"Authorization failed: {error}")
            type(self).result = {"error": error}
            return
        if not secrets.compare_digest(state, type(self).expected_state):
            self._respond("State mismatch — possible CSRF. Aborted.")
            type(self).result = {"error": "state_mismatch"}
            return
        type(self).result = {"code": code}
        self._respond("Authorization complete. You can close this tab and return to the terminal.")

    def _respond(self, message: str) -> None:
        body = (
            "<html><body style='font-family:system-ui;padding:2rem'>"
            f"<h3>{message}</h3></body></html>"
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:  # silence default logging
        return


def login(settings: Settings | None = None, *, open_browser: bool = True) -> TokenBundle:
    """Run the one-time interactive PKCE login. Returns the saved TokenBundle."""
    settings = settings or load_settings()
    store = TokenStore(settings.state_dir)
    pending = build_authorize_url(settings)

    _CallbackHandler.expected_state = pending.state
    _CallbackHandler.result = {}

    # Bind strictly to the loopback host from the redirect URI.
    server = http.server.HTTPServer(
        (settings.redirect_host, settings.redirect_port), _CallbackHandler
    )
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()

    print("\nOpen this URL in your browser to authorize (only Spotify sees it):\n")
    print(pending.authorize_url + "\n")
    if open_browser:
        try:
            webbrowser.open(pending.authorize_url)
        except Exception:
            pass

    # Wait for the single callback request to complete (with a timeout).
    thread.join(timeout=300)
    server.server_close()
    result = _CallbackHandler.result
    if "error" in result:
        raise AuthError(f"Login failed: {result['error']}")
    code = result.get("code")
    if not code:
        raise AuthError("Timed out waiting for the authorization callback.")

    bundle = exchange_code(settings, code, pending.code_verifier)
    store.save(bundle)
    return bundle


def logout(settings: Settings | None = None) -> None:
    settings = settings or load_settings()
    TokenStore(settings.state_dir).clear()


def cli_main(argv: list[str] | None = None) -> int:
    import sys

    args = argv if argv is not None else sys.argv[1:]
    cmd = args[0] if args else "login"
    if cmd in ("-h", "--help", "help"):
        print("Usage: local-mood-auth [login|logout|status]")
        return 0
    try:
        settings = load_settings()
    except RuntimeError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        return 1
    if cmd == "login":
        bundle = login(settings)
        print(
            f"Logged in. Scopes granted: {bundle.scope}\n"
            f"Tokens stored securely in your OS keyring (or encrypted fallback)."
        )
        return 0
    if cmd == "logout":
        logout(settings)
        print("Logged out; stored tokens cleared.")
        return 0
    if cmd == "status":
        store = TokenStore(settings.state_dir)
        bundle = store.load()
        if not bundle:
            print("Not authenticated.")
            return 1
        remaining = int(bundle.expires_at - time.time())
        if remaining > 0:
            print(f"Authenticated. Access token expires in ~{remaining}s.")
        else:
            print(
                "Authenticated. Access token is expired; it refreshes "
                "automatically on the next call."
            )
        print(f"Scopes: {bundle.scope}")
        return 0
    print(f"Unknown command: {cmd!r}. Use one of: login, logout, status.")
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(cli_main())
