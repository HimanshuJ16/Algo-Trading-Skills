"""
perpetual-futures-funding-rate-handling: signed funding payment, annualized carry
drag and adverse-drag audit for **linear (USDT/USDC-margined) crypto perpetual
swaps**.

Core identity, as published by the venues themselves:

    notional      = |position_qty| * mark_price          (quote currency)
    payment       = direction * notional * funding_rate  (+ = you pay, - = you receive)
    direction     = +1 for a long, -1 for a short

Binance states it as "Nominal Value of Positions = Mark Price x Size of a Contract"
and settles the fee against that nominal value; Bybit applies the rate to position
value marked at the mark price; both pay longs-to-shorts when the rate is positive.
The funding rate itself is produced by the venue (premium index plus a clamped
interest component) -- this module consumes a published rate, it does not forecast one.

What the annualized figures mean
--------------------------------
``annualized_funding_apr`` is a *simple* extrapolation: rate x periods_per_year,
where periods_per_year = 8760 / funding_interval_hours. ``annualized_funding_apy``
compounds the same per-period rate. Both answer one hypothetical question -- "what
if this single print repeated unchanged for a year?" -- and funding rates do not
behave that way. At +0.1% per 8h the two answers are 109.5% and 198.8%; quoting the
simple number as "the APR" while actually rolling the carry understates the cost by
most of a factor of two. Both are reported, both are labelled, neither is a forecast.

Sign convention (applies to *both* the payment and the annualized figures)
-------------------------------------------------------------------------
Positive = a cost to **this position**. Negative = income to this position. A short
under a positive funding rate therefore reports a negative payment and a negative
APR, because it is being paid. This is deliberately position-relative rather than
rate-relative: an agent that reads ``annualized_funding_apr > 0`` as "this position
is bleeding carry" is then correct for longs and shorts alike.

Limitations (deliberate, verified)
----------------------------------
- **Linear contracts only.** For inverse / COIN-margined contracts the notional is
  ``contracts * contract_multiplier / mark_price`` and the fee settles in the base
  coin, not in quote currency. Feeding a COIN-M position here produces a number with
  the wrong magnitude *and* the wrong unit.
- **Discrete settlement is assumed.** Binance, Bybit and OKX charge the whole
  interval's funding to whoever holds the position at the funding timestamp, with no
  proration -- close before the timestamp and you pay nothing. Deribit is the
  counterexample: its rate is *quoted* as an 8-hour rate but accrues continuously
  (payment = rate x size x elapsed / 8h). On a continuous-funding venue this module's
  per-interval payment is an upper bound on a partially-held interval, not the amount
  actually charged.
- **The 8-hour interval is a default, never an assumption.** Binance switches a symbol
  to hourly settlement when the previous rate reaches the cap/floor; OKX runs 4-hour
  contracts; Bybit sets the interval per symbol and adjusts it live. Read
  ``fundingIntervalHours`` (or the venue equivalent) per symbol per settlement and
  pass it in -- a symbol on a 1-hour interval annualizes at 8x the 8-hour figure.
- **One symbol, one print, one position.** No netting across sub-accounts, no
  portfolio aggregation, no funding-history integration.
- **Advisory only.** ``recommended_action`` is a string. This module places no orders,
  closes nothing, and reads no clock of its own.
- **Entry price does not enter the calculation.** Funding is charged on mark-priced
  notional; ``entry_price`` is carried for reporting context only.

References: see ``references/standards.md`` for the sourced venue table.
"""
import logging
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

#: Hours in a 365-day year -- the numerator for periods-per-year.
HOURS_PER_YEAR = 365.0 * 24.0

#: Venue default. Binance/Bybit/OKX all *default* to 8h (00:00, 08:00, 16:00 UTC),
#: and all three also run symbols on shorter intervals. Never assume it.
DEFAULT_FUNDING_INTERVAL_HOURS = 8

#: Unit-confusion guard, expressed as a per-interval decimal rate (0.05 = 5%).
#: Deliberately set well above every published venue cap so it never rejects a real
#: print: Binance caps most contracts at +/-2% (majors at 0.75 x maintenance margin
#: ratio, ~0.3% for BTCUSDT), Deribit documents +/-0.5% for BTC and +/-1% for ETH,
#: and OKX sets caps per contract. Anything above 5% per interval is almost certainly
#: a percent value that was never divided by 100.
#:
#: It catches gross confusions (0.75 handed over meaning "0.75%") and nothing subtler:
#: 0.01 could be a mistyped 0.01% or a genuine 1% print, and 1% sits inside Binance's
#: general cap, so the guard passes it rather than rejecting real funding. Validate
#: the units at the API boundary; this is a backstop, not a unit checker.
DEFAULT_MAX_PLAUSIBLE_FUNDING_RATE = 0.05

