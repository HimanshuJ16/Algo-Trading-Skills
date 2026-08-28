"""
schwab-api-oauth-pkce-flow: Charles Schwab Trader API OAuth 2.0 client.

IMPORTANT — read before using this module.

Schwab's published Trader API documentation describes a **confidential-client
authorization-code flow**, not PKCE. The authorization request carries
``client_id`` and ``redirect_uri`` only, and the token request authenticates the
client with an HTTP Basic header built from ``app_key:app_secret``. Schwab
publishes no ``code_challenge`` / ``code_challenge_method`` parameter and no
statement of PKCE support.

This module therefore implements the flow Schwab actually documents. The RFC 7636
helper (:class:`SchwabPKCEGenerator`) is retained because the skill slug promises
it and because a caller may have out-of-band confirmation that PKCE is accepted,
but **nothing here sends PKCE parameters unless the caller explicitly supplies
them**. See ``references/standards.md`` for the sourced evidence.

Lifetimes (Schwab-documented): access token 30 minutes; refresh token 7 days from
creation. Refreshing does not extend the 7-day window — when it elapses, the
browser authorization step must be repeated by a human.
"""
import base64
import hashlib
import json
import logging
import math
import os
import secrets
import stat
import string
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Mapping, Optional

logger = logging.getLogger(__name__)

# Schwab-documented endpoints (see references/standards.md).
SCHWAB_AUTHORIZE_URL = "https://api.schwabapi.com/v1/oauth/authorize"
SCHWAB_TOKEN_URL = "https://api.schwabapi.com/v1/oauth/token"

# Schwab-documented lifetimes.
REFRESH_TOKEN_LIFETIME_SECONDS = 7 * 86400.0
DEFAULT_ACCESS_BUFFER_SECONDS = 300.0
DEFAULT_REFRESH_WARN_SECONDS = 86400.0

# Schwab-documented constraint: "There is a 255 character limit on this field".
MAX_REDIRECT_URI_LENGTH = 255

# RFC 7636 s4.1: code_verifier is 43-128 characters from the unreserved set.
PKCE_MIN_VERIFIER_LENGTH = 43
PKCE_MAX_VERIFIER_LENGTH = 128

# Transport callable: (url, form_payload, headers) -> decoded JSON body.
HttpPostFn = Callable[[str, Dict[str, str], Dict[str, str]], Mapping[str, Any]]


class SchwabAuthError(RuntimeError):
    """Base class for every Schwab OAuth failure raised by this module."""


class SchwabTokenExchangeError(SchwabAuthError):
    """Schwab returned a definitive rejection. The request did not produce tokens."""


class SchwabAmbiguousTokenError(SchwabAuthError):
    """
    The token request left the process but no usable response came back.

    Schwab may or may not have acted on it. For an authorization-code exchange the
    single-use ``code`` may already be spent; for a refresh the stored refresh token
    may already have been rotated. Stored token state is left untouched — reconcile
    before retrying rather than blindly resubmitting.
    """


class SchwabRefreshTokenExpiredError(SchwabAuthError):
    """
    The 7-day refresh window has elapsed (or Schwab rejected the token as invalid).

    There is no programmatic recovery: a human must repeat the browser
    authorization step. Raised as a distinct type so an operator alert can be wired
    to it specifically.
    """


class SchwabTokenPersistenceError(SchwabAuthError):
    """
    Freshly issued tokens could not be written to durable storage.

    The in-memory state is still valid and is retained, so the running process can
    continue; but a restart will lose the tokens and force a manual re-login.
    """


