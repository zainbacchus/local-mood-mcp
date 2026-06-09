# Security Policy

local-mood-mcp handles OAuth tokens for your Spotify account and, optionally,
your full listening history (which contains IP addresses and timestamps).

## Reporting a vulnerability

If you find a vulnerability — token leakage, a way to bypass the keyring or
encrypted-file storage, redirect/CSRF weaknesses in the PKCE flow, or personal
data escaping the git-ignored drop folder — please report it **privately**
rather than opening a public issue. Use GitHub's private vulnerability
reporting ("Report a vulnerability" under the repository's Security tab).

You can expect an acknowledgement within a few days, and a fix or documented
mitigation before public disclosure.

## Out of scope

- Issues that require an already-compromised machine: anything with access to
  your OS keyring or your user account already has your tokens.
- Spotify-side API behavior (rate limits, endpoint deprecations, what data the
  API exposes).