#: Accepted direction tokens. Anything else -- including Binance's one-way-mode
#: ``positionSide="BOTH"`` -- is rejected rather than guessed at, because guessing
#: wrong flips the sign of a real cash flow.
LONG_SIDE_ALIASES = frozenset({"LONG", "BUY"})
SHORT_SIDE_ALIASES = frozenset({"SHORT", "SELL"})

#: Position is receiving funding.
STATUS_INFLOW = "FUNDING_INFLOW_INCOME"
#: Position is paying funding, within policy.
STATUS_OUTFLOW_OK = "FUNDING_OUTFLOW_OK"
#: Position is paying funding above the policy APR ceiling.
STATUS_BREACH = "ADVERSE_FUNDING_DRAG_BREACH"
#: Rate printed exactly zero -- neither a cost nor income.
STATUS_NEUTRAL = "FUNDING_NEUTRAL"


class FundingInputError(ValueError):
    """Raised when position, funding-update or policy input cannot be trusted."""


def funding_timestamp_from_epoch_ms(epoch_ms: int) -> str:
    """
    Convert a venue epoch-millisecond funding time (Binance ``nextFundingTime``,
    Bybit ``nextFundingTime``) into the ISO-8601 UTC string this module expects.
    """
    if isinstance(epoch_ms, bool) or not isinstance(epoch_ms, int):
        raise FundingInputError(f"epoch_ms must be an int, got {type(epoch_ms).__name__}")
    if epoch_ms < 0:
        raise FundingInputError(f"epoch_ms must be non-negative, got {epoch_ms}")
    stamp = datetime.fromtimestamp(epoch_ms / 1000.0, tz=timezone.utc)
    return stamp.isoformat().replace("+00:00", "Z")


