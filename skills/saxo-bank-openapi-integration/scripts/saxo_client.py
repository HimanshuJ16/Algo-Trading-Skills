"""
saxo-bank-openapi-integration: Reference client for Saxo Bank OpenAPI covering
multi-asset UIC instrument resolution, order routing, and portfolio position tracking.

Endpoint shapes, field locations, enum values and token/rate-limit semantics used here
are taken from Saxo's developer portal; see ``references/standards.md`` for the
per-claim citations. The HTTP layer is injected (``http_fn``) so the module stays
dependency-free and fully testable offline.
"""
from dataclasses import dataclass, field
import logging
import math
import uuid
from enum import Enum
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import urlencode

logger = logging.getLogger(__name__)

# Saxo rejects ExternalReference values longer than 50 characters.
MAX_EXTERNAL_REFERENCE_LENGTH = 50

# Transport may return (status, body) or (status, body, headers).
HttpResponse = Any
HttpFn = Callable[[str, str, Dict[str, str], Optional[Dict[str, Any]]], HttpResponse]


class SaxoAPIError(Exception):
    """Raised when Saxo Bank OpenAPI returns an error or an unusable response."""

    def __init__(self, message: str, status_code: Optional[int] = None, payload: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


class SaxoAuthError(SaxoAPIError):
    """Raised on HTTP 401 - token absent, invalid, or expired (access tokens live 20 minutes)."""


class SaxoRateLimitError(SaxoAPIError):
    """
    Raised on HTTP 429.

    ``retry_after_seconds`` is taken from the exhausted dimension's
    ``X-RateLimit-<dimension>-Reset`` header, which Saxo defines as the number of seconds
    until that quota resets. It is ``None`` when the transport supplies no headers.
    """

    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        payload: Any = None,
        retry_after_seconds: Optional[float] = None,
    ):
        super().__init__(message, status_code=status_code, payload=payload)
        self.retry_after_seconds = retry_after_seconds


class SaxoAssetType(Enum):
    """
    Tradable Saxo ``AssetType`` values covered by this skill.

    Saxo's full enum is much larger (Bond, Etf, FxForwards, CfdOnIndex, ...); these are
    the values this client has been exercised against. ``OptionRoot`` is deliberately
    absent - it is an instrument-search summary concept, not a tradable AssetType, so it
    cannot be used on an order payload. Trade the concrete option UIC instead
    (``StockOption`` / ``FuturesOption`` / ``StockIndexOption`` / ``FxVanillaOption``).
    """

    FX_SPOT = "FxSpot"
    STOCK = "Stock"
    CONTRACT_FUTURES = "ContractFutures"
    CFD_ON_STOCK = "CfdOnStock"
    STOCK_OPTION = "StockOption"
    FUTURES_OPTION = "FuturesOption"
    STOCK_INDEX_OPTION = "StockIndexOption"
    FX_VANILLA_OPTION = "FxVanillaOption"


class SaxoOrderType(Enum):
    """Saxo ``OrderType`` values. Every type except ``Market`` requires an ``OrderPrice``."""

    MARKET = "Market"
    LIMIT = "Limit"
    STOP = "Stop"
    STOP_IF_TRADED = "StopIfTraded"
    STOP_LIMIT = "StopLimit"
    TRAILING_STOP = "TrailingStop"
    TRAILING_STOP_IF_TRADED = "TrailingStopIfTraded"


class SaxoOrderDuration(Enum):
    DAY_ORDER = "DayOrder"
    GOOD_TILL_CANCEL = "GoodTillCancel"
    IMMEDIATE_OR_CANCEL = "ImmediateOrCancel"


#: Order types whose payload must carry ``OrderPrice``.
PRICED_ORDER_TYPES = frozenset(t for t in SaxoOrderType if t is not SaxoOrderType.MARKET)


@dataclass
class SaxoInstrument:
    uic: int
    symbol: str
    description: str
    asset_type: SaxoAssetType
    currency: str
    exchange_id: str = ""
    tradable_as: List[str] = field(default_factory=list)


