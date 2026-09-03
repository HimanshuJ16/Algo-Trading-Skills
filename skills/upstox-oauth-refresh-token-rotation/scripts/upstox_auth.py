"""
upstox-oauth-refresh-token-rotation: Upstox API v2/v3 daily access-token lifecycle
manager -- correct 03:30 IST expiry derivation, single-flight re-authorisation,
0600 atomic persistence, and read-only-token misuse guards.

IMPORTANT -- READ BEFORE USING THIS MODULE:

Upstox does NOT issue refresh tokens and has no ``grant_type=refresh_token`` exchange.
Upstox staff state it directly: "We do not support refresh tokens. Our access token is
valid until 3:30 AM and expires after that." (Upstox Community, 01-Aug-2025). The Get
Token API documents only ``grant_type=authorization_code`` and its response contains no
``refresh_token`` field.

An earlier revision of this module implemented a ``rotate_refresh_token()`` exchange
against ``https://api.upstox.com/v2/login/auth/token``. Neither that endpoint nor that
grant exists. ``rotate_refresh_token`` is retained below solely to raise an explanatory
error, so that any caller (human or agent) still wired to it fails loudly with the
reason rather than silently against a 404.

What is real, and what this module implements:

* The access token expires at a fixed wall-clock instant -- 03:30 IST the following day
  -- regardless of when it was issued. It is NOT valid for a fixed number of seconds,
  and the token response carries no ``expires_in``. Deriving expiry as ``now + 86400``
  overstates validity by up to ~24h and leaves a bot believing it is authenticated
  through an entire trading day during which every call returns 401 UDAPI100050.
* Re-acquisition is therefore a daily event, by one of three documented paths:
  authorization-code OAuth (human at a browser), the Access Token Request API
  (user approves a push/WhatsApp prompt; token arrives at a notifier webhook), or a
  read-only Analytics Token (1-year, cannot place orders).
* Because every path is expensive -- two of them wake a human -- concurrent workers must
  be funnelled through a single-flight lock, and the acquired token must be persisted
  atomically before the lock is released.

JURISDICTION NOTE (India, SEBI/NSE): the absence of a long-lived refresh credential is
not an Upstox oversight. NSE circular NSE/INVG/67858 (05-May-2025), Annexure para A.8,
requires that "All API sessions shall be compulsorily logged out every day before the
start of the next trading day." A design premised on a session surviving across days is
not achievable compliantly for an Indian broker. See references/standards.md.

Broker APIs change without notice -- re-verify endpoints and expiry rules against
Upstox's own documentation before relying on any constant in this file.
"""
from dataclasses import dataclass, field
import datetime
import json
import logging
import os
import threading
import time
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

# India has observed no daylight saving time since 1945, so IST is a fixed UTC+05:30
# offset. A fixed offset is exact here and avoids depending on the `tzdata` package,
# which is not present by default on Windows hosts (where `ZoneInfo("Asia/Kolkata")`
# raises ZoneInfoNotFoundError). Do not copy this shortcut to a DST-observing venue.
IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

# Upstox: "The access_token ... has a specific validity period that lasts until 3:30 AM
# the following day, regardless of the time it was generated."
SESSION_EXPIRY_HOUR_IST = 3
SESSION_EXPIRY_MINUTE_IST = 30

# Upper bound on any single Upstox session: a token issued one second after 03:30 IST
# lives until 03:30 IST the next day. Nothing may be configured to exceed it.
MAX_SESSION_SECONDS = 86400.0

# Documented endpoints. The authorization-code exchange is v2; the user-approval token
# request is v3. Both are POST, but with *different* body encodings -- see below.
TOKEN_ENDPOINT_V2 = "https://api.upstox.com/v2/login/authorization/token"
AUTHORIZE_DIALOG_V2 = "https://api.upstox.com/v2/login/authorization/dialog"
TOKEN_REQUEST_ENDPOINT_V3 = "https://api.upstox.com/v3/login/auth/token/request/{client_id}"

# Error codes worth branching on rather than treating as generic failures.
ERROR_INVALID_TOKEN = "UDAPI100050"          # 401 - invalid/expired token
ERROR_INVALID_CREDENTIALS = "UDAPI100016"    # 401 - bad client_id/client_secret/code
ERROR_EXTENDED_TOKEN_FORBIDDEN = "UDAPI100067"  # 403 - API not permitted with extended_token
ERROR_CLIENT_INACTIVE = "UDAPI100073"        # 403 - client_id inactive

