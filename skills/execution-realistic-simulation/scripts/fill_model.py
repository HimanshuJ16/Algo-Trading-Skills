"""
execution-realistic-simulation: directional bid-ask fill pricing, square-root market
impact, top-of-book depth limiting, and a dated, source-attributed statutory fee stack.

Fill price model
----------------

A marketable order never fills at the mid. It crosses the spread, then pays impact::

    fill_BUY  = mid + half_spread + I(Q)
    fill_SELL = mid - half_spread - I(Q)

where I(Q) is the square-root law of market impact (Torre/BARRA; Almgren et al. 2005,
"Direct estimates of equity market impact", Risk 18(7); Bouchaud, Bonart, Donier and
Gould, "Trades, Quotes and Prices", CUP 2018, Ch. 12)::

    I(Q) = gamma * sigma * sqrt(Q / V) * P_mid

Q is the executed metaorder size, V the average daily volume in the same units as Q,
sigma the *daily* volatility as a fraction (0.02 = 2%/day), and gamma a dimensionless
prefactor empirically of order 1 — published calibrations cluster in 0.5-1.0, which is
why DEFAULT_IMPACT_GAMMA is 0.5. gamma is a free parameter and MUST be recalibrated
against the desk's own realized fills; the shipped default is a starting point, not a
measurement of any particular market.

The law is estimated on metaorders that are small relative to daily volume. Above
SQRT_LAW_MAX_VALIDATED_PARTICIPATION the model is extrapolating beyond the regime it
was fitted in, and the simulator logs a warning rather than silently returning a
number that looks authoritative.

Fee model
---------

Statutory rates are data, not code: every rate lives in a :class:`FeeSchedule` that
carries the date it was verified and the source it came from, so a stale rate is
visible rather than buried in an ``if`` branch. Rates change (Indian F&O STT moved
twice in eighteen months); re-verify against FEE_SCHEDULES_VERIFIED_ON before trusting
a backtest's cost figures, and pass a custom FeeSchedule when your broker's commercial
terms differ from the shipped defaults.

Limitations (documented, deliberate)
------------------------------------

- **Latency is the caller's responsibility.** This module does not queue, delay, or
  timestamp anything. Modelling latency means selecting the *post-latency* quote: pass
  the mid and half-spread observed when the order could actually have reached the
  exchange, not the signal bar's own quote. Feeding the signal bar's quote
  reintroduces exactly the look-ahead this skill exists to remove (see
  ``lookahead-bias-elimination``).
- **Fee schedules are retail-equivalent and turnover-based.** Brokerage defaults follow
  a published discount-broker schedule and are not a quote for any specific account.
  Per-share and per-contract levies cannot be expressed as a fraction of turnover and
  are therefore *not* modelled: FINRA's Trading Activity Fee (per share sold), clearing
  member charges, and depository (DP) charges on delivery sells.
- **STT on exercised or assigned options is not modelled.** It is charged on intrinsic
  value, not on premium turnover, so it cannot be derived from the arguments here.
- **Single-venue, single-price-level.** Depth limiting truncates at one aggregate depth
  figure; it does not walk a multi-level book. For a book-walking fill see
  ``order-book-depth-processing-l2-l3``.
- **Impact is applied to the executed quantity.** A partially filled order pays impact
  on what actually traded, not on the parent order size.
"""
from dataclasses import dataclass
from enum import Enum
import logging
import math
import warnings
from typing import Dict, Mapping, Optional

logger = logging.getLogger(__name__)

#: Date on which every rate in :data:`DEFAULT_FEE_SCHEDULES` was last verified against
#: the source cited on that schedule. Re-verify before relying on cost figures.
FEE_SCHEDULES_VERIFIED_ON = "2026-08-24"

#: Square-root law prefactor. Published calibrations put it at order 1 (0.5-1.0);
#: recalibrate per market and instrument against realized fills.
DEFAULT_IMPACT_GAMMA = 0.5

#: Default daily volatility as a fraction (2%/day). A placeholder, not a measurement.
DEFAULT_DAILY_VOLATILITY = 0.02

#: Participation (Q/ADV) above which the square-root law is extrapolating beyond the
#: small-metaorder regime it is fitted on. Warn rather than silently extrapolate.
SQRT_LAW_MAX_VALIDATED_PARTICIPATION = 0.10

