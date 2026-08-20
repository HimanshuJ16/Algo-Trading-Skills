"""
bybit-derivatives-api-integration:
Request signing, timestamp-window control and rate-limit accounting for the
Bybit V5 REST API.

What this module is
-------------------
It is the *signing and pacing* layer, not an HTTP client. ``sign_request``
returns the exact URL, headers and body a request must carry; sending them is
the caller's job. Keeping transport out means the signed string and the
transmitted string can be asserted equal in a unit test, which is the single
property the whole scheme depends on.

The signing rule (Bybit V5 "Integration Guidance", Create A Request)
--------------------------------------------------------------------
The string to sign is::

    GET  : timestamp + api_key + recv_window + queryString
    POST : timestamp + api_key + recv_window + jsonBodyString

signed with HMAC-SHA256 and rendered as a **lowercase hex** string.

Two things about that rule are widely misread:

  - **Alphabetical ordering is not a requirement.** The V5 documentation states
    no ordering rule for the query string. The actual requirement is that the
    string that is signed is byte-for-byte the string that is transmitted. This
    module sorts keys only because the official Python SDK (``pybit``) does, so
    two independent implementations produce the same canonical form - not
    because Bybit demands it.
  - **"Compact JSON" is not a requirement either.** Bybit's own POST example
    signs ``{"category": "option"}`` with its spaces intact. Again the rule is
    only that the signed bytes are the transmitted bytes. This module emits
    compact JSON and returns it, so the caller must send that exact string and
    must not re-serialise the dict.

Timestamp acceptance window
---------------------------
Bybit accepts a request only when::

    server_time - recv_window <= timestamp < server_time + 1000

Note the asymmetry: ``recv_window`` buys tolerance for a clock that is *behind*
Bybit's, but a clock that is *ahead* is tolerated by only 1000 ms no matter how
large ``recv_window`` is. Raising ``recv_window`` therefore does nothing for a
fast clock, and it weakens replay protection - Bybit states a smaller window is
more secure. The documented recommendation is to use local device time kept
NTP-synchronised; ``sync_with_server_time`` exists for hosts that cannot run an
NTP daemon, and is opt-in.

Two independent rate limits
---------------------------
  - **Per-UID, per-endpoint**, a rolling one-second window. Breaching it returns
    ``retCode 10006`` ("Too many visits"). Every response carries
    ``X-Bapi-Limit`` (the limit), ``X-Bapi-Limit-Status`` (requests remaining)
    and ``X-Bapi-Limit-Reset-Timestamp``. ``RateLimitSnapshot`` reads these.
  - **Per-IP**, 600 requests per 5-second window, which is *not* reflected in
    those headers at all. Breaching it returns HTTP 403 "access too frequent"
    (``retCode 10018`` on the API side); Bybit's instruction is to terminate all
    HTTP sessions and wait at least 10 minutes for the automatic unban. Header
    based pacing cannot protect against this one - only a global request budget
    across every strategy sharing the egress IP can.

Retry safety
------------
``POST /v5/order/create`` is not idempotent. A client that loses the response to
a submission cannot tell acceptance from rejection, and a blind retry opens a
second position. Bybit's mechanism for this is ``orderLinkId``: a caller-chosen
id, unique per account and at most 36 characters. Re-sending an identical order
under the same ``orderLinkId`` is rejected with ``retCode 110072``
("OrderLinkedID is duplicate") rather than accepted as a new order, which turns
an ambiguous timeout into a safe, bounded retry. ``new_order_link_id`` mints one
and ``sign_request`` refuses to sign an ``orderLinkId`` that exceeds the limit.

Sources
-------
Bybit V5 API documentation (consulted 2026-08-20): "Integration Guidance",
"Rate Limit Rules", "Error Codes", "Place Order". See ``references/standards.md``.
"""
import base64
import hashlib
import hmac
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional

logger = logging.getLogger(__name__)

MAINNET_BASE_URL = "https://api.bybit.com"
TESTNET_BASE_URL = "https://api-testnet.bybit.com"

#: Bybit's default X-BAPI-RECV-WINDOW, in milliseconds.
DEFAULT_RECV_WINDOW_MS = 5_000
#: Forward tolerance of the acceptance window. Fixed by Bybit at 1000 ms and
#: NOT widened by recv_window.
FORWARD_TOLERANCE_MS = 1_000
#: Maximum length of a caller-supplied orderLinkId.
MAX_ORDER_LINK_ID_LEN = 36

