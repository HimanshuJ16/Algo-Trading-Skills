"""
deribit-crypto-options-api: JSON-RPC 2.0 payload construction, inverse-option
premium conversion, portfolio Greeks aggregation and pre-trade capital checks
for Deribit API v2 inverse (coin-settled) options.

What this module is and is not
------------------------------
It is a **payload builder and pre-trade gate**. It returns the exact JSON-RPC
object a request must carry and an approve/reject decision; *sending* it is the
caller's job. There is no socket, no HTTP client and no credential handling
here -- authentication is a separate concern (``public/auth`` with
``grant_type=client_credentials``, ``client_signature`` or ``refresh_token``).

Keeping transport out is deliberate: it means the approved payload and the
transmitted payload can be asserted equal in a unit test, and it means nothing
in this file can accidentally place an order.

**Nothing here dispatches an order.** The report field is
``is_approved_for_dispatch``, not ``is_dispatched``. An approval is a statement
about the *inputs*, and it goes stale the moment the market moves.

Units, and the three ways they are misread
------------------------------------------
For Deribit **options**, ``public/get_instrument`` documents ``contract_size``
and ``min_trade_amount`` in the **underlying base currency coin**, and
``private/buy`` documents ``amount`` the same way ("USD units for
perpetual/inverse futures; base currency coin for options/linear futures").
A separate ``contracts`` parameter expresses size in contract units instead.
This module works in coin and names the field ``amount_coin`` for that reason.

``price`` for an inverse option is quoted **in the base currency**, as a ratio
of one coin of underlying. So a 0.05 quote means 0.05 BTC per 1 BTC of
underlying, and the USD premium is ``price_coin * index_price_usd``.

Greeks are, per the ``public/ticker`` documentation, "calculated using standard
Black Scholes **without adjustments**". They are therefore ordinary
dimensionless BS greeks, *not* adjusted for the coin-settled payoff. Two
consequences this module makes explicit:

  - ``position_delta_coin = amount_coin * delta`` and
    ``position_delta_usd = position_delta_coin * index_price_usd`` are correct
    as the standard USD-exposure convention, and match what Deribit's own
    position view shows.
  - Because the premium is **paid and received in coin**, the trade moves the
    account's coin balance by ``amount_coin * price_coin`` on top of the option
    exposure. A buyer parts with that coin, a seller receives it. The
    coin-denominated exposure net of the premium leg is therefore
    ``side * amount_coin * (delta - price_coin)``, reported as
    ``net_coin_delta_after_premium``. This is the "inverse delta drift" that
    a hedge sized on ``delta`` alone misses.

``post_only`` defaults to TRUE
------------------------------
``private/buy`` and ``private/sell`` default ``post_only`` to ``true``, and a
post-only order that would cross is **repriced below the spread** rather than
filled (unless ``reject_post_only=true``, which rejects it instead). A caller
that builds a "limit" order expecting it to take liquidity gets a resting maker
order and no fill. This module therefore always emits ``post_only`` explicitly
and requires the caller to choose.

Rate limiting is session-fatal
------------------------------
Matching-engine requests are credit-limited by 7-day volume tier (5 req/s
sustained at the base tier, up to 30 req/s). On exhaustion Deribit returns
``too_many_requests`` (**code 10028**) *and terminates the session*. Because a
terminated session forces a reconnect, a naive "reconnect and resend" is exactly
how a duplicate order gets placed. Every order this module builds therefore
carries the caller's ``order_id`` as the Deribit ``label`` (documented maximum
64 characters), so the order can be located with the order-query endpoints after
a reconnect **before** any resend is considered.

Margin
------
This module does **not** model Deribit's margin formula. Short options require
initial margin that depends on moneyness and the account's margin model, and
inventing that formula would be a fabricated number on a live-trading path.
Instead ``private/get_margins`` (which takes ``instrument_name``, ``amount``,
``price`` and returns ``buy``/``sell`` margin plus per-side maker and taker fee
estimates) is the authoritative source, and its output is a **required input**
to ``process_option_order`` for any sell. Selling without it is refused, not
approved.

References
----------
See ``references/standards.md`` for the endpoint and field citations.
"""
from __future__ import annotations

import itertools
import logging
import math
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

