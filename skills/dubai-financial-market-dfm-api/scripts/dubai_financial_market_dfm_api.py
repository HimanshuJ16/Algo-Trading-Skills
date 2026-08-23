"""
Dubai Financial Market (DFM) pre-trade order validation and FIX 4.4 payload builder.

Scope and honesty note
----------------------
This module performs **pre-trade validation and message construction only**. It opens
no session and sends nothing: a returned report with ``is_accepted=True`` means the
order passed local checks and a payload was built, NOT that the exchange received it.

The FIX 4.4 framing implemented here (SOH delimiters, BodyLength tag 9, CheckSum tag 10)
follows the published FIX 4.4 standard and is independently verifiable. The
**venue-specific field mapping is NOT verified** -- DFM's own Membership, Trading and
Derivatives Rules (Module Three) do not mention FIX at all, and DFM member connectivity
specifications are not public. Treat the NIN-in-Tag-1 mapping and the session identifiers
as illustrative defaults that MUST be confirmed against DFM's member technical
specification before any production use.

Reference data that MUST be sourced externally
----------------------------------------------
Under DFM Module Three Rule 16.16(a), Upper and Lower Price Limits are set by the Market
**by Circular, per listed Security** -- they are not a single universal band. Likewise
Rule 16.17(a)(ii) provides that tick sizes are specified **by Circular** per kind of
Security. The defaults in this module encode the tick structure effective 06 April 2026
and the commonly reported equity band, but per-security values must come from current
DFM circulars. See ``references/standards.md``.
"""
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

SOH = "\x01"
VALID_SIDES = ("BUY", "SELL")
SIDE_TO_FIX_TAG54 = {"BUY": 1, "SELL": 2}

# DFM supports AED-denominated securities; Nasdaq Dubai lists both USD-denominated
# (board 200) and AED-denominated (board 210) equity products, so USD is permitted.
SUPPORTED_CURRENCIES = ("AED", "USD")

# Tick structure for DFM listed equities, ETFs and REITs effective 06 April 2026.
# Each entry is (upper_bound_exclusive, tick). The final entry covers all higher prices.
DFM_TICK_BANDS_2026: Tuple[Tuple[float, float], ...] = (
    (1.00, 0.001),
    (10.00, 0.01),
    (50.00, 0.02),
    (100.00, 0.05),
    (math.inf, 0.10),
)

# Commonly reported DFM equity band: 10% limit down, 15% limit up. NOT universal --
# Rule 16.16(a) makes limits per-security by Circular. Override per instrument.
DEFAULT_LIMIT_UP_PCT = 0.15
DEFAULT_LIMIT_DOWN_PCT = 0.10

# Relative tolerance when comparing against a band edge, so an order sitting exactly on
# the limit is not rejected by floating-point representation error alone.
_BAND_EPS = 1e-9


@dataclass
class DfmOrderRequest:
    """A single DFM order to validate.

    Attributes:
        prior_settlement_price_aed: Prior Closing Price used as the price-band benchmark.
            Required unless ``is_first_trading_session`` is True. A missing or
            non-positive value is an error, never a reason to skip the band check.
        is_first_trading_session: Set True only for a security in its first Trading
            Session, where DFM Rule 16.16(c) floats the price and no Upper/Lower Price
            Limit applies. For a dual-listed issuer whose principal listing is on a
            Foreign Market, Rule 16.16(d) instead requires the foreign closing price as
            the benchmark -- supply that as the prior settlement rather than setting
            this flag.
        limit_up_pct / limit_down_pct: Per-security band from the applicable DFM
            Circular, as fractions (0.15 = 15%). Defaults are the commonly reported
            equity values and are not authoritative for every security.
    """
    cl_ord_id: str
    nin_investor_number: str            # 10-digit DFM/Dubai CSD Investor Number (NIN)
    symbol: str                         # e.g. 'EMAAR', 'DEWA', 'DIB'
    side: str                           # 'BUY' or 'SELL'
    order_qty: int
    price_aed: float
    prior_settlement_price_aed: Optional[float] = None
    currency: str = "AED"
    is_first_trading_session: bool = False
    limit_up_pct: float = DEFAULT_LIMIT_UP_PCT
    limit_down_pct: float = DEFAULT_LIMIT_DOWN_PCT


@dataclass
class DfmFixMessagePayload:
    fix_version: str                    # 'FIX.4.4'
    msg_type: str                       # 'D' (New Order Single)
    cl_ord_id: str
    account_nin: str                    # Tag 1
    symbol: str                         # Tag 55
    side: int                           # Tag 54 (1=Buy, 2=Sell)
    order_qty: int                      # Tag 38
    price: float                        # Tag 44
    currency: str                       # Tag 15
    fix_raw_string: str                 # SOH-delimited, with BodyLength and CheckSum
    body_length: int                    # Tag 9
    check_sum: str                      # Tag 10, three digits