#: Characters that either break query-string parsing or are rewritten in transit
#: after signing, which invalidates the signature. Bybit's own SDK does not
#: percent-encode the query string, so the only safe behaviour is to refuse
#: values that would be altered on the wire rather than emit a signature that
#: cannot match.
#:
#: ``=`` and ``%`` are deliberately *allowed*: Bybit's ``nextPageCursor`` values
#: routinely contain both, an HTTP client leaves a well-formed ``%XX`` escape and
#: an embedded ``=`` untouched, and the server splits each pair on its first
#: ``=``. Rejecting them would break pagination for no safety gain. A malformed
#: ``%`` escape *is* rewritten (to ``%25``), and is checked separately.
_UNSAFE_QUERY_CHARS = frozenset(" &#+")

#: A '%' that does not begin a valid two-hex-digit escape gets encoded to '%25'
#: by the HTTP client, after the signature has been computed over the raw form.
_PERCENT_ESCAPE = re.compile(r"%(?![0-9A-Fa-f]{2})")

#: POST fields Bybit expects as decimal *strings*. Passing a float lets JSON
#: serialisation decide the representation (0.1 + 0.2 -> "0.30000000000000004"),
#: which is a quantity bug wearing a formatting costume.
_STRING_ONLY_ORDER_FIELDS = frozenset(
    {"qty", "price", "triggerPrice", "takeProfit", "stopLoss"}
)


class BybitAuthError(ValueError):
    """Raised when a request cannot be signed correctly as specified."""


class BybitClockError(RuntimeError):
    """Raised when the timestamp cannot satisfy Bybit's acceptance window."""


@dataclass
class BybitConfig:
    """
    Credentials and environment for one Bybit V5 API key.

    ``api_secret`` is excluded from the generated ``repr`` and masked by the
    explicit one below. A dataclass that prints its secret leaks the key into
    every log line, exception traceback and debugger frame that touches the
    config object.
    """

    api_key: str
    api_secret: str = field(repr=False)
    is_testnet: bool = True
    recv_window: int = DEFAULT_RECV_WINDOW_MS

    def __post_init__(self) -> None:
        if not isinstance(self.api_key, str) or not self.api_key.strip():
            raise BybitAuthError("api_key must be a non-empty string")
        if not isinstance(self.api_secret, str) or not self.api_secret.strip():
            raise BybitAuthError("api_secret must be a non-empty string")
        if isinstance(self.recv_window, bool) or not isinstance(self.recv_window, int):
            raise BybitAuthError("recv_window must be an int (milliseconds)")
        if self.recv_window <= 0:
            raise BybitAuthError("recv_window must be positive")
        if self.recv_window > 60_000:
            # Not a documented hard cap; a window this wide is a replay-window
            # decision the caller should have to make deliberately.
            raise BybitAuthError(
                "recv_window above 60000 ms widens the replay window with no "
                "benefit for a fast clock; Bybit tolerates only "
                f"{FORWARD_TOLERANCE_MS} ms of forward drift regardless"
            )

    def __repr__(self) -> str:
        return (
            f"BybitConfig(api_key={self.api_key!r}, api_secret=***, "
            f"is_testnet={self.is_testnet!r}, recv_window={self.recv_window!r})"
        )