WS_ENDPOINT_MAINNET = "wss://www.deribit.com/ws/api/v2"
WS_ENDPOINT_TESTNET = "wss://test.deribit.com/ws/api/v2"

#: Deribit error code returned when the credit pool is exhausted. The session is
#: terminated along with it, which is why it needs naming rather than a bare int.
ERROR_TOO_MANY_REQUESTS = 10028

#: Maximum length of the user-defined order label, per private/buy.
MAX_LABEL_LENGTH = 64

ACTION_BUY = "buy"
ACTION_SELL = "sell"
_VALID_ACTIONS = frozenset({ACTION_BUY, ACTION_SELL})

#: Order types accepted by private/buy and private/sell.
_VALID_ORDER_TYPES = frozenset({
    "limit", "stop_limit", "take_limit", "market", "stop_market", "take_market",
    "market_limit", "trailing_stop",
})
#: Order types that carry no ``price`` field.
_MARKET_ORDER_TYPES = frozenset({"market", "stop_market", "take_market"})

#: Documented time_in_force values.
_VALID_TIME_IN_FORCE = frozenset({
    "good_til_cancelled", "good_til_day", "fill_or_kill", "immediate_or_cancel",
})

OPTION_CALL = "call"
OPTION_PUT = "put"

#: Inverse (coin-settled) option symbols: BTC-28MAR26-60000-C.
_INVERSE_OPTION_RE = re.compile(
    r"^(?P<currency>[A-Z]+)-(?P<expiry>\d{1,2}[A-Z]{3}\d{2})-(?P<strike>\d+(?:\.\d+)?)-(?P<cp>[CP])$"
)
#: Linear USDC/USDT-settled option symbols: BTC_USDC-28MAR26-60000-C. These are
#: NOT inverse and must not go through the coin->USD premium conversion.
_LINEAR_MARKER_RE = re.compile(r"^[A-Z]+_(USDC|USDT)-")


class DeribitApiError(ValueError):
    """Raised when an instrument, order or engine configuration is invalid.

    Pre-trade validation on a live-order path must fail loudly. Silently
    coercing an unparseable option type, or approving a short without a margin
    quote, produces an authoritative-looking approval for an order the exchange
    will reject -- or worse, accept.
    """


@dataclass
class DeribitInstrumentSpec:
    """Components of a parsed Deribit inverse option symbol."""

    instrument_name: str
    base_currency: str          # 'BTC' or 'ETH'
    quote_currency: str         # 'USD' for inverse options
    expiry_date: str            # '28MAR26' as written in the symbol
    strike: float
    option_type: str            # OPTION_CALL or OPTION_PUT


@dataclass
class DeribitOptionTicker:
    """Subset of a ``public/ticker`` response used for pre-trade checks.

    ``mark_price_coin``, ``best_bid_price_coin`` and ``best_ask_price_coin`` are
    in base currency as a ratio of one coin of underlying. ``mark_iv`` is a
    percentage (65.4 means 65.4%). The greeks are standard Black-Scholes,
    unadjusted for coin settlement.
    """

    instrument_name: str
    index_price_usd: float
    mark_price_coin: float
    mark_iv: float
    delta: float
    gamma: float
    vega: float
    theta: float
    best_bid_price_coin: Optional[float] = None
    best_ask_price_coin: Optional[float] = None


@dataclass
class DeribitMarginQuote:
    """Authoritative pre-trade figures from ``private/get_margins``.

    Attributes:
        initial_margin_coin: The ``buy`` or ``sell`` value for the side being
            traded, in base currency. Required for sells.
        fee_coin: Estimated commission for the side and liquidity role being
            traded (``buy_taker_fee``, ``sell_maker_fee``, ...), in base
            currency. Deribit's option commission is charged as a percentage of
            the **underlying**, subject to a cap expressed as a percentage of
            the option premium; this module does not assume either rate, it
            consumes the exchange's own estimate.
        min_price_coin: Lower bound of the accepted price band, if returned.
        max_price_coin: Upper bound of the accepted price band, if returned.
    """

    initial_margin_coin: Optional[float] = None
    fee_coin: float = 0.0
    min_price_coin: Optional[float] = None
    max_price_coin: Optional[float] = None


