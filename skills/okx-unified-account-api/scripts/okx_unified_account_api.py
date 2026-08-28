"""okx-unified-account-api: request signing, environment separation, multi-currency
margin-risk arithmetic, and order-payload validation for the OKX v5 Unified Account.

Scope
-----
This module is an **offline helper**. It signs requests, builds headers, builds and
validates ``POST /api/v5/trade/order`` payloads, and computes a local margin-risk
view. It deliberately performs **no network I/O**: transport, retry policy, and
order-state reconciliation belong to the caller (see ``order-placement-idempotency``
and ``multi-broker-rate-limit-handling``).

Authority
---------
The exchange is the authoritative source for account risk. ``adjEq`` and ``mgnRatio``
from ``GET /api/v5/account/balance`` are what OKX actually liquidates against; the
numbers produced here are a *local, conservative approximation* used for pre-trade
gating and alerting between polls. When the two disagree, OKX wins.

Margin model (multi-currency margin mode, ``acctLv = 3``)
--------------------------------------------------------
OKX defines, for multi-currency cross-margin:

* ``Discounted equity = SUM over currencies [ Currency equity * Discount rate * USD price ]``
* ``Adjusted equity = Discounted equity + spot/spot-with-margin order loss
  - assets frozen in options buy orders for closing positions
  - assets frozen in isolated margin mode orders
  - estimated trading fees from all open orders``
* ``Maintenance margin ratio = Adjusted equity / (Maintenance margin + Liquidation fees)``
* Risk alert at ``<= 300%``; order cancellation then forced liquidation at ``<= 100%``.

Source: OKX Help Center, "IV. Multi-currency margin mode: cross margin trading".

This module computes discounted equity exactly, and models the adjusted-equity
deductions and the liquidation-fee term as **caller-supplied inputs**
(``equity_deductions_usd``, ``liquidation_fee_usd``) rather than inventing them.
Both default to zero, which is the *optimistic* end of the range -- pass real values,
or treat the resulting margin ratio as an upper bound.

Discount rates are **tiered by currency amount and applied marginally**, like tax
brackets, not as one rate over the whole holding. Tiers change: fetch them from
``GET /api/v5/public/discount-rate-interest-free-quota`` rather than hard-coding.

Negative equity
---------------
A negative currency equity is a liability. OKX describes the discount rate as the
value attributed to an asset *when used as collateral*, and its published worked
examples apply it to positive holdings only; OKX does **not** publish the exact
treatment of negative equity inside ``adjEq``. Applying a haircut to a liability
would shrink it and inflate the margin ratio, so this module deliberately values
negative equity at full USD magnitude with no haircut benefit. That is the
conservative direction, and it is an approximation, not a reproduction of OKX's
internal formula.

Environment separation
----------------------
OKX demo trading is not a separate host in current SDKs -- it is the
``x-simulated-trading`` header plus a demo-specific API key. Because a key and a
header can be mismatched independently, the environment is a constructor argument
here and every signed header set carries it explicitly. ``simulated_trading``
defaults to ``False`` (live), so a caller that forgets it gets live behaviour with
live keys rather than a silent paper-trading no-op.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import math
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Dict, List, Optional, Sequence, Union

logger = logging.getLogger(__name__)

__all__ = [
    "ACCOUNT_MODE_MULTI_CURRENCY_MARGIN",
    "CLIENT_ORDER_ID_PATTERN",
    "HEADER_SIMULATED_TRADING",
    "LIQUIDATION_MARGIN_RATIO_PCT",
    "MAX_CLIENT_ORDER_ID_LENGTH",
    "MAX_TIMESTAMP_SKEW_SECONDS",
    "PLACE_ORDER_RATE_LIMIT",
    "PRICED_ORDER_TYPES",
    "RISK_ALERT_MARGIN_RATIO_PCT",
    "STATUS_LIQUIDATION_RISK_CALL",
    "STATUS_MARGIN_WARNING",
    "STATUS_SAFE",
    "UNPRICED_ORDER_TYPES",
    "VALID_POS_SIDES",
    "VALID_SIDES",
    "VALID_TD_MODES",
    "VALID_TGT_CCY",
    "OKXAccountReport",
    "OKXDiscountTier",
    "OKXTokenBalance",
    "OKXUnifiedAccountEngine",
    "OKXUnifiedAccountError",
    "new_client_order_id",
]

# --- OKX-documented constants -------------------------------------------------

#: Account mode this margin model applies to (``acctLv = 3``).
ACCOUNT_MODE_MULTI_CURRENCY_MARGIN = "multi_currency_margin"

#: Risk alert: OKX warns to reduce positions at a maintenance margin ratio <= 300%.
RISK_ALERT_MARGIN_RATIO_PCT = 300.0
#: Pre-liquidation: at <= 100% OKX cancels open orders, then force-liquidates.
LIQUIDATION_MARGIN_RATIO_PCT = 100.0

STATUS_SAFE = "SAFE"
STATUS_MARGIN_WARNING = "MARGIN_WARNING"
STATUS_LIQUIDATION_RISK_CALL = "LIQUIDATION_RISK_CALL"

#: Demo-trading header. "1" routes to simulated trading, "0" to live.
HEADER_SIMULATED_TRADING = "x-simulated-trading"

#: OKX rejects a request whose OK-ACCESS-TIMESTAMP is further than this from server
#: time, with error 50102 ("Timestamp request expired").
MAX_TIMESTAMP_SKEW_SECONDS = 30.0

#: ``OK-ACCESS-TIMESTAMP`` must be ISO 8601 UTC with exactly milliseconds, e.g.
#: ``2020-12-08T09:08:57.715Z``. Epoch seconds/millis are rejected by OKX.
_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%S.%f"
_TIMESTAMP_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")

#: clOrdId: case-sensitive alphanumerics only, up to 32 characters. A hyphenated
#: UUID (36 chars, non-alphanumeric) is rejected by OKX.
MAX_CLIENT_ORDER_ID_LENGTH = 32
CLIENT_ORDER_ID_PATTERN = re.compile(r"^[A-Za-z0-9]{1,32}$")

VALID_TD_MODES = ("cross", "isolated", "cash")
VALID_SIDES = ("buy", "sell")
VALID_POS_SIDES = ("long", "short", "net")
VALID_TGT_CCY = ("base_ccy", "quote_ccy")

#: ``px`` is only applicable to these order types.
PRICED_ORDER_TYPES = ("limit", "post_only", "fok", "ioc")
#: Order types that must not carry ``px``.
UNPRICED_ORDER_TYPES = ("market", "optimal_limit_ioc")

#: Documented rate limit for POST /api/v5/trade/order (UserID + instrument ID).
PLACE_ORDER_RATE_LIMIT = "60 requests per 2 seconds"

_HTTP_METHODS = ("GET", "POST", "PUT", "DELETE")

Number = Union[int, float, str, Decimal]


class OKXUnifiedAccountError(ValueError):
    """Raised when an input cannot be turned into a valid OKX request or risk view."""


# --- helpers ------------------------------------------------------------------


def _require_text(value: object, field_name: str) -> str:
    """Returns a stripped, non-empty string or raises."""
    if not isinstance(value, str) or not value.strip():
        raise OKXUnifiedAccountError(
            f"{field_name} must be a non-empty string, got {value!r}")
    return value.strip()


def _require_finite(value: object, field_name: str) -> float:
    """Returns a finite float or raises.

    NaN is rejected rather than scored: every ``<``/``>`` comparison against NaN is
    ``False``, so a NaN that reaches a threshold ladder silently picks a branch.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise OKXUnifiedAccountError(
            f"{field_name} must be a real number, got {value!r}")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise OKXUnifiedAccountError(f"{field_name} must be finite, got {value!r}")
    return numeric