@dataclass
class SaxoOrder:
    """
    Result of an accepted order placement.

    ``status`` is ``None`` unless the response actually carried a status field: Saxo's
    documented placement response contains ``OrderId`` (and related ``Orders``) only.
    Acceptance is not a fill - poll ``/port/v1/orders`` or a position subscription for
    the working/filled state.
    """

    order_id: str
    account_key: str
    uic: int
    asset_type: SaxoAssetType
    buy_sell: str  # "Buy" or "Sell"
    amount: float
    order_type: SaxoOrderType
    price: Optional[float]
    status: Optional[str] = None
    external_reference: Optional[str] = None
    related_order_ids: List[str] = field(default_factory=list)


@dataclass
class SaxoPosition:
    """
    A single open position.

    ``unrealized_pnl`` (``ProfitLossOnTrade``) is denominated in the instrument's own
    currency (``pnl_currency``). Summing it across a multi-currency portfolio is wrong -
    aggregate ``unrealized_pnl_base_currency`` (``ProfitLossOnTradeInBaseCurrency``)
    instead, which Saxo has already converted to the account's base currency.
    """

    position_id: str
    uic: int
    symbol: str
    asset_type: SaxoAssetType
    amount: float
    average_open_price: float
    current_price: float
    unrealized_pnl: float
    net_position_id: str = ""
    pnl_currency: str = ""
    unrealized_pnl_base_currency: Optional[float] = None
    calculation_reliability: str = ""
    status: str = ""

    @property
    def valuation_is_reliable(self) -> bool:
        """
        True only when Saxo reported ``CalculationReliability == "Ok"``.

        Defensive convention, not a documented Saxo rule: any other value (or an absent
        field) is treated as "valuation not confirmed", so risk logic fails closed rather
        than sizing off a figure Saxo declined to vouch for.
        """
        return self.calculation_reliability == "Ok"


