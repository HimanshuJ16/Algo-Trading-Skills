"""
upstox-oauth-refresh-token-rotation: Production-grade Upstox API v2 OAuth2 token manager,
single-use refresh token rotation handler, thread-safe atomic persistence,
and preemptive access token renewal.
"""
from dataclasses import dataclass, field
import json
import logging
import os
import threading
import time
from typing import Any, Callable, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


class UpstoxAuthError(RuntimeError):
    """Raised when Upstox OAuth token refresh fails or refresh token is revoked."""
    pass


@dataclass
class UpstoxTokenState:
    access_token: str
    refresh_token: str
    expires_at: float
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "UpstoxTokenState":
        return cls(
            access_token=data.get("access_token", ""),
            refresh_token=data.get("refresh_token", ""),
            expires_at=float(data.get("expires_at", 0)),
            updated_at=float(data.get("updated_at", time.time())),
        )


class UpstoxTokenManager:
    """
    Manages Upstox API v2 OAuth2 tokens with single-use refresh token rotation,
    atomic persistent storage, and preemptive access token renewal.
    """

    def __init__(self, token_file_path: str = "upstox_tokens.json", buffer_seconds: float = 900.0):
        self.token_file_path = token_file_path
        self.buffer_seconds = buffer_seconds
        self._lock = threading.Lock()
        self.state: Optional[UpstoxTokenState] = self._load_from_storage()

    def _load_from_storage(self) -> Optional[UpstoxTokenState]:
        if not os.path.exists(self.token_file_path):
            return None
        try:
            with open(self.token_file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return UpstoxTokenState.from_dict(data)
        except Exception as e:
            logger.error(f"Error loading Upstox token storage '{self.token_file_path}': {e}")
            return None

    def _save_to_storage(self, state: UpstoxTokenState):
        """Atomically saves token state to disk."""
        tmp_path = f"{self.token_file_path}.tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(state.to_dict(), f, indent=2)
            os.replace(tmp_path, self.token_file_path)
            logger.info(f"Upstox token state atomically updated in '{self.token_file_path}'.")
        except Exception as e:
            logger.error(f"Failed to persist Upstox token state: {e}")

    def is_token_expiring(self) -> bool:
        if not self.state or not self.state.access_token:
            return True
        now = time.time()
        return now >= (self.state.expires_at - self.buffer_seconds)

    def rotate_refresh_token(
        self,
        client_id: str,
        client_secret: str,
        http_post_fn: Callable[[str, Dict[str, Any]], Dict[str, Any]],
    ) -> UpstoxTokenState:
        """
        Executes single-use refresh token rotation:
        1. Acquires lock.
        2. Posts refresh request to Upstox API.
        3. Parses new access_token AND new refresh_token.
        4. Persists new token state before returning.
        """
        with self._lock:
            # Re-check state inside lock
            if not self.state or not self.state.refresh_token:
                raise UpstoxAuthError("No valid refresh token available for rotation.")

            if not self.is_token_expiring():
                return self.state

            endpoint = "https://api.upstox.com/v2/login/auth/token"
            payload = {
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": self.state.refresh_token,
                "grant_type": "refresh_token",
            }

            logger.info("Executing Upstox refresh token rotation POST...")
            response = http_post_fn(endpoint, payload)

            if response.get("status") != "success" and "access_token" not in response:
                err_msg = response.get("errors", [{}])[0].get("message", "Refresh token rotation failed")
                logger.critical(f"Upstox refresh token rotation FAILED: {err_msg}")
                raise UpstoxAuthError(err_msg)

            new_access_token = response.get("access_token", "")
            new_refresh_token = response.get("refresh_token", self.state.refresh_token)
            expires_in = float(response.get("expires_in", 86400))
            now = time.time()

            new_state = UpstoxTokenState(
                access_token=new_access_token,
                refresh_token=new_refresh_token,
                expires_at=now + expires_in,
                updated_at=now,
            )

            # Atomic persistence
            self._save_to_storage(new_state)
            self.state = new_state
            logger.info("Upstox token rotation completed successfully.")
            return new_state

    def get_valid_access_token(
        self,
        client_id: str,
        client_secret: str,
        http_post_fn: Callable[[str, Dict[str, Any]], Dict[str, Any]],
        reauth_fallback_fn: Optional[Callable[[], UpstoxTokenState]] = None,
    ) -> str:
        """
        Returns valid access token, rotating if expiring, or invoking fallback re-auth if revoked.
        """
        if not self.is_token_expiring() and self.state:
            return self.state.access_token

        try:
            state = self.rotate_refresh_token(client_id, client_secret, http_post_fn)
            return state.access_token
        except UpstoxAuthError as e:
            logger.warning(f"Refresh token rotation failed ({e}). Invoking fallback re-auth if available...")
            if reauth_fallback_fn:
                new_state = reauth_fallback_fn()
                self._save_to_storage(new_state)
                self.state = new_state
                return new_state.access_token
            raise
