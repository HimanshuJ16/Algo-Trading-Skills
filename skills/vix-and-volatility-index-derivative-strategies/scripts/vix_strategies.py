"""
vix-and-volatility-index-derivative-strategies: VIX futures term-structure
classification, front-month basis decay, short-volatility sizing with an explicit
stop, and Black-76 pricing of VIX call spreads off the front-month future.

Two contract multipliers, and they are not the same
---------------------------------------------------
This is the single most expensive mistake in VIX derivatives code, and the
older version of this module made it:

  * **VIX futures (VX)** settle at **$1,000 per index point**
    (Cboe VX contract specification, "Contract Multiplier: 1000").
  * **VIX options** settle at **$100 per index point** -- the exercise-settlement
    amount is the difference between the settlement value and the strike,
    "multiplied by $100" (Cboe VIX options contract specification).

Using $1,000 for the options leg overstates every option P&L, every max-profit
figure and every premium outlay by exactly 10x. ``VIX_FUTURES_MULTIPLIER`` and
``VIX_OPTIONS_MULTIPLIER`` are kept separate here so the two can never be
transposed silently.

What the roll yield number means
--------------------------------
``annualized_roll_yield_pct`` is the annualized front-month **basis**,
``(F1 - S) / S * 365 / D``. It is the return a short-F1 position earns *if spot
VIX is unchanged on the settlement date*, because VX futures converge to the
Special Opening Quotation (SOQ) of the VIX Index on their final settlement date.
Spot VIX is not a martingale and is not unchanged: the number is a carry estimate
under a static-spot assumption, never a forecast.

It is also **not** the curve roll ``F1 -> F2`` that constant-maturity ETPs such
as VXX and SVXY harvest. Those two quantities share a sign in a normal curve but
differ in magnitude; do not substitute one for the other.

Position sizing is notional, not margin
---------------------------------------
``notional_exposure_usd`` for a short VX position is ``contracts * F1 * 1000``.
That is an exposure figure, not a capital requirement and not a loss bound. FCM
initial margin on short VX is set by the clearing member, varies with volatility,
and is deliberately not modelled here. A short VIX future has unbounded loss: on
2018-02-05 the intraday indicative value of the XIV inverse-VIX ETN fell to at or
below 20% of the prior closing indicative value ($108.3681 on 2018-02-02),
triggering the acceleration clause that terminated the note (Credit Suisse 6-K,
2018-02-06). ``stop_loss_trigger_price`` and ``loss_at_stop_usd`` exist so the
stop is a computed number rather than a checklist aspiration.

Scope
-----
Classification, carry arithmetic, sizing arithmetic, and European cash-settled
call-spread pricing. No order routing, no margin model, no surface calibration,
no term-structure forecast, no ETP tracking model.
"""
import datetime
import logging
import math
from dataclasses import dataclass
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)

#: Cboe VX futures contract multiplier: $1,000 per VIX index point.
#: Source: Cboe VIX Futures contract specification ("Contract Multiplier: 1000").
VIX_FUTURES_MULTIPLIER = 1000.0

#: Cboe VIX options contract multiplier: $100 per VIX index point. The exercise
#: settlement amount is (settlement value - strike) multiplied by $100.
#: Source: Cboe VIX Options contract specification ("Contract Multiplier: 100").
VIX_OPTIONS_MULTIPLIER = 100.0

#: Minimum price interval for a single (non-spread) VX futures contract:
#: 0.05 index points = $50.00 per contract.
VIX_FUTURES_MIN_TICK = 0.05

#: Calendar-day convention for annualizing basis and for option year-fractions.
#: VIX derivatives DTE and the 30-day VIX index horizon are both quoted in
#: calendar days, so a 365-day year is the consistent choice.
DAYS_PER_YEAR = 365.0