def parse_funding_timestamp(raw: str) -> datetime:
    """
    Parse an ISO-8601 funding timestamp into a timezone-aware UTC datetime.

    A trailing ``Z`` is accepted. A value carrying no offset is treated as UTC --
    the field is named ``..._utc`` and the venues publish funding times in UTC.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise FundingInputError(
            "next_funding_timestamp_utc must be a non-empty ISO-8601 string"
        )
    text = raw.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise FundingInputError(
            f"next_funding_timestamp_utc {raw!r} is not ISO-8601 (Binance/Bybit publish "
            "epoch ms -- convert with funding_timestamp_from_epoch_ms)"
        ) from exc
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def periods_per_year(funding_interval_hours: int) -> float:
    """Number of funding settlements in a 365-day year for the given interval."""
    if funding_interval_hours <= 0:
        raise FundingInputError(
            f"funding_interval_hours must be positive, got {funding_interval_hours}"
        )
    return HOURS_PER_YEAR / float(funding_interval_hours)


def annualize_funding_rate(
    per_period_rate: float, funding_interval_hours: int, compound: bool = False
) -> float:
    """
    Annualize a per-interval funding rate, returned as a decimal (0.1095 = 10.95%).

    ``compound=False`` gives the simple extrapolation rate x periods (an APR);
    ``compound=True`` gives ``(1 + rate) ** periods - 1`` (an APY). Both assume the
    single print repeats unchanged for a year, which it will not.
    """
    if not math.isfinite(per_period_rate):
        raise FundingInputError(f"per_period_rate must be finite, got {per_period_rate!r}")
    n = periods_per_year(funding_interval_hours)
    if not compound:
        return per_period_rate * n
    if per_period_rate <= -1.0:
        # A per-period loss of >=100% compounds to total loss; log1p is undefined here.
        return -1.0
    try:
        return math.expm1(n * math.log1p(per_period_rate))
    except OverflowError:
        return math.inf


def _format_pct(value: float) -> str:
    """Fixed notation for readable magnitudes, scientific beyond +/-1,000,000%."""
    if not math.isfinite(value):
        return f"{value:+}"
    if abs(value) >= 1e6:
        return f"{value:+.3e}"
    return f"{value:+.2f}"


def _normalized_side(side: str) -> str:
    if not isinstance(side, str):
        raise FundingInputError(f"side must be a string, got {type(side).__name__}")
    token = side.strip().upper()
    if token in LONG_SIDE_ALIASES:
        return "LONG"
    if token in SHORT_SIDE_ALIASES:
        return "SHORT"
    if token == "BOTH":
        raise FundingInputError(
            "side='BOTH' is Binance one-way mode and carries no direction; derive "
            "LONG/SHORT from the sign of positionAmt before calling"
        )
    raise FundingInputError(
        f"side {side!r} is not a recognised direction; expected one of "
        f"{sorted(LONG_SIDE_ALIASES | SHORT_SIDE_ALIASES)}"
    )


def _validate_position(pos: "PerpetualPosition") -> None:
    """
    Normalize and validate a position in place.

    Re-run at use as well as at construction: these are mutable dataclasses, and
    ``pos.mark_price = new_mark`` (or worse, ``pos.side = "BOTH"``) after construction
    would otherwise walk straight past every check.
    """
    if not isinstance(pos.symbol, str) or not pos.symbol.strip():
        raise FundingInputError("symbol must be a non-empty string")
    pos.symbol = pos.symbol.strip().upper()
    pos.side = _normalized_side(pos.side)

    for name in ("position_qty", "entry_price", "mark_price"):
        value = getattr(pos, name)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise FundingInputError(f"{name} must be numeric, got {type(value).__name__}")
        value = float(value)
        if not math.isfinite(value):
            raise FundingInputError(f"{name} must be finite, got {value!r}")
        setattr(pos, name, value)

    if pos.position_qty == 0.0:
        raise FundingInputError(
            "position_qty must be non-zero -- a flat position pays no funding"
        )
    if pos.entry_price <= 0.0:
        raise FundingInputError(f"entry_price must be positive, got {pos.entry_price}")
    if pos.mark_price <= 0.0:
        raise FundingInputError(f"mark_price must be positive, got {pos.mark_price}")

    if pos.side == "LONG" and pos.position_qty < 0.0:
        raise FundingInputError(
            f"side='LONG' contradicts position_qty={pos.position_qty}; resolve the "
            "direction before computing funding"
        )


@dataclass
class PerpetualPosition:
    """
    An open linear perpetual position at (or immediately before) a funding timestamp.

    ``side`` is authoritative for direction; ``position_qty`` supplies the magnitude.
    A quantity whose sign contradicts ``side`` (a LONG with a negative quantity) is
    rejected rather than silently reinterpreted.
    """
    symbol: str
    position_qty: float                  # + for Long, - for Short (magnitude is used)
    side: str                            # 'LONG'/'BUY' or 'SHORT'/'SELL'
    entry_price: float                   # Reporting context only -- funding ignores it.
    mark_price: float                    # Mark price at the funding timestamp.

    def __post_init__(self) -> None:
        _validate_position(self)


def _validate_update(update: "FundingRateUpdate") -> None:
    """Normalize and validate a funding print in place. Re-run at use; see above."""
    if not isinstance(update.symbol, str) or not update.symbol.strip():
        raise FundingInputError("symbol must be a non-empty string")
    update.symbol = update.symbol.strip().upper()

    if isinstance(update.funding_rate, bool) or not isinstance(
        update.funding_rate, (int, float)
    ):
        raise FundingInputError(
            f"funding_rate must be numeric, got {type(update.funding_rate).__name__}"
        )
    update.funding_rate = float(update.funding_rate)
    if not math.isfinite(update.funding_rate):
        raise FundingInputError(
            f"funding_rate must be finite, got {update.funding_rate!r} -- a NaN rate "
            "would otherwise propagate into a benign-looking zero-cost report"
        )

    if isinstance(update.funding_interval_hours, bool) or not isinstance(
        update.funding_interval_hours, int
    ):
        raise FundingInputError(
            "funding_interval_hours must be an int number of hours, got "
            f"{type(update.funding_interval_hours).__name__}"
        )
    if update.funding_interval_hours <= 0:
        raise FundingInputError(
            f"funding_interval_hours must be positive, got {update.funding_interval_hours}"
        )

    # Parsed purely to reject an unusable timestamp before it reaches a report.
    parse_funding_timestamp(update.next_funding_timestamp_utc)


@dataclass
class FundingRateUpdate:
    """
    A single published funding print for one symbol.

    ``funding_interval_hours`` must come from the venue for *this* symbol and *this*
    settlement (Binance ``GET /fapi/v1/fundingInfo`` -> ``fundingIntervalHours``);
    the default of 8 exists only so the common case is not boilerplate.
    """
    symbol: str
    funding_rate: float                  # Per-interval decimal, e.g. 0.0001 = +0.01%
    next_funding_timestamp_utc: str      # ISO-8601 UTC, e.g. '2026-07-31T16:00:00Z'
    funding_interval_hours: int = DEFAULT_FUNDING_INTERVAL_HOURS

    def __post_init__(self) -> None:
        _validate_update(self)


@dataclass
class FundingPolicyConfig:
    """
    Operator policy for the adverse-drag audit.

    ``max_adverse_funding_apr`` is a decimal annualized *cost* ceiling (0.25 = 25%),
    compared against the simple APR. The breach test is strict: a position sitting
    exactly on the limit is not a breach.
    """
    max_adverse_funding_apr: float = 0.25
    auto_close_high_drag: bool = True     # Advisory only -- drives recommended_action.
    max_plausible_funding_rate: float = DEFAULT_MAX_PLAUSIBLE_FUNDING_RATE

    def __post_init__(self) -> None:
        if not math.isfinite(self.max_adverse_funding_apr) or self.max_adverse_funding_apr < 0.0:
            raise FundingInputError(
                "max_adverse_funding_apr must be a finite non-negative decimal, got "
                f"{self.max_adverse_funding_apr!r}"
            )
        if (
            not math.isfinite(self.max_plausible_funding_rate)
            or self.max_plausible_funding_rate <= 0.0
        ):
            raise FundingInputError(
                "max_plausible_funding_rate must be a finite positive decimal, got "
                f"{self.max_plausible_funding_rate!r}"
            )
        if not isinstance(self.auto_close_high_drag, bool):
            raise FundingInputError("auto_close_high_drag must be a bool")


@dataclass
class FundingRateReport:
    """
    Funding audit for one position against one funding print.

    ``funding_payment_usd`` and both annualized figures are signed from the
    position's perspective: positive = cost, negative = income.
    """
    symbol: str
    position_notional_usd: float
    funding_rate_pct: float                        # The venue print, as a percent.
    funding_payment_usd: float                     # + = outflow (fee), - = inflow (income)
    annualized_funding_apr: float                  # Simple: rate * periods, percent.
    is_adverse_drag_high: bool
    status: str                                    # One of the STATUS_* values.
    audit_notes: str
    annualized_funding_apy: float = 0.0            # Compounded, percent.
    funding_interval_hours: int = DEFAULT_FUNDING_INTERVAL_HOURS
    periods_per_year: float = 0.0
    hours_to_next_funding: Optional[float] = None  # None unless now_utc supplied.
    recommended_action: str = "HOLD"


class PerpetualFuturesFundingRateHandlingEngine:
    """
    Computes the signed funding payment, annualized carry drag (simple and
    compounded) and adverse-drag verdict for a single linear perpetual position
    against a single published funding print.

    The engine is stateless and deterministic: it reads no clock, holds no position
    state and places no orders. Supply ``now_utc`` explicitly if a time-to-funding
    figure is wanted.
    """

    def __init__(self, policy: Optional[FundingPolicyConfig] = None):
        self.policy = policy or FundingPolicyConfig()

    def process_funding_update(
        self,
        pos: PerpetualPosition,
        update: FundingRateUpdate,
        now_utc: Optional[datetime] = None,
    ) -> FundingRateReport:
        """
        Audit one position against one funding print.

        Raises ``FundingInputError`` on a symbol mismatch, an implausible rate
        (percent/decimal confusion) or a naive ``now_utc``. Every rejection is
        deliberate: a funding number that is silently wrong by 100x, or attributed to
        the wrong instrument, is worse than no number at all.
        """
        if not isinstance(pos, PerpetualPosition):
            raise FundingInputError(
                f"pos must be a PerpetualPosition, got {type(pos).__name__}"
            )
        if not isinstance(update, FundingRateUpdate):
            raise FundingInputError(
                f"update must be a FundingRateUpdate, got {type(update).__name__}"
            )
        # Dataclasses are mutable: revalidate what was constructed earlier.
        _validate_position(pos)
        _validate_update(update)
        if pos.symbol != update.symbol:
            raise FundingInputError(
                f"symbol mismatch: position {pos.symbol!r} vs funding update {update.symbol!r}"
            )

        rate = update.funding_rate
        if abs(rate) > self.policy.max_plausible_funding_rate:
            raise FundingInputError(
                f"funding_rate {rate!r} exceeds the plausibility guard "
                f"{self.policy.max_plausible_funding_rate} per interval "
                f"({rate * 100.0:+.4f}% per interval would be unprecedented against "
                "published venue caps) -- this is almost certainly a percent value that "
                "was not divided by 100. Raise max_plausible_funding_rate deliberately "
                "if the print is genuine."
            )

        notional_val = abs(pos.position_qty) * pos.mark_price
        if not math.isfinite(notional_val):
            raise FundingInputError(
                f"notional |{pos.position_qty}| x {pos.mark_price} overflowed to "
                f"{notional_val!r}; the position or mark price is not a real one"
            )
        direction = 1.0 if pos.side == "LONG" else -1.0

        # Positive rate => longs pay shorts, on every venue this skill covers.
        payment_usd = direction * notional_val * rate

        # Cost rate from this position's perspective: + = paying, - = receiving.
        position_rate = direction * rate
        n_periods = periods_per_year(update.funding_interval_hours)
        annualized_apr = 100.0 * annualize_funding_rate(
            position_rate, update.funding_interval_hours, compound=False
        )
        annualized_apy = 100.0 * annualize_funding_rate(
            position_rate, update.funding_interval_hours, compound=True
        )

        is_adverse = (
            payment_usd > 0.0
            and annualized_apr > (self.policy.max_adverse_funding_apr * 100.0)
        )

        if is_adverse:
            status = STATUS_BREACH
        elif payment_usd > 0.0:
            status = STATUS_OUTFLOW_OK
        elif payment_usd < 0.0:
            status = STATUS_INFLOW
        else:
            status = STATUS_NEUTRAL

        hours_to_next = self._hours_to_next_funding(update, now_utc)
        stale_suffix = ""
        if hours_to_next is not None and hours_to_next < 0.0:
            stale_suffix = (
                f" STALE FUNDING TIMESTAMP: next_funding_timestamp_utc is "
                f"{abs(hours_to_next):.2f}h in the past -- refresh the print before acting."
            )

        recommended_action = "HOLD"
        if is_adverse:
            recommended_action = "CLOSE_OR_HEDGE" if self.policy.auto_close_high_drag else "REVIEW"

        flow_label = {STATUS_NEUTRAL: "ZERO", STATUS_INFLOW: "INCOME"}.get(status, "FEE")

        notes = (
            f"PERPETUAL FUNDING AUDIT [{pos.symbol} {pos.side} - {status}]: "
            f"Notional = ${notional_val:,.2f}, Rate = {rate * 100.0:+.6f}% per "
            f"{update.funding_interval_hours}h, Payment = ${payment_usd:+,.2f} ({flow_label}), "
            f"Annualized APR = {_format_pct(annualized_apr)}% (simple, {n_periods:.0f} "
            f"periods/yr), APY = {_format_pct(annualized_apy)}% (compounded). "
            f"Action = {recommended_action}."
            f"{stale_suffix}"
        )

        if is_adverse:
            logger.warning("ADVERSE FUNDING DRAG ALERT: %s", notes)
        else:
            logger.info("%s", notes)

        return FundingRateReport(
            symbol=pos.symbol,
            position_notional_usd=round(notional_val, 2),
            funding_rate_pct=round(rate * 100.0, 6),
            funding_payment_usd=round(payment_usd, 2),
            annualized_funding_apr=round(annualized_apr, 2),
            is_adverse_drag_high=is_adverse,
            status=status,
            audit_notes=notes,
            annualized_funding_apy=round(annualized_apy, 2),
            funding_interval_hours=update.funding_interval_hours,
            periods_per_year=round(n_periods, 4),
            hours_to_next_funding=(None if hours_to_next is None else round(hours_to_next, 6)),
            recommended_action=recommended_action,
        )

    @staticmethod
    def _hours_to_next_funding(
        update: FundingRateUpdate, now_utc: Optional[datetime]
    ) -> Optional[float]:
        if now_utc is None:
            return None
        if not isinstance(now_utc, datetime):
            raise FundingInputError(
                f"now_utc must be a datetime, got {type(now_utc).__name__}"
            )
        if now_utc.tzinfo is None or now_utc.tzinfo.utcoffset(now_utc) is None:
            raise FundingInputError(
                "now_utc must be timezone-aware; a naive local clock silently shifts the "
                "time-to-funding by the host's UTC offset"
            )
        next_funding = parse_funding_timestamp(update.next_funding_timestamp_utc)
        return (next_funding - now_utc.astimezone(timezone.utc)).total_seconds() / 3600.0