def _require_nonempty_str(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SchwabAuthError(f"{name} must be a non-empty string")
    return value


def _validate_redirect_uri(redirect_uri: str) -> str:
    """
    Validates the callback URL against Schwab's published constraints.

    Schwab: "Callback URLs must be HTTPS" and are subject to a 255-character limit.
    An ``http://`` callback is rejected at the authorize step, which surfaces as an
    opaque Schwab error page rather than a useful message — so fail locally instead.
    """
    _require_nonempty_str(redirect_uri, "redirect_uri")
    parsed = urllib.parse.urlparse(redirect_uri)
    if parsed.scheme != "https":
        raise SchwabAuthError(
            f"redirect_uri must use https (Schwab requires HTTPS callbacks); got scheme "
            f"{parsed.scheme!r}"
        )
    if not parsed.netloc:
        raise SchwabAuthError("redirect_uri must include a host")
    if len(redirect_uri) > MAX_REDIRECT_URI_LENGTH:
        raise SchwabAuthError(
            f"redirect_uri exceeds Schwab's {MAX_REDIRECT_URI_LENGTH}-character limit "
            f"({len(redirect_uri)} characters)"
        )
    return redirect_uri


def _basic_auth_header(app_key: str, app_secret: str) -> str:
    """
    Builds the HTTP Basic header Schwab requires on the token endpoint.

    A colon inside the app key would be parsed as the credential separator by the
    server, silently authenticating as a different client id, so it is rejected here.
    """
    _require_nonempty_str(app_key, "app_key")
    _require_nonempty_str(app_secret, "app_secret")
    if ":" in app_key:
        raise SchwabAuthError("app_key must not contain ':' — it is the Basic-auth separator")
    encoded = base64.b64encode(f"{app_key}:{app_secret}".encode("utf-8")).decode("ascii")
    return f"Basic {encoded}"


def _describe_response(resp: Mapping[str, Any]) -> str:
    """
    Renders a token response for an error message **without leaking credentials**.

    A raw Schwab token response contains ``access_token``, ``refresh_token`` and
    ``id_token``. Interpolating it into an exception string puts live credentials
    into logs, tracebacks and crash reports. Only OAuth error fields (RFC 6749 s5.2)
    and the key names are echoed.
    """
    error = resp.get("error")
    description = resp.get("error_description")
    keys = sorted(str(k) for k in resp.keys())
    parts = []
    if error:
        parts.append(f"error={error!r}")
    if description:
        parts.append(f"error_description={description!r}")
    parts.append(f"keys={keys}")
    return ", ".join(parts)


class SchwabPKCEGenerator:
    """
    RFC 7636 code_verifier / S256 code_challenge helper.

    Correct per RFC 7636, but **not required by Schwab** — Schwab's documented
    authorization request carries no ``code_challenge``. Use this only against a
    server you have confirmed accepts PKCE.
    """

    @staticmethod
    def generate_verifier(length: int = 64) -> str:
        """
        Generates a cryptographically secure ``code_verifier``.

        RFC 7636 s4.1 constrains the verifier to 43-128 characters drawn from the
        unreserved set ``[A-Z] [a-z] [0-9] - . _ ~``. A shorter verifier reduces
        entropy below the specified floor, so out-of-range lengths are rejected
        rather than silently accepted.
        """
        if not isinstance(length, int) or isinstance(length, bool):
            raise SchwabAuthError("verifier length must be an int")
        if not PKCE_MIN_VERIFIER_LENGTH <= length <= PKCE_MAX_VERIFIER_LENGTH:
            raise SchwabAuthError(
                f"RFC 7636 requires a code_verifier of "
                f"{PKCE_MIN_VERIFIER_LENGTH}-{PKCE_MAX_VERIFIER_LENGTH} characters; "
                f"got {length}"
            )
        alphabet = string.ascii_letters + string.digits + "-._~"
        return "".join(secrets.choice(alphabet) for _ in range(length))

    @staticmethod
    def derive_challenge(verifier: str) -> str:
        """
        Derives ``BASE64URL-ENCODE(SHA256(ASCII(code_verifier)))`` with padding stripped.

        RFC 7636 s4.2 specifies base64url **without** padding; a trailing ``=`` is a
        common cause of an authorization server rejecting the request.
        """
        _require_nonempty_str(verifier, "verifier")
        if not PKCE_MIN_VERIFIER_LENGTH <= len(verifier) <= PKCE_MAX_VERIFIER_LENGTH:
            raise SchwabAuthError(
                f"code_verifier must be {PKCE_MIN_VERIFIER_LENGTH}-"
                f"{PKCE_MAX_VERIFIER_LENGTH} characters; got {len(verifier)}"
            )
        try:
            ascii_bytes = verifier.encode("ascii")
        except UnicodeEncodeError as exc:
            raise SchwabAuthError("code_verifier must be ASCII (RFC 7636 unreserved set)") from exc
        digest = hashlib.sha256(ascii_bytes).digest()
        return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


@dataclass
class SchwabTokenState:
    """
    Persisted Schwab token state.

    ``access_token`` and ``refresh_token`` are excluded from ``repr`` so that
    logging or printing the object cannot leak live credentials.

    ``refresh_expires_at`` is anchored to the *original* authorization. Schwab's
    7-day refresh window runs from creation and is not extended by refreshing, so
    this field must never be pushed forward on a refresh.
    """

    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
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
    def from_dict(cls, data: Mapping[str, Any]) -> "SchwabTokenState":
        if not isinstance(data, Mapping):
            raise ValueError("token state must be a JSON object")
        access_token = data.get("access_token", "")
        refresh_token = data.get("refresh_token", "")
        if not isinstance(access_token, str) or not isinstance(refresh_token, str):
            raise ValueError("access_token and refresh_token must be strings")
        return cls(
            access_token=access_token,
            refresh_token=refresh_token,
            access_expires_at=float(data.get("access_expires_at", 0)),
            refresh_expires_at=float(data.get("refresh_expires_at", 0)),
            updated_at=float(data.get("updated_at", time.time())),
        )

    def access_seconds_remaining(self, now: Optional[float] = None) -> float:
        return self.access_expires_at - (time.time() if now is None else now)

    def refresh_seconds_remaining(self, now: Optional[float] = None) -> float:
        return self.refresh_expires_at - (time.time() if now is None else now)


class SchwabOAuthManager:
    """
    Manages the Schwab Trader API authorization-code flow, the 30-minute access
    token, and the hard 7-day refresh-token window.

    Transport is injected as ``http_post_fn`` so timeouts, TLS verification and
    retry policy stay under caller control and the flow stays unit-testable. The
    callable receives ``(url, form_payload, headers)`` and must return the decoded
    JSON body; it must raise on transport failure and on non-2xx responses that
    carry no JSON body.

    Expiry decisions use wall-clock time because the deadlines are persisted across
    restarts, where a monotonic clock has no meaning. A large backward clock step
    can therefore make a dead token look live; keep hosts NTP-disciplined and see
    ``clock-drift-monitoring-alerting-thresholds``.
    """

    def __init__(
        self,
        token_file_path: str = "schwab_tokens.json",
        access_buffer_seconds: float = DEFAULT_ACCESS_BUFFER_SECONDS,
        refresh_warn_seconds: float = DEFAULT_REFRESH_WARN_SECONDS,
    ) -> None:
        if access_buffer_seconds < 0 or refresh_warn_seconds < 0:
            raise SchwabAuthError("buffer/warning windows must be non-negative")
        self.token_file_path = _require_nonempty_str(token_file_path, "token_file_path")
        self.access_buffer_seconds = float(access_buffer_seconds)
        self.refresh_warn_seconds = float(refresh_warn_seconds)
        self.state: Optional[SchwabTokenState] = self._load_storage()

    # ------------------------------------------------------------------ storage

    def _load_storage(self) -> Optional[SchwabTokenState]:
        if not os.path.exists(self.token_file_path):
            return None
        try:
            with open(self.token_file_path, "r", encoding="utf-8") as handle:
                state = SchwabTokenState.from_dict(json.load(handle))
        except (OSError, json.JSONDecodeError, ValueError, TypeError) as exc:
            # A corrupt token file is not recoverable here, but it must not be
            # deleted either — an operator needs to see it. Returning None forces
            # the caller down the re-authorization path.
            logger.error("Unreadable Schwab token file %s: %s", self.token_file_path, exc)
            return None
        self._warn_if_permissive(self.token_file_path)
        return state

    @staticmethod
    def _warn_if_permissive(path: str) -> None:
        """Warns when a token file is group/world readable (POSIX only)."""
        if os.name != "posix":
            return
        try:
            mode = os.stat(path).st_mode
        except OSError:
            return
        if mode & (stat.S_IRWXG | stat.S_IRWXO):
            logger.warning(
                "Schwab token file %s is group/world accessible (mode %o); "
                "it holds live account credentials — restrict it to 0600",
                path,
                stat.S_IMODE(mode),
            )

    def _save_storage(self, state: SchwabTokenState) -> None:
        """
        Writes token state atomically with owner-only permissions.

        The temp file is created with mode 0600 *before* any secret is written, so
        the tokens are never briefly readable by other local users. ``fsync`` runs
        before ``os.replace`` so a crash cannot leave a truncated file in place of a
        valid one. Failure raises — a silently swallowed write leaves the operator
        believing the tokens survived a restart when they did not.

        No cross-process lock is taken. Two processes sharing one token file is
        last-writer-wins, and if Schwab rotates the refresh token the loser is left
        holding a stale one. Run exactly one token owner per Schwab app.
        """
        directory = os.path.dirname(os.path.abspath(self.token_file_path))
        tmp_path = f"{self.token_file_path}.{os.getpid()}.tmp"
        try:
            fd = os.open(tmp_path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(state.to_dict(), handle, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, self.token_file_path)
        except OSError as exc:
            self._discard_temp(tmp_path)
            raise SchwabTokenPersistenceError(
                f"Failed to persist Schwab token state to {self.token_file_path}: {exc}"
            ) from exc
        except BaseException:
            # Anything else (a serialisation error, an interrupt) must still not
            # leave a partially written credential file lying around.
            self._discard_temp(tmp_path)
            raise
        logger.info("Schwab token state persisted atomically to %s", self.token_file_path)
        if os.name == "posix":
            try:
                dir_fd = os.open(directory, os.O_RDONLY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except OSError as exc:  # pragma: no cover - filesystem dependent
                logger.warning("Could not fsync token directory %s: %s", directory, exc)

    @staticmethod
    def _discard_temp(tmp_path: str) -> None:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    def _commit(self, state: SchwabTokenState) -> SchwabTokenState:
        """
        Installs new state in memory, then persists it.

        In-memory first: if the write fails the process still holds a usable token
        and can retry persistence, which is strictly better than discarding a token
        Schwab has already issued (and, for a refresh, already rotated).
        """
        self.state = state
        self._save_storage(state)
        return state

    # -------------------------------------------------------------- authorize

    @staticmethod
    def get_authorization_url(
        app_key: str,
        redirect_uri: str,
        code_challenge: Optional[str] = None,
    ) -> str:
        """
        Builds the Schwab authorization URL.

        Schwab documents exactly two parameters — ``client_id`` and ``redirect_uri``
        — and this method adds ``response_type=code`` for RFC 6749 conformance.
        Parameters are percent-encoded: an unencoded ``redirect_uri`` truncates at
        its own ``&`` or ``?`` and Schwab then compares a mangled value against the
        registered callback and rejects the login.

        ``code_challenge`` defaults to ``None`` and is omitted. Schwab publishes no
        PKCE support; pass a challenge only if you have confirmed the endpoint
        accepts it, in which case ``code_challenge_method=S256`` is sent with it.
        """
        _require_nonempty_str(app_key, "app_key")
        _validate_redirect_uri(redirect_uri)
        params = {
            "client_id": app_key,
            "redirect_uri": redirect_uri,
            "response_type": "code",
        }
        if code_challenge is not None:
            _require_nonempty_str(code_challenge, "code_challenge")
            if "=" in code_challenge:
                raise SchwabAuthError(
                    "code_challenge must be unpadded base64url (RFC 7636 s4.2); "
                    "strip '=' padding"
                )
            params["code_challenge"] = code_challenge
            params["code_challenge_method"] = "S256"
            logger.warning(
                "Sending a PKCE code_challenge to Schwab: Schwab publishes no PKCE "
                "support for the Trader API, so this is unverified behaviour"
            )
        return f"{SCHWAB_AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"

    @staticmethod
    def extract_code_from_callback(callback_url: str) -> str:
        """
        Extracts and percent-decodes the ``code`` from the callback URL.

        Schwab's authorization code is percent-encoded in the redirect and typically
        ends in ``%40`` (``@``). Schwab's own documentation states the code "must be
        URL decoded prior to making the request". Hand-rolled substring slicing on
        ``code=``/``%40`` — the pattern most community examples use — truncates the
        code or keeps it encoded, and the exchange then fails with an opaque error.
        ``parse_qs`` decodes it correctly.
        """
        _require_nonempty_str(callback_url, "callback_url")
        parsed = urllib.parse.urlparse(callback_url)
        query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        if "error" in query:
            raise SchwabAuthError(
                f"Authorization callback reported error={query['error'][0]!r}"
            )
        codes = query.get("code") or []
        if len(codes) != 1 or not codes[0]:
            raise SchwabAuthError(
                f"Callback URL does not carry exactly one non-empty 'code' parameter "
                f"(found {len(codes)})"
            )
        return codes[0]

    # ---------------------------------------------------------------- lifetime

    def is_access_token_expiring(self, now: Optional[float] = None) -> bool:
        """True when there is no access token, or it expires within the buffer."""
        if not self.state or not self.state.access_token:
            return True
        current = time.time() if now is None else now
        return current >= (self.state.access_expires_at - self.access_buffer_seconds)

    def is_refresh_token_expiring_soon(
        self,
        warn_buffer_seconds: Optional[float] = None,
        now: Optional[float] = None,
    ) -> bool:
        """
        True when the 7-day refresh window closes within ``warn_buffer_seconds``.

        Also true when no refresh token is held at all — in both cases the operator
        must act, and the action is the same manual browser authorization.
        """
        if not self.state or not self.state.refresh_token:
            return True
        buffer_seconds = (
            self.refresh_warn_seconds if warn_buffer_seconds is None else float(warn_buffer_seconds)
        )
        current = time.time() if now is None else now
        return current >= (self.state.refresh_expires_at - buffer_seconds)

    def is_refresh_token_expired(self, now: Optional[float] = None) -> bool:
        if not self.state or not self.state.refresh_token:
            return True
        current = time.time() if now is None else now
        return current >= self.state.refresh_expires_at

    def get_bearer_header(self, now: Optional[float] = None) -> Dict[str, str]:
        """
        Returns the ``Authorization: Bearer`` header for a Trader API call.

        Raises rather than returning a header built from an expired (or
        within-buffer) access token: a caller that ships a dead token gets an
        HTTP 401 from Schwab mid-order, which is far harder to diagnose than a
        local failure at the point the token should have been refreshed.
        """
        if self.state is None or self.is_access_token_expiring(now=now):
            raise SchwabAuthError(
                "Access token is missing or within the refresh buffer; "
                "call refresh_access_token() before issuing API requests"
            )
        return {"Authorization": f"Bearer {self.state.access_token}"}

    # ---------------------------------------------------------------- exchange

    def _post_token_request(
        self,
        payload: Dict[str, str],
        headers: Dict[str, str],
        http_post_fn: HttpPostFn,
        operation: str,
    ) -> Mapping[str, Any]:
        if not callable(http_post_fn):
            raise SchwabAuthError("http_post_fn must be callable")
        try:
            resp = http_post_fn(SCHWAB_TOKEN_URL, payload, headers)
        except Exception as exc:  # transport-layer failure: outcome is unknown
            raise SchwabAmbiguousTokenError(
                f"Schwab {operation} request failed in transport ({type(exc).__name__}: {exc}); "
                f"the request may have been processed — reconcile before retrying"
            ) from exc
        if not isinstance(resp, Mapping):
            raise SchwabAmbiguousTokenError(
                f"Schwab {operation} returned a non-JSON-object body of type "
                f"{type(resp).__name__}; outcome unknown"
            )
        return resp

    @staticmethod
    def _read_expires_in(resp: Mapping[str, Any]) -> float:
        """
        Reads ``expires_in`` strictly.

        Schwab always returns it. Defaulting to 1800 s when it is absent means the
        client invents a lifetime the server never stated: if the real token is
        shorter-lived, every subsequent request 401s for a reason nothing in the
        logs explains.
        """
        if "expires_in" not in resp:
            raise SchwabTokenExchangeError(
                "Schwab token response omitted 'expires_in'; refusing to assume a lifetime"
            )
        raw = resp["expires_in"]
        if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
            raise SchwabTokenExchangeError(
                f"Schwab token response carried a non-numeric 'expires_in' of type "
                f"{type(raw).__name__}"
            )
        try:
            expires_in = float(raw)
        except (TypeError, ValueError) as exc:
            raise SchwabTokenExchangeError(
                "Schwab token response carried an unparsable 'expires_in'"
            ) from exc
        if not math.isfinite(expires_in) or expires_in <= 0:
            raise SchwabTokenExchangeError(
                f"Schwab token response carried a non-positive or non-finite "
                f"'expires_in' ({expires_in})"
            )
        return expires_in

    @staticmethod
    def _read_access_token(resp: Mapping[str, Any]) -> str:
        token = resp.get("access_token")
        if not isinstance(token, str) or not token:
            raise SchwabTokenExchangeError(
                f"Schwab token exchange returned no usable access_token "
                f"({_describe_response(resp)})"
            )
        return token

    def exchange_code(
        self,
        app_key: str,
        app_secret: str,
        auth_code: str,
        redirect_uri: str,
        code_verifier: Optional[str],
        http_post_fn: HttpPostFn,
    ) -> SchwabTokenState:
        """
        Exchanges a one-time authorization code for an access and refresh token.

        ``auth_code`` must already be percent-decoded — use
        :meth:`extract_code_from_callback`. ``code_verifier`` may be ``None``
        (Schwab's documented flow) and is only sent when supplied.

        Starts the 7-day refresh window at "now". This is the only operation that
        may set that anchor.
        """
        headers = {
            "Authorization": _basic_auth_header(app_key, app_secret),
            "Content-Type": "application/x-www-form-urlencoded",
        }
        _require_nonempty_str(auth_code, "auth_code")
        _validate_redirect_uri(redirect_uri)
        if "%" in auth_code:
            logger.warning(
                "auth_code still contains a '%%' escape — Schwab requires the code to "
                "be URL-decoded before exchange; use extract_code_from_callback()"
            )
        payload = {
            "grant_type": "authorization_code",
            "code": auth_code,
            "redirect_uri": redirect_uri,
        }
        if code_verifier is not None:
            payload["code_verifier"] = _require_nonempty_str(code_verifier, "code_verifier")

        resp = self._post_token_request(payload, headers, http_post_fn, "authorization-code exchange")
        access_token = self._read_access_token(resp)
        refresh_token = resp.get("refresh_token")
        if not isinstance(refresh_token, str) or not refresh_token:
            raise SchwabTokenExchangeError(
                f"Schwab authorization-code exchange returned no refresh_token; "
                f"unattended operation is impossible without it ({_describe_response(resp)})"
            )
        expires_in = self._read_expires_in(resp)

        now = time.time()
        return self._commit(
            SchwabTokenState(
                access_token=access_token,
                refresh_token=refresh_token,
                access_expires_at=now + expires_in,
                refresh_expires_at=now + REFRESH_TOKEN_LIFETIME_SECONDS,
                updated_at=now,
            )
        )

    def refresh_access_token(
        self,
        app_key: str,
        app_secret: str,
        http_post_fn: HttpPostFn,
    ) -> SchwabTokenState:
        """
        Exchanges the stored refresh token for a fresh 30-minute access token.

        Two behaviours matter here and both are deliberate:

        * **The 7-day window is never extended.** Schwab's refresh token is valid
          for 7 days from creation and refreshing does not restart that clock. A
          client that re-anchors the deadline on every refresh will never warn, and
          the flow dies without notice mid-week.
        * **A rotated refresh token is stored if one is returned.** Schwab's
          documented response includes a ``refresh_token`` field. Whether the value
          rotates is not stated; storing whatever comes back is correct either way,
          whereas keeping the old value would break if it does rotate.
        """
        if not self.state or not self.state.refresh_token:
            raise SchwabRefreshTokenExpiredError(
                "No refresh token on file — the browser authorization step must be repeated"
            )
        if self.is_refresh_token_expired():
            raise SchwabRefreshTokenExpiredError(
                "Stored refresh token is past its 7-day window; Schwab will reject it with "
                "invalid_client. A human must repeat the browser authorization step."
            )

        headers = {
            "Authorization": _basic_auth_header(app_key, app_secret),
            "Content-Type": "application/x-www-form-urlencoded",
        }
        payload = {
            "grant_type": "refresh_token",
            "refresh_token": self.state.refresh_token,
        }
        resp = self._post_token_request(payload, headers, http_post_fn, "refresh")

        # Schwab rejects an over-age refresh token with invalid_client. Classify on
        # the error field, never by substring-matching a message.
        if resp.get("error") == "invalid_client":
            raise SchwabRefreshTokenExpiredError(
                f"Schwab rejected the refresh token ({_describe_response(resp)}); "
                f"repeat the browser authorization step"
            )
        access_token = self._read_access_token(resp)
        expires_in = self._read_expires_in(resp)

        rotated = resp.get("refresh_token")
        refresh_token = (
            rotated if isinstance(rotated, str) and rotated else self.state.refresh_token
        )

        now = time.time()
        return self._commit(
            SchwabTokenState(
                access_token=access_token,
                refresh_token=refresh_token,
                # Anchor preserved: refreshing does not buy another 7 days.
                access_expires_at=now + expires_in,
                refresh_expires_at=self.state.refresh_expires_at,
                updated_at=now,
            )
        )


__all__ = [
    "SCHWAB_AUTHORIZE_URL",
    "SCHWAB_TOKEN_URL",
    "REFRESH_TOKEN_LIFETIME_SECONDS",
    "SchwabAmbiguousTokenError",
    "SchwabAuthError",
    "SchwabOAuthManager",
    "SchwabPKCEGenerator",
    "SchwabRefreshTokenExpiredError",
    "SchwabTokenExchangeError",
    "SchwabTokenPersistenceError",
    "SchwabTokenState",
]
