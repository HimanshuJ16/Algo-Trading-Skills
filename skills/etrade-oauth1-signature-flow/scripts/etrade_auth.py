"""
etrade-oauth1-signature-flow: OAuth 1.0a HMAC-SHA1 authentication and request
signing for E*TRADE's API, which uses OAuth 1.0a instead of OAuth 2.0.

Signature construction follows RFC 5849 (OAuth 1.0):
  - Section 3.4.1.2   Base String URI normalization (lowercase scheme/host,
                      default port stripped, query and fragment excluded).
  - Section 3.4.1.3.1 Parameter sources (URI query component + Authorization
                      header params + form-encoded body params).
  - Section 3.4.1.3.2 Parameter normalization (percent-encode first, then sort
                      by encoded name and, for repeated names, by encoded value).
  - Section 3.6       Percent-encoding (unreserved set is ALPHA/DIGIT/-/./_/~).

This module performs signing only; it issues no HTTP requests and pulls in no
third-party dependencies. Callers supply their own HTTP client.
"""
import base64
import collections.abc
import hashlib
import hmac
import logging
import time
import urllib.parse
import uuid
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Union

logger = logging.getLogger(__name__)

ParamPairs = Union[Mapping[str, str], Iterable[Tuple[str, str]]]

# Parameters that RFC 5849 Section 3.4.1.3.1 excludes from the base string.
_EXCLUDED_FROM_BASE_STRING = frozenset({"oauth_signature", "realm"})

_DEFAULT_PORTS = {"http": "80", "https": "443"}


class ETradeAuthError(ValueError):
    """Raised when OAuth 1.0a inputs or E*TRADE token responses are invalid.

    Subclasses ``ValueError`` so existing callers that catch ``ValueError``
    around signing calls keep working.
    """


@dataclass
class OAuth1Token:
    """An OAuth 1.0a token pair. ``token_secret`` is excluded from ``repr``."""

    token: str
    token_secret: str = field(repr=False)
    verifier: Optional[str] = None


@dataclass
class OAuth1Credentials:
    """Consumer and access credentials.

    Secrets are excluded from ``repr`` so they cannot leak into logs,
    tracebacks, or debugger output.
    """

    consumer_key: str
    consumer_secret: str = field(repr=False)
    access_token: Optional[str] = None
    access_token_secret: Optional[str] = field(default=None, repr=False)