@dataclass
class DeribitOrderRequest:
    """An order to be validated and rendered as a JSON-RPC payload.

    Attributes:
        order_id: Caller-side identifier. Sent to Deribit as ``label`` so the
            order remains findable after a dropped session. Must be <= 64 chars.
        amount_coin: Size in the underlying base currency coin, matching
            ``private/buy``'s ``amount`` for options. Not a contract count.
        price_coin: Limit price in base currency. Ignored (and rejected if set
            inconsistently) for market order types.
        post_only: Explicit by design -- Deribit defaults this to True and
            reprices a crossing order instead of filling it.
        reject_post_only: With ``post_only``, rejects rather than reprices an
            order that would cross immediately.
    """

    order_id: str
    instrument_name: str
    action: str
    amount_coin: float
    price_coin: Optional[float] = None
    order_type: str = "limit"
    post_only: bool = True
    reject_post_only: bool = False
    reduce_only: bool = False
    time_in_force: str = "good_til_cancelled"
    valid_until_ms: Optional[int] = None


@dataclass
class DeribitOptionsOrderReport:
    order_id: str
    instrument_name: str
    action: str
    amount_coin: float
    price_coin: Optional[float]
    price_usd_equivalent: Optional[float]
    total_premium_coin: float
    total_premium_usd: float
    #: Standard BS exposure: amount_coin * delta, signed by side.
    position_delta_coin: float
    position_delta_usd: float
    #: Coin exposure net of the coin-denominated premium leg:
    #: side * amount_coin * (delta - price_coin). See the module docstring.
    net_coin_delta_after_premium: float
    #: Premium + estimated fee for a buy; estimated fee + initial margin for a sell.
    required_coin: float
    estimated_fee_coin: float
    initial_margin_coin: Optional[float]
    json_rpc_payload: Dict[str, Any]
    is_approved_for_dispatch: bool
    rejection_reasons: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


@dataclass
class DeribitPortfolioGreeks:
    """Position-weighted greeks across a set of option holdings."""

    delta_coin: float
    delta_usd: float
    gamma_coin: float
    vega_coin: float
    theta_coin: float
    net_coin_delta_after_premium: float
    position_count: int