# Epoch-millisecond plausibility window, used to catch the classic seconds/milliseconds
# mix-up. 1e12 ms is 2001-09-09; 1e13 ms is 2286-11-20.
_MIN_EPOCH_MILLIS = 1_000_000_000_000
_MAX_EPOCH_MILLIS = 10_000_000_000_000


class UpstoxAuthError(RuntimeError):
    """Raised when an Upstox token cannot be acquired, parsed, or safely used.

    ``error_code`` carries Upstox's own ``error_code`` (e.g. ``UDAPI100050``) when the
    failure came from an API error envelope, so callers can branch on the broker's code
    instead of substring-matching a message.
    """

    def __init__(self, message: str, error_code: Optional[str] = None) -> None:
        super().__init__(message)
        self.error_code = error_code


def next_session_expiry(now: Optional[datetime.datetime] = None) -> float:
    """Epoch seconds of the next 03:30 IST boundary strictly after ``now``.

    This is the *only* correct way to date an Upstox access token: the token response
    carries no ``expires_in``, and validity is a fixed wall-clock instant rather than a
    duration. Upstox's own worked examples:

      * issued 20:00 IST Tuesday -> expires 03:30 IST Wednesday (~7.5h of validity)
      * issued 02:30 IST Wednesday -> expires 03:30 IST Wednesday (~1h of validity)

    ``now`` is injectable (any aware datetime, any zone; naive values are read as UTC)
    so callers and tests can evaluate boundaries without patching the process clock.
    """
    if now is None:
        now = datetime.datetime.now(datetime.timezone.utc)
    elif now.tzinfo is None:
        # A naive datetime here is almost always a bug. Assume UTC rather than local
        # time: local-time interpretation silently shifts the boundary by the host's
        # offset, which is exactly the failure this function exists to prevent.
        logger.warning("next_session_expiry received a naive datetime; assuming UTC.")
        now = now.replace(tzinfo=datetime.timezone.utc)

    now_ist = now.astimezone(IST)
    boundary = now_ist.replace(
        hour=SESSION_EXPIRY_HOUR_IST,
        minute=SESSION_EXPIRY_MINUTE_IST,
        second=0,
        microsecond=0,
    )
    if boundary <= now_ist:
        boundary += datetime.timedelta(days=1)
    return boundary.timestamp()


def parse_upstox_epoch_millis(value: Any) -> float:
    """Convert an Upstox epoch-millisecond timestamp to epoch seconds.

    Upstox delivers ``expires_at``/``issued_at`` as milliseconds, and as *strings*, in
    the Access Token Request notifier payload (e.g. ``"expires_at": "1731448800000"``).
    Feeding that straight into a seconds-based comparison dates the token to the year
    56000 and disables every expiry check downstream, so the magnitude is validated
    rather than trusted.

    Raises ValueError on anything that is not a plausible epoch-millisecond value --
    including a value that looks like epoch *seconds*, which is the common mix-up.
    """
    if isinstance(value, bool):  # bool is an int subclass; never a timestamp
        raise ValueError(f"expected an epoch-millisecond timestamp, got bool {value!r}")
    if isinstance(value, str):
        value = value.strip()
    try:
        millis = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"not a numeric epoch-millisecond timestamp: {value!r}") from None
    if not _MIN_EPOCH_MILLIS <= millis < _MAX_EPOCH_MILLIS:
        raise ValueError(
            f"{value!r} is outside the plausible epoch-millisecond range "
            f"[{_MIN_EPOCH_MILLIS}, {_MAX_EPOCH_MILLIS}); a seconds-valued timestamp "
            "passed here would silently disable expiry checking"
        )
    return millis / 1000.0


def raise_for_upstox_error(payload: Dict[str, Any]) -> None:
    """Raise :class:`UpstoxAuthError` if ``payload`` is an Upstox error envelope.

    Upstox errors take the shape ``{"status": "error", "errors": [{"error_code": ...,
    "message": ...}]}``. The ``errorCode``/``message`` camelCase spelling is deprecated
    but still seen in the wild, so both are read. HTTP 200 is not proof of success for
    this API family -- always run the decoded body through here.
    """
    if payload.get("status") != "error":
        return
    errors = payload.get("errors") or []
    first = errors[0] if isinstance(errors, list) and errors and isinstance(errors[0], dict) else {}
    code = first.get("error_code") or first.get("errorCode")
    message = first.get("message") or "Upstox returned an error with no message"
    raise UpstoxAuthError(f"Upstox error {code or '<no code>'}: {message}", error_code=code)


