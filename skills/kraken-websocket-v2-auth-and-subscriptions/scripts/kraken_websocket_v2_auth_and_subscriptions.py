"""kraken-websocket-v2-auth-and-subscriptions: sign the Kraken REST call that
mints a WebSocket token, and build correctly-routed Kraken **WebSocket v2**
subscribe frames from a validated channel registry.

Scope
-----
This module is an **offline frame builder and pre-flight validator**. It does no
network I/O: it produces the exact JSON your transport sends, plus an audit
record of why that frame is (or is not) valid. Keeping it I/O-free is what makes
every check here unit-testable and deterministic.

It is *not* a WebSocket client. Reconnect handling, subscription bookkeeping
across reconnects, and heartbeat/ping management live in
``websocket-reconnect-without-duplicate-subscriptions`` and
``websocket-reconnection-with-state-recovery``.

What the 15-minute token lifetime actually means
------------------------------------------------
This is the single most misread fact about Kraken's WebSocket auth. Kraken's
``GetWebSocketsToken`` returns ``expires: 900``, and the REST reference states:

    "The token should be used within 15 minutes of creation, but it does not
    expire once a successful Websockets connection and private subscription has
    been made and is maintained."

So 900 seconds is a **use-by window on the token, not a session TTL**. A bot
holding an authenticated connection with a live private subscription must *not*
tear it down and resubscribe every 15 minutes — that churn buys nothing and
walks into Kraken's connection rate limit. The window binds at the moment the
token is *used*, which is exactly when a subscribe frame is built. Every frame
this engine emits is such a use, so the freshness gate applies on every call.

Channel routing is not a public/private binary
----------------------------------------------
Kraken v2 has three endpoints, not two, and the split does not follow "is this
market data or account data":

* ``wss://ws.kraken.com/v2``      — public channels, no token.
* ``wss://ws-auth.kraken.com/v2`` — ``executions`` and ``balances``, token required.
* ``wss://ws-l3.kraken.com/v2``   — ``level3``, which is order book data yet
  **authenticated**: it needs a token *and* its own host.

Order entry (``add_order``, ``cancel_order``, ...) is a set of **request
methods**, not subscribable channels. ``{"method": "subscribe", "params":
{"channel": "add_order"}}`` is not a valid frame and Kraken rejects it; the
correct shape is ``{"method": "add_order", "params": {...}}``. The channel
registry below is the authority, and anything absent from it is rejected rather
than guessed at.

See ``references/standards.md`` for the per-channel parameter table and sources.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import logging
import math
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, List, Optional, Tuple

logger = logging.getLogger(__name__)

__all__ = [
    "PUBLIC_WS_URL_V2",
    "AUTH_WS_URL_V2",
    "LEVEL3_WS_URL_V2",
    "TOKEN_USE_BY_SECONDS",
    "DEFAULT_REFRESH_THRESHOLD_SECONDS",
    "BOOK_DEPTHS",
    "LEVEL3_DEPTHS",
    "OHLC_INTERVALS",
    "CHANNEL_REGISTRY",
    "V2_REQUEST_METHODS",
    "STATUS_FRAME_CREATED",
    "STATUS_MISSING_WS_TOKEN",
    "STATUS_TOKEN_INACTIVE",
    "STATUS_TOKEN_REFRESH_REQUIRED",
    "STATUS_TOKEN_EXPIRED",
    "STATUS_TOKEN_CLOCK_SKEW",
    "STATUS_INVALID_CHANNEL",
    "STATUS_MISSING_SYMBOL",
    "STATUS_INVALID_DEPTH",
    "KrakenWsV2Error",
    "KrakenV2ChannelSpec",
    "KrakenNonceGenerator",
    "KrakenWsTokenState",
    "KrakenWsV2SubscriptionSpec",
    "KrakenWsV2Report",
    "KrakenWsV2ManagerEngine",
    "redact_ws_token",
]

# --- Endpoints -------------------------------------------------------------
PUBLIC_WS_URL_V2 = "wss://ws.kraken.com/v2"
AUTH_WS_URL_V2 = "wss://ws-auth.kraken.com/v2"
#: ``level3`` is authenticated but does **not** live on the ws-auth host.
LEVEL3_WS_URL_V2 = "wss://ws-l3.kraken.com/v2"

# --- Token lifetime --------------------------------------------------------
#: The ``expires`` value returned by ``GetWebSocketsToken``: a use-by window,
#: not a session TTL. See the module docstring.
TOKEN_USE_BY_SECONDS = 900.0
#: Default margin before the use-by boundary. Leaves 180s for the REST refresh
#: round-trip, the reconnect, and the resubscribe to all complete.
DEFAULT_REFRESH_THRESHOLD_SECONDS = 720.0
#: Tolerance for benign clock jitter between the token issuer and this host.
_CLOCK_SKEW_TOLERANCE_SECONDS = 1.0
#: Default ``depth`` on :class:`KrakenWsV2SubscriptionSpec`, mirroring Kraken's
#: own default for ``book``.
_DEFAULT_SPEC_DEPTH = 10
#: Kraken private keys decode to 64 bytes. A different length is a strong hint
#: the secret was truncated or double-encoded; warned about, never fatal.
_EXPECTED_SECRET_BYTES = 64

# --- Report statuses -------------------------------------------------------
STATUS_FRAME_CREATED = "SUBSCRIPTION_FRAME_CREATED"
STATUS_MISSING_WS_TOKEN = "MISSING_WS_TOKEN"
STATUS_TOKEN_INACTIVE = "TOKEN_INACTIVE"
STATUS_TOKEN_REFRESH_REQUIRED = "TOKEN_REFRESH_REQUIRED"
STATUS_TOKEN_EXPIRED = "TOKEN_EXPIRED"
STATUS_TOKEN_CLOCK_SKEW = "TOKEN_CLOCK_SKEW"
STATUS_INVALID_CHANNEL = "INVALID_CHANNEL"
STATUS_MISSING_SYMBOL = "MISSING_SYMBOL"
STATUS_INVALID_DEPTH = "INVALID_DEPTH"

#: Allowed ``depth`` values differ per channel — ``book`` and ``level3`` are not
#: interchangeable.
BOOK_DEPTHS: FrozenSet[int] = frozenset({10, 25, 100, 500, 1000})
LEVEL3_DEPTHS: FrozenSet[int] = frozenset({10, 100, 1000})
OHLC_INTERVALS: FrozenSet[int] = frozenset({1, 5, 15, 30, 60, 240, 1440, 10080, 21600})


class KrakenWsV2Error(ValueError):
    """Raised for caller errors that can never produce a usable frame or
    signature — a malformed secret, a non-finite timestamp, a blank path."""


@dataclass(frozen=True)
class KrakenV2ChannelSpec:
    """One row of the Kraken WS v2 channel registry."""

    name: str
    ws_url: str
    requires_token: bool = False
    requires_symbol: bool = False
    allowed_depths: FrozenSet[int] = frozenset()


#: The authoritative set of *subscribable* v2 channels. Anything not here is
#: rejected as ``INVALID_CHANNEL`` rather than passed through to the venue.
CHANNEL_REGISTRY: Dict[str, KrakenV2ChannelSpec] = {
    spec.name: spec
    for spec in (
        KrakenV2ChannelSpec("book", PUBLIC_WS_URL_V2, requires_symbol=True,
                            allowed_depths=BOOK_DEPTHS),
        KrakenV2ChannelSpec("ticker", PUBLIC_WS_URL_V2, requires_symbol=True),
        KrakenV2ChannelSpec("trade", PUBLIC_WS_URL_V2, requires_symbol=True),
        KrakenV2ChannelSpec("ohlc", PUBLIC_WS_URL_V2, requires_symbol=True),
        KrakenV2ChannelSpec("instrument", PUBLIC_WS_URL_V2),
        KrakenV2ChannelSpec("executions", AUTH_WS_URL_V2, requires_token=True),
        KrakenV2ChannelSpec("balances", AUTH_WS_URL_V2, requires_token=True),
        KrakenV2ChannelSpec("level3", LEVEL3_WS_URL_V2, requires_token=True,
                            requires_symbol=True, allowed_depths=LEVEL3_DEPTHS),
    )
}

#: v2 *request methods*. These are top-level ``method`` values, never a
#: ``params.channel``. Naming them explicitly turns a silent malformed frame
#: into an actionable rejection.
V2_REQUEST_METHODS: FrozenSet[str] = frozenset(
    {"add_order", "cancel_order", "subscribe", "unsubscribe", "ping"}
)


def redact_ws_token(text: str, token: Optional[str]) -> str:
    """Replace a live WS token with a fingerprint so audit text is loggable.

    A WebSocket token is a bearer credential for the account's ``executions``
    and order-entry surface. It must never reach a log line, a ticket, or a
    stored audit record verbatim.
    """
    if not token:
        return text
    fingerprint = hashlib.sha256(token.encode("utf-8")).hexdigest()[:8]
    return text.replace(token, f"<ws_token:{fingerprint}>")


class KrakenNonceGenerator:
    """Thread-safe source of strictly increasing millisecond nonces.

    Kraken requires "an always increasing, unsigned 64-bit integer for each
    request" per API key, and answers a regression with ``EAPI:Invalid nonce``;
    repeated invalid nonces earn a temporary ban. A bare
    ``int(time.time() * 1000)`` regresses on an NTP step backwards and collides
    whenever two requests land in the same millisecond, so the counter is
    latched: it never returns a value it has already returned.

    One generator instance must be shared by every caller using the same API
    key. Two processes signing with one key cannot be ordered by this class —
    give each process its own key, or raise the account's nonce window.
    """

    def __init__(self, start_nonce: Optional[int] = None) -> None:
        self._lock = threading.Lock()
        self._last = int(start_nonce) if start_nonce is not None else 0

    def next_nonce(self) -> int:
        """Return the next nonce, strictly greater than every prior one."""
        with self._lock:
            candidate = int(time.time() * 1000)
            self._last = candidate if candidate > self._last else self._last + 1
            return self._last


@dataclass
class KrakenWsTokenState:
    """A WS token plus the wall-clock instant it was minted.

    ``expires_in_seconds`` mirrors the REST ``expires`` field. It is the window
    in which the token must be *used* to establish a private subscription — not
    a countdown on an already-established one.
    """

    token: str
    created_timestamp_epoch: float
    expires_in_seconds: float = TOKEN_USE_BY_SECONDS
    is_active: bool = True


@dataclass
class KrakenWsV2SubscriptionSpec:
    """A requested subscription, before validation against the registry."""

    channel: str
    symbols: Optional[List[str]] = None      # v2 format, e.g. ['BTC/USD']
    depth: Optional[int] = _DEFAULT_SPEC_DEPTH  # book / level3 only
    snap_orders: bool = True                 # executions only
    snap_trades: bool = True                 # executions only
    snapshot: Optional[bool] = None          # emitted only when set
    order_status: Optional[bool] = None      # executions only, emitted when set
    interval: Optional[int] = None           # ohlc only
    req_id: Optional[int] = None             # echoed back on the ack


@dataclass
class KrakenWsV2Report:
    """Audit record for one frame-build attempt.

    ``subscription_json_frame`` carries the live token for private channels.
    Send it; do not log it. ``audit_notes`` is the redacted, loggable view.
    """

    channel: str
    is_private_channel: bool
    ws_url: str
    subscription_json_frame: Dict[str, Any]
    token_age_seconds: float
    is_token_valid: bool
    status: str
    audit_notes: str
    token_expires_in_seconds: Optional[float] = None
    warnings: List[str] = field(default_factory=list)


class KrakenWsV2ManagerEngine:
    """Signs Kraken private REST requests and builds validated WS v2 subscribe
    frames.

    The engine is stateless per call and holds no connection. Pass
    ``current_time_epoch`` explicitly for reproducible audits.
    """

    def __init__(
        self,
        api_key: str,
        api_secret_b64: str,
        *,
        refresh_threshold_seconds: float = DEFAULT_REFRESH_THRESHOLD_SECONDS,
        token_use_by_seconds: float = TOKEN_USE_BY_SECONDS,
    ) -> None:
        """Credentials are required, never defaulted.

        An auth engine that constructs successfully with placeholder credentials
        signs every request with the wrong key and surfaces the mistake only as
        an opaque ``EAPI:Invalid signature`` from the venue.
        """
        if not api_key or not api_key.strip():
            raise KrakenWsV2Error("api_key must be a non-empty Kraken API key.")
        if not api_secret_b64 or not api_secret_b64.strip():
            raise KrakenWsV2Error(
                "api_secret_b64 must be the Base64 private key from Kraken."
            )
        if not 0.0 < refresh_threshold_seconds <= token_use_by_seconds:
            raise KrakenWsV2Error(
                f"refresh_threshold_seconds must fall in (0, {token_use_by_seconds}]; "
                f"got {refresh_threshold_seconds}."
            )

        self.api_key = api_key
        self.api_secret_b64 = api_secret_b64
        self.refresh_threshold_seconds = float(refresh_threshold_seconds)
        self.token_use_by_seconds = float(token_use_by_seconds)
        self.public_ws_url = PUBLIC_WS_URL_V2
        self.private_ws_url = AUTH_WS_URL_V2
        self.level3_ws_url = LEVEL3_WS_URL_V2

    # -- REST signing -------------------------------------------------------

    def _decode_api_secret(self) -> bytes:
        """Base64-decode the private key, failing loudly on a corrupt secret.

        The alternative — falling back to the raw string as the HMAC key — is
        the worst possible behaviour: it yields a structurally valid signature
        computed with the *wrong key*, so the only symptom is an HTTP 401
        ``EAPI:Invalid signature`` with nothing pointing at the secret. Surface
        it at signing time instead.

        Surrounding whitespace (a secret read from a file or wrapped in a
        config) is stripped before validation, so tolerant handling of layout is
        kept while genuine corruption is rejected.
        """
        candidate = "".join(self.api_secret_b64.split())
        try:
            secret_bytes = base64.b64decode(candidate, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise KrakenWsV2Error(
                "api_secret_b64 is not valid Base64. Pass the private key exactly as "
                f"Kraken issued it; do not pre-decode it. ({exc})"
            ) from exc
        if not secret_bytes:
            raise KrakenWsV2Error("api_secret_b64 decoded to zero bytes.")
        if len(secret_bytes) != _EXPECTED_SECRET_BYTES:
            logger.warning(
                "Kraken private key decoded to %d bytes, expected %d - check for a "
                "truncated or double-encoded secret.",
                len(secret_bytes), _EXPECTED_SECRET_BYTES,
            )
        return secret_bytes

    def generate_kraken_rest_hmac_signature(
        self,
        url_path: str,
        nonce: str,
        post_data: str,
    ) -> str:
        """Compute the ``API-Sign`` header for a Kraken private REST request.

        ``API-Sign = Base64(HMAC-SHA512(url_path + SHA256(nonce + post_data),
        Base64Decode(api_secret)))``

        ``url_path`` is the path only — ``/0/private/GetWebSocketsToken`` — not
        the full URL, and ``post_data`` must be the request body **byte-for-byte
        as sent**. Signing a re-serialised copy of the body (different key
        order, different float formatting) produces a signature the venue cannot
        reproduce.

        Verified against Kraken's own published example; see
        ``references/standards.md``.
        """
        if not url_path.startswith("/"):
            raise KrakenWsV2Error(
                f"url_path must be the request path starting with '/', got {url_path!r}."
            )
        nonce_str = str(nonce).strip()
        if not nonce_str.isdigit():
            raise KrakenWsV2Error(
                f"nonce must be an unsigned integer string, got {nonce!r}."
            )
        # The signed payload and the posted body must agree. A body that does
        # not carry this nonce is signed correctly and rejected anyway.
        if nonce_str not in post_data:
            raise KrakenWsV2Error(
                "post_data does not contain the nonce being signed. Kraken requires the "
                "nonce as a payload parameter, and the signature must cover the body "
                "actually sent."
            )

        secret_bytes = self._decode_api_secret()
        sha256_digest = hashlib.sha256((nonce_str + post_data).encode("utf-8")).digest()
        hmac_msg = url_path.encode("utf-8") + sha256_digest
        signature = hmac.new(secret_bytes, hmac_msg, hashlib.sha512).digest()
        return base64.b64encode(signature).decode("utf-8")

    # -- Subscribe frame construction ---------------------------------------

    def _evaluate_token(
        self,
        token_state: Optional[KrakenWsTokenState],
        now: float,
    ) -> Tuple[float, Optional[float], Optional[str], str]:
        """Return ``(age, remaining, rejection_status, note)`` for a token.

        ``rejection_status`` is ``None`` when the token may be used.
        """
        if token_state is None or not token_state.token or not token_state.token.strip():
            return 0.0, None, STATUS_MISSING_WS_TOKEN, (
                "private channel requires an active WebSocket token from "
                "/0/private/GetWebSocketsToken"
            )
        if not token_state.is_active:
            return 0.0, None, STATUS_TOKEN_INACTIVE, (
                "token is flagged inactive; mint a replacement before subscribing"
            )
        if not math.isfinite(token_state.created_timestamp_epoch):
            raise KrakenWsV2Error(
                "KrakenWsTokenState.created_timestamp_epoch must be finite; got "
                f"{token_state.created_timestamp_epoch!r}."
            )
        if (not math.isfinite(token_state.expires_in_seconds)
                or token_state.expires_in_seconds <= 0):
            raise KrakenWsV2Error(
                "KrakenWsTokenState.expires_in_seconds must be finite and positive; got "
                f"{token_state.expires_in_seconds!r}."
            )

        age = round(now - token_state.created_timestamp_epoch, 2)
        use_by = min(float(token_state.expires_in_seconds), self.token_use_by_seconds)
        remaining = round(use_by - age, 2)
        # Hold the *margin* constant rather than the threshold, so a venue that
        # returns a shorter `expires` still leaves room to refresh instead of
        # jumping straight from valid to expired.
        margin = self.token_use_by_seconds - self.refresh_threshold_seconds
        refresh_at = max(0.0, use_by - margin)

        if age < -_CLOCK_SKEW_TOLERANCE_SECONDS:
            # A future-dated token would otherwise sail through every freshness
            # comparison, which is precisely the check being defeated.
            return age, remaining, STATUS_TOKEN_CLOCK_SKEW, (
                f"token is dated {abs(age):.1f}s in the future; the issuing clock and this "
                "host disagree, so its age cannot be trusted"
            )
        if age >= use_by:
            return age, remaining, STATUS_TOKEN_EXPIRED, (
                f"token age {age:.1f}s has passed its {use_by:.0f}s use-by window and can no "
                "longer establish a private subscription"
            )
        if age >= refresh_at:
            return age, remaining, STATUS_TOKEN_REFRESH_REQUIRED, (
                f"token age {age:.1f}s has passed the {refresh_at:.0f}s refresh point "
                f"({remaining:.1f}s of the {use_by:.0f}s use-by window left); "
                "mint a fresh token before subscribing"
            )
        return age, remaining, None, ""

    def build_v2_subscription_frame(
        self,
        spec: KrakenWsV2SubscriptionSpec,
        token_state: Optional[KrakenWsTokenState] = None,
        current_time_epoch: Optional[float] = None,
    ) -> KrakenWsV2Report:
        """Validate ``spec`` against the channel registry and build the frame.

        Returns a report in every case. A rejection carries an empty frame and a
        status naming the specific failure; it never returns a frame the venue
        would refuse.
        """
        now = current_time_epoch if current_time_epoch is not None else time.time()
        if not math.isfinite(now):
            raise KrakenWsV2Error(f"current_time_epoch must be finite; got {now!r}.")

        channel = spec.channel.strip().lower()
        warnings: List[str] = []
        live_token = token_state.token if token_state else None

        def reject(status: str, note: str, *, ws_url: str, is_private: bool,
                   age: float = 0.0,
                   remaining: Optional[float] = None) -> KrakenWsV2Report:
            text = f"KRAKEN WS V2 REJECT [{channel}]: {note}."
            logger.warning("%s", redact_ws_token(text, live_token))
            return KrakenWsV2Report(
                channel=channel, is_private_channel=is_private, ws_url=ws_url,
                subscription_json_frame={}, token_age_seconds=age, is_token_valid=False,
                status=status, audit_notes=redact_ws_token(text, live_token),
                token_expires_in_seconds=remaining, warnings=warnings,
            )

        channel_spec = CHANNEL_REGISTRY.get(channel)
        if channel_spec is None:
            if channel in V2_REQUEST_METHODS:
                note = (
                    f"'{channel}' is a WS v2 request method, not a subscribable channel. "
                    f'Send {{"method": "{channel}", "params": {{...}}}} directly instead '
                    "of wrapping it in a subscribe frame"
                )
            else:
                note = (
                    f"'{channel}' is not a Kraken WS v2 channel. Known channels: "
                    f"{', '.join(sorted(CHANNEL_REGISTRY))}"
                )
            return reject(STATUS_INVALID_CHANNEL, note, ws_url="", is_private=False)

        is_private = channel_spec.requires_token
        ws_endpoint = channel_spec.ws_url

        token_age, token_remaining = 0.0, None
        if is_private:
            token_age, token_remaining, rejection, note = self._evaluate_token(
                token_state, now
            )
            if rejection is not None:
                return reject(rejection, note, ws_url=ws_endpoint, is_private=True,
                              age=token_age, remaining=token_remaining)

        symbols = [s.strip() for s in (spec.symbols or []) if s and s.strip()]
        if channel_spec.requires_symbol and not symbols:
            return reject(
                STATUS_MISSING_SYMBOL,
                f"channel '{channel}' requires a non-empty symbol list, e.g. ['BTC/USD']",
                ws_url=ws_endpoint, is_private=is_private,
                age=token_age, remaining=token_remaining,
            )
        for symbol in symbols:
            if "/" not in symbol:
                # v2 uses 'BASE/QUOTE'; a REST altname such as 'XXBTZUSD' produces
                # a silent no-data subscription rather than an error.
                warnings.append(
                    f"symbol '{symbol}' is not in WS v2 'BASE/QUOTE' form - REST altnames "
                    "are not accepted by the v2 feed"
                )

        depth = spec.depth
        if channel_spec.allowed_depths and depth is not None:
            if depth not in channel_spec.allowed_depths:
                return reject(
                    STATUS_INVALID_DEPTH,
                    f"depth {depth} is not valid for '{channel}'; allowed: "
                    f"{sorted(channel_spec.allowed_depths)}",
                    ws_url=ws_endpoint, is_private=is_private,
                    age=token_age, remaining=token_remaining,
                )
        if depth is not None and depth != _DEFAULT_SPEC_DEPTH and not channel_spec.allowed_depths:
            # The field default cannot be told apart from an unset value, so only
            # a deliberately different depth is worth flagging. Either way it is
            # dropped rather than sent to a channel that does not accept it.
            warnings.append(
                f"channel '{channel}' takes no depth parameter; {depth} ignored"
            )

        if spec.interval is not None:
            if channel != "ohlc":
                warnings.append(
                    f"channel '{channel}' takes no interval parameter; ignored"
                )
            elif spec.interval not in OHLC_INTERVALS:
                warnings.append(
                    f"ohlc interval {spec.interval} is not one of {sorted(OHLC_INTERVALS)}"
                )

        params: Dict[str, Any] = {"channel": channel}
        if channel_spec.requires_symbol:
            params["symbol"] = symbols
        elif symbols:
            # executions, balances and instrument take no symbol filter; sending
            # one is a malformed subscribe rather than a narrower stream.
            warnings.append(
                f"channel '{channel}' takes no symbol parameter; {symbols} ignored"
            )
        if channel_spec.allowed_depths and depth is not None:
            params["depth"] = depth
        if channel == "ohlc" and spec.interval is not None:
            params["interval"] = spec.interval
        if channel == "executions":
            params["snap_orders"] = spec.snap_orders
            params["snap_trades"] = spec.snap_trades
            if spec.order_status is not None:
                params["order_status"] = spec.order_status
        if spec.snapshot is not None:
            params["snapshot"] = spec.snapshot
        if is_private and token_state is not None:
            params["token"] = token_state.token

        frame: Dict[str, Any] = {"method": "subscribe", "params": params}
        if spec.req_id is not None:
            # Kraken echoes req_id on the ack, which is the only way to tie an
            # error response back to the request that caused it.
            frame["req_id"] = spec.req_id

        notes = (
            f"KRAKEN WS V2 SUB CREATED [{channel}]: endpoint = {ws_endpoint}. "
            f"Token required = {is_private}. Frame = "
            f"{redact_ws_token(json.dumps(frame), live_token)}."
        )
        if warnings:
            notes += f" Warnings: {'; '.join(warnings)}."
        logger.info("%s", notes)

        return KrakenWsV2Report(
            channel=channel,
            is_private_channel=is_private,
            ws_url=ws_endpoint,
            subscription_json_frame=frame,
            token_age_seconds=token_age,
            is_token_valid=True,
            status=STATUS_FRAME_CREATED,
            audit_notes=notes,
            token_expires_in_seconds=token_remaining,
            warnings=warnings,
        )
