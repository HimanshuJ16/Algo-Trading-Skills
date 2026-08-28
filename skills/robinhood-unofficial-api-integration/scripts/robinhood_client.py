"""
robinhood-unofficial-api-integration: authentication, order placement and position
polling against Robinhood's unofficial/reverse-engineered REST endpoints.

READ FIRST -- this module automates a surface Robinhood does not offer to you.

The RHF-RHS Customer Agreement (effective 2026-07-02) section 29.1 states that a
customer "may not use the API Package or develop Licensee Products without
Robinhood's express written consent", and section 4.7 states "You agree not to
allow any person access to your Account, your Account username or password, or
permit any other person to give orders or instructions on your Account to
Robinhood, without the prior consent of Robinhood."  Robinhood now runs a
sanctioned alternative -- the Agentic Trading MCP server at
https://agent.robinhood.com/mcp/trading (stocks and options since 2026-05-27,
crypto since 2026-07-20) and the official Crypto Trading API at
https://docs.robinhood.com/ -- so credential-based access to the endpoints below
is both contractually restricted and, for most use cases, unnecessary.

This module exists so that an integration that has *already* been authorised, or
that is being audited, at least fails safely.  It is deliberately conservative:
it refuses to invent a device token, refuses to invent an account URL, refuses to
invent an instrument symbol, and refuses to retry an order whose outcome is
unknown.
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Protocol, Tuple

logger = logging.getLogger(__name__)

BASE_URL = "https://api.robinhood.com"

# Harvested from Robinhood's own web client by the community; it is not a
# credential Robinhood issued to you, and it can be rotated or revoked at any
# time.  Overridable so a caller is never silently bound to a stale value.
DEFAULT_CLIENT_ID = "c82SH0WZOsabOXGP2sxqcj34FxkvfnWRZBKlBjFS"

# Robinhood publishes no rate limit for these endpoints.  This is a conservative
# default chosen by this skill, not an observed or documented broker figure.
DEFAULT_MIN_POLL_INTERVAL_S = 2.0

# Bound on pagination following, so a malformed or looping `next` cursor cannot
# spin forever against a broker that is already rate-limiting you.
DEFAULT_MAX_PAGES = 20

# Documented order states (sanko/Robinhood Order.md).  A state outside this set
# means the schema moved under you.
ORDER_STATES = frozenset({
    "queued", "unconfirmed", "confirmed", "partially_filled",
    "filled", "rejected", "canceled", "failed",
})


class HttpTransport(Protocol):
    """Caller-supplied transport.

    Kept out of this module on purpose: timeouts, TLS verification, proxying and
    retry policy stay under the caller's control, and this module never performs
    network I/O it cannot attribute to the caller.
    """

    def __call__(
        self,
        method: str,
        url: str,
        headers: Dict[str, str],
        payload: Dict[str, Any],
    ) -> Tuple[int, Dict[str, Any]]:
        ...


class RobinhoodError(Exception):
    """Base class for every error raised by this module."""


class RobinhoodAuthError(RobinhoodError):
    """Raised when authentication fails or no usable session exists."""


class RobinhoodMFARequired(RobinhoodAuthError):
    """Robinhood answered the token request with an MFA challenge.

    Attributes:
        mfa_type: Robinhood's reported challenge type (e.g. ``sms``, ``app``),
            or ``None`` when the response did not say.
    """

    def __init__(self, mfa_type: Optional[str] = None) -> None:
        self.mfa_type = mfa_type
        super().__init__(
            f"MFA_REQUIRED: Robinhood requires an MFA code. "
            f"MFA type: {mfa_type or 'unknown'}"
        )


class RobinhoodDeviceApprovalRequired(RobinhoodAuthError):
    """Robinhood answered with a device-approval verification workflow.

    This is the flow Robinhood pushes most logins through today, and it cannot be
    satisfied by an ``mfa_code``: approval is granted by tapping a prompt in the
    Robinhood mobile app, and Robinhood states that approvals "only go to a
    trusted Robinhood device".  A headless host is never a trusted device, so
    there is no unattended completion path for this challenge in this module.

    Attributes:
        workflow_id: Robinhood's ``verification_workflow`` id, for a caller that
            implements the out-of-band approval poll itself.
    """

    def __init__(self, workflow_id: Optional[str] = None) -> None:
        self.workflow_id = workflow_id
        super().__init__(
            "DEVICE_APPROVAL_REQUIRED: Robinhood requires out-of-band device "
            "approval in the mobile app; an mfa_code cannot satisfy it. "
            f"workflow_id={workflow_id or 'unknown'}"
        )


class RobinhoodOrderError(RobinhoodError):
    """Robinhood definitively rejected an order. No order was created."""


class RobinhoodAmbiguousOrderError(RobinhoodError):
    """The order submission outcome is unknown -- it may have been accepted.

    Raised when the transport itself fails (timeout, connection reset, unparsable
    response) after a submission has left the process.  DO NOT retry on this
    exception: reconcile against Robinhood's order history using
    :attr:`client_ref_id` first, and only resubmit if no matching order exists.

    Attributes:
        client_ref_id: The ``ref_id`` sent with the submission, which is the only
            client-controlled handle for finding the possibly-created order.
    """

    def __init__(self, client_ref_id: str, cause: str) -> None:
        self.client_ref_id = client_ref_id
        super().__init__(
            f"Order outcome unknown for ref_id={client_ref_id} ({cause}). "
            f"Reconcile by ref_id before any resubmission."
        )


class OrderSide(Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(Enum):
    MARKET = "market"
    LIMIT = "limit"


class TimeInForce(Enum):
    """Documented time-in-force values (sanko/Robinhood Order.md)."""

    GFD = "gfd"
    GTC = "gtc"
    IOC = "ioc"
    OPG = "opg"


def new_device_token() -> str:
    """Mint a device token.

    Deliberately a free function rather than a constructor default: a device
    token is a *persistent* identity.  Minting a fresh UUID per process makes
    every login look like a new device, which is precisely what drives repeated
    device-approval challenges and account security flags.  Call this once, store
    the result, and pass the stored value to the client from then on.
    """
    return str(uuid.uuid4())


@dataclass
class AuthToken:
    """An access token plus the monotonic deadline it dies at.

    ``expires_at`` is a :func:`time.monotonic` deadline, not a wall-clock
    timestamp: an NTP step or a DST change must not be able to resurrect a dead
    token or kill a live one.  Secret fields are excluded from ``repr`` so a
    token cannot reach a log through an incidental ``print``/``%r``.
    """

    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    expires_at: float
    device_token: str = field(repr=False)

    @property
    def is_expired(self) -> bool:
        return time.monotonic() >= self.expires_at

    @property
    def seconds_remaining(self) -> float:
        return max(0.0, self.expires_at - time.monotonic())


@dataclass
class RobinhoodOrder:
    order_id: str
    symbol: str
    side: str
    order_type: str
    quantity: float
    limit_price: Optional[float]
    status: str
    created_at: float
    client_ref_id: str = ""


@dataclass
class RobinhoodPosition:
    """A position as Robinhood actually returns it.

    ``/positions/`` carries an ``instrument`` URL and **no ticker symbol**, so
    ``symbol`` is ``None`` unless a caller-supplied resolver filled it in.  It is
    never a placeholder string: a fabricated ticker in a reconciliation report is
    worse than an absent one.
    """

    instrument_url: str
    quantity: float
    average_cost: float
    symbol: Optional[str] = None
    shares_held_for_sells: float = 0.0


class RobinhoodUnofficialClient:
    """Client for Robinhood's unofficial API.

    WARNING: these are reverse-engineered endpoints.  Robinhood's Customer
    Agreement section 29.1 requires its express written consent before a customer
    uses an API to reach their account, and section 4.7 bars permitting anything
    else to give orders on the account without prior consent.  Confirm your
    authorisation before using this, and prefer Robinhood's sanctioned Agentic
    Trading MCP server or Crypto Trading API where they cover your use case.
    """

    def __init__(
        self,
        http_fn: HttpTransport,
        device_token: str,
        *,
        account_url: Optional[str] = None,
        client_id: str = DEFAULT_CLIENT_ID,
        min_poll_interval_s: float = DEFAULT_MIN_POLL_INTERVAL_S,
        max_pages: int = DEFAULT_MAX_PAGES,
    ) -> None:
        """
        Args:
            http_fn: Required transport, ``(method, url, headers, payload)`` ->
                ``(status, body_dict)``.  Required rather than optional so a
                misconfigured client fails at construction instead of at the
                first order.
            device_token: A **persisted** device token from
                :func:`new_device_token`.  Required: this client will not mint a
                throwaway token for you.
            account_url: The account URL from Robinhood's account profile.
                Required before placing an order; there is no default, because a
                guessed account URL addresses the wrong account.
            client_id: OAuth client id used on the token request.
            min_poll_interval_s: Minimum spacing between polling calls.  Not a
                published Robinhood limit -- a conservative local default.
            max_pages: Cap on pages followed when walking a paginated response.

        Raises:
            ValueError: if ``http_fn`` or ``device_token`` is missing/blank.
        """
        if http_fn is None:
            raise ValueError("http_fn is required; supply an HTTP transport.")
        if not device_token or not str(device_token).strip():
            raise ValueError(
                "device_token is required and must be persisted across restarts. "
                "Generate one once with new_device_token(), store it, and reuse it."
            )
        if min_poll_interval_s < 0:
            raise ValueError("min_poll_interval_s must be >= 0.")
        if max_pages < 1:
            raise ValueError("max_pages must be >= 1.")

        self._http: HttpTransport = http_fn
        self.device_token: str = str(device_token).strip()
        self.account_url: Optional[str] = account_url
        self.client_id: str = client_id
        self.min_poll_interval_s: float = min_poll_interval_s
        self.max_pages: int = max_pages
        self.auth_token: Optional[AuthToken] = None
        self._orders: List[RobinhoodOrder] = []
        self._last_poll_monotonic: Optional[float] = None

    def __repr__(self) -> str:
        state = "authenticated" if self.auth_token else "unauthenticated"
        return f"<RobinhoodUnofficialClient {state} account={self.account_url!r}>"

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    def authenticate(
        self,
        email: str,
        password: str,
        mfa_code: Optional[str] = None,
    ) -> AuthToken:
        """Exchange credentials for an access token.

        Raises:
            RobinhoodMFARequired: Robinhood asked for an MFA code.
            RobinhoodDeviceApprovalRequired: Robinhood opened a device-approval
                verification workflow, which no ``mfa_code`` can satisfy.
            RobinhoodAuthError: any other authentication failure, including a
                response with no ``expires_in``.
        """
        if not email or not password:
            raise ValueError("email and password are required.")

        payload: Dict[str, Any] = {
            "client_id": self.client_id,
            "grant_type": "password",
            "username": email,
            "password": password,
            "device_token": self.device_token,
            "scope": "internal",
        }
        if mfa_code:
            payload["mfa_code"] = mfa_code

        status, body = self._http("POST", f"{BASE_URL}/oauth2/token/", {}, payload)
        body = body or {}

        # Device approval is checked first: Robinhood can return it alongside a
        # 200, and treating it as success would install a session that does not
        # exist.
        workflow = body.get("verification_workflow")
        if workflow:
            workflow_id = workflow.get("id") if isinstance(workflow, dict) else None
            raise RobinhoodDeviceApprovalRequired(workflow_id=workflow_id)

        if body.get("mfa_required"):
            raise RobinhoodMFARequired(mfa_type=body.get("mfa_type"))

        if status != 200 or "access_token" not in body:
            # Deliberately does not echo the response body: it can carry the
            # submitted identifiers back.
            raise RobinhoodAuthError(f"Authentication failed (HTTP {status}).")

        expires_in = body.get("expires_in")
        if not isinstance(expires_in, (int, float)) or isinstance(expires_in, bool):
            # Never default this. A client that assumes a lifetime the server did
            # not state will keep using a token that died, and every subsequent
            # call 401s for a reason nothing in the logs explains.
            raise RobinhoodAuthError(
                "Authentication response omitted a numeric 'expires_in'; "
                "refusing to assume a token lifetime."
            )
        if expires_in <= 0:
            raise RobinhoodAuthError(f"Authentication returned expires_in={expires_in}.")

        self.auth_token = AuthToken(
            access_token=body["access_token"],
            refresh_token=body.get("refresh_token", ""),
            expires_at=time.monotonic() + float(expires_in),
            device_token=self.device_token,
        )
        logger.info(
            "Robinhood authentication successful; token valid for %.0fs.", expires_in
        )
        return self.auth_token

    def _ensure_auth(self) -> Dict[str, str]:
        """Return auth headers, raising if there is no usable session."""
        if self.auth_token is None:
            raise RobinhoodAuthError("Not authenticated. Call authenticate() first.")
        if self.auth_token.is_expired:
            raise RobinhoodAuthError("Token expired. Re-authenticate or refresh.")
        return {"Authorization": f"Bearer {self.auth_token.access_token}"}

    # ------------------------------------------------------------------
    # Orders
    # ------------------------------------------------------------------

    def place_order(
        self,
        symbol: str,
        side: OrderSide,
        quantity: float,
        order_type: OrderType = OrderType.MARKET,
        limit_price: Optional[float] = None,
        *,
        instrument_url: str,
        client_ref_id: Optional[str] = None,
        time_in_force: TimeInForce = TimeInForce.GFD,
        extended_hours: bool = False,
    ) -> RobinhoodOrder:
        """Submit an equity order.

        ``client_ref_id`` becomes the payload's ``ref_id``.  Reuse the *same*
        value when resubmitting a logically identical order so Robinhood can
        de-duplicate it; generate a new one for a genuinely new order.  Robinhood
        publishes no idempotency contract for ``ref_id``, so treat it as a
        reconciliation handle first and a duplicate guard second.

        Note on market orders: Robinhood's own clients submit a "market" buy as a
        *limit* order collared a few percent above the ask.  A market buy is
        therefore not guaranteed to fill in a fast market, whatever ``type`` you
        send.

        Raises:
            ValueError: invalid arguments (quantity, limit price, missing account
                or instrument URL).
            RobinhoodAuthError: no usable session.
            RobinhoodOrderError: Robinhood rejected the order; nothing was created.
            RobinhoodAmbiguousOrderError: the transport failed; the order may
                exist. Reconcile by ``ref_id``, do not blindly retry.
        """
        headers = self._ensure_auth()

        if not self.account_url:
            raise ValueError(
                "account_url is required before placing an order. Fetch it from "
                "the account profile; do not guess it."
            )
        if not instrument_url:
            raise ValueError(
                "instrument_url is required: Robinhood's /orders/ endpoint keys "
                "the security off the instrument URL, not the ticker."
            )
        if not symbol or not symbol.strip():
            raise ValueError("symbol is required.")
        if not isinstance(quantity, (int, float)) or isinstance(quantity, bool):
            raise ValueError(f"quantity must be numeric, got {type(quantity).__name__}.")
        if not quantity > 0 or quantity == float("inf"):
            # Rejects 0, negatives, NaN (fails `> 0`) and inf. Side is expressed
            # by `side`, never by the sign of the quantity.
            raise ValueError(f"quantity must be a finite positive number, got {quantity!r}.")
        if order_type is OrderType.LIMIT:
            if limit_price is None:
                raise ValueError("limit_price is required for a LIMIT order.")
            if not limit_price > 0 or limit_price == float("inf"):
                raise ValueError(f"limit_price must be positive and finite, got {limit_price!r}.")
        if extended_hours and order_type is not OrderType.LIMIT:
            # Robinhood only accepts limit orders outside regular hours.
            raise ValueError("extended_hours orders must be LIMIT orders.")

        ref_id = client_ref_id or str(uuid.uuid4())
        normalized_symbol = symbol.strip().upper()

        payload: Dict[str, Any] = {
            "account": self.account_url,
            "instrument": instrument_url,
            "symbol": normalized_symbol,
            "side": side.value,
            "type": order_type.value,
            "quantity": quantity,
            "time_in_force": time_in_force.value,
            "trigger": "immediate",
            "extended_hours": extended_hours,
            "ref_id": ref_id,
        }
        if order_type is OrderType.LIMIT:
            payload["price"] = limit_price

        try:
            status, body = self._http("POST", f"{BASE_URL}/orders/", headers, payload)
        except Exception as exc:  # noqa: BLE001 - any transport failure is ambiguous
            # The request left this process. Robinhood may have accepted it.
            logger.error(
                "Order submission outcome unknown for ref_id=%s: %s", ref_id, exc
            )
            raise RobinhoodAmbiguousOrderError(ref_id, f"{type(exc).__name__}: {exc}") from exc

        body = body or {}
        if status not in (200, 201):
            raise RobinhoodOrderError(
                f"Order rejected (HTTP {status}) for ref_id={ref_id}: "
                f"{body.get('detail', 'no detail')}"
            )

        order_id = body.get("id")
        if not order_id:
            # A 2xx with no id is not a success we can reconcile against.
            raise RobinhoodAmbiguousOrderError(
                ref_id, f"HTTP {status} response carried no order id"
            )

        state = body.get("state", "")
        if state not in ORDER_STATES:
            logger.warning(
                "Order %s returned undocumented state %r; treat the order as live "
                "and reconcile before acting.", order_id, state,
            )

        order = RobinhoodOrder(
            order_id=order_id,
            symbol=normalized_symbol,
            side=side.value,
            order_type=order_type.value,
            quantity=quantity,
            limit_price=limit_price if order_type is OrderType.LIMIT else None,
            status=state,
            created_at=time.time(),
            client_ref_id=ref_id,
        )
        self._orders.append(order)
        logger.info(
            "Order submitted: %s %s %s (%s) ref_id=%s state=%s",
            side.value, quantity, normalized_symbol, order_type.value, ref_id, state,
        )
        return order

    @property
    def submitted_orders(self) -> List[RobinhoodOrder]:
        """Orders this client submitted, in submission order (a copy)."""
        return list(self._orders)

    # ------------------------------------------------------------------
    # Positions
    # ------------------------------------------------------------------

    def get_positions(
        self,
        symbol_resolver: Optional[Callable[[str], Optional[str]]] = None,
    ) -> List[RobinhoodPosition]:
        """Poll open positions, following pagination to completion.

        ``/positions/`` is paginated and returns no ticker symbol -- only an
        ``instrument`` URL.  Reading page one only silently understates the
        portfolio, which is why this walks every page and raises rather than
        truncating if it runs past ``max_pages``.

        Args:
            symbol_resolver: Optional ``instrument_url -> symbol`` lookup. Without
                it, ``RobinhoodPosition.symbol`` stays ``None`` rather than being
                filled with a placeholder.

        Raises:
            RobinhoodAuthError: no usable session.
            RobinhoodError: a non-200 page, a looping cursor, or more pages than
                ``max_pages``.
        """
        headers = self._ensure_auth()

        positions: List[RobinhoodPosition] = []
        url: Optional[str] = f"{BASE_URL}/positions/?nonzero=true"
        seen_urls = set()
        pages = 0

        while url:
            if url in seen_urls:
                raise RobinhoodError(f"Pagination cursor looped on {url!r}.")
            seen_urls.add(url)

            pages += 1
            if pages > self.max_pages:
                raise RobinhoodError(
                    f"Position pagination exceeded max_pages={self.max_pages}; "
                    f"refusing to return a truncated portfolio."
                )

            self._throttle()
            status, body = self._http("GET", url, headers, {})
            if status != 200:
                raise RobinhoodError(
                    f"Position query failed (HTTP {status}) on page {pages}."
                )
            body = body or {}

            for result in body.get("results", []):
                instrument_url = result.get("instrument", "")
                try:
                    qty = float(result.get("quantity", 0) or 0)
                    held = float(result.get("shares_held_for_sells", 0) or 0)
                    avg = float(result.get("average_buy_price", 0) or 0)
                except (TypeError, ValueError):
                    logger.warning(
                        "Skipping position with unparsable numerics: instrument=%s",
                        instrument_url,
                    )
                    continue

                if qty == 0:
                    continue

                symbol = symbol_resolver(instrument_url) if symbol_resolver else None
                positions.append(RobinhoodPosition(
                    instrument_url=instrument_url,
                    quantity=qty,
                    average_cost=avg,
                    symbol=symbol,
                    shares_held_for_sells=held,
                ))

            url = body.get("next") or None

        logger.info(
            "Polled %d open position(s) across %d page(s).", len(positions), pages
        )
        return positions

    def _throttle(self) -> None:
        """Space out polling calls by ``min_poll_interval_s``.

        Robinhood publishes no rate limit for these endpoints, so this is a local
        conservative default rather than compliance with a stated budget.
        """
        if self.min_poll_interval_s <= 0:
            return
        now = time.monotonic()
        if self._last_poll_monotonic is not None:
            elapsed = now - self._last_poll_monotonic
            if elapsed < self.min_poll_interval_s:
                time.sleep(self.min_poll_interval_s - elapsed)
        self._last_poll_monotonic = time.monotonic()