@dataclass
class DfmOrderExecutionReport:
    cl_ord_id: str
    symbol: str
    # 'STATUS_OK', 'INVALID_NIN', 'INVALID_TICK_SIZE', 'CIRCUIT_BREAKER_BAND_BREACH',
    # 'INVALID_ORDER_FIELD', 'MISSING_REFERENCE_PRICE'
    status: str
    is_accepted: bool                   # Passed local validation; NOT sent to any venue
    fix_payload: Optional[DfmFixMessagePayload]
    rejection_reason: Optional[str]
    required_tick_size: Optional[float] = None
    upper_price_limit: Optional[float] = None
    lower_price_limit: Optional[float] = None
    warnings: Tuple[str, ...] = ()


class DubaiFinancialMarketApiEngine:
    """
    Pre-trade validation engine for Dubai Financial Market (DFM) orders: Investor Number
    (NIN) presence, per-security tick size regime, and Upper/Lower Price Limit bands,
    followed by FIX 4.4 New Order Single payload construction.
    """

    def __init__(
        self,
        sender_comp_id: str = "BROKER01",
        target_comp_id: str = "DFM_GW_01",
        tick_bands: Sequence[Tuple[float, float]] = DFM_TICK_BANDS_2026,
    ) -> None:
        if not sender_comp_id or not target_comp_id:
            raise ValueError("sender_comp_id and target_comp_id must be non-empty.")
        if not tick_bands:
            raise ValueError("tick_bands must contain at least one (upper_bound, tick) pair.")

        bounds = [b for b, _ in tick_bands]
        if bounds != sorted(bounds):
            raise ValueError("tick_bands must be ordered by ascending upper bound.")
        if not math.isinf(bounds[-1]):
            raise ValueError(
                "The final tick band must have an infinite upper bound so every price "
                "maps to a tick. A finite top band silently under-ticks high-priced "
                "securities."
            )
        for _, tick in tick_bands:
            if not math.isfinite(tick) or tick <= 0:
                raise ValueError(f"Tick size must be a positive finite number, got {tick}.")

        self.sender_comp_id = sender_comp_id
        self.target_comp_id = target_comp_id
        self.tick_bands = tuple(tick_bands)
        self._msg_seq_num = 0

    # ------------------------------------------------------------------
    # Tick size
    # ------------------------------------------------------------------

    def required_tick_size(self, price: float) -> float:
        """Returns the applicable tick for a price under the configured band table."""
        for upper_bound, tick in self.tick_bands:
            if price < upper_bound:
                return tick
        # Unreachable while the final bound is infinite (enforced in __init__).
        return self.tick_bands[-1][1]

    def audit_dfm_tick_size(self, price_aed: float) -> Tuple[bool, float]:
        """
        Audits a price against the DFM tick structure effective 06 April 2026:
        - Price < 1.00      -> 0.001
        - 1.00  <= P < 10   -> 0.01
        - 10.00 <= P < 50   -> 0.02
        - 50.00 <= P < 100  -> 0.05
        - P >= 100.00       -> 0.10

        Comparison is done on integer tick counts rather than float modulo: ``price %
        tick`` is unreliable near band edges because neither the price nor the tick is
        exactly representable in binary floating point.
        """
        if not math.isfinite(price_aed) or price_aed <= 0:
            raise ValueError(f"Price must be a positive finite number, got {price_aed}.")

        tick = self.required_tick_size(price_aed)
        ticks = price_aed / tick
        is_valid = abs(ticks - round(ticks)) < 1e-6
        return is_valid, tick

    # ------------------------------------------------------------------
    # Price band
    # ------------------------------------------------------------------

    @staticmethod
    def price_limits(reference_price: float, limit_up_pct: float,
                     limit_down_pct: float) -> Tuple[float, float]:
        """Returns (lower_limit, upper_limit) around the benchmark closing price."""
        return (reference_price * (1.0 - limit_down_pct),
                reference_price * (1.0 + limit_up_pct))

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    @staticmethod
    def _fix_field_error(name: str, value: object) -> Optional[str]:
        """Rejects values that would break FIX framing if placed on the wire.

        A field carrying SOH or '=' lets caller-supplied text forge additional FIX
        fields -- e.g. a cl_ord_id containing SOH + '10=000' injects a premature
        CheckSum and produces a malformed message.
        """
        if not isinstance(value, str) or not value.strip():
            return f"{name} must be a non-empty string."
        if SOH in value or "=" in value:
            return (f"{name} must not contain SOH or '=' characters; "
                    f"such a value would corrupt FIX message framing.")
        if not value.isprintable():
            return f"{name} must contain only printable characters."
        return None

    def _validate_request(self, req: DfmOrderRequest) -> Optional[str]:
        """Returns a rejection message for a structurally invalid order, else None."""
        for name, value in (("cl_ord_id", req.cl_ord_id), ("symbol", req.symbol)):
            error = self._fix_field_error(name, value)
            if error is not None:
                return error

        side = req.side.upper().strip() if isinstance(req.side, str) else None
        if side not in VALID_SIDES:
            # Never coerce an unrecognised side: defaulting to SELL would silently
            # invert the intent of an order.
            return f"side must be one of {VALID_SIDES}, got {req.side!r}."

        if isinstance(req.order_qty, bool) or not isinstance(req.order_qty, int):
            return f"order_qty must be an int, got {type(req.order_qty).__name__}."
        if req.order_qty <= 0:
            return f"order_qty must be strictly positive, got {req.order_qty}."

        if isinstance(req.price_aed, bool) or not isinstance(req.price_aed, (int, float)):
            return f"price must be numeric, got {type(req.price_aed).__name__}."
        if not math.isfinite(req.price_aed) or req.price_aed <= 0:
            return f"price must be a positive finite number, got {req.price_aed}."

        if req.currency not in SUPPORTED_CURRENCIES:
            return f"currency must be one of {SUPPORTED_CURRENCIES}, got {req.currency!r}."

        for name in ("limit_up_pct", "limit_down_pct"):
            value = getattr(req, name)
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                return f"{name} must be numeric."
            if not math.isfinite(value) or value < 0:
                return f"{name} must be a non-negative finite fraction, got {value}."
        if req.limit_down_pct >= 1.0:
            return "limit_down_pct must be below 1.0 (a 100% limit down implies a zero price)."
        return None

    # ------------------------------------------------------------------
    # FIX 4.4 construction
    # ------------------------------------------------------------------

    @staticmethod
    def _fix_body_length(body: str) -> int:
        """Tag 9: byte count of everything after 9's SOH up to and including 35..the SOH before tag 10."""
        return len(body.encode("ascii"))

    @staticmethod
    def _fix_checksum(prefix: str) -> str:
        """Tag 10: sum of all bytes up to and including the SOH before tag 10, mod 256."""
        return f"{sum(prefix.encode('ascii')) % 256:03d}"

    def format_fix_44_payload(
        self,
        req: DfmOrderRequest,
        tick: float,
        sending_time: Optional[datetime] = None,
    ) -> DfmFixMessagePayload:
        """Builds a standard-framed FIX 4.4 New Order Single (MsgType D).

        Args:
            sending_time: UTC timestamp for tag 52. Injectable so message construction is
                deterministic and testable; defaults to the current UTC time.

        Note: the DFM-specific mapping (notably NIN in Tag 1) is unverified -- see the
        module docstring.
        """
        side = req.side.upper().strip()
        side_tag = SIDE_TO_FIX_TAG54[side]

        stamp = sending_time or datetime.now(timezone.utc)
        if stamp.tzinfo is None:
            raise ValueError("sending_time must be timezone-aware (UTC).")
        sending_time_str = stamp.astimezone(timezone.utc).strftime("%Y%m%d-%H:%M:%S.%f")[:-3]

        # Render the price at the tick's own precision so the wire value cannot carry
        # binary floating-point noise (e.g. 7.8500000000000005).
        decimals = max(0, -int(round(math.log10(tick))))
        price_str = f"{req.price_aed:.{decimals}f}"

        self._msg_seq_num += 1

        body = SOH.join([
            "35=D",
            f"49={self.sender_comp_id}",
            f"56={self.target_comp_id}",
            f"34={self._msg_seq_num}",
            f"52={sending_time_str}",
            f"1={req.nin_investor_number}",
            f"11={req.cl_ord_id}",
            f"55={req.symbol}",
            f"54={side_tag}",
            f"38={req.order_qty}",
            "40=2",                        # OrdType = Limit
            f"44={price_str}",
            f"15={req.currency}",
            "59=0",                        # TimeInForce = Day
        ]) + SOH

        body_length = self._fix_body_length(body)
        prefix = f"8=FIX.4.4{SOH}9={body_length}{SOH}{body}"
        check_sum = self._fix_checksum(prefix)
        raw = f"{prefix}10={check_sum}{SOH}"

        return DfmFixMessagePayload(
            fix_version="FIX.4.4",
            msg_type="D",
            cl_ord_id=req.cl_ord_id,
            account_nin=req.nin_investor_number,
            symbol=req.symbol,
            side=side_tag,
            order_qty=req.order_qty,
            price=req.price_aed,
            currency=req.currency,
            fix_raw_string=raw,
            body_length=body_length,
            check_sum=check_sum,
        )

    # ------------------------------------------------------------------
    # Orchestration
    # ------------------------------------------------------------------

    def process_dfm_order(
        self,
        req: DfmOrderRequest,
        sending_time: Optional[datetime] = None,
    ) -> DfmOrderExecutionReport:
        """
        Audits NIN, order fields, tick size, and Upper/Lower Price Limits, then builds a
        FIX 4.4 payload. Nothing is transmitted -- ``is_accepted`` means locally valid.
        """
        def reject(status: str, msg: str, **kw) -> DfmOrderExecutionReport:
            logger.error("DFM ORDER REJECTED [%s] %s: %s", req.cl_ord_id, status, msg)
            return DfmOrderExecutionReport(
                cl_ord_id=req.cl_ord_id, symbol=req.symbol, status=status,
                is_accepted=False, fix_payload=None, rejection_reason=msg, **kw
            )

        warnings: List[str] = []

        # 1. Investor Number (NIN): 10 digits, issued by Dubai CSD at DFM.
        nin = req.nin_investor_number
        if not isinstance(nin, str) or len(nin) != 10 or not nin.isdigit():
            return reject(
                "INVALID_NIN",
                f"National Investor Number {nin!r} must be a 10-digit numeric string.",
            )

        # 2. Structural order fields.
        field_error = self._validate_request(req)
        if field_error is not None:
            return reject("INVALID_ORDER_FIELD", field_error)

        # 3. Tick size regime.
        is_tick_ok, tick = self.audit_dfm_tick_size(req.price_aed)
        if not is_tick_ok:
            return reject(
                "INVALID_TICK_SIZE",
                f"Price {req.price_aed} {req.currency} is not a multiple of the "
                f"{tick} tick applicable to its price band.",
                required_tick_size=tick,
            )

        # 4. Upper/Lower Price Limits (DFM Rule 16.16).
        lower = upper = None
        if req.is_first_trading_session:
            # Rule 16.16(c): price floats in the first Trading Session; no band applies.
            warnings.append(
                "First Trading Session: no Upper/Lower Price Limit applied "
                "(DFM Rule 16.16(c)). Confirm the security is genuinely unbanded."
            )
        else:
            ref = req.prior_settlement_price_aed
            if ref is None or not isinstance(ref, (int, float)) or isinstance(ref, bool) \
                    or not math.isfinite(ref) or ref <= 0:
                # Fail closed: a missing benchmark must never mean "no band check".
                return reject(
                    "MISSING_REFERENCE_PRICE",
                    f"prior_settlement_price_aed {ref!r} is missing or not a positive "
                    "finite number, so the Upper/Lower Price Limit cannot be evaluated. "
                    "Supply the benchmark closing price, or set is_first_trading_session "
                    "if the security is genuinely unbanded.",
                )

            lower, upper = self.price_limits(ref, req.limit_up_pct, req.limit_down_pct)
            # Tolerant comparison: an order exactly on the limit must not be rejected
            # because of representation error in the band arithmetic.
            if req.price_aed < lower * (1.0 - _BAND_EPS) or req.price_aed > upper * (1.0 + _BAND_EPS):
                return reject(
                    "CIRCUIT_BREAKER_BAND_BREACH",
                    f"Price {req.price_aed} {req.currency} is outside the permitted band "
                    f"[{lower:.4f}, {upper:.4f}] around benchmark {ref} "
                    f"(-{req.limit_down_pct:.1%}/+{req.limit_up_pct:.1%}).",
                    required_tick_size=tick,
                    upper_price_limit=upper,
                    lower_price_limit=lower,
                )

        payload = self.format_fix_44_payload(req, tick, sending_time=sending_time)
        logger.info(
            "DFM FIX 4.4 ORDER BUILT [%s]: %s %d x %s @ %s %s (NIN=%s). Not transmitted.",
            req.cl_ord_id, req.side.upper().strip(), req.order_qty, req.symbol,
            req.price_aed, req.currency, nin,
        )
        for message in warnings:
            logger.warning(message)

        return DfmOrderExecutionReport(
            cl_ord_id=req.cl_ord_id,
            symbol=req.symbol,
            status="STATUS_OK",
            is_accepted=True,
            fix_payload=payload,
            rejection_reason=None,
            required_tick_size=tick,
            upper_price_limit=upper,
            lower_price_limit=lower,
            warnings=tuple(warnings),
        )