def build_authorization_code_form(
    code: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
) -> Dict[str, str]:
    """Build the Get Token request body for the authorization-code exchange.

    POST to :data:`TOKEN_ENDPOINT_V2` with ``Accept: application/json`` and
    ``Content-Type: application/x-www-form-urlencoded``. This body is **form-encoded,
    not JSON** -- posting it as JSON is rejected. (The v3 Access Token Request API is
    the opposite: it takes a JSON body. Do not generalise one to the other.)

    ``code`` is single-use and short-lived; a retry after a failed exchange needs a new
    authorization code, not a replay of this one.
    """
    for name, value in (
        ("code", code),
        ("client_id", client_id),
        ("client_secret", client_secret),
        ("redirect_uri", redirect_uri),
    ):
        if not value:
            raise ValueError(f"{name} is required for the authorization_code exchange")
    return {
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }


@dataclass
class UpstoxTokenState:
    """A single Upstox access token plus everything needed to decide if it is usable.

    ``expires_at`` and ``issued_at`` are epoch *seconds* (the module boundary converts
    Upstox's milliseconds once, at parse time, so nothing downstream has to remember).
    ``read_only`` marks a token that cannot place, modify, or cancel orders -- an
    Analytics/extended token. ``source`` records which documented path produced the
    token, which is the first thing you want in a post-incident log.
    """

    access_token: str
    expires_at: float
    issued_at: float = field(default_factory=time.time)
    source: str = "authorization_code"
    read_only: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "access_token": self.access_token,
            "expires_at": self.expires_at,
            "issued_at": self.issued_at,
            "source": self.source,
            "read_only": self.read_only,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "UpstoxTokenState":
        access_token = data.get("access_token") or ""
        if not access_token:
            raise ValueError("persisted Upstox token state has no access_token")
        if "expires_at" not in data:
            # Refusing is deliberate. Substituting a default here would resurrect the
            # "assume ~24h" bug that this module exists to remove.
            raise ValueError("persisted Upstox token state has no expires_at")
        return cls(
            access_token=access_token,
            expires_at=float(data["expires_at"]),
            issued_at=float(data.get("issued_at", 0.0)),
            source=str(data.get("source", "unknown")),
            read_only=bool(data.get("read_only", False)),
        )


def state_from_token_response(
    payload: Dict[str, Any],
    now: Optional[datetime.datetime] = None,
) -> UpstoxTokenState:
    """Build token state from a Get Token (authorization_code) response.

    The response has no ``expires_in``; expiry is derived from the 03:30 IST rule via
    :func:`next_session_expiry`. The response also carries an ``extended_token``, which
    is deliberately ignored here -- it is a read-only credential with a different
    lifetime and must not be conflated with the tradeable access token. Wrap it with
    :func:`state_for_read_only_token` if you actually want it.
    """
    raise_for_upstox_error(payload)
    access_token = payload.get("access_token") or ""
    if not access_token:
        raise UpstoxAuthError("Upstox token response contained no access_token")
    issued = now or datetime.datetime.now(datetime.timezone.utc)
    if issued.tzinfo is None:
        # Match next_session_expiry: a naive value is read as UTC, never as local time.
        issued = issued.replace(tzinfo=datetime.timezone.utc)
    return UpstoxTokenState(
        access_token=access_token,
        expires_at=next_session_expiry(issued),
        issued_at=issued.timestamp(),
        source="authorization_code",
        read_only=False,
    )