def _format_decimal(value: Number, field_name: str) -> str:
    """Renders a quantity as a plain fixed-point decimal string for the OKX JSON body.

    ``str(1e-8)`` is ``'1e-08'``, which OKX will not parse as a number; scientific
    notation is normalised away here. Pass ``str`` or ``Decimal`` when the exact
    digits matter -- a ``float`` can only render the value it actually holds
    (``str(0.1 + 0.2)`` is ``'0.30000000000000004'``), which this function
    reproduces faithfully rather than silently rounding to something the exchange
    might happen to accept.
    """
    if isinstance(value, bool):
        raise OKXUnifiedAccountError(f"{field_name} must be a number, got {value!r}")
    try:
        if isinstance(value, Decimal):
            decimal_value = value
        elif isinstance(value, int):
            decimal_value = Decimal(value)
        elif isinstance(value, float):
            if not math.isfinite(value):
                raise OKXUnifiedAccountError(
                    f"{field_name} must be finite, got {value!r}")
            # repr() is the shortest string that round-trips the float exactly.
            decimal_value = Decimal(repr(value))
        elif isinstance(value, str):
            decimal_value = Decimal(value.strip())
        else:
            raise OKXUnifiedAccountError(
                f"{field_name} must be a number, got {value!r}")
    except InvalidOperation as exc:
        raise OKXUnifiedAccountError(
            f"{field_name} is not a valid decimal: {value!r}") from exc

    if not decimal_value.is_finite():
        raise OKXUnifiedAccountError(f"{field_name} must be finite, got {value!r}")
    if decimal_value <= 0:
        raise OKXUnifiedAccountError(
            f"{field_name} must be strictly positive, got {value!r}")
    return format(decimal_value, "f")


