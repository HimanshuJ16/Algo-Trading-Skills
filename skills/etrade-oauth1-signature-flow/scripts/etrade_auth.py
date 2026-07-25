"""
etrade-oauth1-signature-flow: OAuth1 HMAC-SHA1 authentication and request signing
for E*TRADE's API, which uses OAuth1 instead of OAuth2.
"""
import base64
import hashlib
import hmac
import logging
import time
import urllib.parse
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class OAuth1Token:
    token: str
    token_secret: str
    verifier: Optional[str] = None


@dataclass
class OAuth1Credentials:
    consumer_key: str
    consumer_secret: str
    access_token: Optional[str] = None
    access_token_secret: Optional[str] = None


class ETradeOAuth1Client:
    """
    Implements OAuth1 three-legged authentication and HMAC-SHA1 request signing
    for E*TRADE's API.
    """

    BASE_URL = "https://api.etrade.com"
    SANDBOX_URL = "https://apisb.etrade.com"

    def __init__(
        self,
        consumer_key: str,
        consumer_secret: str,
        use_sandbox: bool = True,
    ):
        self.credentials = OAuth1Credentials(
            consumer_key=consumer_key,
            consumer_secret=consumer_secret,
        )
        self.base_url = self.SANDBOX_URL if use_sandbox else self.BASE_URL
        self.request_token: Optional[OAuth1Token] = None

    @staticmethod
    def percent_encode(s: str) -> str:
        """RFC 5849 percent-encoding."""
        return urllib.parse.quote(str(s), safe="")

    @staticmethod
    def generate_nonce() -> str:
        """Generate a unique nonce for each request."""
        return uuid.uuid4().hex

    @staticmethod
    def generate_timestamp() -> str:
        """Generate OAuth1 timestamp (seconds since epoch)."""
        return str(int(time.time()))

    def build_base_string(
        self,
        method: str,
        url: str,
        params: Dict[str, str],
    ) -> str:
        """Build the OAuth1 signature base string per RFC 5849."""
        sorted_params = "&".join(
            f"{self.percent_encode(k)}={self.percent_encode(v)}"
            for k, v in sorted(params.items())
        )
        return "&".join([
            method.upper(),
            self.percent_encode(url),
            self.percent_encode(sorted_params),
        ])

    def sign_hmac_sha1(
        self,
        base_string: str,
        consumer_secret: str,
        token_secret: str = "",
    ) -> str:
        """Generate HMAC-SHA1 signature."""
        signing_key = f"{self.percent_encode(consumer_secret)}&{self.percent_encode(token_secret)}"
        hashed = hmac.new(
            signing_key.encode("utf-8"),
            base_string.encode("utf-8"),
            hashlib.sha1,
        )
        return base64.b64encode(hashed.digest()).decode("utf-8")

    def build_auth_header(
        self,
        method: str,
        url: str,
        token: str = "",
        token_secret: str = "",
        verifier: str = "",
        extra_params: Optional[Dict[str, str]] = None,
    ) -> str:
        """Build a complete OAuth1 Authorization header."""
        oauth_params = {
            "oauth_consumer_key": self.credentials.consumer_key,
            "oauth_nonce": self.generate_nonce(),
            "oauth_signature_method": "HMAC-SHA1",
            "oauth_timestamp": self.generate_timestamp(),
            "oauth_version": "1.0",
        }
        if token:
            oauth_params["oauth_token"] = token
        if verifier:
            oauth_params["oauth_verifier"] = verifier

        all_params = {**oauth_params}
        if extra_params:
            all_params.update(extra_params)

        base_string = self.build_base_string(method, url, all_params)
        signature = self.sign_hmac_sha1(
            base_string,
            self.credentials.consumer_secret,
            token_secret,
        )
        oauth_params["oauth_signature"] = signature

        header_parts = ", ".join(
            f'{self.percent_encode(k)}="{self.percent_encode(v)}"'
            for k, v in sorted(oauth_params.items())
        )
        return f"OAuth {header_parts}"

    def get_request_token_url(self) -> str:
        """Return the request token endpoint URL."""
        return f"{self.base_url}/oauth/request_token"

    def get_access_token_url(self) -> str:
        """Return the access token endpoint URL."""
        return f"{self.base_url}/oauth/access_token"

    def set_access_token(self, token: str, token_secret: str) -> None:
        """Set the access token after completing the OAuth1 flow."""
        self.credentials.access_token = token
        self.credentials.access_token_secret = token_secret
        logger.info("E*TRADE access token set successfully.")

    def sign_request(self, method: str, url: str) -> Dict[str, str]:
        """Sign an API request and return headers with Authorization."""
        if not self.credentials.access_token or not self.credentials.access_token_secret:
            raise ValueError("Access token not set. Complete OAuth1 flow first.")

        auth_header = self.build_auth_header(
            method, url,
            token=self.credentials.access_token,
            token_secret=self.credentials.access_token_secret,
        )
        return {"Authorization": auth_header}
