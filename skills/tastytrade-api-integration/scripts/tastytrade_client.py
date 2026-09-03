"""
tastytrade-api-integration: OAuth2 session management, strict OCC option symbol
construction, dry-run pre-trade validation, and multi-leg option order placement
against the Tastytrade REST API.

Authentication note
-------------------
Tastytrade discontinued ``POST /sessions`` username/password session-token
authentication on 2025-12-01. This module implements the replacement OAuth2
refresh-token grant (``POST /oauth/token`` -> ``Authorization: Bearer ...``).
:meth:`TastytradeClient.login` is retained only to fail loudly for callers still
written against the old flow.

Transport is injected. The caller owns timeouts, TLS verification and retry
policy, because retry policy for order submission is a risk decision this module
must not make silently -- see :class:`TastytradeAmbiguousOrderError`.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple, Union

logger = logging.getLogger(__name__)

#: Transport contract: ``(method, url, headers, json_body) -> (status_code, decoded_json)``.
#: Implementations MUST raise on transport failure rather than returning a
#: synthetic status code, so that an ambiguous order submission stays ambiguous.
HttpFn = Callable[
    [str, str, Dict[str, str], Optional[Dict[str, Any]]],
    Tuple[int, Any],
]

NumberLike = Union[int, float, str, Decimal]

#: Documented Tastytrade OAuth2 access token lifetime (15 minutes). Used only as
#: a conservative fallback when the token response omits ``expires_in``.
DEFAULT_ACCESS_TOKEN_LIFETIME_SECONDS = 900

#: An ``expires_in`` beyond this is implausible for a 15-minute token and is
#: treated as a malformed response rather than trusted.
MAX_PLAUSIBLE_TOKEN_LIFETIME_SECONDS = 86_400

#: Refresh this many seconds before nominal expiry so a token round trip never
#: lands on the critical path of an order submission.
DEFAULT_REFRESH_BUFFER_SECONDS = 60

#: OCC option symbols are exactly 21 characters.
OCC_SYMBOL_LENGTH = 21

#: Largest strike representable in the 8-digit (strike x 1000) OCC field.
MAX_OCC_STRIKE = Decimal("99999.999")

_OCC_ROOT_RE = re.compile(r"^[A-Z0-9]{1,6}$")
_OCC_SYMBOL_RE = re.compile(r"^([A-Z0-9]{1,6}) *(\d{6})([CP])(\d{8})$")
_YYMMDD_RE = re.compile(r"^\d{6}$")
#: Tastytrade rejects requests whose User-Agent is not ``<product>/<version>``.
_USER_AGENT_RE = re.compile(r"^\S+/\S+$")


# --------------------------------------------------------------------------
# Exceptions
# --------------------------------------------------------------------------
class TastytradeAPIError(Exception):
    """Base class for every Tastytrade API failure raised by this module."""


class TastytradeAuthError(TastytradeAPIError):
    """OAuth2 token acquisition or validation failed."""


class TastytradeAuthDiscontinuedError(TastytradeAuthError):
    """Raised when a caller invokes the retired ``/sessions`` password flow."""


class TastytradeSessionExpiredError(TastytradeAuthError):
    """No usable access token: never authenticated, or the token has expired."""


class TastytradeSymbolError(TastytradeAPIError, ValueError):
    """An option symbol could not be built or parsed as a valid OCC symbol."""


class TastytradeOrderValidationError(TastytradeAPIError, ValueError):
    """An order was rejected locally, before any network call was made."""


class TastytradeOrderRejectedError(TastytradeAPIError):
    """Tastytrade rejected the order. No order exists; the payload must change."""

    def __init__(self, message: str, status_code: int, error_codes: Sequence[str] = ()):
        super().__init__(message)
        self.status_code = status_code
        self.error_codes: Tuple[str, ...] = tuple(error_codes)


class TastytradeAmbiguousOrderError(TastytradeAPIError):
    """
    An order submission's outcome is unknown -- the request may have reached the
    matching engine.

    Tastytrade publishes no client-supplied idempotency key for order placement,
    so this MUST NOT be retried blindly. Reconcile with
    :meth:`TastytradeClient.get_live_orders` before taking any further action.
    """

    def __init__(
        self,
        message: str,
        account_number: str,
        external_identifier: Optional[str] = None,
    ):
        super().__init__(message)
        self.account_number = account_number
        self.external_identifier = external_identifier


# --------------------------------------------------------------------------
# Enums
# --------------------------------------------------------------------------
class LegAction(Enum):
    BUY_TO_OPEN = "Buy to Open"
    SELL_TO_OPEN = "Sell to Open"
    BUY_TO_CLOSE = "Buy to Close"
    SELL_TO_CLOSE = "Sell to Close"


class PriceEffect(Enum):
    """
    Direction of an order's net price.

    The Tastytrade API does not accept negative prices: ``price`` carries the
    magnitude and ``price-effect`` carries the direction. ``Debit`` means the
    account pays, ``Credit`` means the account receives.
    """

    CREDIT = "Credit"
    DEBIT = "Debit"


class OrderType(Enum):
    LIMIT = "Limit"
    MARKET = "Market"


class InstrumentType(Enum):
    EQUITY = "Equity"
    EQUITY_OPTION = "Equity Option"
    FUTURE = "Future"
    FUTURE_OPTION = "Future Option"


#: Order types that carry a net price. Tastytrade's Market order model has no
#: price field at all; sending one is a malformed request.
_PRICED_ORDER_TYPES = frozenset({OrderType.LIMIT})

#: Instrument types quoted in whole contracts.
_WHOLE_CONTRACT_INSTRUMENTS = frozenset(
    {InstrumentType.EQUITY_OPTION, InstrumentType.FUTURE_OPTION, InstrumentType.FUTURE}
)

#: Only equity options use the 21-character OCC symbology. Future options use
#: Tastytrade's own format (e.g. ``./ESU4 EW4Q4 240823C5750``).
_OCC_SYMBOLOGY_INSTRUMENTS = frozenset({InstrumentType.EQUITY_OPTION})


# --------------------------------------------------------------------------
# Value objects
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class OccSymbol:
    """Decomposed OCC option symbol, as produced by :func:`parse_occ_symbol`."""

    root: str
    expiration: str  # YYMMDD
    option_type: str  # 'C' or 'P'
    strike: Decimal
    symbol: str


@dataclass(frozen=True)
class TastytradeCredentials:
    """
    OAuth2 credentials for a Tastytrade application grant.

    ``refresh_token`` is a long-lived bearer credential for the account: treat it
    exactly as you would a password. Both fields are excluded from ``repr``.
    """

    client_secret: str = field(repr=False)
    refresh_token: str = field(repr=False)

    def __post_init__(self) -> None:
        if not self.client_secret or not self.client_secret.strip():
            raise TastytradeAuthError("client_secret must be a non-empty string.")
        if not self.refresh_token or not self.refresh_token.strip():
            raise TastytradeAuthError("refresh_token must be a non-empty string.")


@dataclass
class TastytradeSession:
    """
    A live OAuth2 access token and its expiry.

    ``access_token`` is excluded from ``repr`` so that logging a session object
    cannot ship a live credential to a log aggregator.
    """

    access_token: str = field(repr=False)
    expires_at: float

    def seconds_remaining(self, now: float) -> float:
        return self.expires_at - now

    def is_expiring(self, now: float, buffer_seconds: float) -> bool:
        """True when the token is inside the refresh buffer (or already dead)."""
        return now >= self.expires_at - buffer_seconds


@dataclass
class OptionLeg:
    """One leg of an option order."""

    occ_symbol: str
    action: LegAction
    quantity: NumberLike
    instrument_type: InstrumentType = InstrumentType.EQUITY_OPTION

    def __post_init__(self) -> None:
        if isinstance(self.instrument_type, str):
            try:
                self.instrument_type = InstrumentType(self.instrument_type)
            except ValueError as exc:
                raise TastytradeOrderValidationError(
                    f"Unknown instrument_type {self.instrument_type!r}."
                ) from exc
        if not isinstance(self.action, LegAction):
            raise TastytradeOrderValidationError(
                f"leg action must be a LegAction, got {type(self.action).__name__}."
            )
        if not isinstance(self.occ_symbol, str) or not self.occ_symbol.strip():
            raise TastytradeOrderValidationError("leg occ_symbol must be a non-empty string.")

        self.quantity = _to_decimal(self.quantity, "leg quantity", TastytradeOrderValidationError)
        if self.quantity <= 0:
            raise TastytradeOrderValidationError(
                f"leg quantity must be strictly positive, got {self.quantity}."
            )
        if self.instrument_type in _WHOLE_CONTRACT_INSTRUMENTS and self.quantity % 1 != 0:
            raise TastytradeOrderValidationError(
                f"{self.instrument_type.value} quantity must be a whole number of "
                f"contracts, got {self.quantity}."
            )
        if self.instrument_type in _OCC_SYMBOLOGY_INSTRUMENTS:
            # Fail here rather than let the venue resolve a malformed symbol.
            parse_occ_symbol(self.occ_symbol)

    def to_payload(self) -> Dict[str, Any]:
        return {
            "instrument-type": self.instrument_type.value,
            "symbol": self.occ_symbol,
            "action": self.action.value,
            "quantity": _decimal_to_json(self.quantity),
        }


@dataclass(frozen=True)
class OrderMessage:
    """A warning or error entry returned alongside an order response."""

    code: str
    message: str


@dataclass
class OrderPreview:
    """Result of a ``/orders/dry-run`` pre-trade validation."""

    account_number: str
    buying_power_effect: Dict[str, Any]
    fee_calculation: Dict[str, Any]
    warnings: List[OrderMessage]
    errors: List[OrderMessage]

    @property
    def is_acceptable(self) -> bool:
        return not self.errors


@dataclass
class TastytradeOrder:
    """A submitted order as acknowledged by Tastytrade."""

    order_id: str
    account_number: str
    order_type: OrderType
    legs: List[OptionLeg]
    price: Optional[Decimal]
    price_effect: Optional[PriceEffect]
    status: str
    external_identifier: Optional[str] = None
    warnings: List[OrderMessage] = field(default_factory=list)


# --------------------------------------------------------------------------
# Numeric / symbol helpers
# --------------------------------------------------------------------------
def _to_decimal(value: NumberLike, label: str, exc_type: type) -> Decimal:
    """
    Convert to ``Decimal`` without inheriting binary float artefacts.

    ``float`` inputs go through ``str`` first: ``Decimal(0.1)`` is
    ``0.1000000000000000055511151231257827021181583404541015625`` whereas
    ``Decimal(str(0.1))`` is exactly ``0.1``.
    """
    if isinstance(value, bool):
        raise exc_type(f"{label} must be a number, got bool.")
    if isinstance(value, Decimal):
        candidate = value
    elif isinstance(value, (int, float, str)):
        try:
            candidate = Decimal(str(value).strip())
        except (InvalidOperation, ValueError) as exc:
            raise exc_type(f"{label} is not a valid decimal: {value!r}.") from exc
    else:
        raise exc_type(f"{label} must be a number, got {type(value).__name__}.")

    if not candidate.is_finite():
        raise exc_type(f"{label} must be finite, got {value!r}.")
    return candidate


def _decimal_to_json(value: Decimal) -> Union[int, str]:
    """Serialise a Decimal without going through float."""
    if value == value.to_integral_value():
        return int(value)
    return format(value.normalize(), "f")


def price_effect_for_signed_price(price: NumberLike) -> PriceEffect:
    """
    Map a signed net price to a Tastytrade ``price-effect``.

    Negative means the account pays (``Debit``); positive means the account
    receives (``Credit``). Use this when your strategy computes a signed net
    price, then pass the *absolute* value as ``net_price``.
    """
    value = _to_decimal(price, "price", TastytradeOrderValidationError)
    if value == 0:
        raise TastytradeOrderValidationError(
            "A zero net price has no direction; specify PriceEffect explicitly."
        )
    return PriceEffect.DEBIT if value < 0 else PriceEffect.CREDIT


def format_occ_symbol(
    ticker: str,
    exp_date_yymmdd: str,
    option_type: str,
    strike: NumberLike,
) -> str:
    """
    Build a 21-character OCC equity option symbol.

    ``('AAPL', '240816', 'C', 200) -> 'AAPL  240816C00200000'``

    Layout: 6-char space-padded root, 6-char ``YYMMDD`` expiration, ``C``/``P``,
    then strike x 1000 zero-padded to 8 digits.

    Every component is validated. A silently malformed symbol is the worst
    outcome here: it either resolves to a different contract than intended or is
    rejected by the venue with an error that names nothing.

    :raises TastytradeSymbolError: on any invalid component.
    """
    if not isinstance(ticker, str):
        raise TastytradeSymbolError(f"ticker must be a string, got {type(ticker).__name__}.")
    root = ticker.strip().upper()
    if not _OCC_ROOT_RE.match(root):
        raise TastytradeSymbolError(
            f"ticker {ticker!r} is not a valid OCC root: expected 1-6 alphanumeric "
            f"characters. Roots longer than 6 characters cannot be represented; "
            f"resolve the correct root from /option-chains/{{symbol}}."
        )

    if not isinstance(exp_date_yymmdd, str) or not _YYMMDD_RE.match(exp_date_yymmdd):
        raise TastytradeSymbolError(
            f"expiration {exp_date_yymmdd!r} must be exactly 6 digits in YYMMDD form."
        )
    try:
        datetime.strptime(exp_date_yymmdd, "%y%m%d")
    except ValueError as exc:
        raise TastytradeSymbolError(
            f"expiration {exp_date_yymmdd!r} is not a real calendar date."
        ) from exc

    if not isinstance(option_type, str):
        raise TastytradeSymbolError(
            f"option_type must be a string, got {type(option_type).__name__}."
        )
    opt_type = option_type.strip().upper()
    if opt_type not in ("C", "P"):
        raise TastytradeSymbolError(
            f"option_type must be exactly 'C' or 'P', got {option_type!r}."
        )

    strike_dec = _to_decimal(strike, "strike", TastytradeSymbolError)
    if strike_dec <= 0:
        raise TastytradeSymbolError(f"strike must be strictly positive, got {strike_dec}.")
    if strike_dec > MAX_OCC_STRIKE:
        raise TastytradeSymbolError(
            f"strike {strike_dec} exceeds {MAX_OCC_STRIKE}, the maximum representable "
            f"in the 8-digit OCC strike field."
        )
    scaled = strike_dec * 1000
    if scaled != scaled.to_integral_value():
        # Rounding here would silently trade a different contract.
        raise TastytradeSymbolError(
            f"strike {strike_dec} has finer precision than the OCC field's "
            f"1/1000 increment; supply an exact listed strike."
        )

    symbol = f"{root.ljust(6)}{exp_date_yymmdd}{opt_type}{int(scaled):08d}"
    if len(symbol) != OCC_SYMBOL_LENGTH:  # pragma: no cover - defensive
        raise TastytradeSymbolError(
            f"constructed symbol {symbol!r} is {len(symbol)} characters, expected "
            f"{OCC_SYMBOL_LENGTH}."
        )
    return symbol


def parse_occ_symbol(symbol: str) -> OccSymbol:
    """
    Parse and validate a 21-character OCC equity option symbol.

    Use this to confirm that a symbol taken from an option chain, a config file
    or an upstream signal is the contract you believe it is before trading it.

    :raises TastytradeSymbolError: if the symbol is not a well-formed OCC symbol.
    """
    if not isinstance(symbol, str):
        raise TastytradeSymbolError(f"symbol must be a string, got {type(symbol).__name__}.")
    if len(symbol) != OCC_SYMBOL_LENGTH:
        raise TastytradeSymbolError(
            f"OCC symbol {symbol!r} is {len(symbol)} characters, expected "
            f"{OCC_SYMBOL_LENGTH}."
        )
    match = _OCC_SYMBOL_RE.match(symbol)
    if not match:
        raise TastytradeSymbolError(f"OCC symbol {symbol!r} is malformed.")

    root, expiration, opt_type, strike_digits = match.groups()
    try:
        datetime.strptime(expiration, "%y%m%d")
    except ValueError as exc:
        raise TastytradeSymbolError(
            f"OCC symbol {symbol!r} carries an impossible expiration {expiration!r}."
        ) from exc

    strike = Decimal(strike_digits) / 1000
    if strike <= 0:
        raise TastytradeSymbolError(f"OCC symbol {symbol!r} has a non-positive strike.")
    return OccSymbol(
        root=root,
        expiration=expiration,
        option_type=opt_type,
        strike=strike,
        symbol=symbol,
    )


def _parse_messages(raw: Any) -> List[OrderMessage]:
    """Normalise a Tastytrade ``warnings``/``errors`` array."""
    messages: List[OrderMessage] = []
    if not isinstance(raw, list):
        return messages
    for item in raw:
        if isinstance(item, Mapping):
            messages.append(
                OrderMessage(
                    code=str(item.get("code", "unknown")),
                    message=str(item.get("message", "")),
                )
            )
        else:
            messages.append(OrderMessage(code="unknown", message=str(item)))
    return messages


def _describe_api_error(status_code: int, body: Any) -> Tuple[str, Tuple[str, ...]]:
    """
    Build a safe error description from a Tastytrade error envelope.

    Never interpolates the raw body: token and OAuth responses travel through the
    same code path and would otherwise land in tracebacks and log aggregators.
    """
    codes: List[str] = []
    parts: List[str] = []
    if isinstance(body, Mapping):
        error = body.get("error")
        if isinstance(error, Mapping):
            nested = error.get("errors")
            entries = nested if isinstance(nested, list) and nested else [error]
            for entry in entries:
                if not isinstance(entry, Mapping):
                    continue
                code = str(entry.get("code") or entry.get("domain") or "unknown")
                message = str(entry.get("message") or entry.get("reason") or "")
                codes.append(code)
                parts.append(f"{code}: {message}" if message else code)
        elif isinstance(error, str):
            codes.append(error)
            parts.append(error)
    detail = "; ".join(parts) if parts else "no parseable error envelope"
    return f"HTTP {status_code} -- {detail}", tuple(codes)


# --------------------------------------------------------------------------
# Client
# --------------------------------------------------------------------------
class TastytradeClient:
    """
    Tastytrade REST client for OAuth2 session management and multi-leg option
    order routing.

    :param is_production: route to production instead of the certification
        sandbox. Certification is a separate environment with separate
        credentials; it is not a mirror of production balances or fills.
    :param http_fn: transport, see :data:`HttpFn`. Must raise on transport
        failure.
    :param user_agent: Tastytrade requires a ``<product>/<version>`` User-Agent
        and returns ``401`` from its edge proxy without one.
    :param refresh_buffer_seconds: refresh the access token this long before
        nominal expiry.
    :param clock: elapsed-time source, injectable for testing. Defaults to
        ``time.monotonic`` because token expiry is pure interval arithmetic: a
        wall-clock step (NTP correction, VM resume) must not be able to extend
        the apparent life of an access token. ``TastytradeSession.expires_at`` is
        therefore a monotonic reading, not a Unix timestamp -- do not log it as
        a date.
    """

    CERT_BASE_URL = "https://api.cert.tastyworks.com"
    PROD_BASE_URL = "https://api.tastyworks.com"

    #: Sent on production requests only; the sandbox rejects it.
    ACCEPT_VERSION = "20251101"

    def __init__(
        self,
        is_production: bool = False,
        http_fn: Optional[HttpFn] = None,
        user_agent: str = "algo-trading-skills/1.0",
        refresh_buffer_seconds: float = DEFAULT_REFRESH_BUFFER_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ):
        if http_fn is None:
            raise TastytradeAPIError("HTTP transport function not configured.")
        if not _USER_AGENT_RE.match(user_agent or ""):
            raise TastytradeAPIError(
                f"user_agent {user_agent!r} must be '<product>/<version>'; Tastytrade "
                f"returns 401 from its edge proxy for other shapes."
            )
        if refresh_buffer_seconds < 0:
            raise TastytradeAPIError("refresh_buffer_seconds must be non-negative.")

        self.is_production = is_production
        self.base_url = self.PROD_BASE_URL if is_production else self.CERT_BASE_URL
        self.user_agent = user_agent
        self.refresh_buffer_seconds = refresh_buffer_seconds
        self.session: Optional[TastytradeSession] = None
        self._credentials: Optional[TastytradeCredentials] = None
        self._http_fn: HttpFn = http_fn
        self._clock = clock

    # -- auth ------------------------------------------------------------
    def login(self, *args: Any, **kwargs: Any) -> TastytradeSession:
        """
        Retired. ``POST /sessions`` password authentication was discontinued by
        Tastytrade on 2025-12-01.

        :raises TastytradeAuthDiscontinuedError: always.
        """
        raise TastytradeAuthDiscontinuedError(
            "Tastytrade discontinued /sessions username+password session-token "
            "authentication on 2025-12-01. Use authenticate() with an OAuth2 "
            "client secret and refresh token: see "
            "https://developer.tastytrade.com/api-guides/oauth/"
        )

    def authenticate(self, credentials: TastytradeCredentials) -> TastytradeSession:
        """
        Exchange an OAuth2 refresh token for an access token via
        ``POST /oauth/token``.

        The credentials are retained so that :meth:`ensure_access_token` can
        refresh unattended. Access tokens are short-lived (15 minutes).
        """
        if not isinstance(credentials, TastytradeCredentials):
            raise TastytradeAuthError(
                "authenticate() requires a TastytradeCredentials instance."
            )
        self._credentials = credentials
        return self._refresh_access_token()

    def _refresh_access_token(self) -> TastytradeSession:
        if self._credentials is None:
            raise TastytradeSessionExpiredError(
                "No credentials configured. Call authenticate() first."
            )

        url = f"{self.base_url}/oauth/token"
        headers = self._base_headers()
        payload = {
            "grant_type": "refresh_token",
            "client_secret": self._credentials.client_secret,
            "refresh_token": self._credentials.refresh_token,
        }

        status_code, body = self._http_fn("POST", url, headers, payload)

        if status_code // 100 != 2:
            detail, _codes = _describe_api_error(status_code, body)
            # The refresh token is long-lived but revocable; a 4xx here needs a
            # human re-grant, not a retry loop.
            raise TastytradeAuthError(f"Tastytrade OAuth2 token request failed: {detail}")
        if not isinstance(body, Mapping):
            raise TastytradeAuthError(
                f"Tastytrade OAuth2 token response was {type(body).__name__}, expected "
                f"a JSON object."
            )

        access_token = body.get("access_token")
        if not isinstance(access_token, str) or not access_token.strip():
            raise TastytradeAuthError(
                "Tastytrade OAuth2 token response contained no usable 'access_token'."
            )

        lifetime = self._resolve_token_lifetime(body.get("expires_in"))
        self.session = TastytradeSession(
            access_token=access_token,
            expires_at=self._clock() + lifetime,
        )
        logger.info(
            "Tastytrade OAuth2 access token acquired (environment=%s, lifetime=%ss).",
            "production" if self.is_production else "certification",
            lifetime,
        )
        return self.session

    def _resolve_token_lifetime(self, raw: Any) -> int:
        """
        Resolve the access token lifetime.

        An absent ``expires_in`` falls back to the documented 15 minutes -- a
        conservative value that can only cause an early refresh, never use of a
        dead token. A present-but-implausible value is fatal: silently trusting
        it would leave the client using an expired token and 401-ing mid-order.
        """
        if raw is None:
            logger.warning(
                "Tastytrade OAuth2 response omitted 'expires_in'; assuming the "
                "documented %ss lifetime.",
                DEFAULT_ACCESS_TOKEN_LIFETIME_SECONDS,
            )
            return DEFAULT_ACCESS_TOKEN_LIFETIME_SECONDS
        if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
            raise TastytradeAuthError(
                f"Tastytrade OAuth2 'expires_in' was {type(raw).__name__}, expected a number."
            )
        try:
            lifetime = int(Decimal(str(raw).strip()))
        except (InvalidOperation, ValueError) as exc:
            raise TastytradeAuthError(
                f"Tastytrade OAuth2 'expires_in' is not numeric: {raw!r}."
            ) from exc
        if lifetime <= 0:
            raise TastytradeAuthError(
                f"Tastytrade OAuth2 'expires_in' must be positive, got {lifetime}."
            )
        if lifetime > MAX_PLAUSIBLE_TOKEN_LIFETIME_SECONDS:
            raise TastytradeAuthError(
                f"Tastytrade OAuth2 'expires_in' of {lifetime}s exceeds the plausible "
                f"maximum of {MAX_PLAUSIBLE_TOKEN_LIFETIME_SECONDS}s."
            )
        return lifetime

    def ensure_access_token(self) -> str:
        """
        Return a live access token, refreshing inside the buffer.

        Refreshing proactively keeps the token round trip off the critical path
        of an order submission, where a 401 costs a retry decision on a request
        that may already have been accepted.
        """
        if self.session is None:
            if self._credentials is None:
                raise TastytradeSessionExpiredError(
                    "Not authenticated. Call authenticate() first."
                )
            self._refresh_access_token()
        elif self.session.is_expiring(self._clock(), self.refresh_buffer_seconds):
            logger.info("Tastytrade access token inside refresh buffer; refreshing.")
            self._refresh_access_token()

        assert self.session is not None  # narrowed by the branches above
        return self.session.access_token

    def _base_headers(self) -> Dict[str, str]:
        headers = {
            "User-Agent": self.user_agent,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if self.is_production:
            headers["Accept-Version"] = self.ACCEPT_VERSION
        return headers

    def auth_headers(self) -> Dict[str, str]:
        """Headers for an authenticated request, refreshing the token if needed."""
        headers = self._base_headers()
        headers["Authorization"] = f"Bearer {self.ensure_access_token()}"
        return headers

    # -- account / reconciliation ---------------------------------------
    def get_accounts(self) -> List[Dict[str, Any]]:
        """Return the authenticated customer's accounts (``/customers/me/accounts``)."""
        body = self._authenticated_get("/customers/me/accounts")
        return _extract_items(body)

    def get_positions(self, account_number: str) -> List[Dict[str, Any]]:
        """Return open positions for an account (``/accounts/{acct}/positions``)."""
        account_number = _validate_account_number(account_number)
        body = self._authenticated_get(f"/accounts/{account_number}/positions")
        return _extract_items(body)

    def get_live_orders(self, account_number: str) -> List[Dict[str, Any]]:
        """
        Return today's working and recently-terminal orders
        (``/accounts/{acct}/orders/live``).

        This is the reconciliation path after a
        :class:`TastytradeAmbiguousOrderError`, so an unreadable response raises
        rather than returning an empty list: "no orders" and "I could not tell"
        lead to opposite decisions, and only one of them places a duplicate.
        """
        account_number = _validate_account_number(account_number)
        body = self._authenticated_get(f"/accounts/{account_number}/orders/live")
        return _extract_items(body, strict=True, context="orders/live")

    def find_orders_by_external_identifier(
        self, account_number: str, external_identifier: str
    ) -> List[Dict[str, Any]]:
        """
        Find live orders tagged with ``external_identifier``.

        Tastytrade echoes ``external-identifier`` back on the order but does not
        document server-side de-duplication on it, so this is a *reconciliation*
        tool, not an idempotency guarantee. Use it to answer "did my ambiguous
        submission land?" before deciding whether to resubmit.
        """
        if not external_identifier or not str(external_identifier).strip():
            raise TastytradeOrderValidationError(
                "external_identifier must be a non-empty string to reconcile against."
            )
        target = str(external_identifier).strip()
        orders = self.get_live_orders(account_number)
        matches = [
            order
            for order in orders
            if str(order.get("external-identifier") or "") == target
        ]
        if not matches and orders and not any("external-identifier" in o for o in orders):
            # No order carries the field at all, so an empty result says nothing
            # about whether the submission landed. Reporting "not found" here
            # would license exactly the duplicate this method exists to prevent.
            raise TastytradeAPIError(
                f"Cannot reconcile by external identifier: none of the "
                f"{len(orders)} live orders on account {account_number} echo an "
                f"'external-identifier'. Inspect get_live_orders() manually before "
                f"resubmitting."
            )
        return matches

    def _authenticated_get(self, path: str) -> Any:
        url = f"{self.base_url}{path}"
        status_code, body = self._http_fn("GET", url, self.auth_headers(), None)
        if status_code // 100 != 2:
            detail, _codes = _describe_api_error(status_code, body)
            raise TastytradeAPIError(f"GET {path} failed: {detail}")
        return body

    # -- orders ----------------------------------------------------------
    def dry_run_option_order(
        self,
        account_number: str,
        legs: Sequence[OptionLeg],
        order_type: OrderType,
        net_price: Optional[NumberLike] = None,
        price_effect: Optional[PriceEffect] = None,
        time_in_force: str = "Day",
        external_identifier: Optional[str] = None,
    ) -> OrderPreview:
        """
        Pre-trade validation via ``POST /accounts/{acct}/orders/dry-run``.

        The dry run returns the buying-power effect, projected fees and any
        warnings or errors without creating an order. A dry run is safe to retry:
        unlike a live submission it creates nothing.
        """
        account_number = _validate_account_number(account_number)
        payload = self._build_order_payload(
            tuple(legs), order_type, net_price, price_effect, time_in_force, external_identifier
        )
        url = f"{self.base_url}/accounts/{account_number}/orders/dry-run"
        status_code, body = self._http_fn("POST", url, self.auth_headers(), payload)

        if status_code // 100 != 2:
            detail, codes = _describe_api_error(status_code, body)
            raise TastytradeOrderRejectedError(
                f"Dry run rejected: {detail}", status_code, codes
            )

        data = body.get("data", {}) if isinstance(body, Mapping) else {}
        if not isinstance(data, Mapping):
            data = {}
        preview = OrderPreview(
            account_number=account_number,
            buying_power_effect=dict(data.get("buying-power-effect") or {}),
            fee_calculation=dict(data.get("fee-calculation") or {}),
            warnings=_parse_messages(data.get("warnings")),
            errors=_parse_messages(data.get("errors")),
        )
        for warning in preview.warnings:
            logger.warning("Tastytrade dry-run warning [%s]: %s", warning.code, warning.message)
        for error in preview.errors:
            logger.error("Tastytrade dry-run error [%s]: %s", error.code, error.message)
        return preview

    def place_complex_option_order(
        self,
        account_number: str,
        legs: Sequence[OptionLeg],
        order_type: OrderType,
        net_price: Optional[NumberLike] = None,
        price_effect: Optional[PriceEffect] = None,
        time_in_force: str = "Day",
        external_identifier: Optional[str] = None,
    ) -> TastytradeOrder:
        """
        Submit a single or multi-leg option order to
        ``POST /accounts/{account_number}/orders``.

        "Complex" here means multi-leg (vertical, condor, straddle) in one order.
        It is *not* Tastytrade's ``/complex-orders`` endpoint, which groups
        several orders into OCO/OTOCO structures and is out of scope here.

        Run :meth:`dry_run_option_order` first: it is the only pre-trade check
        that reflects the account's actual buying power.

        :raises TastytradeOrderRejectedError: the venue rejected the order and
            no order exists.
        :raises TastytradeAmbiguousOrderError: the outcome is unknown. Do not
            retry; reconcile with :meth:`get_live_orders` first.
        """
        account_number = _validate_account_number(account_number)
        legs = tuple(legs)
        payload = self._build_order_payload(
            legs, order_type, net_price, price_effect, time_in_force, external_identifier
        )
        url = f"{self.base_url}/accounts/{account_number}/orders"

        # Resolve the token *before* the submission so an auth refresh can never
        # be confused with an ambiguous order submission.
        headers = self.auth_headers()

        try:
            status_code, body = self._http_fn("POST", url, headers, payload)
        except Exception as exc:
            raise TastytradeAmbiguousOrderError(
                f"Order submission for account {account_number} failed in transport "
                f"({type(exc).__name__}). The order may already have been accepted. "
                f"Reconcile with get_live_orders() before any resubmission.",
                account_number=account_number,
                external_identifier=external_identifier,
            ) from exc

        if status_code // 100 != 2:
            detail, codes = _describe_api_error(status_code, body)
            if status_code in (408, 425, 429) or status_code // 100 == 5:
                # The request reached Tastytrade; whether it reached the order
                # book is not knowable from the status alone.
                raise TastytradeAmbiguousOrderError(
                    f"Order submission for account {account_number} returned an "
                    f"indeterminate response: {detail}. Reconcile with "
                    f"get_live_orders() before any resubmission.",
                    account_number=account_number,
                    external_identifier=external_identifier,
                )
            raise TastytradeOrderRejectedError(
                f"Option order placement rejected: {detail}", status_code, codes
            )

        data = body.get("data", {}) if isinstance(body, Mapping) else {}
        order_data = data.get("order") if isinstance(data, Mapping) else None
        order_id = order_data.get("id") if isinstance(order_data, Mapping) else None
        if order_id is None or str(order_id).strip() == "":
            # A 2xx with no id most likely means the order DID reach the book and
            # we merely cannot name it. Inventing an id would hand the caller a
            # handle that can never cancel anything.
            raise TastytradeAmbiguousOrderError(
                f"Tastytrade accepted the submission for account {account_number} "
                f"(HTTP {status_code}) but returned no order id. The order may be "
                f"live. Reconcile with get_live_orders() before any resubmission.",
                account_number=account_number,
                external_identifier=external_identifier,
            )

        status = order_data.get("status")
        if not isinstance(status, str) or not status.strip():
            # Do not invent "Received": the caller must be able to tell a routed
            # order from a rejected one.
            raise TastytradeAmbiguousOrderError(
                f"Tastytrade returned order id {order_id} for account "
                f"{account_number} with no status. Reconcile with get_live_orders() "
                f"before any resubmission.",
                account_number=account_number,
                external_identifier=external_identifier,
            )

        warnings = _parse_messages(data.get("warnings"))
        for warning in warnings:
            logger.warning(
                "Tastytrade accepted order %s with warning [%s]: %s",
                order_id,
                warning.code,
                warning.message,
            )

        resolved_price = (
            _to_decimal(net_price, "net_price", TastytradeOrderValidationError)
            if order_type in _PRICED_ORDER_TYPES and net_price is not None
            else None
        )
        logger.info(
            "Tastytrade order %s placed on account %s (%s, %d legs, status=%s).",
            order_id,
            account_number,
            order_type.value,
            len(payload["legs"]),
            status,
        )
        return TastytradeOrder(
            order_id=str(order_id),
            account_number=account_number,
            order_type=order_type,
            legs=list(legs),
            price=resolved_price,
            price_effect=price_effect if order_type in _PRICED_ORDER_TYPES else None,
            status=status,
            external_identifier=external_identifier,
            warnings=warnings,
        )

    def _build_order_payload(
        self,
        legs: Sequence[OptionLeg],
        order_type: OrderType,
        net_price: Optional[NumberLike],
        price_effect: Optional[PriceEffect],
        time_in_force: str,
        external_identifier: Optional[str],
    ) -> Dict[str, Any]:
        if not isinstance(order_type, OrderType):
            raise TastytradeOrderValidationError(
                f"order_type must be an OrderType, got {type(order_type).__name__}."
            )
        # Materialise first: a generator would be exhausted by the checks below
        # and silently serialise as a zero-leg order.
        legs = tuple(legs)
        if not legs:
            raise TastytradeOrderValidationError("An order requires at least one leg.")
        if not all(isinstance(leg, OptionLeg) for leg in legs):
            raise TastytradeOrderValidationError("Every leg must be an OptionLeg.")

        seen: Dict[Tuple[str, str], int] = {}
        for leg in legs:
            key = (leg.occ_symbol, leg.action.value)
            seen[key] = seen.get(key, 0) + 1
        duplicates = [f"{sym} {action}" for (sym, action), n in seen.items() if n > 1]
        if duplicates:
            # Two identical legs double the intended size at the same price and
            # are almost always a construction bug, not a strategy.
            raise TastytradeOrderValidationError(
                "Duplicate (symbol, action) legs in one order; combine their "
                f"quantities instead: {', '.join(sorted(duplicates))}."
            )

        if not isinstance(time_in_force, str) or not time_in_force.strip():
            raise TastytradeOrderValidationError("time_in_force must be a non-empty string.")

        payload: Dict[str, Any] = {
            "order-type": order_type.value,
            "time-in-force": time_in_force,
            "legs": [leg.to_payload() for leg in legs],
        }

        if order_type in _PRICED_ORDER_TYPES:
            if net_price is None:
                raise TastytradeOrderValidationError(
                    f"{order_type.value} orders require a net_price."
                )
            if not isinstance(price_effect, PriceEffect):
                raise TastytradeOrderValidationError(
                    f"{order_type.value} orders require a PriceEffect; the Tastytrade "
                    f"API carries direction in 'price-effect', not in the sign of "
                    f"'price'."
                )
            price = _to_decimal(net_price, "net_price", TastytradeOrderValidationError)
            if price < 0:
                raise TastytradeOrderValidationError(
                    f"net_price must be the absolute value ({abs(price)}); the "
                    f"Tastytrade API does not accept negative prices. Use "
                    f"price_effect_for_signed_price() to derive PriceEffect from a "
                    f"signed price."
                )
            payload["price"] = _decimal_to_json(price)
            payload["price-effect"] = price_effect.value
        else:
            if net_price is not None or price_effect is not None:
                # Tastytrade's Market order model has no price field; sending one
                # is a malformed request, and a "market order with a limit price"
                # is a dangerous thing for a caller to believe they sent.
                raise TastytradeOrderValidationError(
                    f"{order_type.value} orders must not carry net_price or "
                    f"price_effect."
                )

        if external_identifier is not None:
            tag = str(external_identifier).strip()
            if not tag:
                raise TastytradeOrderValidationError(
                    "external_identifier must be non-empty when supplied."
                )
            payload["external-identifier"] = tag

        return payload