def new_client_order_id(prefix: str = "") -> str:
    """Mints an OKX-compliant ``clOrdId``: alphanumeric, at most 32 characters.

    ``uuid.uuid4().hex`` is exactly 32 alphanumeric characters, so a prefix is
    accommodated by truncating the random tail. At least 8 random characters are
    always retained.
    """
    prefix = prefix.strip()
    if prefix and not prefix.isalnum():
        raise OKXUnifiedAccountError(
            "clOrdId prefix must be alphanumeric (OKX rejects '-' and '_'), got "
            f"{prefix!r}")
    if len(prefix) > MAX_CLIENT_ORDER_ID_LENGTH - 8:
        raise OKXUnifiedAccountError(
            "clOrdId prefix must leave at least 8 random characters within the "
            f"{MAX_CLIENT_ORDER_ID_LENGTH}-character limit, got {len(prefix)} "
            "characters")
    return (prefix + uuid.uuid4().hex)[:MAX_CLIENT_ORDER_ID_LENGTH]


# --- data model ---------------------------------------------------------------


@dataclass(frozen=True)
class OKXDiscountTier:
    """One bracket of an OKX discount-rate schedule.

    ``max_ccy_amount`` is the inclusive upper bound of the bracket **in currency
    amount** (e.g. BTC), not USD. ``None`` means unbounded and is valid only as the
    final tier. Rates apply marginally: under a 0-20 BTC first tier, a 100 BTC
    holding consumes 20 BTC at that tier's rate before moving to the next.
    """

    max_ccy_amount: Optional[float]
    discount_rate: float

    def __post_init__(self) -> None:
        if self.max_ccy_amount is not None:
            bound = _require_finite(
                self.max_ccy_amount, "OKXDiscountTier.max_ccy_amount")
            if bound <= 0:
                raise OKXUnifiedAccountError(
                    f"OKXDiscountTier.max_ccy_amount must be > 0, got {bound!r}")
        rate = _require_finite(self.discount_rate, "OKXDiscountTier.discount_rate")
        if not 0.0 <= rate <= 1.0:
            raise OKXUnifiedAccountError(
                f"OKXDiscountTier.discount_rate must be within [0, 1], got {rate!r}")