@dataclass(frozen=True)
class RateLimitSnapshot:
    """
    The per-UID, per-endpoint rate-limit state carried on every V5 response.

    ``reset_timestamp_ms`` is only a genuine reset time when the limit has been
    exceeded; Bybit documents that otherwise it is "just the current timestamp".
    Sleeping until it unconditionally therefore does nothing useful, and reading
    it as a reset instant when ``remaining`` is healthy is a misuse.
    """

    limit: int
    remaining: int
    reset_timestamp_ms: Optional[int] = None

    @property
    def utilisation(self) -> float:
        """Fraction of the endpoint's budget already consumed, in [0.0, 1.0]."""
        if self.limit <= 0:
            return 1.0
        used = self.limit - self.remaining
        return min(1.0, max(0.0, used / self.limit))

    def should_throttle(self, reserve_fraction: float = 0.2) -> bool:
        """
        True when less than ``reserve_fraction`` of the endpoint budget is left.

        The test is deliberately *relative*. An absolute rule such as "back off
        below 10 remaining" is meaningless on Bybit, because several order
        endpoints have a limit of exactly 10/s - the rule would fire on every
        single request while leaving a 50/s endpoint unprotected until it is
        80% consumed.
        """
        if not 0.0 <= reserve_fraction <= 1.0:
            raise ValueError("reserve_fraction must be in [0.0, 1.0]")
        if self.limit <= 0:
            return True
        return (self.remaining / self.limit) < reserve_fraction

    @classmethod
    def from_headers(cls, headers: Mapping[str, Any]) -> Optional["RateLimitSnapshot"]:
        """
        Parse ``X-Bapi-Limit`` / ``X-Bapi-Limit-Status`` from a response.

        Header lookup is case-insensitive because HTTP header case is not
        guaranteed and Bybit's own documentation differs between request
        (``X-BAPI-*``) and response (``X-Bapi-*``) casing. Returns ``None`` when
        the headers are absent or unparseable - the caller must treat an unknown
        budget as unknown, not as full.
        """
        lowered = {str(k).lower(): v for k, v in headers.items()}
        raw_limit = lowered.get("x-bapi-limit")
        raw_remaining = lowered.get("x-bapi-limit-status")
        if raw_limit is None or raw_remaining is None:
            return None
        try:
            limit = int(raw_limit)
            remaining = int(raw_remaining)
        except (TypeError, ValueError):
            logger.warning(
                "unparseable Bybit rate-limit headers: limit=%r status=%r",
                raw_limit,
                raw_remaining,
            )
            return None
        reset_ms: Optional[int]
        raw_reset = lowered.get("x-bapi-limit-reset-timestamp")
        try:
            reset_ms = None if raw_reset is None else int(raw_reset)
        except (TypeError, ValueError):
            reset_ms = None
        return cls(limit=limit, remaining=remaining, reset_timestamp_ms=reset_ms)


def new_order_link_id(prefix: str = "") -> str:
    """
    Mint a unique ``orderLinkId`` of at most 36 characters.

    Reuse the returned id verbatim on every retry of the *same* logical order.
    That is what makes a retry after an ambiguous timeout safe: Bybit rejects
    the duplicate (``retCode 110072``) instead of opening a second position.
    """
    if not isinstance(prefix, str):
        raise BybitAuthError("prefix must be a string")
    # 128 bits of uuid4 in unpadded base32: 26 alphanumeric characters, which
    # leaves room for a caller prefix inside Bybit's 36-character limit. Hex
    # would consume 32 of the 36 and leave the prefix useless.
    suffix = base64.b32encode(uuid.uuid4().bytes).decode("ascii").rstrip("=")
    link_id = f"{prefix}{suffix}"
    if len(link_id) > MAX_ORDER_LINK_ID_LEN:
        raise BybitAuthError(
            f"prefix {prefix!r} leaves no room: orderLinkId must be at most "
            f"{MAX_ORDER_LINK_ID_LEN} characters, so the prefix is limited to "
            f"{MAX_ORDER_LINK_ID_LEN - len(suffix)}"
        )
    return link_id