class ETradeOAuth1Client:
    """
    Implements OAuth 1.0a three-legged authentication and HMAC-SHA1 request
    signing for E*TRADE's API.

    E*TRADE-specific behavior encoded here (see ``references/standards.md``
    for citations):

    - ``oauth_callback`` must always be sent on the request-token call, set to
      ``"oob"`` whether or not a callback URL is configured on the app.
    - The authorization step happens on ``us.etrade.com``, not on the API host,
      and uses the same host for sandbox and production.
    - Request tokens are valid for 5 minutes.
    - Access tokens expire at the end of the current calendar day, US Eastern,
      and are separately *inactivated* after 2 hours with no API requests. An
      inactivated token is recovered with ``renew_access_token``, not by
      repeating the three-legged flow.
    - ``oauth_timestamp`` must be within 5 minutes of E*TRADE's clock.
    """

    BASE_URL = "https://api.etrade.com"
    SANDBOX_URL = "https://apisb.etrade.com"

    # The authorization page lives on the retail site for BOTH sandbox and
    # production. Building it from ``base_url`` yields a dead URL.
    AUTHORIZE_URL = "https://us.etrade.com/e/t/etws/authorize"

    # E*TRADE-documented lifetimes, in seconds.
    REQUEST_TOKEN_TTL_SECONDS = 300
    ACCESS_TOKEN_IDLE_INACTIVATION_SECONDS = 7200
    TIMESTAMP_TOLERANCE_SECONDS = 300

    def __init__(
        self,
        consumer_key: str,
        consumer_secret: str,
        use_sandbox: bool = True,
    ):
        if not isinstance(consumer_key, str) or not consumer_key.strip():
            raise ETradeAuthError("consumer_key must be a non-empty string.")
        if not isinstance(consumer_secret, str) or not consumer_secret.strip():
            raise ETradeAuthError("consumer_secret must be a non-empty string.")

        self.credentials = OAuth1Credentials(
            consumer_key=consumer_key,
            consumer_secret=consumer_secret,
        )
        self.use_sandbox = use_sandbox
        self.base_url = self.SANDBOX_URL if use_sandbox else self.BASE_URL
        self.request_token: Optional[OAuth1Token] = None

    # ------------------------------------------------------------------
    # RFC 5849 primitives
    # ------------------------------------------------------------------

    @staticmethod
    def percent_encode(s: str) -> str:
        """RFC 5849 Section 3.6 percent-encoding.

        Only ALPHA, DIGIT, ``-``, ``.``, ``_`` and ``~`` are left unescaped.
        ``safe="~"`` is passed explicitly rather than relying on the
        interpreter default, which did not treat ``~`` as unreserved before
        Python 3.7.
        """
        return urllib.parse.quote(str(s), safe="~")

    @staticmethod
    def generate_nonce() -> str:
        """Generate a unique nonce for each request.

        E*TRADE rejects a nonce reused with the same timestamp, so this must be
        called once per request and never cached alongside the timestamp.
        """
        return uuid.uuid4().hex

    @staticmethod
    def generate_timestamp() -> str:
        """Generate the OAuth1 timestamp (seconds since the epoch, UTC).

        E*TRADE requires this to be within ``TIMESTAMP_TOLERANCE_SECONDS`` of
        its own clock.
        """
        return str(int(time.time()))

    @classmethod
    def normalize_base_string_uri(cls, url: str) -> str:
        """Normalize a request URL per RFC 5849 Section 3.4.1.2.

        Lowercases scheme and host, drops the query and fragment, and removes
        the port when it is the default for the scheme. The query is *not*
        discarded from signing — it is collected separately by
        :meth:`collect_query_parameters` and folded into the parameter string.
        """
        if not isinstance(url, str) or not url.strip():
            raise ETradeAuthError("url must be a non-empty string.")

        parts = urllib.parse.urlsplit(url.strip())
        scheme = parts.scheme.lower()
        if scheme not in ("http", "https"):
            raise ETradeAuthError(
                f"url must use the http or https scheme, got {parts.scheme!r}."
            )
        if not parts.hostname:
            raise ETradeAuthError(f"url is missing a host: {url!r}")

        authority = parts.hostname.lower()
        port = parts.port
        if port is not None and str(port) != _DEFAULT_PORTS[scheme]:
            authority = f"{authority}:{port}"

        return urllib.parse.urlunsplit((scheme, authority, parts.path, "", ""))

    @staticmethod
    def collect_query_parameters(url: str) -> List[Tuple[str, str]]:
        """Decode the URL query component into name/value pairs.

        RFC 5849 Section 3.4.1.3.1 requires query parameters to participate in
        the signature. Blank values are kept, because ``?flag=`` and ``?flag``
        are both parameters with an empty value rather than absent parameters —
        the RFC's own worked example includes exactly such a parameter.
        """
        query = urllib.parse.urlsplit(url).query
        if not query:
            return []
        return urllib.parse.parse_qsl(query, keep_blank_values=True)

    @staticmethod
    def _as_pairs(params: Optional[ParamPairs]) -> List[Tuple[str, str]]:
        """Normalize a mapping or an iterable of pairs into a list of pairs.

        Malformed input is reported as :class:`ETradeAuthError` rather than as
        a bare unpacking error, so a caller guarding a signing call catches it
        with everything else this module raises.
        """
        if params is None:
            return []
        if isinstance(params, collections.abc.Mapping):
            return [(str(k), str(v)) for k, v in params.items()]
        try:
            return [(str(name), str(value)) for name, value in params]
        except (TypeError, ValueError) as exc:
            raise ETradeAuthError(
                "params must be a mapping or an iterable of (name, value) pairs."
            ) from exc

    def normalize_parameters(self, params: Sequence[Tuple[str, str]]) -> str:
        """Build the normalized parameter string per RFC 5849 Section 3.4.1.3.2.

        Names and values are percent-encoded *first*, then sorted by encoded
        name and, for repeated names, by encoded value. Sorting the raw values
        instead produces a different ordering — and therefore a rejected
        signature — whenever a value contains a character that encodes to a
        different byte sequence (a space, ``+``, or any non-ASCII character).
        """
        encoded = [
            (self.percent_encode(name), self.percent_encode(value))
            for name, value in params
            if name not in _EXCLUDED_FROM_BASE_STRING
        ]
        encoded.sort()
        return "&".join(f"{name}={value}" for name, value in encoded)

    def build_base_string(
        self,
        method: str,
        url: str,
        params: Optional[ParamPairs] = None,
    ) -> str:
        """Build the OAuth1 signature base string per RFC 5849 Section 3.4.1.

        ``url`` may carry a query string; its parameters are decoded and signed
        alongside ``params`` while the base string URI itself is normalized
        without the query. ``params`` accepts either a mapping or an iterable
        of ``(name, value)`` pairs — use pairs when a name repeats, which a
        mapping cannot express.

        Any ``oauth_signature`` or ``realm`` entry is excluded, as the RFC
        requires.
        """
        if not isinstance(method, str) or not method.strip():
            raise ETradeAuthError("method must be a non-empty string.")

        base_uri = self.normalize_base_string_uri(url)
        collected = self.collect_query_parameters(url) + self._as_pairs(params)

        return "&".join([
            method.strip().upper(),
            self.percent_encode(base_uri),
            self.percent_encode(self.normalize_parameters(collected)),
        ])

    def sign_hmac_sha1(
        self,
        base_string: str,
        consumer_secret: str,
        token_secret: str = "",
    ) -> str:
        """Generate the HMAC-SHA1 signature (RFC 5849 Section 3.4.2).

        The signing key is the percent-encoded consumer secret and token
        secret joined by ``&``. The trailing ``&`` is required even when there
        is no token secret yet, as on the request-token call.
        """
        signing_key = (
            f"{self.percent_encode(consumer_secret)}&"
            f"{self.percent_encode(token_secret or '')}"
        )
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
        extra_params: Optional[ParamPairs] = None,
        callback: str = "",
    ) -> str:
        """Build a complete OAuth1 ``Authorization`` header value.

        ``extra_params`` are non-OAuth parameters that must participate in the
        signature — form-encoded body fields, typically. They are signed but
        deliberately not emitted in the header, per RFC 5849 Section 3.5.1;
        they belong in the body or query where the server already reads them.
        Query parameters present in ``url`` are signed automatically and must
        not be repeated here.

        ``callback`` sets ``oauth_callback``. E*TRADE requires it on the
        request-token call and documents that it must always be ``"oob"``,
        whether or not the app has a callback URL configured.
        """
        oauth_params: Dict[str, str] = {
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
        if callback:
            oauth_params["oauth_callback"] = callback

        all_params: List[Tuple[str, str]] = list(oauth_params.items())
        all_params.extend(self._as_pairs(extra_params))

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

    # ------------------------------------------------------------------
    # E*TRADE three-legged flow
    # ------------------------------------------------------------------

    def get_request_token_url(self) -> str:
        """Return the request token endpoint URL. E*TRADE serves this over GET."""
        return f"{self.base_url}/oauth/request_token"

    def get_access_token_url(self) -> str:
        """Return the access token endpoint URL. E*TRADE serves this over GET."""
        return f"{self.base_url}/oauth/access_token"

    def get_renew_access_token_url(self) -> str:
        """Return the endpoint that reactivates an idle-inactivated access token."""
        return f"{self.base_url}/oauth/renew_access_token"

    def get_revoke_access_token_url(self) -> str:
        """Return the endpoint that permanently revokes an access token."""
        return f"{self.base_url}/oauth/revoke_access_token"

    def build_request_token_header(self) -> str:
        """Sign the leg-one request-token call, including the mandatory
        ``oauth_callback="oob"``.

        Omitting ``oauth_callback`` is a common cause of a rejected
        request-token call, because the parameter has no server-side default.
        """
        return self.build_auth_header(
            "GET",
            self.get_request_token_url(),
            callback="oob",
        )

    def get_authorize_url(self, request_token: Optional[str] = None) -> str:
        """Build the leg-two user authorization URL.

        Uses ``AUTHORIZE_URL`` on ``us.etrade.com`` — the same host for sandbox
        and production — and percent-encodes both the consumer key and the
        request token, which routinely contain ``+``, ``/``, and ``=``. Pasting
        an unencoded token into the query silently authorizes a different
        (usually nonexistent) token.
        """
        token = request_token
        if token is None:
            if self.request_token is None:
                raise ETradeAuthError(
                    "No request token available. Call set_request_token() or pass "
                    "request_token explicitly."
                )
            token = self.request_token.token

        query = urllib.parse.urlencode(
            {"key": self.credentials.consumer_key, "token": token},
            quote_via=urllib.parse.quote,
            safe="~",
        )
        return f"{self.AUTHORIZE_URL}?{query}"

    def build_access_token_header(self, verifier: str) -> str:
        """Sign the leg-three access-token exchange using the stored request token."""
        if self.request_token is None:
            raise ETradeAuthError(
                "No request token available. Call set_request_token() first."
            )
        if not isinstance(verifier, str) or not verifier.strip():
            raise ETradeAuthError("verifier must be a non-empty string.")

        return self.build_auth_header(
            "GET",
            self.get_access_token_url(),
            token=self.request_token.token,
            token_secret=self.request_token.token_secret,
            verifier=verifier.strip(),
        )

    @staticmethod
    def parse_token_response(body: str) -> OAuth1Token:
        """Parse a form-encoded E*TRADE OAuth token response.

        E*TRADE returns ``oauth_token=...&oauth_token_secret=...``. A failed
        call returns an error body instead, which ``parse_qsl`` happily decodes
        into something with no token fields; accepting that silently yields a
        client that signs every subsequent request with empty credentials and
        fails opaquely at the first API call. Missing fields raise here
        instead.
        """
        if not isinstance(body, str) or not body.strip():
            raise ETradeAuthError("Empty OAuth token response body.")

        fields = dict(urllib.parse.parse_qsl(body.strip(), keep_blank_values=True))
        token = fields.get("oauth_token", "")
        secret = fields.get("oauth_token_secret", "")
        if not token or not secret:
            raise ETradeAuthError(
                "OAuth token response missing oauth_token/oauth_token_secret. "
                f"Fields present: {sorted(fields)}"
            )
        return OAuth1Token(token=token, token_secret=secret)

    def set_request_token(self, token: str, token_secret: str) -> None:
        """Store the leg-one request token.

        The request token is valid for ``REQUEST_TOKEN_TTL_SECONDS`` (5
        minutes); if the user has not authorized within that window, restart at
        leg one rather than retrying the exchange.
        """
        if not token or not token_secret:
            raise ETradeAuthError("Request token and secret must both be non-empty.")
        self.request_token = OAuth1Token(token=token, token_secret=token_secret)
        logger.info(
            "E*TRADE request token stored (valid for %ds).",
            self.REQUEST_TOKEN_TTL_SECONDS,
        )

    def set_access_token(self, token: str, token_secret: str) -> None:
        """Set the access token after completing the OAuth1 flow."""
        if not token or not token_secret:
            raise ETradeAuthError("Access token and secret must both be non-empty.")
        self.credentials.access_token = token
        self.credentials.access_token_secret = token_secret
        logger.info("E*TRADE access token set successfully.")

    def sign_request(
        self,
        method: str,
        url: str,
        extra_params: Optional[ParamPairs] = None,
    ) -> Dict[str, str]:
        """Sign an API request and return headers with ``Authorization``.

        Pass the URL exactly as it will be sent, query string included — the
        query participates in the signature, so signing a bare path and then
        appending ``?detailFlag=ALL`` produces a signature E*TRADE rejects.
        """
        if not self.credentials.access_token or not self.credentials.access_token_secret:
            raise ETradeAuthError("Access token not set. Complete OAuth1 flow first.")

        auth_header = self.build_auth_header(
            method, url,
            token=self.credentials.access_token,
            token_secret=self.credentials.access_token_secret,
            extra_params=extra_params,
        )
        return {"Authorization": auth_header}

    def sign_renew_access_token(self) -> Dict[str, str]:
        """Sign the ``renew_access_token`` call.

        E*TRADE inactivates an access token after
        ``ACCESS_TOKEN_IDLE_INACTIVATION_SECONDS`` (2 hours) with no requests.
        Renewal reactivates it; it does *not* extend the token past its
        end-of-calendar-day US Eastern expiry, which requires the full
        three-legged flow again.
        """
        return self.sign_request("GET", self.get_renew_access_token_url())

    def sign_revoke_access_token(self) -> Dict[str, str]:
        """Sign the ``revoke_access_token`` call.

        Revoke on shutdown or when credentials may be compromised; the token
        stops granting access immediately.
        """
        return self.sign_request("GET", self.get_revoke_access_token_url())