@dataclass
class OKXTokenBalance:
    """A single currency's equity inside a unified account.

    ``total_balance`` is **signed**: a negative value is a liability (a borrowing).
    ``discount_factor`` is the flat fallback used when ``discount_tiers`` is absent;
    it is ignored when tiers are supplied.
    """

    currency: str
    total_balance: float
    usd_price: float
    discount_factor: float = 1.0
    discount_tiers: Optional[Sequence[OKXDiscountTier]] = None

    def __post_init__(self) -> None:
        self.currency = _require_text(self.currency, "OKXTokenBalance.currency").upper()
        self.total_balance = _require_finite(
            self.total_balance, "OKXTokenBalance.total_balance")
        self.usd_price = _require_finite(self.usd_price, "OKXTokenBalance.usd_price")
        if self.usd_price < 0:
            raise OKXUnifiedAccountError(
                f"OKXTokenBalance.usd_price must be >= 0, got {self.usd_price!r}")
        self.discount_factor = _require_finite(
            self.discount_factor, "OKXTokenBalance.discount_factor")
        if not 0.0 <= self.discount_factor <= 1.0:
            raise OKXUnifiedAccountError(
                "OKXTokenBalance.discount_factor is a haircut within [0, 1]; got "
                f"{self.discount_factor!r}")
        if self.discount_tiers is not None:
            self.discount_tiers = self._validate_tiers(self.discount_tiers)

    @staticmethod
    def _validate_tiers(
        tiers: Sequence[OKXDiscountTier],
    ) -> Sequence[OKXDiscountTier]:
        tiers = tuple(tiers)
        if not tiers:
            raise OKXUnifiedAccountError(
                "discount_tiers, when supplied, must not be empty")
        previous_bound = 0.0
        for index, tier in enumerate(tiers):
            if not isinstance(tier, OKXDiscountTier):
                raise OKXUnifiedAccountError(
                    f"discount_tiers[{index}] must be an OKXDiscountTier, got {tier!r}")
            if tier.max_ccy_amount is None:
                if index != len(tiers) - 1:
                    raise OKXUnifiedAccountError(
                        "only the final discount tier may be unbounded "
                        f"(max_ccy_amount=None), found one at index {index}")
                continue
            if tier.max_ccy_amount <= previous_bound:
                raise OKXUnifiedAccountError(
                    "discount tier bounds must strictly ascend; tier "
                    f"{index} bound {tier.max_ccy_amount} follows {previous_bound}")
            previous_bound = tier.max_ccy_amount
        return tiers

    def gross_usd_value(self) -> float:
        """Signed USD value before any haircut."""
        return self.total_balance * self.usd_price

    def discounted_usd_value(self) -> float:
        """Signed USD value after the collateral haircut.

        A negative balance is a liability and is returned at full magnitude: a
        haircut applied to a liability would shrink it and inflate the margin ratio.
        """
        if self.total_balance <= 0:
            return self.gross_usd_value()
        if self.discount_tiers:
            return self._tiered_discounted_usd_value()
        return self.gross_usd_value() * self.discount_factor

    def _tiered_discounted_usd_value(self) -> float:
        remaining = self.total_balance
        covered = 0.0
        discounted = 0.0
        for tier in self.discount_tiers or ():
            if remaining <= 0:
                break
            bracket_width = (
                math.inf if tier.max_ccy_amount is None
                else tier.max_ccy_amount - covered)
            consumed = min(remaining, bracket_width)
            discounted += consumed * self.usd_price * tier.discount_rate
            remaining -= consumed
            covered += consumed
        if remaining > 0:
            raise OKXUnifiedAccountError(
                f"{self.currency}: discount tier schedule tops out at {covered} "
                f"{self.currency} but the balance is {self.total_balance}. Refusing "
                "to value the uncovered remainder -- refresh the schedule from "
                "GET /api/v5/public/discount-rate-interest-free-quota, or add an "
                "unbounded final tier if that matches the published schedule.")
        return discounted


@dataclass
class OKXAccountReport:
    """Local margin-risk view for a multi-currency margin unified account."""

    account_mode: str
    total_usd_equity: float
    discounted_usd_equity: float
    adjusted_usd_equity: float
    maintenance_margin_usd: float
    liquidation_fee_usd: float
    equity_deductions_usd: float
    margin_ratio_pct: float
    status: str
    audit_notes: str
    warnings: List[str] = field(default_factory=list)


# --- engine -------------------------------------------------------------------