def state_from_notifier_payload(payload: Dict[str, Any]) -> UpstoxTokenState:
    """Build token state from an Access Token Request notifier webhook delivery.

    The v3 Access Token Request API does not return the token; on user approval Upstox
    POSTs it to the app's registered ``notifier_url`` as::

        {"client_id": ..., "user_id": ..., "access_token": ..., "token_type": "Bearer",
         "expires_at": "1731448800000", "issued_at": "1731412800000",
         "message_type": "access_token"}

    ``expires_at`` here is authoritative -- prefer it over the derived boundary. (As a
    sanity check on the rule: Upstox's own documented example, 1731448800000 ms, is
    2024-11-13 03:30:00 IST.)

    Treat this webhook as untrusted input: verify the delivery is genuinely from Upstox
    at the transport layer before calling this, and check ``client_id``/``user_id``
    match the account you expect. This function validates shape, not authenticity.
    """
    message_type = payload.get("message_type")
    if message_type != "access_token":
        raise UpstoxAuthError(
            f"notifier payload is not an access_token delivery (message_type={message_type!r})"
        )
    access_token = payload.get("access_token") or ""
    if not access_token:
        raise UpstoxAuthError("notifier payload contained no access_token")
    try:
        expires_at = parse_upstox_epoch_millis(payload["expires_at"])
        issued_at = parse_upstox_epoch_millis(payload.get("issued_at", payload["expires_at"]))
    except KeyError as e:
        raise UpstoxAuthError(f"notifier payload missing {e.args[0]!r}") from None
    except ValueError as e:
        raise UpstoxAuthError(f"notifier payload has an unusable timestamp: {e}") from None
    return UpstoxTokenState(
        access_token=access_token,
        expires_at=expires_at,
        issued_at=issued_at,
        source="token_request_webhook",
        read_only=False,
    )


def state_for_read_only_token(access_token: str, expires_at: float) -> UpstoxTokenState:
    """Wrap an Analytics/extended token, flagged read-only.

    The Analytics Token is valid for one year and is generated from the Developer Apps
    console rather than via OAuth, but it supports GET requests only: using it to place,
    modify, or cancel an order fails with 403 ``UDAPI100067``. Marking it here lets
    :meth:`UpstoxTokenManager.get_valid_access_token` refuse the misuse locally instead
    of discovering it from a rejected order.
    """
    if not access_token:
        raise ValueError("access_token is required")
    return UpstoxTokenState(
        access_token=access_token,
        expires_at=float(expires_at),
        issued_at=time.time(),
        source="analytics_token",
        read_only=True,
    )


