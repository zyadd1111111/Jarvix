"""Desktop OAuth with bounded loopback callbacks, single-use state and PKCE.

Only fixed provider endpoints are trusted. Client secrets and refresh tokens belong
in IntegrationVault; this module never writes credentials or callback URLs to disk.
"""
from __future__ import annotations

import base64
import hashlib
import math
import secrets
import threading
import time
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

GOOGLE = "https://www.googleapis.com/auth/"


@dataclass(frozen=True)
class OAuthProvider:
    authorize: str
    token: str
    read_scopes: tuple[str, ...]
    write_scopes: tuple[str, ...]
    port: int = 0


PROVIDERS = {
    "gmail": OAuthProvider("https://accounts.google.com/o/oauth2/v2/auth",
        "https://oauth2.googleapis.com/token", (GOOGLE + "gmail.readonly",),
        (GOOGLE + "gmail.modify",)),
    "google_calendar": OAuthProvider("https://accounts.google.com/o/oauth2/v2/auth",
        "https://oauth2.googleapis.com/token", (GOOGLE + "calendar.readonly",),
        (GOOGLE + "calendar.events",)),
    "google_drive": OAuthProvider("https://accounts.google.com/o/oauth2/v2/auth",
        "https://oauth2.googleapis.com/token", (GOOGLE + "drive.readonly",),
        (GOOGLE + "drive.file",)),
    "spotify": OAuthProvider("https://accounts.spotify.com/authorize",
        "https://accounts.spotify.com/api/token", ("user-read-private", "user-read-playback-state"),
        ("user-modify-playback-state",), 8766),
}


class OAuthError(RuntimeError):
    """Sanitized authentication error safe for local UI and activity history."""


class OAuthAttempt:
    def __init__(self, redirect_uri: str, lifetime: float = 180):
        parsed = urlsplit(redirect_uri)
        if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.path != "/callback":
            raise ValueError("Only the local Jarvix callback is supported.")
        self.redirect_uri = redirect_uri
        self.state = secrets.token_urlsafe(32)
        self.verifier = secrets.token_urlsafe(64)
        self.expires = time.monotonic() + min(lifetime, 300)
        self.used = False
        self._lock = threading.Lock()

    @property
    def challenge(self):
        return base64.urlsafe_b64encode(hashlib.sha256(self.verifier.encode("ascii")).digest()).decode().rstrip("=")

    def consume(self, target: str) -> str:
        with self._lock:
            if self.used or time.monotonic() >= self.expires:
                raise OAuthError("The account connection expired. Start Connect again.")
            parsed = urlsplit(target)
            if parsed.path != "/callback":
                raise OAuthError("Invalid account callback.")
            values = parse_qs(parsed.query, max_num_fields=12)
            state = values.get("state", [])
            if len(state) != 1 or not secrets.compare_digest(state[0], self.state):
                raise OAuthError("The account connection state did not match.")
            self.used = True
            if "error" in values:
                raise OAuthError("Account authorization was declined or failed.")
            codes = values.get("code", [])
            if len(codes) != 1 or not codes[0] or len(codes[0]) > 4096:
                raise OAuthError("The account provider did not return an authorization code.")
            return codes[0]


def token_request(provider: OAuthProvider, data: dict, client=None) -> dict:
    """No redirects, exception bodies, tokens, or provider errors escape this API."""
    owned = client is None
    client = client or httpx.Client(timeout=20, follow_redirects=False)
    try:
        with client.stream("POST", provider.token, data=data, headers={"Accept": "application/json"},
                           follow_redirects=False) as response:
            if response.status_code != 200:
                raise OAuthError("Account authorization failed. Check the client configuration and permissions.")
            raw = bytearray()
            for chunk in response.iter_bytes():
                raw.extend(chunk)
                if len(raw) > 32768:
                    raise OAuthError("The account provider returned an oversized token response.")
            import json
            result = json.loads(raw)
        token = result.get("access_token") if isinstance(result, dict) else None
        if not isinstance(token, str) or not token or len(token) > 4096:
            raise OAuthError("The account provider did not return a valid token.")
        if str(result.get("token_type", "Bearer")).lower() != "bearer":
            raise OAuthError("Unsupported account token type.")
        for name in ("refresh_token", "scope"):
            if name in result and (not isinstance(result[name], str) or len(result[name]) > 8192):
                raise OAuthError("The account provider returned invalid token metadata.")
        if "expires_in" in result:
            expiry = float(result["expires_in"])
            if not math.isfinite(expiry) or expiry <= 0 or expiry > 365 * 86400:
                raise OAuthError("The account provider returned an invalid token lifetime.")
        return {key: result[key] for key in ("access_token", "refresh_token", "expires_in", "scope") if key in result}
    except OAuthError:
        raise
    except Exception:
        raise OAuthError("Account authorization could not reach the provider.") from None
    finally:
        if owned:
            client.close()