#: Decimal places the curve slope is rounded to before it is compared against the
#: state thresholds. Binary floats make an exactly-on-threshold slope
#: non-deterministic: F1=20.00, F2=20.40 is 2% on paper but evaluates to
#: 1.999999999999993, which silently classifies FLAT instead of CONTANGO and
#: suppresses the trade. Rounding first makes the boundary behave as written.
#: 1e-6 of a percent is far finer than anything quotable -- the VX minimum tick of
#: 0.05 points is a 0.25% slope increment at F1=20 -- so this can never merge two
#: genuinely distinct quoted slopes.
SLOPE_COMPARISON_DECIMALS = 6


class TermStructureState(Enum):
    CONTANGO = "CONTANGO"               # F2 sufficiently above F1: calm regime
    BACKWARDATION = "BACKWARDATION"     # F2 sufficiently below F1: stress regime
    FLAT = "FLAT"                       # Slope inside the configured dead band


class VIXStrategyType(Enum):
    ROLL_YIELD_HARVEST = "ROLL_YIELD_HARVEST"        # Short F1 in steep contango
    TAIL_RISK_HEDGE = "TAIL_RISK_HEDGE"              # Long OTM call spread in backwardation
    TERM_STRUCTURE_SPREAD = "TERM_STRUCTURE_SPREAD"  # Long F2 / short F1 calendar spread
    NEUTRAL = "NEUTRAL"                              # Cash / no position


#: ``recommended_position`` value used when the strategy branch fired but the
#: budget could not fund a single contract. Contract count is 0; the caller must
#: not round it up.
POSITION_NONE_INSUFFICIENT_CAPITAL = "NONE_INSUFFICIENT_CAPITAL"

#: ``recommended_position`` value used when a tail hedge is called for but no
#: priced spread was supplied. An option position cannot be sized without a
#: premium, so no contract count is invented.
POSITION_PENDING_SPREAD_QUOTE = "LONG_VIX_CALL_SPREAD_PENDING_QUOTE"


class VIXEngineError(Exception):
    """Base exception for VIX Strategy Engine errors."""


def _require_finite(name: str, value: float) -> float:
    """
    Rejects NaN/Inf before it reaches any comparison.

    Every comparison against NaN evaluates False, so a plain ``if value <= 0``
    guard passes NaN straight through. a naive implementation did exactly
    that: a NaN spot VIX cleared the positivity check and produced a term
    structure whose roll yield was NaN, which then propagated into sizing.
    """
    numeric = float(value)
    if not math.isfinite(numeric):
        raise VIXEngineError(f"{name} must be a finite number, got {value!r}.")
    return numeric


def _require_positive(name: str, value: float) -> float:
    """Finite and strictly greater than zero."""
    numeric = _require_finite(name, value)
    if numeric <= 0.0:
        raise VIXEngineError(f"{name} must be strictly positive, got {numeric}.")
    return numeric


def _require_rate(name: str, value: float) -> float:
    """
    Finite and within +/-100% per annum.

    Without a bound, ``math.exp(-r * T)`` raises ``OverflowError`` -- not a
    ``VIXEngineError`` -- for an absurd rate, so a fat-fingered input escapes the
    module's own exception contract. No plausible discount rate for a
    weeks-to-months VIX option sits outside this band.
    """
    numeric = _require_finite(name, value)
    if abs(numeric) > 1.0:
        raise VIXEngineError(
            f"{name} must be a decimal rate within +/-1.0 (+/-100% p.a.), got {numeric}. "
            "Pass 0.042 for 4.2%, not 4.2."
        )
    return numeric