_VALID_SIDES = ("BUY", "SELL")


class MarketType(str, Enum):
    INDIAN_EQUITY_INTRADAY = "INDIAN_EQUITY_INTRADAY"
    INDIAN_EQUITY_DELIVERY = "INDIAN_EQUITY_DELIVERY"
    INDIAN_FUTURES = "INDIAN_FUTURES"
    INDIAN_OPTIONS = "INDIAN_OPTIONS"
    US_EQUITY = "US_EQUITY"
    CRYPTO_SPOT = "CRYPTO_SPOT"


@dataclass(frozen=True)
class FeeSchedule:
    """
    One market's turnover-based fee stack, with provenance.

    All ``*_rate`` fields are fractions of turnover (0.0015 = 0.15%), applied to the
    turnover currency of the market in question — INR for the Indian schedules, USD for
    ``US_EQUITY``. Mixing currencies across a portfolio is the caller's problem (see
    ``multi-currency-pnl-and-fx-conversion``).
    """

    market_type: MarketType
    effective_from: str            # Date the rates themselves took legal/commercial effect
    source: str                    # Where each rate was verified
    brokerage_rate: float = 0.0            # Fraction of turnover
    brokerage_cap: Optional[float] = None  # Per-order cap on the ad-valorem component
    brokerage_flat: float = 0.0            # Flat per-order charge added on top
    stt_buy_rate: float = 0.0              # Securities Transaction Tax / CTT, buy side
    stt_sell_rate: float = 0.0             # STT / CTT, sell side
    exchange_txn_rate: float = 0.0         # Exchange transaction charge (IPFT included)
    sebi_turnover_rate: float = 0.0        # SEBI turnover fee
    stamp_duty_buy_rate: float = 0.0       # Stamp duty — buy side only, never GST-able
    gst_rate: float = 0.0                  # Applied to brokerage + exchange + SEBI only
    other_regulatory_sell_rate: float = 0.0  # e.g. SEC Section 31, sell side only
    other_regulatory_label: str = ""

    def brokerage_on(self, turnover: float) -> float:
        """Ad-valorem brokerage subject to its per-order cap, plus any flat charge."""
        ad_valorem = self.brokerage_rate * turnover
        if self.brokerage_cap is not None:
            ad_valorem = min(ad_valorem, self.brokerage_cap)
        return ad_valorem + self.brokerage_flat


# ---------------------------------------------------------------------------
# Default schedules. Every rate below was verified on FEE_SCHEDULES_VERIFIED_ON
# against the source cited on the schedule. Statutory rates are jurisdiction-specific
# and change: the Indian F&O STT rates below are the post-Budget-2026 rates effective
# 1 April 2026, which replaced the 1 October 2024 rates (0.10% options / 0.02%
# futures), which had themselves replaced 0.0625% / 0.0125%.
# ---------------------------------------------------------------------------

_NSE_SEBI_TURNOVER_RATE = 0.000001   # Rs 10 per crore, non-debt securities
_INDIA_GST_RATE = 0.18               # On brokerage + exchange charges + SEBI fees