def authorize(integration_id: str, client_id: str, *, client_secret="", write=False,
              cancel=None, open_browser=webbrowser.open, on_ready=None, timeout=180, client=None) -> dict:
    """Run on a worker. Listen only on loopback and stop promptly on cancellation."""
    provider = PROVIDERS[integration_id]
    if not client_id.strip() or len(client_id) > 512:
        raise OAuthError("Configure the provider's desktop OAuth client ID first.")
    cancellation = cancel or threading.Event()
    outcome = {}
    attempt = None

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass  # Callback query strings contain authorization codes.

        def setup(self):
            self.request.settimeout(1)
            super().setup()

        def do_GET(self):
            try:
                if len(self.path) > 8192 or self.headers.get("Host") != urlsplit(attempt.redirect_uri).netloc:
                    raise OAuthError("Invalid account callback.")
                code = attempt.consume(self.path)
                outcome["code"] = code
                status, message = 200, "Account authorized. You can close this tab and return to Jarvix."
            except (OAuthError, ValueError):
                status, message = 400, "Account authorization was not accepted. Return to Jarvix."
                if attempt.used:
                    outcome["error"] = True
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", "default-src 'none'")
            self.end_headers()
            self.wfile.write(message.encode())

    try:
        server = HTTPServer(("127.0.0.1", provider.port), Handler)
    except OSError:
        raise OAuthError("The local account callback port is unavailable. Close the other login and retry.") from None
    server.timeout = .2
    try:
        attempt = OAuthAttempt(f"http://127.0.0.1:{server.server_port}/callback", timeout)
        scopes = provider.read_scopes + (provider.write_scopes if write else ())
        query = {"response_type": "code", "client_id": client_id, "redirect_uri": attempt.redirect_uri,
                 "scope": " ".join(scopes), "state": attempt.state,
                 "code_challenge": attempt.challenge, "code_challenge_method": "S256"}
        if integration_id != "spotify":
            query.update(access_type="offline", prompt="consent")
        if on_ready:
            on_ready(attempt.redirect_uri)
        if not open_browser(provider.authorize + "?" + urlencode(query)):
            raise OAuthError("Could not open the browser for account authorization.")
        while not outcome:
            if cancellation.is_set():
                raise OAuthError("Account connection cancelled.")
            if time.monotonic() >= attempt.expires:
                raise OAuthError("Account connection timed out. Start Connect again.")
            server.handle_request()
        if cancellation.is_set():
            raise OAuthError("Account connection cancelled.")
        if "error" in outcome:
            raise OAuthError("Account authorization was declined or failed.")
        data = {"grant_type": "authorization_code", "code": outcome["code"], "client_id": client_id,
                "redirect_uri": attempt.redirect_uri, "code_verifier": attempt.verifier}
        if client_secret:
            data["client_secret"] = client_secret
        token = token_request(provider, data, client)
        if cancellation.is_set():
            raise OAuthError("Account connection cancelled.")
        token.update(client_id=client_id, expires_at=time.time() + float(token.pop("expires_in", 3600)))
        token.setdefault("scope", " ".join(scopes))
        if not set(scopes).issubset(set(token["scope"].split())):
            raise OAuthError("The requested account permissions were not granted. Reconnect and review permissions.")
        return token
    finally:
        server.server_close()