class SaxoBankOpenAPIClient:
    """
    Client for Saxo Bank OpenAPI providing multi-asset trading across Equities, FX,
    Futures, and Options.

    The injected ``http_fn`` is called as ``http_fn(method, url, headers, body)`` and must
    return either ``(status_code, parsed_body)`` or ``(status_code, parsed_body, headers)``.
    Supplying headers is what enables ``SaxoRateLimitError.retry_after_seconds``.
    """

    SIM_BASE_URL = "https://gateway.saxobank.com/sim/openapi"
    LIVE_BASE_URL = "https://gateway.saxobank.com/openapi"

    # FieldGroups must be requested explicitly; DisplayAndFormat is the only place the
    # human-readable Symbol is returned for a position.
    POSITION_FIELD_GROUPS = ("PositionBase", "PositionView", "DisplayAndFormat")

    def __init__(
        self,
        access_token: str,
        account_key: str,
        is_simulation: bool = True,
        http_fn: Optional[HttpFn] = None,
    ):
        if not access_token:
            raise ValueError("access_token must be a non-empty string.")
        if not account_key:
            raise ValueError("account_key must be a non-empty string.")

        self.access_token = access_token
        self.account_key = account_key
        self.is_simulation = is_simulation
        self.base_url = self.SIM_BASE_URL if is_simulation else self.LIVE_BASE_URL
        self._http_fn = http_fn

    # ----------------------------------------------------------------- transport --

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _normalize_response(raw: HttpResponse) -> Tuple[int, Any, Mapping[str, str]]:
        """Accepts a (status, body) or (status, body, headers) transport result."""
        if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)) or len(raw) not in (2, 3):
            raise SaxoAPIError(
                "http_fn must return (status_code, body) or (status_code, body, headers); "
                f"got {type(raw).__name__}."
            )
        if len(raw) == 2:
            status, body = raw
            headers: Mapping[str, str] = {}
        else:
            status, body, raw_headers = raw
            headers = raw_headers or {}
        if not isinstance(status, int) or isinstance(status, bool):
            raise SaxoAPIError(f"http_fn returned a non-integer status code: {status!r}.")
        return status, body, headers

    @staticmethod
    def _retry_after_seconds(headers: Mapping[str, str]) -> Optional[float]:
        """
        Reads the reset delay from Saxo's rate-limit headers.

        Saxo emits ``X-RateLimit-<dimension>-{Limit,Remaining,Reset}`` per quota bucket,
        where ``Reset`` is the number of seconds until that quota resets. Prefer the
        dimension that is actually exhausted; fall back to the largest Reset present,
        then to a standard ``Retry-After``.
        """
        lowered = {str(k).lower(): v for k, v in headers.items()}

        def _as_float(value: Any) -> Optional[float]:
            try:
                return float(value)
            except (TypeError, ValueError):
                return None

        exhausted: List[float] = []
        all_resets: List[float] = []
        for key, value in lowered.items():
            if not key.startswith("x-ratelimit-") or not key.endswith("-reset"):
                continue
            reset = _as_float(value)
            if reset is None:
                continue
            all_resets.append(reset)
            remaining = _as_float(lowered.get(key[: -len("-reset")] + "-remaining"))
            if remaining is not None and remaining <= 0:
                exhausted.append(reset)

        if exhausted:
            return max(exhausted)
        if all_resets:
            return max(all_resets)
        return _as_float(lowered.get("retry-after"))

    def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        body: Optional[Dict[str, Any]] = None,
    ) -> Tuple[int, Any, Mapping[str, str]]:
        if self._http_fn is None:
            raise SaxoAPIError("HTTP transport function not configured.")

        url = f"{self.base_url}{path}"
        if params:
            # urlencode is what keeps keywords containing spaces, '&' or '#' from
            # silently corrupting the query string.
            url = f"{url}?{urlencode(params)}"

        return self._normalize_response(self._http_fn(method, url, self._headers(), body))

    def _raise_for_status(
        self,
        status: int,
        body: Any,
        headers: Mapping[str, str],
        context: str,
        accepted: Sequence[int] = (200,),
    ) -> None:
        if status in accepted:
            return
        if status == 401:
            raise SaxoAuthError(
                f"{context} rejected (HTTP 401): access token missing, invalid, or expired. "
                "Saxo OAuth2 access tokens expire after 20 minutes - refresh and retry. "
                f"Response: {body}",
                status_code=status,
                payload=body,
            )
        if status == 429:
            raise SaxoRateLimitError(
                f"{context} rate limited (HTTP 429). Response: {body}",
                status_code=status,
                payload=body,
                retry_after_seconds=self._retry_after_seconds(headers),
            )
        raise SaxoAPIError(
            f"{context} failed (HTTP {status}): {body}", status_code=status, payload=body
        )

    @staticmethod
    def _data_rows(body: Any, context: str) -> List[Mapping[str, Any]]:
        """
        Extracts the ``Data`` array and warns when the page is demonstrably partial.

        Saxo's collection endpoints are OData-paged (``$top`` / ``$skip``). Silently
        returning a truncated position list would understate exposure, so a ``__count``
        larger than the rows received - or a continuation link - is surfaced rather than
        swallowed. This is a consistency check on fields observed in Saxo responses, not
        a claim about a documented paging contract.
        """
        if not isinstance(body, Mapping):
            raise SaxoAPIError(f"{context} returned a non-object body: {body!r}", payload=body)
        rows = body.get("Data", [])
        if not isinstance(rows, list):
            raise SaxoAPIError(f"{context} returned a non-list 'Data' field: {rows!r}", payload=body)

        total = body.get("__count")
        if isinstance(total, int) and not isinstance(total, bool) and total > len(rows):
            logger.warning(
                "%s returned %d of %d rows - the result is PAGED and incomplete. Page with "
                "$top/$skip before treating it as the full set.",
                context,
                len(rows),
                total,
            )
        elif body.get("__next"):
            logger.warning(
                "%s returned a '__next' continuation link - the result is PAGED and "
                "incomplete. Follow it before treating it as the full set.",
                context,
            )
        return [row for row in rows if isinstance(row, Mapping)]

    # ------------------------------------------------------------- reference data --

    def search_instrument(
        self,
        keywords: str,
        asset_type: SaxoAssetType,
        top: Optional[int] = None,
    ) -> List[SaxoInstrument]:
        """
        Resolves ticker keywords to Saxo UICs via ``GET /ref/v1/instruments``.

        The UIC is returned in the ``Identifier`` field of each instrument summary. A
        keyword search is a fuzzy match, not a lookup: it can return several instruments
        (or none). Never assume ``result[0]`` is the intended instrument - confirm
        ``symbol``, ``exchange_id`` and ``currency`` before routing an order to that UIC.
        """
        if not keywords or not keywords.strip():
            raise ValueError("keywords must be a non-empty string.")

        params: Dict[str, Any] = {"Keywords": keywords, "AssetTypes": asset_type.value}
        if top is not None:
            if top <= 0:
                raise ValueError("top must be a positive integer when supplied.")
            params["$top"] = top

        status, body, headers = self._request("GET", "/ref/v1/instruments", params=params)
        self._raise_for_status(status, body, headers, "Instrument search")

        instruments: List[SaxoInstrument] = []
        for item in self._data_rows(body, "Instrument search"):
            identifier = item.get("Identifier")
            try:
                uic = int(identifier)
            except (TypeError, ValueError):
                logger.warning(
                    "Instrument summary with missing/non-numeric Identifier (UIC) skipped: %r",
                    identifier,
                )
                continue

            tradable_as = item.get("TradableAs")
            instruments.append(
                SaxoInstrument(
                    uic=uic,
                    symbol=str(item.get("Symbol", "")),
                    description=str(item.get("Description", "")),
                    # Trust the response's own AssetType; a keyword search can return an
                    # instrument whose primary AssetType differs from the one requested.
                    asset_type=self._coerce_asset_type(item.get("AssetType"), asset_type),
                    currency=str(item.get("CurrencyCode", "")),
                    exchange_id=str(item.get("ExchangeId", "")),
                    tradable_as=[str(t) for t in tradable_as] if isinstance(tradable_as, list) else [],
                )
            )
        return instruments

    @staticmethod
    def _coerce_asset_type(raw: Any, fallback: SaxoAssetType) -> SaxoAssetType:
        if raw is None:
            return fallback
        try:
            return SaxoAssetType(str(raw))
        except ValueError:
            logger.warning(
                "Saxo returned AssetType %r which this client does not model; using %s.",
                raw,
                fallback.value,
            )
            return fallback

    # ---------------------------------------------------------------- order entry --

    @staticmethod
    def generate_external_reference(prefix: str = "ats") -> str:
        """
        Builds a random ``ExternalReference`` (<= 50 chars) for order-recovery lookups.

        Saxo does *not* deduplicate on this value - it is a client-side correlation tag,
        not an idempotency key. See ``place_order`` for the retry consequences.
        """
        return f"{prefix}-{uuid.uuid4().hex}"[:MAX_EXTERNAL_REFERENCE_LENGTH]

    def place_order(
        self,
        uic: int,
        asset_type: SaxoAssetType,
        buy_sell: str,  # "Buy" or "Sell"
        amount: float,
        order_type: SaxoOrderType = SaxoOrderType.MARKET,
        price: Optional[float] = None,
        duration: SaxoOrderDuration = SaxoOrderDuration.DAY_ORDER,
        manual_order: bool = False,
        external_reference: Optional[str] = None,
        extra_fields: Optional[Dict[str, Any]] = None,
    ) -> SaxoOrder:
        """
        Places a multi-asset order via ``POST /trade/v2/orders``.

        ``manual_order`` must reflect reality: ``False`` for orders generated by an
        algorithm, ``True`` only for orders a human entered. It is sent on every request
        because Saxo documents it as mandatory for almost all applications.

        ``extra_fields`` is merged into the payload for order types needing parameters
        beyond ``OrderPrice`` (for example the trailing-stop distance/step fields); consult
        Saxo's order-placement reference for their exact names. Values in ``extra_fields``
        never overwrite the fields this method computes.

        NOT retry-safe. ``ExternalReference`` is not an idempotency key - Saxo explicitly
        does not check it for uniqueness and will not reject a repeat. If this call raises
        on a network timeout, the order may already be working: call
        ``find_open_orders_by_external_reference`` and inspect positions before resubmitting.
        """
        self._validate_order_inputs(uic, buy_sell, amount, order_type, price, external_reference)

        payload: Dict[str, Any] = {}
        if extra_fields:
            payload.update(extra_fields)
        payload.update(
            {
                "AccountKey": self.account_key,
                "Uic": uic,
                "AssetType": asset_type.value,
                "BuySell": buy_sell,
                "Amount": amount,
                "OrderType": order_type.value,
                "OrderDuration": {"DurationType": duration.value},
                "ManualOrder": manual_order,
            }
        )
        if order_type in PRICED_ORDER_TYPES:
            payload["OrderPrice"] = price
        if external_reference:
            payload["ExternalReference"] = external_reference

        logger.info(
            "Submitting %s %s %s of Uic=%s (%s) to %s environment [ExternalReference=%s]",
            order_type.value,
            buy_sell,
            amount,
            uic,
            asset_type.value,
            "SIM" if self.is_simulation else "LIVE",
            external_reference,
        )

        status, body, headers = self._request("POST", "/trade/v2/orders", body=payload)
        self._raise_for_status(status, body, headers, "Order placement", accepted=(200, 201))

        if not isinstance(body, Mapping):
            raise SaxoAPIError(
                f"Order placement returned a non-object body: {body!r}",
                status_code=status,
                payload=body,
            )

        raw_order_id = body.get("OrderId")
        if raw_order_id in (None, ""):
            # The order may still have reached the market. Never invent an id - the caller
            # must reconcile via ExternalReference rather than assume failure.
            raise SaxoAPIError(
                f"Order placement returned HTTP {status} without an OrderId; order state is "
                f"UNKNOWN. Reconcile via ExternalReference={external_reference!r} before "
                f"resubmitting. Response: {body}",
                status_code=status,
                payload=body,
            )

        related = body.get("Orders") or []
        related_ids = [
            str(o["OrderId"])
            for o in related
            if isinstance(o, Mapping) and o.get("OrderId") not in (None, "")
        ]

        order = SaxoOrder(
            order_id=str(raw_order_id),
            account_key=self.account_key,
            uic=uic,
            asset_type=asset_type,
            buy_sell=buy_sell,
            amount=amount,
            order_type=order_type,
            price=price,
            status=body.get("Status"),
            external_reference=body.get("ExternalReference", external_reference),
            related_order_ids=related_ids,
        )
        logger.info("Order accepted: OrderId=%s related=%s", order.order_id, related_ids)
        return order

    @staticmethod
    def _validate_order_inputs(
        uic: int,
        buy_sell: str,
        amount: float,
        order_type: SaxoOrderType,
        price: Optional[float],
        external_reference: Optional[str],
    ) -> None:
        if not isinstance(uic, int) or isinstance(uic, bool) or uic <= 0:
            raise ValueError(f"uic must be a positive integer UIC, got {uic!r}.")
        if buy_sell not in ("Buy", "Sell"):
            raise ValueError(f"buy_sell must be exactly 'Buy' or 'Sell', got {buy_sell!r}.")
        if not isinstance(amount, (int, float)) or isinstance(amount, bool):
            raise ValueError(f"amount must be numeric, got {amount!r}.")
        if not math.isfinite(amount) or amount <= 0:
            raise ValueError(f"amount must be a finite positive number, got {amount!r}.")
        if order_type in PRICED_ORDER_TYPES:
            if price is None:
                raise ValueError(
                    f"order_type {order_type.value} requires an OrderPrice; price was None."
                )
            if not isinstance(price, (int, float)) or isinstance(price, bool):
                raise ValueError(f"price must be numeric, got {price!r}.")
            if not math.isfinite(price) or price <= 0:
                raise ValueError(f"price must be a finite positive number, got {price!r}.")
        if external_reference is not None:
            if not external_reference.strip():
                raise ValueError("external_reference must be non-empty when supplied.")
            if len(external_reference) > MAX_EXTERNAL_REFERENCE_LENGTH:
                raise ValueError(
                    "external_reference exceeds Saxo's "
                    f"{MAX_EXTERNAL_REFERENCE_LENGTH}-character limit "
                    f"({len(external_reference)} chars)."
                )

    def find_open_orders_by_external_reference(
        self, external_reference: str
    ) -> List[Mapping[str, Any]]:
        """
        Returns *open* orders on this account carrying ``external_reference``.

        Use after an ambiguous placement (timeout, connection reset) to decide whether the
        order actually reached Saxo. Saxo echoes ExternalReference back on ``/port/v1/orders``.

        An empty result does NOT prove the order was never placed: ``/port/v1/orders``
        returns working orders only, so an order that has already filled has left this
        endpoint. Confirm against positions before resubmitting.
        """
        if not external_reference or not external_reference.strip():
            raise ValueError("external_reference must be a non-empty string.")

        status, body, headers = self._request(
            "GET", "/port/v1/orders", params={"AccountKey": self.account_key}
        )
        self._raise_for_status(status, body, headers, "Open order lookup")

        matches = [
            row
            for row in self._data_rows(body, "Open order lookup")
            if row.get("ExternalReference") == external_reference
        ]
        if len(matches) > 1:
            logger.warning(
                "ExternalReference=%s matched %d open orders - Saxo does not enforce "
                "uniqueness on this field; a duplicate submission is likely.",
                external_reference,
                len(matches),
            )
        return matches

    # ------------------------------------------------------------------ portfolio --

    def get_positions(self) -> List[SaxoPosition]:
        """
        Queries open multi-asset positions via ``GET /port/v1/positions``.

        ``FieldGroups`` is sent explicitly: ``PositionId`` sits at the top level of each
        row (not inside ``PositionBase``), and the instrument ``Symbol`` is only returned
        inside ``DisplayAndFormat``, which Saxo omits unless that field group is requested.
        """
        params = {
            "AccountKey": self.account_key,
            "FieldGroups": ",".join(self.POSITION_FIELD_GROUPS),
        }
        status, body, headers = self._request("GET", "/port/v1/positions", params=params)
        self._raise_for_status(status, body, headers, "Position query")

        positions: List[SaxoPosition] = []
        for item in self._data_rows(body, "Position query"):
            pos_base = item.get("PositionBase") or {}
            pos_view = item.get("PositionView") or {}
            display = item.get("DisplayAndFormat") or {}
            if not isinstance(pos_base, Mapping) or not isinstance(pos_view, Mapping):
                logger.warning(
                    "Position row with malformed PositionBase/PositionView skipped: %s", item
                )
                continue
            if not isinstance(display, Mapping):
                display = {}

            reliability = str(pos_view.get("CalculationReliability", ""))
            if reliability != "Ok":
                logger.warning(
                    "Position %s reports CalculationReliability=%r; treat its valuation as "
                    "unconfirmed and do not size risk off it.",
                    item.get("PositionId"),
                    reliability,
                )

            positions.append(
                SaxoPosition(
                    position_id=str(item.get("PositionId", "")),
                    uic=self._to_int(pos_base.get("Uic"), 0),
                    symbol=str(display.get("Symbol", "")),
                    asset_type=self._coerce_asset_type(
                        pos_base.get("AssetType"), SaxoAssetType.STOCK
                    ),
                    amount=self._to_float(pos_base.get("Amount"), 0.0),
                    average_open_price=self._to_float(pos_base.get("OpenPrice"), 0.0),
                    current_price=self._to_float(pos_view.get("CurrentPrice"), 0.0),
                    unrealized_pnl=self._to_float(pos_view.get("ProfitLossOnTrade"), 0.0),
                    net_position_id=str(item.get("NetPositionId", "")),
                    pnl_currency=str(display.get("Currency", "")),
                    unrealized_pnl_base_currency=self._to_optional_float(
                        pos_view.get("ProfitLossOnTradeInBaseCurrency")
                    ),
                    calculation_reliability=reliability,
                    status=str(pos_base.get("Status", "")),
                )
            )
        return positions

    @staticmethod
    def _to_int(value: Any, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _to_float(value: Any, default: float) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return default
        return parsed if math.isfinite(parsed) else default

    @staticmethod
    def _to_optional_float(value: Any) -> Optional[float]:
        if value is None:
            return None
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        return parsed if math.isfinite(parsed) else None