class DeribitCryptoOptionsApiEngine:
    """JSON-RPC payload builder and pre-trade gate for Deribit inverse options.

    Args:
        is_testnet: Selects the WebSocket endpoint. Defaults to True so that a
            miswired caller reaches the test venue, not production.
        max_equity_utilisation: House risk policy -- the fraction of available
            balance a single order may consume. This is **not** a Deribit rule;
            the exchange enforces its own margin requirements independently.
            Set to 1.0 to disable.

    Raises:
        DeribitApiError: on invalid configuration.
    """

    def __init__(
        self,
        is_testnet: bool = True,
        *,
        max_equity_utilisation: float = 0.80,
    ) -> None:
        if not math.isfinite(max_equity_utilisation) or not 0.0 < max_equity_utilisation <= 1.0:
            raise DeribitApiError(
                "max_equity_utilisation must be in (0, 1], got "
                f"{max_equity_utilisation!r}")

        self.is_testnet = is_testnet
        self.endpoint_url = WS_ENDPOINT_TESTNET if is_testnet else WS_ENDPOINT_MAINNET
        self.max_equity_utilisation = max_equity_utilisation
        # JSON-RPC ids must be unique per connection: responses arrive out of
        # order on a multiplexed socket and are correlated by id alone.
        self._request_ids = itertools.count(1)

    # -- instruments --------------------------------------------------------

    def parse_instrument_name(self, symbol: str) -> DeribitInstrumentSpec:
        """Parse an inverse option symbol such as ``BTC-28MAR26-60000-C``.

        Raises:
            DeribitApiError: if the symbol is not a well-formed inverse option
                symbol. Linear USDC/USDT-settled options are rejected explicitly
                rather than parsed, because their premium is already quoted in
                the settlement currency and must not be multiplied by the index.
        """
        if not isinstance(symbol, str) or not symbol.strip():
            raise DeribitApiError("instrument name must be a non-empty string")

        candidate = symbol.strip().upper()

        if _LINEAR_MARKER_RE.match(candidate):
            raise DeribitApiError(
                f"{symbol!r} is a linear (USDC/USDT-settled) option. Its premium is "
                "already quoted in the settlement currency; the inverse coin->USD "
                "conversion in this module does not apply to it.")

        match = _INVERSE_OPTION_RE.match(candidate)
        if not match:
            raise DeribitApiError(
                f"Invalid Deribit inverse option name: {symbol!r}. Expected "
                "<CURRENCY>-<DDMMMYY>-<STRIKE>-<C|P>, e.g. 'BTC-28MAR26-60000-C'.")

        strike = float(match.group("strike"))
        if strike <= 0.0:
            raise DeribitApiError(f"{symbol!r}: strike must be positive, got {strike}")

        return DeribitInstrumentSpec(
            instrument_name=candidate,
            base_currency=match.group("currency"),
            quote_currency="USD",
            expiry_date=match.group("expiry"),
            strike=strike,
            # The regex admits only C or P, so this cannot silently fall through
            # to "put" the way an `if suffix == "C" else "put"` would.
            option_type=OPTION_CALL if match.group("cp") == "C" else OPTION_PUT,
        )

    def convert_inverse_premium_to_usd(self, price_coin: float, index_price_usd: float) -> float:
        """Convert a coin-denominated inverse option premium to USD.

        ``P_usd = P_coin * S_index_usd``. Applies only to inverse options; a
        linear USDC option's price is already in USDC.

        Raises:
            DeribitApiError: on non-finite or negative inputs.
        """
        if not math.isfinite(price_coin) or price_coin < 0.0:
            raise DeribitApiError(
                f"price_coin must be finite and >= 0, got {price_coin!r}")
        if not math.isfinite(index_price_usd) or index_price_usd <= 0.0:
            raise DeribitApiError(
                f"index_price_usd must be finite and > 0, got {index_price_usd!r}")
        return round(price_coin * index_price_usd, 2)

    # -- payloads -----------------------------------------------------------

    def next_request_id(self) -> int:
        """Allocate a monotonically increasing JSON-RPC request id."""
        return next(self._request_ids)

    def format_json_rpc_request(
        self, method: str, params: Dict[str, Any], request_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """Wrap ``method``/``params`` in a JSON-RPC 2.0 envelope.

        ``request_id`` defaults to a fresh id from this engine's counter. Reusing
        a fixed id across concurrent requests on one WebSocket makes responses
        impossible to correlate.
        """
        if not method:
            raise DeribitApiError("method must be a non-empty string")
        return {
            "jsonrpc": "2.0",
            "id": self.next_request_id() if request_id is None else request_id,
            "method": method,
            "params": params,
        }

    def format_ticker_request(
        self, instrument_name: str, request_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """Build a ``public/ticker`` request for a validated instrument."""
        spec = self.parse_instrument_name(instrument_name)
        return self.format_json_rpc_request(
            "public/ticker", {"instrument_name": spec.instrument_name}, request_id)

    def format_json_rpc_order(
        self, req: DeribitOrderRequest, request_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """Build the ``private/buy`` or ``private/sell`` payload for ``req``.

        The order's ``order_id`` is carried as Deribit's ``label`` so the order
        can be located after a dropped session instead of blindly resent.
        ``post_only`` is always emitted explicitly because Deribit's default is
        ``true``.

        Raises:
            DeribitApiError: if the request is malformed.
        """
        self._validate_order(req)

        params: Dict[str, Any] = {
            "instrument_name": req.instrument_name.strip().upper(),
            "amount": req.amount_coin,
            "type": req.order_type,
            "label": req.order_id,
            "time_in_force": req.time_in_force,
            "post_only": req.post_only,
            "reduce_only": req.reduce_only,
        }
        # Market order types carry no price; sending one is a malformed request.
        if req.order_type not in _MARKET_ORDER_TYPES:
            params["price"] = req.price_coin
        if req.reject_post_only:
            params["reject_post_only"] = True
        if req.valid_until_ms is not None:
            params["valid_until"] = req.valid_until_ms

        return self.format_json_rpc_request(
            f"private/{req.action}", params, request_id)

    # -- validation ---------------------------------------------------------

    def _validate_order(self, req: DeribitOrderRequest) -> None:
        if not req.order_id:
            raise DeribitApiError("order_id must be a non-empty string")
        if len(req.order_id) > MAX_LABEL_LENGTH:
            raise DeribitApiError(
                f"order_id {req.order_id!r} is {len(req.order_id)} characters; Deribit "
                f"labels are limited to {MAX_LABEL_LENGTH}.")
        if req.action not in _VALID_ACTIONS:
            raise DeribitApiError(
                f"action must be one of {sorted(_VALID_ACTIONS)}, got {req.action!r}")
        if req.order_type not in _VALID_ORDER_TYPES:
            raise DeribitApiError(
                f"order_type must be one of {sorted(_VALID_ORDER_TYPES)}, "
                f"got {req.order_type!r}")
        if req.time_in_force not in _VALID_TIME_IN_FORCE:
            raise DeribitApiError(
                f"time_in_force must be one of {sorted(_VALID_TIME_IN_FORCE)}, "
                f"got {req.time_in_force!r}")
        if not math.isfinite(req.amount_coin) or req.amount_coin <= 0.0:
            raise DeribitApiError(
                f"{req.order_id}: amount_coin must be finite and > 0, "
                f"got {req.amount_coin!r}")

        is_market = req.order_type in _MARKET_ORDER_TYPES
        if is_market:
            if req.price_coin is not None:
                raise DeribitApiError(
                    f"{req.order_id}: order_type {req.order_type!r} takes no price; "
                    "leave price_coin as None.")
        else:
            if req.price_coin is None:
                raise DeribitApiError(
                    f"{req.order_id}: order_type {req.order_type!r} requires price_coin")
            if not math.isfinite(req.price_coin) or req.price_coin <= 0.0:
                raise DeribitApiError(
                    f"{req.order_id}: price_coin must be finite and > 0, "
                    f"got {req.price_coin!r}")

        if req.valid_until_ms is not None and req.valid_until_ms <= 0:
            raise DeribitApiError(
                f"{req.order_id}: valid_until_ms must be a positive epoch-millisecond "
                f"timestamp, got {req.valid_until_ms!r}")

    @staticmethod
    def _validate_ticker(ticker: DeribitOptionTicker) -> None:
        if not math.isfinite(ticker.index_price_usd) or ticker.index_price_usd <= 0.0:
            raise DeribitApiError(
                f"index_price_usd must be finite and > 0, got {ticker.index_price_usd!r}")
        for name in ("mark_price_coin", "delta", "gamma", "vega", "theta"):
            value = getattr(ticker, name)
            if not math.isfinite(value):
                raise DeribitApiError(f"ticker.{name} must be finite, got {value!r}")
        if not -1.0 <= ticker.delta <= 1.0:
            raise DeribitApiError(
                f"ticker.delta must lie in [-1, 1] for a single option, got {ticker.delta!r}")

    # -- pre-trade gate -----------------------------------------------------

    def process_option_order(
        self,
        req: DeribitOrderRequest,
        ticker: DeribitOptionTicker,
        available_balance_coin: float,
        margin_quote: Optional[DeribitMarginQuote] = None,
    ) -> DeribitOptionsOrderReport:
        """Validate an order, cost it, and decide whether it may be dispatched.

        A **buy** must fund premium plus estimated fee. A **sell** must fund the
        initial margin plus estimated fee, and therefore *requires* a
        ``margin_quote`` carrying ``initial_margin_coin`` from
        ``private/get_margins``. A sell without one is rejected: this module does
        not model Deribit's margin formula and will not guess it.

        Approval reflects the inputs given and nothing else. It is not a fill,
        not a dispatch, and is stale as soon as the market moves.

        Raises:
            DeribitApiError: if the order, ticker or balance is malformed.
        """
        self._validate_order(req)
        self._validate_ticker(ticker)
        if not math.isfinite(available_balance_coin) or available_balance_coin < 0.0:
            raise DeribitApiError(
                f"available_balance_coin must be finite and >= 0, "
                f"got {available_balance_coin!r}")

        spec = self.parse_instrument_name(req.instrument_name)
        if ticker.instrument_name.strip().upper() != spec.instrument_name:
            raise DeribitApiError(
                f"{req.order_id}: ticker is for {ticker.instrument_name!r} but the order is "
                f"for {spec.instrument_name!r}; a mismatched ticker would price and hedge "
                "the wrong instrument.")

        quote = margin_quote or DeribitMarginQuote()
        rejections: List[str] = []
        warnings: List[str] = []

        # Premium leg. Market orders have no known price, so the mark price is
        # used and the resulting figures are flagged as estimates.
        if req.price_coin is None:
            price_coin = ticker.mark_price_coin
            warnings.append(
                "Market order: premium and delta figures are estimated from mark price; "
                "the executed price will differ.")
        else:
            price_coin = req.price_coin

        total_premium_coin = round(req.amount_coin * price_coin, 8)
        total_premium_usd = round(total_premium_coin * ticker.index_price_usd, 2)
        price_usd = (None if req.price_coin is None
                     else self.convert_inverse_premium_to_usd(
                         req.price_coin, ticker.index_price_usd))

        # Exposure. side_mult signs both the option leg and the premium leg.
        side_mult = 1.0 if req.action == ACTION_BUY else -1.0
        position_delta_coin = round(req.amount_coin * ticker.delta * side_mult, 8)
        position_delta_usd = round(position_delta_coin * ticker.index_price_usd, 2)
        # The premium is settled in coin, so it is itself a coin exposure.
        net_coin_delta = round(
            req.amount_coin * (ticker.delta - price_coin) * side_mult, 8)

        fee_coin = quote.fee_coin
        if not math.isfinite(fee_coin) or fee_coin < 0.0:
            raise DeribitApiError(
                f"{req.order_id}: margin_quote.fee_coin must be finite and >= 0, "
                f"got {fee_coin!r}")
        if margin_quote is None:
            warnings.append(
                "No margin_quote supplied: commission is treated as zero. Deribit charges "
                "an option commission as a percentage of the underlying, so the true cost "
                "is higher than the figure shown here.")

        # Capital requirement by side.
        initial_margin_coin = quote.initial_margin_coin
        if req.action == ACTION_BUY:
            required_coin = round(total_premium_coin + fee_coin, 8)
        else:
            if initial_margin_coin is None:
                rejections.append(
                    "Sell order requires initial_margin_coin from private/get_margins; "
                    "short option margin is not modelled by this module and will not be "
                    "guessed.")
                required_coin = float("nan")
            elif not math.isfinite(initial_margin_coin) or initial_margin_coin < 0.0:
                raise DeribitApiError(
                    f"{req.order_id}: initial_margin_coin must be finite and >= 0, "
                    f"got {initial_margin_coin!r}")
            else:
                required_coin = round(initial_margin_coin + fee_coin, 8)

        # Capital checks.
        if math.isfinite(required_coin):
            if required_coin > available_balance_coin:
                rejections.append(
                    f"Insufficient balance: requires {required_coin} {spec.base_currency}, "
                    f"available {available_balance_coin} {spec.base_currency}.")
            else:
                budget = available_balance_coin * self.max_equity_utilisation
                if required_coin > budget:
                    rejections.append(
                        f"Exceeds house utilisation policy: requires {required_coin} "
                        f"{spec.base_currency}, policy allows "
                        f"{round(budget, 8)} {spec.base_currency} "
                        f"({self.max_equity_utilisation:.0%} of available balance).")

        # Price band, when private/get_margins supplied one.
        if req.price_coin is not None:
            if quote.min_price_coin is not None and req.price_coin < quote.min_price_coin:
                rejections.append(
                    f"Price {req.price_coin} is below the venue minimum "
                    f"{quote.min_price_coin}; the order would be rejected.")
            if quote.max_price_coin is not None and req.price_coin > quote.max_price_coin:
                rejections.append(
                    f"Price {req.price_coin} is above the venue maximum "
                    f"{quote.max_price_coin}; the order would be rejected.")

        # Post-only semantics: warn when a crossing order will be silently reposted.
        if req.post_only and not req.reject_post_only and req.price_coin is not None:
            crosses = (
                req.action == ACTION_BUY
                and ticker.best_ask_price_coin is not None
                and req.price_coin >= ticker.best_ask_price_coin
            ) or (
                req.action == ACTION_SELL
                and ticker.best_bid_price_coin is not None
                and req.price_coin <= ticker.best_bid_price_coin
            )
            if crosses:
                warnings.append(
                    "post_only order would cross the spread: Deribit will reprice it away "
                    "from the touch rather than fill it. Set post_only=False to take "
                    "liquidity, or reject_post_only=True to be rejected instead of moved.")

        payload = self.format_json_rpc_order(req)
        approved = not rejections

        if approved:
            logger.info(
                "DERIBIT ORDER APPROVED FOR DISPATCH [%s]: %s %s %s @ %s %s "
                "(premium $%s, requires %s %s). Not yet sent.",
                req.order_id, req.action.upper(), req.amount_coin, spec.instrument_name,
                price_coin, spec.base_currency, f"{total_premium_usd:,.2f}",
                required_coin, spec.base_currency)
        else:
            for reason in rejections:
                logger.error("DERIBIT ORDER REJECTED [%s]: %s", req.order_id, reason)
        for warning in warnings:
            logger.warning("DERIBIT ORDER WARNING [%s]: %s", req.order_id, warning)

        return DeribitOptionsOrderReport(
            order_id=req.order_id,
            instrument_name=spec.instrument_name,
            action=req.action,
            amount_coin=req.amount_coin,
            price_coin=req.price_coin,
            price_usd_equivalent=price_usd,
            total_premium_coin=total_premium_coin,
            total_premium_usd=total_premium_usd,
            position_delta_coin=position_delta_coin,
            position_delta_usd=position_delta_usd,
            net_coin_delta_after_premium=net_coin_delta,
            required_coin=required_coin,
            estimated_fee_coin=fee_coin,
            initial_margin_coin=initial_margin_coin,
            json_rpc_payload=payload,
            is_approved_for_dispatch=approved,
            rejection_reasons=rejections,
            warnings=warnings,
        )

    # -- portfolio ----------------------------------------------------------

    def aggregate_portfolio_greeks(
        self, positions: Iterable["DeribitOptionPosition"]
    ) -> DeribitPortfolioGreeks:
        """Aggregate signed greeks across option positions sharing one index.

        Gamma, vega and theta are summed in coin terms exactly as delta is:
        ``size_coin * greek``, signed by direction. Positions must all reference
        the same ``index_price_usd``, since a single USD delta across two
        different underlyings would be meaningless.

        Raises:
            DeribitApiError: if positions disagree on the index price.
        """
        delta_coin = gamma_coin = vega_coin = theta_coin = net_coin = 0.0
        index_price: Optional[float] = None
        count = 0

        for position in positions:
            self._validate_ticker(position.ticker)
            if not math.isfinite(position.size_coin):
                raise DeribitApiError(
                    f"{position.ticker.instrument_name}: size_coin must be finite, "
                    f"got {position.size_coin!r}")

            if index_price is None:
                index_price = position.ticker.index_price_usd
            elif not math.isclose(index_price, position.ticker.index_price_usd,
                                  rel_tol=1e-9):
                raise DeribitApiError(
                    "All positions must share one index price to aggregate a single USD "
                    f"delta; saw {index_price} and {position.ticker.index_price_usd}.")

            size = position.size_coin  # already signed: negative for shorts
            delta_coin += size * position.ticker.delta
            gamma_coin += size * position.ticker.gamma
            vega_coin += size * position.ticker.vega
            theta_coin += size * position.ticker.theta
            net_coin += size * (position.ticker.delta - position.ticker.mark_price_coin)
            count += 1

        if count == 0:
            return DeribitPortfolioGreeks(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0)

        assert index_price is not None  # count > 0 guarantees this
        return DeribitPortfolioGreeks(
            delta_coin=round(delta_coin, 8),
            delta_usd=round(delta_coin * index_price, 2),
            gamma_coin=round(gamma_coin, 8),
            vega_coin=round(vega_coin, 8),
            theta_coin=round(theta_coin, 8),
            net_coin_delta_after_premium=round(net_coin, 8),
            position_count=count,
        )


@dataclass
class DeribitOptionPosition:
    """A held option position.

    ``size_coin`` is signed: positive long, negative short, in the underlying
    base currency coin.
    """

    ticker: DeribitOptionTicker
    size_coin: float
