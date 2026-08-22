"""
coinbase-advanced-trade-api-migration:
Translation of legacy Coinbase Pro / Coinbase Exchange order requests into
Coinbase Advanced Trade v3 ``order_configuration`` payloads.

What this module is
-------------------
It is a *translation* layer, not an HTTP client and not an authenticator.
``translate_order_request`` returns the JSON body for
``POST https://api.coinbase.com/api/v3/brokerage/orders``; signing and sending
it is the caller's job. Advanced Trade authenticates with a short-lived ES256
JWT bearer token derived from a CDP (ECDSA) key, which is a different
credential and a different scheme from the legacy ``CB-ACCESS-KEY`` /
``CB-ACCESS-SIGN`` / ``CB-ACCESS-PASSPHRASE`` HMAC headers - see SKILL.md.

The three translations that are not mechanical
----------------------------------------------
Most legacy fields map across by rename. Three do not, and each one silently
changes what the order *does* if it is guessed at:

1. **Stop direction.** Legacy Pro encodes the trigger condition in a separate
   ``stop`` field: ``"loss"`` triggers when the last trade price moves to or
   below ``stop_price``; ``"entry"`` triggers when it moves to or above.
   Advanced Trade encodes the same thing as ``stop_direction``
   (``STOP_DIRECTION_STOP_DOWN`` / ``STOP_DIRECTION_STOP_UP``). The mapping is
   ``loss -> DOWN``, ``entry -> UP`` and it is **independent of side** - a sell
   stop-entry sits above the market and a buy stop-loss sits below it, so
   inferring the direction from ``side`` inverts the trigger on exactly those
   orders. This module requires ``stop`` and refuses to guess.

2. **Time in force.** Legacy Pro carries ``time_in_force`` alongside the order
   type; Advanced Trade folds it into the configuration key
   (``limit_limit_gtc`` / ``limit_limit_gtd`` / ``limit_limit_fok``). Dropping
   it turns a legacy IOC or FOK order - one that was meant to leave no residue -
   into a resting GTC order. Advanced Trade has no plain limit-IOC
   configuration; the only IOC limit variant, ``sor_limit_ioc``, routes through
   Smart Order Routing and is therefore a different execution instruction, not
   a like-for-like substitute. IOC is rejected rather than downgraded.

3. **Market order sizing.** Legacy Pro sizes a market buy either in base units
   (``size``) or in quote currency (``funds``). Advanced Trade's
   ``market_market_ioc`` accepts ``quote_size`` or ``base_size`` for a buy and
   ``base_size`` only for a sell, and the two are not interchangeable: sending
   a quote amount as ``base_size`` buys orders of magnitude too much.

Numeric formatting
------------------
Advanced Trade takes sizes and prices as decimal *strings*. ``str()`` on a
Python float renders small values in scientific notation (``str(1e-8)`` is
``'1e-08'``), which is not a decimal string. Every numeric field is routed
through ``_format_decimal_string``, which parses with ``decimal.Decimal`` and
renders with ``format(d, 'f')`` - preserving the caller's trailing zeros and
never emitting an exponent. Rounding to a product's ``base_increment`` /
``quote_increment`` is deliberately *not* done here: this module does not fetch
product metadata, and silently rounding a size is a worse failure than a
rejection the caller can see.

References
----------
- Advanced Trade REST, Create Order:
  https://docs.cdp.coinbase.com/api-reference/advanced-trade-api/rest-api/orders/create-order
- Advanced Trade order management guide:
  https://docs.cdp.coinbase.com/coinbase-app/advanced-trade-apis/guides/orders
- Coinbase App API key authentication (JWT/ES256):
  https://docs.cdp.coinbase.com/coinbase-app/authentication-authorization/api-key-authentication
- Legacy Coinbase Exchange/Pro POST /orders:
  https://docs.cloud.coinbase.com/exchange/docs/apis/post-orders
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

ADVANCED_TRADE_HOST = "api.coinbase.com"
ADVANCED_TRADE_CREATE_ORDER_PATH = "/api/v3/brokerage/orders"

_VALID_SIDES = ("BUY", "SELL")
_VALID_STOP_KINDS = ("loss", "entry")
_VALID_TIF = ("GTC", "GTT", "IOC", "FOK")


class AdvancedTradeOrderRejected(RuntimeError):
    """
    Raised when ``POST /api/v3/brokerage/orders`` returns ``success: false``.

    Advanced Trade reports a business rejection inside a response body that may
    still carry HTTP 200, so this is a normal response path, not a transport
    error. The structured fields exist so a caller can classify the rejection
    before deciding what to do next. A rejection is *not* evidence that no
    order was created - see ``parse_v3_response`` - so never re-submit under a
    fresh ``client_order_id`` on the strength of one.
    """

    def __init__(
        self,
        message: str,
        failure_reason: str = "",
        error_details: str = "",
        raw_response: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.failure_reason = failure_reason
        self.error_details = error_details
        self.raw_response: Dict[str, Any] = raw_response or {}


@dataclass
class LegacyProOrderRequest:
    """
    A legacy Coinbase Pro / Coinbase Exchange ``POST /orders`` body.

    Field names mirror the legacy API rather than Advanced Trade, so a system
    being migrated can populate this from what it already sends.
    """

    product_id: str                      # e.g. "BTC-USD"
    side: str                            # "buy" or "sell" (case-insensitive)
    type: str                            # "limit" | "market" | "stop"
    size: Optional[str] = None           # base-currency size
    price: Optional[str] = None          # limit price
    stop_price: Optional[str] = None     # trigger price for stop orders
    post_only: bool = False
    client_oid: Optional[str] = None
    funds: Optional[str] = None          # quote-currency amount (market buy only)
    stop: Optional[str] = None           # "loss" | "entry" - required for stop orders
    time_in_force: str = "GTC"           # "GTC" | "GTT" | "IOC" | "FOK"
    end_time: Optional[str] = None       # RFC3339, required when time_in_force == "GTT"


@dataclass
class StandardizedOrderResponse:
    """
    Normalized view of an accepted Advanced Trade create-order response.

    ``status`` is ``"ACCEPTED"``, not a live order state: the create-order
    response only says Coinbase took the order, and says nothing about whether
    it is open, filled or already cancelled. Query
    ``GET /api/v3/brokerage/orders/historical/{order_id}`` for that.
    """

    order_id: str
    client_order_id: str
    product_id: str
    side: str
    status: str
    order_type: str                      # echoed order_configuration key, e.g. "limit_limit_gtc"
    raw_response: Dict[str, Any] = field(default_factory=dict)


def _format_decimal_string(value: Any, field_name: str) -> str:
    """
    Render a size or price as a positive, exponent-free decimal string.

    Raises ``ValueError`` for anything Advanced Trade would reject: a
    non-numeric string, NaN/Infinity, zero, or a negative amount.
    """
    try:
        parsed = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, ArithmeticError) as exc:
        raise ValueError(f"{field_name} is not a valid decimal value: {value!r}") from exc

    if not parsed.is_finite():
        raise ValueError(f"{field_name} must be a finite decimal value, got {value!r}")
    if parsed <= 0:
        raise ValueError(f"{field_name} must be strictly positive, got {value!r}")

    return format(parsed, "f")


class CoinbaseAdvancedTradeAdapter:
    """
    Converts legacy Coinbase Pro order requests into Advanced Trade v3 payloads.

    Stateless and side-effect free: it neither signs nor sends. Every
    translation that would change the order's execution semantics raises
    ``ValueError`` instead of picking a default.
    """

    def translate_order_request(self, legacy_req: LegacyProOrderRequest) -> Dict[str, Any]:
        """
        Translate a legacy request into the body of
        ``POST /api/v3/brokerage/orders``.

        Raises ``ValueError`` when the legacy request cannot be expressed in
        Advanced Trade without changing what the order does.
        """
        if not legacy_req.product_id or not legacy_req.side or not legacy_req.type:
            raise ValueError("Legacy order request missing required fields (product_id, side, type).")

        side_upper = legacy_req.side.strip().upper()
        if side_upper not in _VALID_SIDES:
            raise ValueError(f"Invalid order side: {legacy_req.side!r}. Must be 'BUY' or 'SELL'.")

        tif = (legacy_req.time_in_force or "GTC").strip().upper()
        if tif not in _VALID_TIF:
            raise ValueError(
                f"Unsupported time_in_force: {legacy_req.time_in_force!r}. Expected one of {_VALID_TIF}."
            )

        order_type_lower = legacy_req.type.strip().lower()
        if order_type_lower == "limit":
            order_config = self._build_limit_config(legacy_req, tif)
        elif order_type_lower == "market":
            order_config = self._build_market_config(legacy_req, side_upper, tif)
        elif order_type_lower == "stop":
            order_config = self._build_stop_limit_config(legacy_req, tif)
        else:
            raise ValueError(f"Unsupported order type for Advanced Trade migration: {legacy_req.type!r}")

        client_oid = legacy_req.client_oid
        if not client_oid:
            client_oid = str(uuid.uuid4())
            logger.warning(
                "No client_oid supplied; generated %s. A generated id is fresh on every call, so a "
                "retry after a timeout submits a second distinct order. Supply a stable client_oid "
                "to keep re-submission idempotent.",
                client_oid,
            )

        v3_payload: Dict[str, Any] = {
            "client_order_id": client_oid,
            "product_id": legacy_req.product_id,
            "side": side_upper,
            "order_configuration": order_config,
        }

        logger.info(
            "Translated legacy %s order to Advanced Trade configuration %s (client_order_id=%s)",
            order_type_lower,
            next(iter(order_config)),
            client_oid,
        )
        return v3_payload

    def _build_limit_config(self, legacy_req: LegacyProOrderRequest, tif: str) -> Dict[str, Any]:
        if not legacy_req.price:
            raise ValueError("Limit orders must specify a price.")
        if not legacy_req.size:
            raise ValueError("Limit orders must specify a base size.")
        if legacy_req.funds:
            raise ValueError(
                "Legacy 'funds' is only valid for market buy orders; limit orders are sized by 'size'."
            )

        leg: Dict[str, Any] = {
            "base_size": _format_decimal_string(legacy_req.size, "size"),
            "limit_price": _format_decimal_string(legacy_req.price, "price"),
        }

        if tif == "GTC":
            leg["post_only"] = bool(legacy_req.post_only)
            return {"limit_limit_gtc": leg}
        if tif == "GTT":
            leg["end_time"] = self._require_end_time(legacy_req)
            leg["post_only"] = bool(legacy_req.post_only)
            return {"limit_limit_gtd": leg}
        if tif == "FOK":
            # limit_limit_fok has no post_only field, and an order that must fill
            # in full immediately while never taking liquidity cannot fill at all.
            # That is a contradiction to surface, not a field to quietly drop.
            if legacy_req.post_only:
                raise ValueError("post_only cannot be combined with time_in_force='FOK'.")
            return {"limit_limit_fok": leg}

        # tif == "IOC"
        raise ValueError(
            "Advanced Trade has no plain limit immediate-or-cancel configuration. The only IOC "
            "limit variant, sor_limit_ioc, routes through Smart Order Routing and is a different "
            "execution instruction, so re-express this order deliberately. Translating it to "
            "limit_limit_gtc would leave a resting order the legacy request never intended."
        )

    def _build_market_config(
        self, legacy_req: LegacyProOrderRequest, side_upper: str, tif: str
    ) -> Dict[str, Any]:
        if legacy_req.post_only:
            raise ValueError("post_only is not applicable to market orders.")
        if tif not in ("GTC", "IOC"):
            # A market order is immediate by construction; GTT/FOK on one is a
            # sign the caller has mis-populated the legacy request.
            raise ValueError(f"time_in_force={tif!r} is not meaningful for a market order.")
        if legacy_req.size and legacy_req.funds:
            raise ValueError(
                "Specify either 'size' (base) or 'funds' (quote) for a market order, not both."
            )

        if side_upper == "SELL":
            if legacy_req.funds:
                raise ValueError(
                    "Advanced Trade market sells accept base_size only; legacy 'funds' cannot be "
                    "translated for a SELL."
                )
            if not legacy_req.size:
                raise ValueError("Market sell orders must specify a base 'size'.")
            return {"market_market_ioc": {"base_size": _format_decimal_string(legacy_req.size, "size")}}

        if legacy_req.funds:
            return {"market_market_ioc": {"quote_size": _format_decimal_string(legacy_req.funds, "funds")}}
        if legacy_req.size:
            return {"market_market_ioc": {"base_size": _format_decimal_string(legacy_req.size, "size")}}
        raise ValueError("Market buy orders must specify either 'size' (base) or 'funds' (quote).")

    def _build_stop_limit_config(self, legacy_req: LegacyProOrderRequest, tif: str) -> Dict[str, Any]:
        if not legacy_req.price or not legacy_req.stop_price:
            raise ValueError("Stop orders must specify both limit price and stop_price.")
        if not legacy_req.size:
            raise ValueError("Stop orders must specify a base size.")
        if legacy_req.post_only:
            raise ValueError("post_only is not a field of the Advanced Trade stop-limit configuration.")

        stop_kind = (legacy_req.stop or "").strip().lower()
        if stop_kind not in _VALID_STOP_KINDS:
            raise ValueError(
                "Stop orders must carry the legacy 'stop' field ('loss' or 'entry') so that "
                "stop_direction can be set correctly. It cannot be inferred from 'side': a sell "
                "stop-entry triggers upward and a buy stop-loss triggers downward, so guessing "
                "from side inverts the trigger on exactly those orders."
            )

        leg: Dict[str, Any] = {
            "base_size": _format_decimal_string(legacy_req.size, "size"),
            "limit_price": _format_decimal_string(legacy_req.price, "price"),
            "stop_price": _format_decimal_string(legacy_req.stop_price, "stop_price"),
            "stop_direction": (
                "STOP_DIRECTION_STOP_DOWN" if stop_kind == "loss" else "STOP_DIRECTION_STOP_UP"
            ),
        }

        if tif == "GTC":
            return {"stop_limit_stop_limit_gtc": leg}
        if tif == "GTT":
            leg["end_time"] = self._require_end_time(legacy_req)
            return {"stop_limit_stop_limit_gtd": leg}
        raise ValueError(
            f"Advanced Trade stop-limit configurations are GTC or GTD only; time_in_force={tif!r} "
            "has no stop-limit equivalent."
        )

    @staticmethod
    def _require_end_time(legacy_req: LegacyProOrderRequest) -> str:
        if not legacy_req.end_time:
            raise ValueError(
                "time_in_force='GTT' maps to an Advanced Trade GTD configuration, which requires "
                "an RFC3339 'end_time'."
            )
        return legacy_req.end_time

    def parse_v3_response(self, v3_response: Dict[str, Any]) -> StandardizedOrderResponse:
        """
        Normalize an Advanced Trade create-order response.

        Advanced Trade signals a business rejection with ``success: false`` in
        the body, which can accompany HTTP 200 - so the caller must inspect the
        body and not only the status code. A rejection raises
        ``AdvancedTradeOrderRejected`` carrying ``new_order_failure_reason`` and
        ``error_details`` so it can be classified. Do not treat a rejection, or
        a lost response, as proof that no order exists: confirm by
        ``client_order_id`` via
        ``GET /api/v3/brokerage/orders/historical/batch`` before re-submitting.
        """
        if not isinstance(v3_response, dict):
            raise TypeError(
                f"Advanced Trade response must be a JSON object, got {type(v3_response).__name__}."
            )

        if not v3_response.get("success", False):
            error_block = v3_response.get("error_response") or {}
            failure_reason = str(
                v3_response.get("failure_reason")
                or error_block.get("new_order_failure_reason")
                or error_block.get("error")
                or ""
            )
            message = str(
                error_block.get("message") or failure_reason or "Unknown Advanced Trade API Error"
            )
            error_details = str(error_block.get("error_details") or "")
            logger.error(
                "Advanced Trade rejected order submission: reason=%s message=%s details=%s",
                failure_reason or "UNSPECIFIED",
                message,
                error_details,
            )
            raise AdvancedTradeOrderRejected(
                f"Advanced Trade Order Submission Failed: {message}",
                failure_reason=failure_reason,
                error_details=error_details,
                raw_response=v3_response,
            )

        success_data = v3_response.get("success_response") or {}
        order_id = str(success_data.get("order_id") or "")
        if not order_id:
            # success:true with no order_id leaves nothing to reconcile against;
            # returning an empty id would let a caller record a phantom order.
            raise AdvancedTradeOrderRejected(
                "Advanced Trade reported success but returned no order_id; the order state is "
                "unknown and must be reconciled by client_order_id before any re-submission.",
                raw_response=v3_response,
            )

        echoed_config = v3_response.get("order_configuration") or {}
        order_type = next(iter(echoed_config), "") if isinstance(echoed_config, dict) else ""

        return StandardizedOrderResponse(
            order_id=order_id,
            client_order_id=str(success_data.get("client_order_id") or ""),
            product_id=str(success_data.get("product_id") or ""),
            side=str(success_data.get("side") or ""),
            status="ACCEPTED",
            order_type=order_type,
            raw_response=v3_response,
        )