def _validate_account_number(account_number: str) -> str:
    if not isinstance(account_number, str):
        raise TastytradeOrderValidationError(
            f"account_number must be a string, got {type(account_number).__name__}."
        )
    account = account_number.strip()
    if not account or "/" in account or account in (".", ".."):
        raise TastytradeOrderValidationError(
            f"account_number {account_number!r} is not a valid account identifier."
        )
    return account


def _extract_items(
    body: Any, strict: bool = False, context: str = "collection"
) -> List[Dict[str, Any]]:
    """
    Unwrap Tastytrade's ``{"data": {"items": [...]}}`` collection envelope.

    With ``strict``, a response that does not carry that envelope raises instead
    of yielding an empty list. Use it wherever an empty list would be read as a
    factual "there are none".
    """
    def _fail(reason: str) -> List[Dict[str, Any]]:
        if strict:
            raise TastytradeAPIError(
                f"Malformed {context} response: {reason}. Treating this as an "
                f"empty result would be unsafe."
            )
        return []

    if not isinstance(body, Mapping):
        return _fail(f"body was {type(body).__name__}, expected a JSON object")
    data = body.get("data")
    if not isinstance(data, Mapping):
        return _fail("no 'data' object")
    items = data.get("items")
    if not isinstance(items, list):
        return _fail("no 'data.items' array")
    return [item for item in items if isinstance(item, dict)]