DEFAULT_FEE_SCHEDULES: Mapping[MarketType, FeeSchedule] = {
    MarketType.INDIAN_OPTIONS: FeeSchedule(
        market_type=MarketType.INDIAN_OPTIONS,
        effective_from="2026-04-01",
        source="STT 0.15% on sell-side premium (Budget 2026, eff. 1 Apr 2026); NSE "
               "equity-options transaction charge 0.03553% of premium (IPFT included); "
               "SEBI turnover fee Rs 10/crore; stamp duty 0.003% buy side; GST 18%.",
        brokerage_flat=20.0,          # Discount-broker flat charge per executed order
        stt_sell_rate=0.0015,
        exchange_txn_rate=0.0003553,
        sebi_turnover_rate=_NSE_SEBI_TURNOVER_RATE,
        stamp_duty_buy_rate=0.00003,
        gst_rate=_INDIA_GST_RATE,
    ),
    MarketType.INDIAN_FUTURES: FeeSchedule(
        market_type=MarketType.INDIAN_FUTURES,
        effective_from="2026-04-01",
        source="STT 0.05% on sell side (Budget 2026, eff. 1 Apr 2026); NSE "
               "equity-futures transaction charge 0.00183%; SEBI turnover fee "
               "Rs 10/crore; stamp duty 0.002% buy side; GST 18%.",
        brokerage_rate=0.0003,
        brokerage_cap=20.0,
        stt_sell_rate=0.0005,
        exchange_txn_rate=0.0000183,
        sebi_turnover_rate=_NSE_SEBI_TURNOVER_RATE,
        stamp_duty_buy_rate=0.00002,
        gst_rate=_INDIA_GST_RATE,
    ),
    MarketType.INDIAN_EQUITY_INTRADAY: FeeSchedule(
        market_type=MarketType.INDIAN_EQUITY_INTRADAY,
        effective_from="2026-03-01",
        source="STT 0.025% on sell side (unchanged by Budget 2026); NSE cash-market "
               "transaction charge 0.00307% (IPFT included); SEBI turnover fee "
               "Rs 10/crore; stamp duty 0.003% buy side; GST 18%.",
        brokerage_rate=0.0003,
        brokerage_cap=20.0,
        stt_sell_rate=0.00025,
        exchange_txn_rate=0.0000307,
        sebi_turnover_rate=_NSE_SEBI_TURNOVER_RATE,
        stamp_duty_buy_rate=0.00003,
        gst_rate=_INDIA_GST_RATE,
    ),
    MarketType.INDIAN_EQUITY_DELIVERY: FeeSchedule(
        market_type=MarketType.INDIAN_EQUITY_DELIVERY,
        effective_from="2026-03-01",
        source="STT 0.1% on BOTH sides (unchanged by Budget 2026); NSE cash-market "
               "transaction charge 0.00307%; SEBI turnover fee Rs 10/crore; stamp duty "
               "0.015% buy side; GST 18%. Zero-brokerage delivery assumed. Depository "
               "(DP) charges on the sell leg are per-scrip and are NOT modelled.",
        stt_buy_rate=0.001,
        stt_sell_rate=0.001,
        exchange_txn_rate=0.0000307,
        sebi_turnover_rate=_NSE_SEBI_TURNOVER_RATE,
        stamp_duty_buy_rate=0.00015,
        gst_rate=_INDIA_GST_RATE,
    ),
    MarketType.US_EQUITY: FeeSchedule(
        market_type=MarketType.US_EQUITY,
        effective_from="2026-04-04",
        source="SEC Section 31 fee USD 20.60 per USD 1,000,000 of sales, sell side only "
               "(SEC FY2026 annual adjustment order; FINRA Information Notice of "
               "17 Mar 2026, eff. 4 Apr 2026). Commission defaults to zero — set "
               "brokerage_rate/brokerage_flat for your broker. FINRA's Trading Activity "
               "Fee is charged per share sold and is NOT modelled here.",
        other_regulatory_sell_rate=20.60 / 1_000_000,
        other_regulatory_label="SEC Section 31 fee",
    ),
    MarketType.CRYPTO_SPOT: FeeSchedule(
        market_type=MarketType.CRYPTO_SPOT,
        effective_from="",
        source="PLACEHOLDER taker fee of 0.1%. Crypto venue fees are tier- and "
               "maker/taker-dependent and no single rate is correct; replace with your "
               "venue's published schedule before trusting any cost figure.",
        brokerage_rate=0.001,
    ),
}


@dataclass
class FeeBreakdown:
    brokerage: float
    stt: float
    exchange_txn_fee: float
    sebi_turnover_fee: float
    stamp_duty: float
    gst: float
    total_fees: float
    other_regulatory_fees: float = 0.0
    schedule_effective_from: str = ""

    def to_dict(self) -> Dict[str, float]:
        return {
            "brokerage": round(self.brokerage, 2),
            "stt": round(self.stt, 2),
            "exchange_txn_fee": round(self.exchange_txn_fee, 2),
            "sebi_turnover_fee": round(self.sebi_turnover_fee, 2),
            "stamp_duty": round(self.stamp_duty, 2),
            "gst": round(self.gst, 2),
            "other_regulatory_fees": round(self.other_regulatory_fees, 2),
            "total_fees": round(self.total_fees, 2),
        }