class OKXUnifiedAccountEngine:
    """Signs OKX v5 requests, builds validated order payloads, and computes a local
    multi-currency margin-risk view.

    The engine holds credentials but performs no I/O and keeps no mutable state
    between calls, so concurrent use is safe. ``repr`` is redacted so a secret
    cannot reach a log line through an exception trace.
    """

    def __init__(
        self,
        api_key: str,
        secret_key: str,
        passphrase: str,
        *,
        simulated_trading: bool = False,
    ) -> None:
        self.api_key = _require_text(api_key, "api_key")
        self.secret_key = _require_text(secret_key, "secret_key")
        self.passphrase = _require_text(passphrase, "passphrase")
        self.simulated_trading = bool(simulated_trading)
        logger.info(
            "OKXUnifiedAccountEngine initialised for %s trading",
            "SIMULATED (demo)" if self.simulated_trading else "LIVE")

    def __repr__(self) -> str:
        return (
            "OKXUnifiedAccountEngine(api_key=<redacted>, secret_key=<redacted>, "
            f"passphrase=<redacted>, simulated_trading={self.simulated_trading})")

    # -- authentication --------------------------------------------------------

    @staticmethod
    def build_timestamp(moment: Optional[datetime] = None) -> str:
        """Formats ``moment`` (default: now) as an ``OK-ACCESS-TIMESTAMP`` string.

        A naive ``moment`` is treated as UTC; an aware one is converted to UTC.
        """
        if moment is None:
            moment = datetime.now(timezone.utc)
        elif moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        else:
            moment = moment.astimezone(timezone.utc)
        return moment.strftime(_TIMESTAMP_FORMAT)[:-3] + "Z"

    @staticmethod
    def parse_timestamp(timestamp: str) -> datetime:
        """Parses an ``OK-ACCESS-TIMESTAMP`` string, raising on any other shape.

        Epoch seconds or milliseconds are the most common cause of OKX error 50102,
        so they are rejected here rather than at the exchange.
        """
        text = _require_text(timestamp, "timestamp")
        if not _TIMESTAMP_PATTERN.match(text):
            raise OKXUnifiedAccountError(
                "OK-ACCESS-TIMESTAMP must be ISO 8601 UTC with exactly milliseconds, "
                f"e.g. '2020-12-08T09:08:57.715Z'; got {timestamp!r}. Epoch "
                "timestamps are rejected by OKX with error 50102.")
        try:
            parsed = datetime.strptime(text[:-1], _TIMESTAMP_FORMAT)
        except ValueError as exc:
            # Shape-valid but impossible, e.g. '2020-13-45T09:08:57.715Z'.
            raise OKXUnifiedAccountError(
                f"OK-ACCESS-TIMESTAMP {timestamp!r} is not a real UTC instant: "
                f"{exc}") from exc
        return parsed.replace(tzinfo=timezone.utc)

    def clock_skew_seconds(self, server_time_ms: float, timestamp: str) -> float:
        """Returns ``timestamp - server_time`` in seconds, warning beyond the window.

        ``server_time_ms`` is the ``ts`` field of ``GET /api/v5/public/time`` (Unix
        epoch milliseconds, UTC). OKX rejects requests further than
        ``MAX_TIMESTAMP_SKEW_SECONDS`` from server time with error 50102.
        """
        server_ms = _require_finite(server_time_ms, "server_time_ms")
        local = self.parse_timestamp(timestamp)
        skew = local.timestamp() - server_ms / 1000.0
        if abs(skew) > MAX_TIMESTAMP_SKEW_SECONDS:
            logger.warning(
                "OKX clock skew %.3fs exceeds the %.0fs window; requests signed with "
                "this timestamp will be rejected with error 50102",
                skew, MAX_TIMESTAMP_SKEW_SECONDS)
        return skew

    def generate_signature(
        self, timestamp: str, method: str, request_path: str, body: str = ""
    ) -> str:
        """Returns the ``OK-ACCESS-SIGN`` value for one request.

        The prehash string is ``timestamp + METHOD + requestPath + body``, signed
        with the secret key using HMAC-SHA256 and Base64-encoded. The secret is used
        as raw UTF-8 bytes -- it is **not** Base64-decoded first (unlike Coinbase).

        ``request_path`` must include the query string for GET requests: OKX counts
        GET parameters as part of the requestPath, not the body. ``body`` must be the
        exact serialised string that is transmitted, byte for byte.
        """
        self.parse_timestamp(timestamp)
        method_upper = _require_text(method, "method").upper()
        if method_upper not in _HTTP_METHODS:
            raise OKXUnifiedAccountError(
                f"method must be one of {_HTTP_METHODS}, got {method!r}")
        path = _require_text(request_path, "request_path")
        if not path.startswith("/"):
            raise OKXUnifiedAccountError(
                "request_path must be the absolute endpoint path including any query "
                f"string, e.g. '/api/v5/account/balance?ccy=BTC'; got {request_path!r}")
        if not isinstance(body, str):
            raise OKXUnifiedAccountError(
                "body must be the exact serialised request body string (use '' when "
                f"there is none), got {body!r}")

        prehash = timestamp + method_upper + path + body
        mac = hmac.new(
            self.secret_key.encode("utf-8"),
            prehash.encode("utf-8"),
            digestmod=hashlib.sha256,
        )
        return base64.b64encode(mac.digest()).decode("utf-8")

    def get_auth_headers(
        self,
        method: str,
        request_path: str,
        body: str = "",
        timestamp: Optional[str] = None,
    ) -> Dict[str, str]:
        """Builds the full OKX v5 header set, including the environment flag.

        ``x-simulated-trading`` is always emitted so a demo key can never be silently
        pointed at live trading, or a live key at demo, by an omitted header.
        """
        if timestamp is None:
            timestamp = self.build_timestamp()
        signature = self.generate_signature(timestamp, method, request_path, body)
        return {
            "OK-ACCESS-KEY": self.api_key,
            "OK-ACCESS-SIGN": signature,
            "OK-ACCESS-TIMESTAMP": timestamp,
            "OK-ACCESS-PASSPHRASE": self.passphrase,
            "Content-Type": "application/json",
            HEADER_SIMULATED_TRADING: "1" if self.simulated_trading else "0",
        }

    # -- margin risk -----------------------------------------------------------

    def compute_multi_currency_margin(
        self,
        balances: Sequence[OKXTokenBalance],
        maintenance_margin_usd: float,
        *,
        equity_deductions_usd: float = 0.0,
        liquidation_fee_usd: float = 0.0,
    ) -> OKXAccountReport:
        """Computes discounted equity, adjusted equity, and the maintenance margin ratio.

        ``equity_deductions_usd`` is the non-negative net amount OKX subtracts from
        discounted equity to reach ``adjEq`` (assets frozen in options buy orders for
        closing positions, assets frozen in isolated margin orders, estimated trading
        fees on open orders, net of spot order loss). ``liquidation_fee_usd`` is the
        liquidation-fee term OKX adds to the maintenance margin in the denominator.
        Both default to ``0.0``, which makes the reported ratio an **upper bound** on
        OKX's own ``mgnRatio``.

        With no maintenance margin requirement the ratio is undefined; it is reported
        as ``+inf`` (or ``-inf`` when adjusted equity is negative) rather than a magic
        sentinel, and the threshold ladder is applied unchanged.
        """
        if isinstance(balances, (str, bytes)) or not isinstance(balances, Sequence):
            raise OKXUnifiedAccountError(
                f"balances must be a sequence of OKXTokenBalance, got {balances!r}")
        for index, balance in enumerate(balances):
            if not isinstance(balance, OKXTokenBalance):
                raise OKXUnifiedAccountError(
                    f"balances[{index}] must be an OKXTokenBalance, got {balance!r}")

        maintenance_margin = _require_finite(
            maintenance_margin_usd, "maintenance_margin_usd")
        if maintenance_margin < 0:
            raise OKXUnifiedAccountError(
                "maintenance_margin_usd must be >= 0; a negative requirement is a "
                f"data error, not a safe account. Got {maintenance_margin!r}")
        liquidation_fee = _require_finite(liquidation_fee_usd, "liquidation_fee_usd")
        if liquidation_fee < 0:
            raise OKXUnifiedAccountError(
                f"liquidation_fee_usd must be >= 0, got {liquidation_fee!r}")
        deductions = _require_finite(equity_deductions_usd, "equity_deductions_usd")
        if deductions < 0:
            raise OKXUnifiedAccountError(
                "equity_deductions_usd must be >= 0; pass the net amount subtracted "
                f"from discounted equity. Got {deductions!r}")

        warnings: List[str] = []
        total_usd_equity = math.fsum(b.gross_usd_value() for b in balances)
        discounted_usd_equity = math.fsum(b.discounted_usd_value() for b in balances)
        # Individually finite balances and prices can still overflow to +/-inf once
        # multiplied and summed. An infinite equity would sail through the threshold
        # ladder as SAFE, so it is refused rather than scored.
        for label, value in (("total_usd_equity", total_usd_equity),
                             ("discounted_usd_equity", discounted_usd_equity)):
            if not math.isfinite(value):
                raise OKXUnifiedAccountError(
                    f"{label} overflowed to {value!r}; check the balance and price "
                    "inputs rather than trusting the resulting risk status")
        adjusted_usd_equity = discounted_usd_equity - deductions

        if not balances:
            warnings.append("No balances supplied: equity is reported as zero.")
        if deductions == 0.0 and liquidation_fee == 0.0:
            warnings.append(
                "equity_deductions_usd and liquidation_fee_usd are both zero, so the "
                "margin ratio is an upper bound on OKX's own mgnRatio.")

        denominator = maintenance_margin + liquidation_fee
        if denominator == 0.0:
            warnings.append(
                "No maintenance margin requirement: margin ratio is undefined and "
                "reported as infinity.")
            margin_ratio_pct = math.inf if adjusted_usd_equity >= 0 else -math.inf
        else:
            margin_ratio_pct = (adjusted_usd_equity / denominator) * 100.0

        if math.isnan(margin_ratio_pct):
            raise OKXUnifiedAccountError(
                "margin ratio evaluated to NaN; refusing to emit a risk status")

        if margin_ratio_pct > RISK_ALERT_MARGIN_RATIO_PCT:
            status = STATUS_SAFE
        elif margin_ratio_pct > LIQUIDATION_MARGIN_RATIO_PCT:
            status = STATUS_MARGIN_WARNING
        else:
            status = STATUS_LIQUIDATION_RISK_CALL

        ratio_text = (
            "undefined" if math.isinf(margin_ratio_pct)
            else f"{margin_ratio_pct:.2f}%")
        notes = (
            f"OKX UNIFIED ACCOUNT [{status}]: Total USD Equity = "
            f"${total_usd_equity:,.2f}, Discounted Equity = "
            f"${discounted_usd_equity:,.2f}, Adjusted Equity = "
            f"${adjusted_usd_equity:,.2f}, Maintenance Margin + Liquidation Fees = "
            f"${denominator:,.2f}, Margin Ratio = {ratio_text}."
        )
        if status == STATUS_SAFE:
            logger.info(notes)
        else:
            logger.warning(notes)

        return OKXAccountReport(
            account_mode=ACCOUNT_MODE_MULTI_CURRENCY_MARGIN,
            total_usd_equity=round(total_usd_equity, 2),
            discounted_usd_equity=round(discounted_usd_equity, 2),
            adjusted_usd_equity=round(adjusted_usd_equity, 2),
            maintenance_margin_usd=round(maintenance_margin, 2),
            liquidation_fee_usd=round(liquidation_fee, 2),
            equity_deductions_usd=round(deductions, 2),
            margin_ratio_pct=(
                margin_ratio_pct if math.isinf(margin_ratio_pct)
                else round(margin_ratio_pct, 2)),
            status=status,
            audit_notes=notes,
            warnings=warnings,
        )

    # -- order payload ---------------------------------------------------------

    def build_order_payload(
        self,
        inst_id: str,
        td_mode: str,
        side: str,
        ord_type: str,
        size: Number,
        price: Optional[Number] = None,
        *,
        cl_ord_id: str,
        pos_side: Optional[str] = None,
        tgt_ccy: Optional[str] = None,
    ) -> Dict[str, str]:
        """Builds a validated ``POST /api/v5/trade/order`` payload.

        ``cl_ord_id`` is **required**: it is the only idempotency handle OKX offers,
        and without it a retry after a timed-out submission can double-fill. Mint one
        with :func:`new_client_order_id`, persist it *before* sending, and reuse the
        same value on every retry of that one order.

        ``size`` maps to ``sz``, whose unit depends on the instrument: for
        FUTURES/SWAP/OPTION it is the **number of contracts**, not the base quantity
        -- read ``ctVal`` from ``GET /api/v5/public/instruments``. For SPOT limit
        orders it is base currency; for SPOT market orders it follows ``tgtCcy``
        (OKX defaults to ``quote_ccy`` for buy and ``base_ccy`` for sell).

        ``pos_side`` is required in long/short (hedge) position mode for
        FUTURES/SWAP and must be omitted, or ``net``, in net mode. This builder
        cannot see the account's position mode, so it validates the value it is given
        but cannot tell you that you needed one.
        """
        payload: Dict[str, str] = {
            "instId": _require_text(inst_id, "inst_id"),
            "tdMode": self._require_enum(td_mode, VALID_TD_MODES, "td_mode"),
            "side": self._require_enum(side, VALID_SIDES, "side"),
            "ordType": self._require_ord_type(ord_type),
            "sz": _format_decimal(size, "size"),
            "clOrdId": self._require_client_order_id(cl_ord_id),
        }

        normalised_ord_type = payload["ordType"]
        if normalised_ord_type in PRICED_ORDER_TYPES:
            if price is None:
                raise OKXUnifiedAccountError(
                    f"price is required for ordType '{normalised_ord_type}': OKX "
                    f"applies px to {PRICED_ORDER_TYPES} orders and rejects them "
                    "without it")
            payload["px"] = _format_decimal(price, "price")
        elif price is not None:
            raise OKXUnifiedAccountError(
                f"price must be omitted for ordType '{normalised_ord_type}': OKX only "
                f"applies px to {PRICED_ORDER_TYPES} orders")

        if pos_side is not None:
            payload["posSide"] = self._require_enum(
                pos_side, VALID_POS_SIDES, "pos_side")
        if tgt_ccy is not None:
            payload["tgtCcy"] = self._require_enum(tgt_ccy, VALID_TGT_CCY, "tgt_ccy")

        logger.info(
            "Built OKX order payload instId=%s tdMode=%s side=%s ordType=%s "
            "clOrdId=%s simulated=%s",
            payload["instId"], payload["tdMode"], payload["side"],
            payload["ordType"], payload["clOrdId"], self.simulated_trading)
        return payload

    @staticmethod
    def _require_enum(value: object, allowed: Sequence[str], field_name: str) -> str:
        text = _require_text(value, field_name).lower()
        if text not in allowed:
            raise OKXUnifiedAccountError(
                f"{field_name} must be one of {tuple(allowed)}, got {value!r}")
        return text

    @staticmethod
    def _require_ord_type(ord_type: object) -> str:
        text = _require_text(ord_type, "ord_type").lower()
        allowed = PRICED_ORDER_TYPES + UNPRICED_ORDER_TYPES
        if text not in allowed:
            raise OKXUnifiedAccountError(
                f"ord_type must be one of {allowed}, got {ord_type!r}. OKX supports "
                "further order types (options MMP variants, spread types); confirm "
                "their px semantics against the current docs before adding them here.")
        return text

    @staticmethod
    def _require_client_order_id(cl_ord_id: object) -> str:
        text = _require_text(cl_ord_id, "cl_ord_id")
        if not CLIENT_ORDER_ID_PATTERN.match(text):
            raise OKXUnifiedAccountError(
                "cl_ord_id must be 1-32 case-sensitive alphanumeric characters -- OKX "
                "rejects '-' and '_', so a hyphenated UUID (36 chars) fails. Got "
                f"{cl_ord_id!r}. Use new_client_order_id() to mint a compliant value.")
        return text