class BybitV5Authenticator:
    """
    Builds signed Bybit V5 requests.

    The object is stateless per request apart from an optional server-time
    offset, and performs no I/O. Signing is therefore safe to call from multiple
    threads; ``sync_with_server_time`` is the only mutator, so call it from one
    thread (typically a startup or housekeeping task) rather than from the
    dispatch path.
    """

    def __init__(self, config: BybitConfig, max_offset_age_s: float = 300.0) -> None:
        self.config = config
        self.base_url = TESTNET_BASE_URL if config.is_testnet else MAINNET_BASE_URL
        self._max_offset_age_s = float(max_offset_age_s)
        self._server_time_offset_ms: int = 0
        self._offset_set_at: Optional[float] = None
        self._secret_bytes = config.api_secret.encode("utf-8")

    # ---------------------------------------------------------------- clock

    @property
    def server_time_offset_ms(self) -> int:
        """Correction added to local time, in ms. Zero until synchronised."""
        return self._server_time_offset_ms

    def sync_with_server_time(
        self, server_time_ms: int, local_time_ms: Optional[int] = None
    ) -> int:
        """
        Record the offset between Bybit's clock and this host's.

        ``server_time_ms`` comes from ``GET /v5/market/time``. This is a
        fallback for hosts that cannot run NTP; Bybit's documented
        recommendation is an NTP-synchronised local clock, because a
        single-sample offset also absorbs part of the round-trip latency of the
        call that measured it.

        Returns the offset in milliseconds.
        """
        if isinstance(server_time_ms, bool) or not isinstance(server_time_ms, int):
            raise BybitClockError("server_time_ms must be an int (milliseconds)")
        if server_time_ms <= 0:
            raise BybitClockError("server_time_ms must be positive")
        local_ms = self._local_time_ms() if local_time_ms is None else local_time_ms
        offset = int(server_time_ms - local_ms)
        if abs(offset) >= self.config.recv_window:
            logger.warning(
                "local clock differs from Bybit by %d ms, at or beyond "
                "recv_window %d ms; uncorrected requests would be rejected with "
                "retCode 10002. Fix NTP rather than relying on this offset.",
                offset,
                self.config.recv_window,
            )
        self._server_time_offset_ms = offset
        self._offset_set_at = time.monotonic()
        return offset

    @staticmethod
    def is_within_acceptance_window(
        timestamp_ms: int, server_time_ms: int, recv_window_ms: int
    ) -> bool:
        """
        Bybit's documented rule:
        ``server_time - recv_window <= ts < server_time + 1000``.

        Exposed so a pre-flight check can assert it against a real
        ``/v5/market/time`` response instead of assuming it.
        """
        return (
            server_time_ms - recv_window_ms
            <= timestamp_ms
            < server_time_ms + FORWARD_TOLERANCE_MS
        )

    @staticmethod
    def _local_time_ms() -> int:
        return int(time.time() * 1000)

    def _current_timestamp_ms(self) -> int:
        if self._offset_set_at is not None:
            age = time.monotonic() - self._offset_set_at
            if age > self._max_offset_age_s:
                raise BybitClockError(
                    f"server-time offset is {age:.0f}s old (limit "
                    f"{self._max_offset_age_s:.0f}s); re-sync with "
                    "GET /v5/market/time before signing"
                )
        return self._local_time_ms() + self._server_time_offset_ms

    # ------------------------------------------------------------ payloads

    @staticmethod
    def _scalar(value: Any) -> str:
        """Render one query value the way Bybit's enums expect."""
        if isinstance(value, bool):
            # str(True) is "True"; Bybit's boolean parameters are lowercase.
            return "true" if value else "false"
        if isinstance(value, (str, int)):
            return str(value)
        raise BybitAuthError(
            "query parameter values must be str, int or bool, got "
            f"{type(value).__name__}; format decimals yourself so the signed "
            "string is the string you meant to send"
        )

    def build_query_string(self, params: Mapping[str, Any]) -> str:
        """
        Build the GET query string that is both signed and transmitted.

        ``None`` values are dropped - Bybit treats an omitted optional parameter
        and the literal string ``"None"`` very differently. Keys are sorted for
        a canonical form. No percent-encoding is applied, matching Bybit's
        official SDK, so any value that an HTTP client would rewrite on the wire
        is rejected rather than silently signed into a signature that cannot
        match. A ``nextPageCursor`` containing ``=`` or well-formed ``%XX``
        escapes passes through untouched, which is what pagination needs.
        """
        parts = []
        for key in sorted(params):
            value = params[key]
            if value is None:
                continue
            rendered = self._scalar(value)
            for token, label in ((str(key), "key"), (rendered, "value")):
                if not token.isascii():
                    raise BybitAuthError(
                        f"non-ASCII query {label} {token!r} would be "
                        "percent-encoded in transit and break the signature"
                    )
                bad = _UNSAFE_QUERY_CHARS.intersection(token)
                if bad:
                    raise BybitAuthError(
                        f"query {label} {token!r} contains {sorted(bad)!r}, "
                        "which an HTTP client re-encodes after signing; pass a "
                        "value that needs no encoding"
                    )
                if _PERCENT_ESCAPE.search(token):
                    raise BybitAuthError(
                        f"query {label} {token!r} contains a '%' that is not a "
                        "valid escape; it becomes '%25' on the wire and breaks "
                        "the signature"
                    )
            parts.append(f"{key}={rendered}")
        return "&".join(parts)

    def build_json_body(self, params: Mapping[str, Any]) -> str:
        """
        Serialise a POST body to the exact string that will be signed and sent.

        Compact separators are a convention, not a Bybit rule - the rule is that
        these bytes are the transmitted bytes. Send the returned string; do not
        hand the dict to a client that will re-serialise it.
        """
        if not params:
            return ""
        for name in sorted(_STRING_ONLY_ORDER_FIELDS.intersection(params)):
            if not isinstance(params[name], str):
                raise BybitAuthError(
                    f"{name!r} must be a decimal string, not "
                    f"{type(params[name]).__name__}: float serialisation "
                    "silently rewrites quantities and prices"
                )
        link_id = params.get("orderLinkId")
        if link_id is not None:
            if not isinstance(link_id, str) or not link_id:
                raise BybitAuthError("orderLinkId must be a non-empty string")
            if len(link_id) > MAX_ORDER_LINK_ID_LEN:
                raise BybitAuthError(
                    f"orderLinkId {link_id!r} is {len(link_id)} characters; "
                    f"Bybit allows at most {MAX_ORDER_LINK_ID_LEN}"
                )
        try:
            return json.dumps(params, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise BybitAuthError(f"POST body is not JSON-serialisable: {exc}") from exc

    # ------------------------------------------------------------- signing

    def _generate_signature(self, timestamp: str, payload: str) -> str:
        """
        HMAC-SHA256 over ``timestamp + api_key + recv_window + payload``,
        rendered as lowercase hex.

        ``payload`` is the query string for GET and the JSON body string for
        POST, and must already be the exact text that will be transmitted.
        """
        param_str = f"{timestamp}{self.config.api_key}{self.config.recv_window}{payload}"
        return hmac.new(
            self._secret_bytes, param_str.encode("utf-8"), hashlib.sha256
        ).hexdigest()

    def sign_request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Produce everything one authenticated V5 request needs.

        Returns a dict with:
          ``method``       - normalised HTTP verb
          ``url``          - full URL, query string already appended for GET
          ``headers``      - authentication headers
          ``query_string`` - the signed query string ("" for POST)
          ``body``         - the signed JSON body ("" for GET)
          ``payload``      - the string that was signed (query for GET, body for POST)

        Send ``url`` and ``body`` verbatim. Re-encoding either of them, or
        letting a client library rebuild the query from a dict, changes the
        transmitted bytes and produces ``retCode 10004`` (error sign).

        Never attach a body to a GET: Bybit returns HTTP 403 for a GET carrying
        an empty JSON body.
        """
        if not isinstance(method, str):
            raise BybitAuthError("method must be a string")
        verb = method.upper()
        if not isinstance(endpoint, str) or not endpoint.startswith("/"):
            raise BybitAuthError(
                "endpoint must be an absolute path such as '/v5/order/create'"
            )
        params = params or {}

        if verb == "GET":
            payload = self.build_query_string(params)
            query_string, body = payload, ""
        elif verb == "POST":
            payload = self.build_json_body(params)
            query_string, body = "", payload
        else:
            raise BybitAuthError(
                f"Unsupported HTTP method: {method!r} "
                "(Bybit V5 signs GET and POST only)"
            )

        timestamp = str(self._current_timestamp_ms())
        signature = self._generate_signature(timestamp, payload)

        headers = {
            "X-BAPI-API-KEY": self.config.api_key,
            "X-BAPI-SIGN": signature,
            "X-BAPI-SIGN-TYPE": "2",
            "X-BAPI-TIMESTAMP": timestamp,
            "X-BAPI-RECV-WINDOW": str(self.config.recv_window),
            "Content-Type": "application/json",
        }

        url = f"{self.base_url}{endpoint}"
        if query_string:
            url = f"{url}?{query_string}"

        logger.debug(
            "signed Bybit V5 request %s %s (payload %d bytes, ts=%s)",
            verb,
            endpoint,
            len(payload),
            timestamp,
        )
        return {
            "method": verb,
            "url": url,
            "headers": headers,
            "query_string": query_string,
            "body": body,
            "payload": payload,
        }
