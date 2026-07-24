"""
schwab-api-oauth-pkce-flow: Production-grade Charles Schwab Developer API OAuth 2.0 PKCE manager (RFC 7636),
code verifier/challenge generator, atomic token persistence, and 7-day refresh lifecycle tracker.
"""
import base64
from dataclasses import dataclass, field
import hashlib
import json
import logging
import os
import secrets
import string
import time
from typing import Any, Callable, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


class SchwabAuthError(RuntimeError):
    """Raised when Schwab OAuth authentication or PKCE validation fails."""
    pass


class SchwabPKCEGenerator:
    """Generates RFC 7636 compliant PKCE code_verifier and S256 code_challenge."""

    @staticmethod
    def generate_verifier(length: int = 64) -> str:
        """Generates cryptographically secure code_verifier string."""
        alphabet = string.ascii_letters + string.digits + "-._~"
        return "".join(secrets.choice(alphabet) for _ in range(length))

    @staticmethod
    def derive_challenge(verifier: str) -> str:
        """Derives URL-safe Base64-encoded SHA-256 code_challenge without padding."""
        digest = hashlib.sha256(verifier.encode("ascii")).digest()
        challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
        return challenge


@dataclass
class SchwabTokenState:
    access_token: str
    refresh_token: str
    access_expires_at: float
    refresh_expires_at: float
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "access_expires_at": self.access_expires_at,
            "refresh_expires_at": self.refresh_expires_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SchwabTokenState":
        return cls(
            access_token=data.get("access_token", ""),
            refresh_token=data.get("refresh_token", ""),
            access_expires_at=float(data.get("access_expires_at", 0)),
            refresh_expires_at=float(data.get("refresh_expires_at", 0)),
            updated_at=float(data.get("updated_at", time.time())),
        )


class SchwabOAuthManager:
    """
    Manages Schwab API OAuth2 PKCE authorization flows, short-lived access tokens (30m),
    and 7-day refresh token lifecycle warning.
    """

    def __init__(self, token_file_path: str = "schwab_tokens.json", access_buffer_seconds: float = 300.0):
        self.token_file_path = token_file_path
        self.access_buffer_seconds = access_buffer_seconds
        self.state: Optional[SchwabTokenState] = self._load_storage()

    def _load_storage(self) -> Optional[SchwabTokenState]:
        if not os.path.exists(self.token_file_path):
            return None
        try:
            with open(self.token_file_path, "r", encoding="utf-8") as f:
                return SchwabTokenState.from_dict(json.load(f))
        except Exception as e:
            logger.error(f"Error loading Schwab token file: {e}")
            return None

    def _save_storage(self, state: SchwabTokenState):
        tmp_path = f"{self.token_file_path}.tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(state.to_dict(), f, indent=2)
            os.replace(tmp_path, self.token_file_path)
            logger.info("Schwab token state saved atomically.")
        except Exception as e:
            logger.error(f"Failed to persist Schwab token state: {e}")

    @staticmethod
    def get_authorization_url(app_key: str, redirect_uri: str, challenge: str) -> str:
        """Constructs Schwab OAuth2 authorization URL with PKCE S256 challenge."""
        return (
            f"https://api.schwabapi.com/v1/oauth/authorize?"
            f"client_id={app_key}&redirect_uri={redirect_uri}&response_type=code&"
            f"code_challenge={challenge}&code_challenge_method=S256"
        )

    def is_access_token_expiring(self) -> bool:
        if not self.state or not self.state.access_token:
            return True
        return time.time() >= (self.state.access_expires_at - self.access_buffer_seconds)

    def is_refresh_token_expiring_soon(self, warn_buffer_seconds: float = 86400.0) -> bool:
        """Returns True if 7-day refresh token expires within 24 hours."""
        if not self.state or not self.state.refresh_token:
            return True
        return time.time() >= (self.state.refresh_expires_at - warn_buffer_seconds)

    def exchange_code(
        self,
        app_key: str,
        app_secret: str,
        auth_code: str,
        redirect_uri: str,
        code_verifier: str,
        http_post_fn: Callable[[str, Dict[str, Any], Dict[str, str]], Dict[str, Any]],
    ) -> SchwabTokenState:
        """Exchanges authorization code + code_verifier for access and refresh tokens."""
        endpoint = "https://api.schwabapi.com/v1/oauth/token"
        headers = {
            "Authorization": f"Basic {base64.b64encode(f'{app_key}:{app_secret}'.encode()).decode()}",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        payload = {
            "grant_type": "authorization_code",
            "code": auth_code,
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier,
        }

        resp = http_post_fn(endpoint, payload, headers)
        if "access_token" not in resp:
            raise SchwabAuthError(f"Schwab token exchange failed: {resp}")

        now = time.time()
        state = SchwabTokenState(
            access_token=resp["access_token"],
            refresh_token=resp.get("refresh_token", ""),
            access_expires_at=now + float(resp.get("expires_in", 1800)),
            refresh_expires_at=now + (7 * 86400),  # 7-day expiry
            updated_at=now,
        )
        self._save_storage(state)
        self.state = state
        return state