@dataclass
class SimulatedFillResult:
    fill_price: float
    filled_qty: float
    requested_qty: float
    slippage_cost: float           # Signed so it is a positive cost on both sides
    fee_breakdown: FeeBreakdown
    is_partial_fill: bool
    participation_ratio: float = 0.0     # filled_qty / adv — the square-root law's Q/V
    market_impact_per_unit: float = 0.0  # I(Q) in price units, excluding the half-spread


def _normalize_side(side: str) -> str:
    """
    Map a side string to "BUY"/"SELL", rejecting anything else.

    An execution simulator must never guess a side: silently treating an unrecognised
    string as one direction turns a typo into a backtest that trades the wrong way with
    no error visible anywhere in the results.
    """
    if not isinstance(side, str):
        raise TypeError(f"side must be a string, got {type(side).__name__}.")
    side_clean = side.strip().upper()
    if side_clean not in _VALID_SIDES:
        raise ValueError(f"side must be one of {_VALID_SIDES}, got {side!r}.")
    return side_clean


def _require_finite(name: str, value: float) -> float:
    """Reject non-numeric, NaN and infinite inputs before they poison a P&L series."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a real number, got {type(value).__name__}.")
    value = float(value)
    if math.isnan(value) or math.isinf(value):
        raise ValueError(f"{name} must be finite, got {value}.")
    return value


class RealisticExecutionSimulator:
    """
    Simulates a marketable order's fill: cross the spread, pay square-root impact,
    truncate at available depth, then apply the market's statutory fee stack.

    Every input is validated. Invalid market state (a non-positive price, a zero ADV, a
    half-spread wider than the mid) raises rather than being clamped: in a backtest a
    silently repaired input produces a plausible-looking equity curve built on a quote
    that never existed.
    """

    def __init__(self, impact_gamma: float = DEFAULT_IMPACT_GAMMA):
        impact_gamma = _require_finite("impact_gamma", impact_gamma)
        if impact_gamma < 0.0:
            raise ValueError(
                f"impact_gamma must be >= 0, got {impact_gamma}. A negative prefactor "
                "would fill buys below the ask, i.e. pay the trader for consuming "
                "liquidity."
            )
        self.impact_gamma = impact_gamma

    def simulate_fill(
        self,
        side: str,  # "BUY" or "SELL"
        order_size: float,
        mid_price: float,
        half_spread: float,
        adv: float,
        volatility: float = DEFAULT_DAILY_VOLATILITY,
        market_depth_available: Optional[float] = None,
        market_type: MarketType = MarketType.INDIAN_OPTIONS,
        fee_schedule: Optional[FeeSchedule] = None,
    ) -> SimulatedFillResult:
        """
        Simulate one marketable order against a single quote snapshot.

        ``mid_price`` and ``half_spread`` must describe the quote at the moment the
        order could actually have reached the exchange — after the caller's own latency
        assumption — not the quote on the bar that produced the signal.

        ``volatility`` is *daily* volatility as a fraction (0.02 = 2% per day) and must
        be on the same horizon as ``adv``, since the square-root law's sigma and V are
        both daily quantities.
        """
        side_clean = _normalize_side(side)
        order_size = _require_finite("order_size", order_size)
        mid_price = _require_finite("mid_price", mid_price)
        half_spread = _require_finite("half_spread", half_spread)
        adv = _require_finite("adv", adv)
        volatility = _require_finite("volatility", volatility)

        if order_size <= 0.0:
            raise ValueError(f"order_size must be > 0, got {order_size}.")
        if mid_price <= 0.0:
            raise ValueError(f"mid_price must be > 0, got {mid_price}.")
        if half_spread < 0.0:
            raise ValueError(f"half_spread must be >= 0, got {half_spread}.")
        if half_spread >= mid_price:
            raise ValueError(
                f"half_spread ({half_spread}) >= mid_price ({mid_price}) implies a "
                "non-positive bid; that quote is corrupt, not merely wide."
            )
        if adv <= 0.0:
            raise ValueError(
                f"adv must be > 0, got {adv}. A zero or unknown ADV cannot be used to "
                "estimate participation — supply a real volume estimate or skip the "
                "instrument rather than substituting a placeholder."
            )
        if volatility < 0.0:
            raise ValueError(f"volatility must be >= 0, got {volatility}.")

        # Liquidity depth check — truncate at what is actually available.
        is_partial = False
        filled_qty = order_size
        if market_depth_available is not None:
            market_depth_available = _require_finite(
                "market_depth_available", market_depth_available
            )
            if market_depth_available < 0.0:
                raise ValueError(
                    f"market_depth_available must be >= 0, got {market_depth_available}."
                )
            if order_size > market_depth_available:
                filled_qty = market_depth_available
                is_partial = True
                logger.info(
                    "Partial fill (%s): requested %s, depth available %s.",
                    side_clean, order_size, market_depth_available,
                )

        participation_ratio = filled_qty / adv
        if participation_ratio > SQRT_LAW_MAX_VALIDATED_PARTICIPATION:
            logger.warning(
                "Participation %.2f%% of ADV exceeds the %.0f%% regime the square-root "
                "law is calibrated on; this impact estimate is an extrapolation.",
                participation_ratio * 100.0,
                SQRT_LAW_MAX_VALIDATED_PARTICIPATION * 100.0,
            )

        # Square-root law: I(Q) = gamma * sigma * sqrt(Q / V) * P_mid
        impact_per_unit = (
            self.impact_gamma * volatility * math.sqrt(participation_ratio) * mid_price
        )

        if side_clean == "BUY":
            fill_price = mid_price + half_spread + impact_per_unit
        else:
            fill_price = mid_price - half_spread - impact_per_unit

        if fill_price <= 0.0:
            raise ValueError(
                f"Modelled {side_clean} fill price is non-positive ({fill_price:.6f}): "
                f"impact {impact_per_unit:.6f} plus half-spread {half_spread} exceeds a "
                f"mid of {mid_price} at {participation_ratio:.1%} of ADV. The order is "
                "far outside the square-root law's calibrated regime — size it down or "
                "model it as a multi-day schedule; do not clamp the price."
            )

        # Signed so slippage is reported as a positive cost on both sides.
        slippage = (
            (fill_price - mid_price) if side_clean == "BUY" else (mid_price - fill_price)
        ) * filled_qty

        turnover = fill_price * filled_qty
        fees = self.calculate_fees(
            turnover=turnover,
            market_type=market_type,
            side=side_clean,
            fee_schedule=fee_schedule,
        )

        # Deliberately unrounded: fill_price * filled_qty must reconcile exactly with
        # the turnover the fees below were charged on. Rounding to an instrument's tick
        # size or a currency's minor unit is the caller's job — see
        # ``minimum-fill-size-and-lot-rounding-logic``. A fixed 4-decimal round here
        # would also be wrong for any instrument quoted more finely than that.
        return SimulatedFillResult(
            fill_price=fill_price,
            filled_qty=filled_qty,
            requested_qty=order_size,
            slippage_cost=slippage,
            fee_breakdown=fees,
            is_partial_fill=is_partial,
            participation_ratio=participation_ratio,
            market_impact_per_unit=impact_per_unit,
        )

    @staticmethod
    def calculate_fees(
        turnover: float,
        market_type: MarketType = MarketType.INDIAN_OPTIONS,
        side: str = "BUY",
        fee_schedule: Optional[FeeSchedule] = None,
    ) -> FeeBreakdown:
        """
        Apply a market's turnover-based fee stack.

        ``turnover`` is executed value (fill price x filled quantity) in the schedule's
        own currency. A zero-quantity (entirely unfilled) order costs nothing — flat
        per-order brokerage is charged on *executed* orders only.

        Pass ``fee_schedule`` to override the shipped defaults with your broker's actual
        commercial terms; the defaults are retail-equivalent, not a quote.
        """
        turnover = _require_finite("turnover", turnover)
        if turnover < 0.0:
            raise ValueError(f"turnover must be >= 0, got {turnover}.")
        side_clean = _normalize_side(side)

        if fee_schedule is None:
            # MarketType is a str enum, so an equivalent string is accepted; anything
            # that is not a known market raises rather than defaulting to a schedule.
            market_type = MarketType(market_type)
            if market_type not in DEFAULT_FEE_SCHEDULES:  # pragma: no cover
                raise NotImplementedError(
                    f"No fee schedule is defined for {market_type}. Add one to "
                    "DEFAULT_FEE_SCHEDULES or pass fee_schedule explicitly — never let "
                    "a market fall back to another market's rates."
                )
            fee_schedule = DEFAULT_FEE_SCHEDULES[market_type]

        if turnover == 0.0:
            return FeeBreakdown(
                brokerage=0.0, stt=0.0, exchange_txn_fee=0.0, sebi_turnover_fee=0.0,
                stamp_duty=0.0, gst=0.0, total_fees=0.0, other_regulatory_fees=0.0,
                schedule_effective_from=fee_schedule.effective_from,
            )

        is_buy = side_clean == "BUY"

        brokerage = fee_schedule.brokerage_on(turnover)
        stt = turnover * (
            fee_schedule.stt_buy_rate if is_buy else fee_schedule.stt_sell_rate
        )
        exch_txn = turnover * fee_schedule.exchange_txn_rate
        sebi = turnover * fee_schedule.sebi_turnover_rate
        stamp = turnover * fee_schedule.stamp_duty_buy_rate if is_buy else 0.0
        other = 0.0 if is_buy else turnover * fee_schedule.other_regulatory_sell_rate

        # GST applies to brokerage and to exchange/SEBI charges. STT and stamp duty are
        # themselves taxes and are not part of the GST base.
        gst = (brokerage + exch_txn + sebi) * fee_schedule.gst_rate

        total = brokerage + stt + exch_txn + sebi + stamp + gst + other
        return FeeBreakdown(
            brokerage=brokerage,
            stt=stt,
            exchange_txn_fee=exch_txn,
            sebi_turnover_fee=sebi,
            stamp_duty=stamp,
            gst=gst,
            total_fees=total,
            other_regulatory_fees=other,
            schedule_effective_from=fee_schedule.effective_from,
        )


# ---------------------------------------------------------------------------
# Deprecated single-function helpers, kept so existing call sites keep working.
# ---------------------------------------------------------------------------

def simulate_fill_price(
    mid_price,
    half_spread,
    side,
    size,
    adv,
    impact_coef=0.1,
    volatility: float = DEFAULT_DAILY_VOLATILITY,
):
    """
    Deprecated. Use :meth:`RealisticExecutionSimulator.simulate_fill`.

    This helper previously applied impact *linearly* in participation — the flat/linear
    slippage assumption this skill's own guidance names as a pitfall, which understates
    cost for small orders and overstates it for large ones. It now delegates to the
    square-root law, so ``impact_coef`` is the prefactor gamma and the price returned
    differs from an earlier output.
    """
    warnings.warn(
        "simulate_fill_price is deprecated; use RealisticExecutionSimulator."
        "simulate_fill, which also returns fees and partial-fill state.",
        DeprecationWarning,
        stacklevel=2,
    )
    result = RealisticExecutionSimulator(impact_gamma=impact_coef).simulate_fill(
        side=side,
        order_size=size,
        mid_price=mid_price,
        half_spread=half_spread,
        adv=adv,
        volatility=volatility,
    )
    return result.fill_price


def estimate_fees(
    turnover,
    brokerage_flat=20.0,
    stt_rate=0.0015,
    exch_txn_rate=0.0003553,
    gst_rate=0.18,
    stamp_rate=0.0,
    sebi_rate=0.000001,
):
    """
    Deprecated. Use :meth:`RealisticExecutionSimulator.calculate_fees`.

    Totals an explicitly supplied rate stack. The defaults are the NSE equity-options
    **sell**-side rates verified on ``FEE_SCHEDULES_VERIFIED_ON`` (stamp duty is zero
    because stamp duty is charged on the buy side only); an earlier defaults were not
    the rates of any actual market. Supplying your own rates keeps the prior behaviour.
    """
    warnings.warn(
        "estimate_fees is deprecated; use RealisticExecutionSimulator.calculate_fees, "
        "which carries the effective date and source of every rate it applies.",
        DeprecationWarning,
        stacklevel=2,
    )
    turnover = _require_finite("turnover", turnover)
    if turnover < 0.0:
        raise ValueError(f"turnover must be >= 0, got {turnover}.")
    stt = turnover * stt_rate
    exch_txn = turnover * exch_txn_rate
    sebi = turnover * sebi_rate
    stamp = turnover * stamp_rate
    gst = (brokerage_flat + exch_txn + sebi) * gst_rate
    return brokerage_flat + stt + exch_txn + sebi + stamp + gst