def _norm_cdf(x: float) -> float:
    """Standard normal cumulative distribution function."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


@dataclass
class VIXFuturesContract:
    """
    A single listed VX futures contract.

    :param symbol: Exchange symbol, e.g. ``VXF25``.
    :param expiry_date: Final settlement date. For monthly VX this is the
        Wednesday 30 days prior to the third Friday of the following calendar
        month, moved to the preceding business day if that Wednesday or the
        Friday 30 days after it is a Cboe Options holiday.
    :param days_to_expiry: Calendar days from the valuation date to
        ``expiry_date``. Must be >= 1: the last trading day precedes the
        settlement morning, so a live quotable contract always has a day left.
    :param price: Futures price in VIX index points.
    """

    symbol: str
    expiry_date: datetime.date
    days_to_expiry: int
    price: float

    def __post_init__(self) -> None:
        if not self.symbol:
            raise VIXEngineError("Futures contract symbol must be non-empty.")
        self.price = _require_positive(f"{self.symbol} price", self.price)
        if int(self.days_to_expiry) != self.days_to_expiry:
            raise VIXEngineError(
                f"{self.symbol} days_to_expiry must be a whole number of calendar days."
            )
        self.days_to_expiry = int(self.days_to_expiry)
        if self.days_to_expiry < 1:
            raise VIXEngineError(
                f"{self.symbol} days_to_expiry must be >= 1 (got {self.days_to_expiry}). "
                "A settled or same-morning contract has no basis left to decay and must "
                "be rolled, not carried."
            )


@dataclass
class VIXTermStructure:
    """Front-two term-structure state and the carry implied by the front basis."""

    spot_vix: float
    f1_contract: VIXFuturesContract
    f2_contract: VIXFuturesContract
    slope_f2_minus_f1: float
    state: TermStructureState
    annualized_roll_yield_pct: float
    slope_pct: float = 0.0
    front_basis: float = 0.0


@dataclass
class VIXCallSpreadQuote:
    """
    A 1x1 vertical VIX call spread priced off the front-month future.

    ``net_debit`` is in VIX index points; the USD fields already carry the $100
    VIX **options** multiplier.
    """

    strike_lower: float
    strike_upper: float
    f1_futures_price: float
    days_to_expiry: int
    net_debit: float
    max_profit_per_contract_usd: float
    max_loss_per_contract_usd: float
    breakeven_vix: float

    @property
    def spread_width(self) -> float:
        return self.strike_upper - self.strike_lower


@dataclass
class VIXStrategySignal:
    """
    A sized tactical recommendation.

    ``target_contracts`` is floored, never rounded up: a budget that cannot fund
    one contract yields zero contracts and
    ``recommended_position == POSITION_NONE_INSUFFICIENT_CAPITAL``.
    """

    strategy_type: VIXStrategyType
    recommended_position: str
    target_contracts: int
    daily_roll_decay_usd: float
    tail_hedge_protection_usd: float
    rationale: str
    notional_exposure_usd: float = 0.0
    premium_outlay_usd: float = 0.0
    stop_loss_trigger_price: Optional[float] = None
    loss_at_stop_usd: float = 0.0


class VIXStrategyEngine:
    """
    VIX futures term-structure classifier, carry calculator, position sizer and
    call-spread pricer.

    Every threshold and budget below is house policy supplied by the caller. None
    is an exchange rule or a regulatory limit; the defaults are illustrative
    starting points, not published standards.
    """

    def __init__(
        self,
        contango_threshold_pct: float = 2.0,
        backwardation_threshold_pct: float = -2.0,
        short_vol_notional_budget_pct: float = 5.0,
        tail_hedge_premium_budget_pct: float = 2.0,
        stop_loss_futures_move_pct: float = 30.0,
    ) -> None:
        """
        :param contango_threshold_pct: Slope ``(F2 - F1) / F1 * 100`` at or above
            which the curve is classified CONTANGO.
        :param backwardation_threshold_pct: Slope at or below which the curve is
            classified BACKWARDATION. Must be strictly below the contango
            threshold, otherwise the two states overlap.
        :param short_vol_notional_budget_pct: Percent of portfolio equity used as
            the *notional* budget for short-F1 sizing. Notional is not margin and
            is not a loss bound; see the module docstring.
        :param tail_hedge_premium_budget_pct: Percent of portfolio equity spendable
            on call-spread *premium*. Premium is the entire loss bound of a long
            debit spread, so this one is a true risk budget.
        :param stop_loss_futures_move_pct: Adverse move in the **front future's own
            price** that triggers the protective buy-stop on a short position. A
            spot-VIX-referenced stop is a different trigger: F1 prices the expected
            VIX at settlement rather than today's level, so in a spike it moves
            proportionally less than spot and a spot-referenced percentage fires
            earlier than the same percentage on F1.
        """
        self.contango_threshold_pct = _require_finite(
            "contango_threshold_pct", contango_threshold_pct
        )
        self.backwardation_threshold_pct = _require_finite(
            "backwardation_threshold_pct", backwardation_threshold_pct
        )
        if self.backwardation_threshold_pct >= self.contango_threshold_pct:
            raise VIXEngineError(
                "backwardation_threshold_pct must be strictly below "
                "contango_threshold_pct, otherwise the two states overlap (got "
                f"{self.backwardation_threshold_pct} and {self.contango_threshold_pct})."
            )
        self.short_vol_notional_budget_pct = _require_positive(
            "short_vol_notional_budget_pct", short_vol_notional_budget_pct
        )
        self.tail_hedge_premium_budget_pct = _require_positive(
            "tail_hedge_premium_budget_pct", tail_hedge_premium_budget_pct
        )
        self.stop_loss_futures_move_pct = _require_positive(
            "stop_loss_futures_move_pct", stop_loss_futures_move_pct
        )
        logger.info(
            "Initialized VIX Strategy Engine: contango>=%.2f%%, backwardation<=%.2f%%, "
            "short-vol notional budget=%.2f%%, tail-hedge premium budget=%.2f%%, "
            "stop=%.2f%% adverse move in F1",
            self.contango_threshold_pct,
            self.backwardation_threshold_pct,
            self.short_vol_notional_budget_pct,
            self.tail_hedge_premium_budget_pct,
            self.stop_loss_futures_move_pct,
        )

    # ------------------------------------------------------------------ #
    # Term structure                                                     #
    # ------------------------------------------------------------------ #

    def analyze_term_structure(
        self, spot_vix: float, f1: VIXFuturesContract, f2: VIXFuturesContract
    ) -> VIXTermStructure:
        """
        Classifies the front-two curve and annualizes the front-month basis.

        ``annualized_roll_yield_pct = (F1 - S) / S * 365 / D * 100`` is the carry a
        short-F1 position earns **if spot VIX is unchanged on the settlement
        date**. It is not the ``F1 -> F2`` curve roll that constant-maturity ETPs
        harvest, and it is not a forecast.

        :raises VIXEngineError: if ``spot_vix`` is not finite and positive, or if
            ``f2`` does not expire strictly after ``f1``. The ordering check
            matters: passing the contracts in the wrong order inverts the sign of
            the slope and silently turns CONTANGO into BACKWARDATION, which flips
            the recommendation from short volatility to long volatility.
        """
        spot_vix = _require_positive("spot_vix", spot_vix)

        if f2.days_to_expiry <= f1.days_to_expiry:
            raise VIXEngineError(
                f"f2 ({f2.symbol}, {f2.days_to_expiry}d) must expire strictly after f1 "
                f"({f1.symbol}, {f1.days_to_expiry}d). Reversed contracts invert the "
                "slope sign and flip the strategy from short to long volatility."
            )
        if f2.expiry_date <= f1.expiry_date:
            raise VIXEngineError(
                f"f2 expiry {f2.expiry_date} must be strictly after f1 expiry "
                f"{f1.expiry_date}; days_to_expiry and expiry_date disagree."
            )

        slope = f2.price - f1.price
        # Rounded before comparison so an exactly-on-threshold slope classifies as
        # written rather than as binary floating point happens to represent it.
        slope_pct = round((slope / f1.price) * 100.0, SLOPE_COMPARISON_DECIMALS)

        if slope_pct >= self.contango_threshold_pct:
            state = TermStructureState.CONTANGO
        elif slope_pct <= self.backwardation_threshold_pct:
            state = TermStructureState.BACKWARDATION
        else:
            state = TermStructureState.FLAT

        front_basis = f1.price - spot_vix
        roll_yield_pct = (
            (front_basis / spot_vix) * (DAYS_PER_YEAR / f1.days_to_expiry) * 100.0
        )

        logger.info(
            "VIX term structure: spot=%.2f F1=%.2f F2=%.2f slope=%.2f (%.2f%%) "
            "state=%s basis=%.2f annualized_basis=%.2f%%",
            spot_vix, f1.price, f2.price, slope, slope_pct, state.value,
            front_basis, roll_yield_pct,
        )

        return VIXTermStructure(
            spot_vix=spot_vix,
            f1_contract=f1,
            f2_contract=f2,
            slope_f2_minus_f1=round(slope, 4),
            state=state,
            annualized_roll_yield_pct=round(roll_yield_pct, 2),
            slope_pct=slope_pct,
            front_basis=round(front_basis, 4),
        )

    # ------------------------------------------------------------------ #
    # Option pricing                                                     #
    # ------------------------------------------------------------------ #

    def black76_call(
        self,
        f1_futures_price: float,
        strike: float,
        days_to_expiry: int,
        implied_vol: float,
        risk_free_rate: float = 0.0,
    ) -> float:
        """
        Black (1976) call price on a futures underlying, in VIX index points.

        ``C = e^{-rT} [F N(d1) - K N(d2)]`` with
        ``d1 = (ln(F/K) + 0.5 sigma^2 T) / (sigma sqrt(T))``, ``d2 = d1 - sigma sqrt(T)``.

        The forward is the **VIX future**, never spot VIX. VIX options settle to
        the same SOQ as the corresponding VX future, so the future -- not the
        index -- is the tradeable forward, and VIX option implied volatilities are
        quoted against it.

        ``implied_vol`` must be the implied volatility *of this strike*. The VIX
        option smile slopes upward in strike (a call skew, the mirror image of the
        equity index put skew), so reusing an at-the-money quote for a far OTM call
        understates its premium. There is deliberately no default.

        Lognormality of the futures price is the market's quoting convention, not a
        property of VIX. VIX is mean-reverting and positively skewed; Black-76 here
        is an interpolator for quoted vols, not a distributional model of the index.

        Multiply the result by ``VIX_OPTIONS_MULTIPLIER`` (100) for USD per contract.
        """
        f1_futures_price = _require_positive("f1_futures_price", f1_futures_price)
        strike = _require_positive("strike", strike)
        implied_vol = _require_positive("implied_vol", implied_vol)
        risk_free_rate = _require_rate("risk_free_rate", risk_free_rate)
        days_to_expiry = int(days_to_expiry)
        if days_to_expiry < 1:
            raise VIXEngineError(
                f"days_to_expiry must be >= 1, got {days_to_expiry}. VIX options are "
                "European and settle to the opening SOQ, so there is no intraday "
                "expiry-day value for this model to price."
            )

        t = days_to_expiry / DAYS_PER_YEAR
        vol_sqrt_t = implied_vol * math.sqrt(t)
        d1 = (math.log(f1_futures_price / strike) + 0.5 * implied_vol**2 * t) / vol_sqrt_t
        d2 = d1 - vol_sqrt_t
        return math.exp(-risk_free_rate * t) * (
            f1_futures_price * _norm_cdf(d1) - strike * _norm_cdf(d2)
        )

    def price_vix_call_spread(
        self,
        f1_futures_price: float,
        strike_lower: float,
        strike_upper: float,
        days_to_expiry: int,
        implied_vol_lower: Optional[float] = None,
        implied_vol_upper: Optional[float] = None,
        risk_free_rate: float = 0.0,
        net_debit: Optional[float] = None,
    ) -> VIXCallSpreadQuote:
        """
        Prices a 1x1 long vertical VIX call spread (buy ``strike_lower``, sell
        ``strike_upper``) off the front-month future.

        Supply either the two per-strike implied volatilities, or an observed
        ``net_debit`` in index points taken from the market. There is no fabricated
        default: a naive implementation assumed the debit was 25% of the
        spread width -- a number with no source -- and then reported the **gross**
        width as max profit, so every spread was advertised as a 4:1 payoff by
        construction, at a $1,000 multiplier that belongs to futures rather than
        options.

        Max profit is ``(width - debit) x $100``: the debit is already spent and
        cannot also be won. Max loss is ``debit x $100``. Breakeven at settlement is
        ``strike_lower + debit``, evaluated against the SOQ.

        :raises VIXEngineError: on inverted strikes, on a debit outside
            ``(0, width * e^{-rT})`` -- outside that band the quote admits an
            arbitrage and the inputs are inconsistent -- or if neither the IV pair
            nor ``net_debit`` is supplied.
        """
        f1_futures_price = _require_positive("f1_futures_price", f1_futures_price)
        strike_lower = _require_positive("strike_lower", strike_lower)
        strike_upper = _require_positive("strike_upper", strike_upper)
        if strike_upper <= strike_lower:
            raise VIXEngineError(
                f"Upper strike ({strike_upper}) must be greater than lower strike "
                f"({strike_lower})."
            )
        days_to_expiry = int(days_to_expiry)
        if days_to_expiry < 1:
            raise VIXEngineError(f"days_to_expiry must be >= 1, got {days_to_expiry}.")
        risk_free_rate = _require_rate("risk_free_rate", risk_free_rate)

        spread_width = strike_upper - strike_lower

        if net_debit is None:
            if implied_vol_lower is None or implied_vol_upper is None:
                raise VIXEngineError(
                    "Supply either both per-strike implied volatilities "
                    "(implied_vol_lower, implied_vol_upper) or an observed net_debit. "
                    "An option spread cannot be priced without one of the two."
                )
            long_leg = self.black76_call(
                f1_futures_price, strike_lower, days_to_expiry,
                implied_vol_lower, risk_free_rate,
            )
            short_leg = self.black76_call(
                f1_futures_price, strike_upper, days_to_expiry,
                implied_vol_upper, risk_free_rate,
            )
            net_debit = long_leg - short_leg
        else:
            net_debit = _require_finite("net_debit", net_debit)

        discounted_width = spread_width * math.exp(
            -risk_free_rate * days_to_expiry / DAYS_PER_YEAR
        )
        if not 0.0 < net_debit < discounted_width:
            raise VIXEngineError(
                f"Net debit {net_debit:.4f} is outside (0, {discounted_width:.4f}) for a "
                f"{spread_width:.2f}-point spread. A long call spread cannot cost nothing "
                "or more than its discounted width without admitting an arbitrage; check "
                "the strike order and the per-strike implied volatilities."
            )

        max_profit_usd = (spread_width - net_debit) * VIX_OPTIONS_MULTIPLIER
        max_loss_usd = net_debit * VIX_OPTIONS_MULTIPLIER
        breakeven = strike_lower + net_debit

        logger.info(
            "VIX call spread %.2f/%.2f @F1=%.2f %dd: debit=%.4f pts, max_profit=$%.2f, "
            "max_loss=$%.2f, breakeven SOQ=%.4f",
            strike_lower, strike_upper, f1_futures_price, days_to_expiry,
            net_debit, max_profit_usd, max_loss_usd, breakeven,
        )

        return VIXCallSpreadQuote(
            strike_lower=strike_lower,
            strike_upper=strike_upper,
            f1_futures_price=f1_futures_price,
            days_to_expiry=days_to_expiry,
            net_debit=round(net_debit, 4),
            max_profit_per_contract_usd=round(max_profit_usd, 2),
            max_loss_per_contract_usd=round(max_loss_usd, 2),
            breakeven_vix=round(breakeven, 4),
        )

    # ------------------------------------------------------------------ #
    # Sizing                                                             #
    # ------------------------------------------------------------------ #

    def generate_strategy_signal(
        self,
        term_struct: VIXTermStructure,
        portfolio_equity_usd: float,
        spread_quote: Optional[VIXCallSpreadQuote] = None,
    ) -> VIXStrategySignal:
        """
        Sizes the strategy implied by the term-structure state.

        CONTANGO sizes a short F1 position against a **notional** budget at the
        $1,000 futures multiplier. BACKWARDATION sizes a long call spread against a
        **premium** budget at the $100 options multiplier, and therefore needs
        ``spread_quote``; without one it returns zero contracts and
        ``POSITION_PENDING_SPREAD_QUOTE`` rather than inventing a premium.

        Contract counts are floored. A budget that cannot fund one contract returns
        zero, not one: an earlier ``max(1, ...)`` floor issued a one-lot short VX
        to any portfolio, so a $50,000 account nominally allocating 5% ($2,500)
        received $16,000 of notional at F1=16 -- a 6.4x breach of its own stated
        limit, in the one strategy whose loss is unbounded.

        :param spread_quote: Required for the BACKWARDATION branch, ignored
            otherwise.
        """
        portfolio_equity_usd = _require_positive(
            "portfolio_equity_usd", portfolio_equity_usd
        )
        f1_price = term_struct.f1_contract.price

        if term_struct.state == TermStructureState.CONTANGO:
            return self._size_short_vol(term_struct, portfolio_equity_usd, f1_price)

        if term_struct.state == TermStructureState.BACKWARDATION:
            return self._size_tail_hedge(term_struct, portfolio_equity_usd, spread_quote)

        return VIXStrategySignal(
            strategy_type=VIXStrategyType.NEUTRAL,
            recommended_position="CASH",
            target_contracts=0,
            daily_roll_decay_usd=0.0,
            tail_hedge_protection_usd=0.0,
            rationale=(
                f"Flat VIX term structure (slope {term_struct.slope_pct:.2f}% inside the "
                f"[{self.backwardation_threshold_pct:.2f}%, "
                f"{self.contango_threshold_pct:.2f}%] dead band). No carry edge and no "
                "stress signal."
            ),
        )

    def _size_short_vol(
        self,
        term_struct: VIXTermStructure,
        portfolio_equity_usd: float,
        f1_price: float,
    ) -> VIXStrategySignal:
        """Sizes a short front-month VX position against the notional budget."""
        notional_budget = portfolio_equity_usd * self.short_vol_notional_budget_pct / 100.0
        contract_notional = f1_price * VIX_FUTURES_MULTIPLIER
        contracts = int(notional_budget // contract_notional)

        if contracts < 1:
            logger.warning(
                "Contango signal not sized: notional budget $%.2f is below one VX "
                "contract notional $%.2f.", notional_budget, contract_notional,
            )
            return VIXStrategySignal(
                strategy_type=VIXStrategyType.ROLL_YIELD_HARVEST,
                recommended_position=POSITION_NONE_INSUFFICIENT_CAPITAL,
                target_contracts=0,
                daily_roll_decay_usd=0.0,
                tail_hedge_protection_usd=0.0,
                rationale=(
                    f"Contango ({term_struct.slope_pct:.2f}%), but the "
                    f"{self.short_vol_notional_budget_pct:.2f}% notional budget "
                    f"(${notional_budget:,.2f}) cannot fund one VX contract "
                    f"(${contract_notional:,.2f} at F1={f1_price:.2f}). Not rounded up: a "
                    "one-lot minimum would breach the stated exposure limit on an "
                    "unbounded-loss position."
                ),
            )

        notional_exposure = contracts * contract_notional
        # Recomputed rather than read from term_struct.front_basis: that field has a
        # 0.0 dataclass default, so a hand-built VIXTermStructure would silently
        # report zero carry instead of failing.
        front_basis = f1_price - term_struct.spot_vix
        daily_decay_pts = front_basis / term_struct.f1_contract.days_to_expiry
        daily_roll_decay_usd = daily_decay_pts * VIX_FUTURES_MULTIPLIER * contracts

        stop_price = f1_price * (1.0 + self.stop_loss_futures_move_pct / 100.0)
        loss_at_stop = (stop_price - f1_price) * VIX_FUTURES_MULTIPLIER * contracts

        return VIXStrategySignal(
            strategy_type=VIXStrategyType.ROLL_YIELD_HARVEST,
            recommended_position="SHORT_F1_VIX_FUTURE",
            target_contracts=contracts,
            daily_roll_decay_usd=round(daily_roll_decay_usd, 2),
            tail_hedge_protection_usd=0.0,
            rationale=(
                f"Contango ({term_struct.slope_f2_minus_f1:.2f} pts, "
                f"{term_struct.slope_pct:.2f}%). Short {contracts} F1 contract(s) to "
                f"harvest a {front_basis:.2f}-point front basis over "
                f"{term_struct.f1_contract.days_to_expiry} days, assuming spot VIX is "
                "unchanged at settlement. Loss is unbounded above; the stop is mandatory."
            ),
            notional_exposure_usd=round(notional_exposure, 2),
            stop_loss_trigger_price=round(stop_price, 4),
            loss_at_stop_usd=round(loss_at_stop, 2),
        )

    def _size_tail_hedge(
        self,
        term_struct: VIXTermStructure,
        portfolio_equity_usd: float,
        spread_quote: Optional[VIXCallSpreadQuote],
    ) -> VIXStrategySignal:
        """Sizes a long call spread against the premium budget, not notional."""
        if spread_quote is None:
            logger.warning(
                "Backwardation signal not sized: no priced call spread supplied."
            )
            return VIXStrategySignal(
                strategy_type=VIXStrategyType.TAIL_RISK_HEDGE,
                recommended_position=POSITION_PENDING_SPREAD_QUOTE,
                target_contracts=0,
                daily_roll_decay_usd=0.0,
                tail_hedge_protection_usd=0.0,
                rationale=(
                    f"Backwardation ({term_struct.slope_pct:.2f}%) calls for a long call "
                    "spread, but no priced spread was supplied. Premium is the entire "
                    "loss bound of a debit spread, so the position cannot be sized "
                    "without it. Call price_vix_call_spread() first."
                ),
            )

        premium_budget = (
            portfolio_equity_usd * self.tail_hedge_premium_budget_pct / 100.0
        )
        # VIXCallSpreadQuote is a plain dataclass, so a caller can build one by hand
        # without going through the pricer's arbitrage checks. A zero premium here
        # would divide by zero; a negative one would produce a negative count.
        premium_per_contract = _require_positive(
            "spread_quote.max_loss_per_contract_usd",
            spread_quote.max_loss_per_contract_usd,
        )
        _require_finite(
            "spread_quote.max_profit_per_contract_usd",
            spread_quote.max_profit_per_contract_usd,
        )
        contracts = int(premium_budget // premium_per_contract)

        if contracts < 1:
            logger.warning(
                "Backwardation signal not sized: premium budget $%.2f is below one "
                "spread premium $%.2f.", premium_budget, premium_per_contract,
            )
            return VIXStrategySignal(
                strategy_type=VIXStrategyType.TAIL_RISK_HEDGE,
                recommended_position=POSITION_NONE_INSUFFICIENT_CAPITAL,
                target_contracts=0,
                daily_roll_decay_usd=0.0,
                tail_hedge_protection_usd=0.0,
                rationale=(
                    f"Backwardation ({term_struct.slope_pct:.2f}%), but the "
                    f"{self.tail_hedge_premium_budget_pct:.2f}% premium budget "
                    f"(${premium_budget:,.2f}) cannot fund one "
                    f"{spread_quote.strike_lower:.0f}/{spread_quote.strike_upper:.0f} "
                    f"spread (${premium_per_contract:,.2f}). Widen the budget or choose a "
                    "cheaper structure."
                ),
            )

        premium_outlay = contracts * premium_per_contract
        protection_usd = contracts * spread_quote.max_profit_per_contract_usd

        return VIXStrategySignal(
            strategy_type=VIXStrategyType.TAIL_RISK_HEDGE,
            recommended_position="LONG_VIX_CALL_SPREAD",
            target_contracts=contracts,
            daily_roll_decay_usd=0.0,
            tail_hedge_protection_usd=round(protection_usd, 2),
            rationale=(
                f"Backwardation ({term_struct.slope_f2_minus_f1:.2f} pts, "
                f"{term_struct.slope_pct:.2f}%). Long {contracts} "
                f"{spread_quote.strike_lower:.0f}/{spread_quote.strike_upper:.0f} call "
                f"spread(s) for ${premium_outlay:,.2f} premium; maximum payoff "
                f"${protection_usd:,.2f} net of premium, and only if the settlement SOQ "
                f"is at or above {spread_quote.strike_upper:.2f}. Below "
                f"{spread_quote.breakeven_vix:.2f} the premium is a total loss."
            ),
            premium_outlay_usd=round(premium_outlay, 2),
        )