class UpstoxTokenManager:
    """Holds the current Upstox access token, persists it, and serialises re-auth.

    The lock is not protecting an HTTP refresh -- there is no refresh. It is protecting
    the *re-authorisation* callback, every documented form of which is expensive:
    the authorization-code path needs a human at a browser, and the Access Token Request
    path pushes an approval prompt to the user's phone and WhatsApp. Ten worker threads
    noticing expiry at 03:30 IST must produce one prompt, not ten.
    """

    def __init__(
        self,
        token_file_path: str = "upstox_tokens.json",
        buffer_seconds: float = 900.0,
    ) -> None:
        # An Upstox session never exceeds one 03:30-to-03:30 span, so a buffer at or
        # above a full day makes every token look expiring forever -- which for the
        # approval flow is an unbounded stream of prompts to the user's phone rather
        # than a working bot. Reject it here instead of discovering it at 03:30 IST.
        if not 0 <= buffer_seconds < MAX_SESSION_SECONDS:
            raise ValueError(
                f"buffer_seconds must be in [0, {MAX_SESSION_SECONDS}); got {buffer_seconds}. "
                "A buffer at or above one full session marks every token as expiring and "
                "produces a re-authentication loop."
            )
        self.token_file_path = token_file_path
        self.buffer_seconds = buffer_seconds
        self._lock = threading.Lock()
        self.state: Optional[UpstoxTokenState] = self._load_from_storage()

    def _load_from_storage(self) -> Optional[UpstoxTokenState]:
        if not os.path.exists(self.token_file_path):
            return None
        try:
            with open(self.token_file_path, "r", encoding="utf-8") as f:
                return UpstoxTokenState.from_dict(json.load(f))
        except (OSError, ValueError) as e:
            # ValueError covers json.JSONDecodeError and from_dict's own validation.
            # Returning None is correct: an unreadable cache means "no token", which
            # triggers re-auth. It must never be mistaken for a *usable* token.
            logger.warning(
                "Could not load Upstox token state from '%s' (%s); treating as no token.",
                self.token_file_path,
                e,
            )
            return None

    def _save_to_storage(self, state: UpstoxTokenState) -> None:
        """Persist token state atomically, with 0600 permissions.

        Failures propagate. Swallowing them would leave the process holding a token that
        exists only in RAM: the bot works until it restarts, then re-authenticates at
        whatever hour the restart happens -- which for the approval flow means waking the
        user, and for the OAuth flow means an unattended bot that simply cannot start.
        """
        tmp_path = f"{self.token_file_path}.tmp"
        # Open with 0600 from the start; chmod-after leaves a window in which the
        # plaintext bearer token is world-readable.
        fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(state.to_dict(), f, indent=2)
            os.replace(tmp_path, self.token_file_path)
        except BaseException:
            try:
                os.remove(tmp_path)
            except OSError:  # pragma: no cover - nothing left to clean up
                pass
            raise
        logger.info("Upstox token state persisted to '%s'.", self.token_file_path)

    def is_token_expiring(self, now: Optional[float] = None) -> bool:
        """True when there is no token, or it dies within ``buffer_seconds``.

        The buffer is not a "refresh early" optimisation -- re-auth is not free and may
        need a human. It exists so a strategy never *begins* a cycle on a token that
        will die mid-flight, which would surface as 401 UDAPI100050 between a submitted
        order and its status poll.
        """
        if not self.state or not self.state.access_token:
            return True
        current = time.time() if now is None else now
        return current >= (self.state.expires_at - self.buffer_seconds)

    def get_valid_access_token(
        self,
        reauth_fn: Callable[[], UpstoxTokenState],
        require_write: bool = False,
    ) -> str:
        """Return a usable access token, re-authenticating once if needed.

        ``reauth_fn`` performs whichever documented acquisition path the deployment uses
        and returns the resulting :class:`UpstoxTokenState` (build it with
        :func:`state_from_token_response` or :func:`state_from_notifier_payload`). It is
        invoked at most once across concurrent callers.

        Set ``require_write=True`` at order-placement call sites: it rejects a read-only
        Analytics/extended token locally rather than letting the order fail with 403
        UDAPI100067 at the exchange gateway.
        """
        state = self.state
        if state is not None and not self.is_token_expiring():
            self._check_write_permission(state, require_write)
            return state.access_token

        with self._lock:
            # Re-check inside the lock: while this thread waited, the holder may have
            # already completed re-auth. Without this, the lock serialises the prompts
            # instead of collapsing them, and the user still gets N approval pushes.
            if self.state is not None and not self.is_token_expiring():
                self._check_write_permission(self.state, require_write)
                return self.state.access_token

            logger.info("Upstox access token expired or absent; invoking re-authentication.")
            new_state = reauth_fn()
            if not isinstance(new_state, UpstoxTokenState):
                raise UpstoxAuthError(
                    f"reauth_fn must return UpstoxTokenState, got {type(new_state).__name__}"
                )
            if not new_state.access_token:
                raise UpstoxAuthError("re-authentication returned an empty access_token")
            if new_state.expires_at <= time.time():
                # A token that is already dead is a configuration error (commonly a
                # seconds/milliseconds mix-up). Failing here beats a hot re-auth loop
                # that spams the user's phone with approval prompts.
                raise UpstoxAuthError(
                    "re-authentication returned an already-expired token "
                    f"(expires_at={new_state.expires_at}); check the timestamp units"
                )

            # Persist before publishing to self.state and before releasing the lock, so
            # a crash cannot leave a token that this process is using but no restart can
            # recover.
            self._save_to_storage(new_state)
            self.state = new_state
            self._check_write_permission(new_state, require_write)
            return new_state.access_token

    @staticmethod
    def _check_write_permission(state: UpstoxTokenState, require_write: bool) -> None:
        if require_write and state.read_only:
            raise UpstoxAuthError(
                f"token from source '{state.source}' is read-only and cannot place, modify, "
                "or cancel orders (Upstox would reject with 403 UDAPI100067)",
                error_code=ERROR_EXTENDED_TOKEN_FORBIDDEN,
            )

    def rotate_refresh_token(self, *args: Any, **kwargs: Any) -> UpstoxTokenState:
        """Removed: Upstox issues no refresh token. Always raises.

        Kept as an explicit failure rather than deleted so that callers still wired to
        the previous (incorrect) API get the reason instead of an AttributeError or a
        404 against an endpoint that never existed.
        """
        raise UpstoxAuthError(
            "Upstox does not support refresh tokens: there is no grant_type=refresh_token "
            "and no https://api.upstox.com/v2/login/auth/token endpoint. The access token "
            "expires at 03:30 IST and must be re-acquired daily via the authorization-code "
            "flow, the v3 Access Token Request approval flow, or a read-only Analytics "
            "Token. Use get_valid_access_token(reauth_fn) instead."
        )
