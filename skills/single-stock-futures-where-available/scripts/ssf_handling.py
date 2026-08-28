"""Single Stock Futures (SSF) no-arbitrage band, arbitrage screening and margin audit.

This module answers one question: *given a spot price, a listed single stock future,
a dividend schedule and a borrow cost, is the future priced outside the range that
cash-and-carry arbitrage can defend?*

Four things about that question are easy to get wrong, and this module is built
around them.

**There is no single fair value once the stock has a borrow cost.** The classic
textbook forward ``F = (S - PV(D)) * exp(r * T)`` assumes the stock can be borrowed and
lent freely at the risk-free rate. It cannot. The two arbitrages have *different*
carry, so the no-arbitrage region is a **band**, not a point:

* **Cash-and-carry** (buy spot, sell the future) funds the stock at ``r`` and earns
  whatever the shares can *reliably* be lent out for, ``lending_income_rate_annual``.
  Its ceiling is ``(S - PV(D)) * exp((r - lending_income) * T)``. The default lending
  income is **0.0**, because a lending fee you have not contracted is not income.
* **Reverse cash-and-carry** (short spot, buy the future) must borrow the shares and
  *pay* ``short_borrow_rate_annual``. Its floor is
  ``(S - PV(D)) * exp((r - short_borrow) * T)``.

A prior revision of this module computed ``exp((r - short_borrow) * T)`` and used the
result as *the* fair value for both directions. That applies the short-seller's borrow
fee to the long side's ceiling, pushing the ceiling down and manufacturing
``CASH_AND_CARRY`` signals on exactly the names where a discount to the naive forward is
*rational* — hard-to-borrow stocks, whose futures trade cheap precisely because the
borrow is expensive. At the shipped 0.5% borrow default the bias is small; at a 20%
borrow fee on a squeezed name it is the whole signal.

**Nothing here is a published exchange or regulatory formula.** The ``0.3%`` arbitrage
threshold, the day-count convention and the shipped margin percentages are engineering
choices or jurisdiction-specific statutory minimums, not universal rules. See
``references/standards.md`` for what each venue actually publishes.

**Ex-dividend contract adjustment is gated, not automatic.** Under SEBI/NSE rules a
dividend below **2%** of the underlying's market value is an *ordinary* dividend and the
derivative contract is **not** adjusted; the drop is left to be absorbed by the market
price. Only an extraordinary dividend (>= 2%) adjusts the futures base price. A prior
revision applied ``previous_settlement - dividend`` unconditionally, which restates a
base price the exchange never moved.

**Settlement type is a trading constraint, not a label.** NSE stock futures have been
compulsorily *physically* settled since the October 2019 expiry, so an unclosed
cash-and-carry leg becomes a delivery obligation for the full notional. The engine
reports this rather than storing ``settlement_type`` and ignoring it.

Requires Python 3.7+ (dataclasses).
"""

import logging
import math
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Sequence

logger = logging.getLogger(__name__)

# --- Model identifiers -----------------------------------------------------
# Recorded on every result so a consumer can tell which pricing model produced the
# numbers, and so a future model cannot silently replace this one in place.
PRICING_MODEL_CARRY_BAND = "COST_OF_CARRY_BORROW_BAND"

# --- Day count -------------------------------------------------------------
# ACT/365 fixed. This is a convention choice, not a published requirement: NSE, Eurex
# and CME each publish contract terms, not a day-count basis for a trader's own
# theoretical value. Override via `day_count_basis` if your funding curve is ACT/360.
DAY_COUNT_ACT_365 = 365.0
DAY_COUNT_ACT_360 = 360.0

# --- Input plausibility bounds --------------------------------------------
# A rate outside this range is a unit error, not a rate: passing `6` for 6% instead of
# `0.06` is the classic one, and it silently inflates every forward by e^(6T).
MAX_PLAUSIBLE_RATE = 5.0
MIN_PLAUSIBLE_RATE = -1.0

# A contract more than 10 years out is not a listed single stock future; it is almost
# always a timestamp subtracted in the wrong direction or in the wrong unit.
MAX_PLAUSIBLE_DAYS_TO_EXPIRY = 3_650

# --- Statutory margin minimums (jurisdiction-specific; see references/standards.md) ---
# US security futures: 15% of current market value for an unhedged position, per the
# joint CFTC/SEC amendments to CFTC Rule 41.45 and SEC Rule 403, effective 24 Dec 2020
# (lowered from 20%). This is a *minimum*; the FCM/broker may require more.
US_SECURITY_FUTURES_MIN_MARGIN_PCT = 0.15
# US equity purchased on margin: 50% initial, Regulation T, 12 CFR 220.12(a).
US_REG_T_INITIAL_MARGIN_PCT = 0.50

# Venues whose SSF margin is a flat statutory percentage of notional, so the engine may
# supply a default. Everything else uses scenario/portfolio margining (NSE Clearing
# SPAN + a 3.5% Extreme Loss Margin for stock futures; Eurex Clearing Prisma), which no
# flat percentage reproduces — for those the caller must pass the venue's own figure.
FLAT_MARGIN_VENUES = {
    "CME": (US_SECURITY_FUTURES_MIN_MARGIN_PCT, US_REG_T_INITIAL_MARGIN_PCT),
}

# SEBI/NSE: dividends below this share of the underlying's market value are "ordinary"
# and trigger no contract adjustment. Threshold lowered from 5% to 2% by SEBI circular
# SEBI/HO/MRD2/MRD2_DCAP/P/CIR/2022/90 (28 June 2022).
SEBI_EXTRAORDINARY_DIVIDEND_THRESHOLD_PCT = 2.0


class SSFInputError(ValueError):
    """Raised when market data or contract inputs cannot support a meaningful valuation.

    Subclasses ``ValueError`` so callers already catching ``ValueError`` keep working.
    """


class SSFConfigError(ValueError):
    """Raised when the engine is not configured well enough to produce an honest number."""


class SSFSettlementType(str, Enum):
    CASH_SETTLED = "CASH_SETTLED"            # e.g. CME single stock futures (2026 relaunch)
    PHYSICAL_DELIVERY = "PHYSICAL_DELIVERY"  # e.g. NSE stock futures since the Oct 2019 expiry


class SSFArbitrageSignal(str, Enum):
    CASH_AND_CARRY = "CASH_AND_CARRY"                  # Buy spot, sell SSF (above the band)
    REVERSE_CASH_AND_CARRY = "REVERSE_CASH_AND_CARRY"  # Short spot, buy SSF (below the band)
    NEUTRAL = "NEUTRAL"                                # Inside the band: no arbitrage


@dataclass
class SSFContractSpec:
    """Terms of one listed single stock future.

    ``risk_free_rate_annual`` is the continuously compounded funding rate for the
    position's currency. ``short_borrow_rate_annual`` is the annualised fee a
    *short seller of the underlying* pays to borrow it; it widens the band downward.
    ``lending_income_rate_annual`` is the fee a *holder* of the underlying can
    contractually earn lending the shares out; it narrows the band from above and
    defaults to 0.0 because uncontracted lending income is not income.
    """

    symbol: str                          # e.g. 'RELIANCE.NS-FUT', 'SIE.DE-FUT', 'AAPLF'
    underlying_spot_symbol: str          # e.g. 'RELIANCE.NS', 'SIE.DE', 'AAPL'
    exchange: str                        # 'NSE', 'EUREX', 'EURONEXT', 'CME'
    lot_size: int                        # Contract multiplier in shares
    days_to_expiry: int
    settlement_type: SSFSettlementType
    risk_free_rate_annual: float = 0.05
    short_borrow_rate_annual: float = 0.005   # Fee the short pays (0.5%)
    lending_income_rate_annual: float = 0.0   # Fee the long can contractually earn
    currency: str = "UNSPECIFIED"             # Reporting currency of prices and margins
    day_count_basis: float = DAY_COUNT_ACT_365


@dataclass
class DividendEvent:
    ex_date_days: int                    # Days from valuation date until the ex-dividend date
    amount_per_share: float              # Cash amount per share, in the contract currency


@dataclass
class SSFFairValueResult:
    """Valuation and screening output. All monetary fields are in ``spec.currency``."""

    symbol: str
    exchange: str
    underlying_spot_price: float
    market_ssf_price: float
    dividend_pv: float
    # (S - PV(D)) * exp(r * T): the zero-borrow-cost reference. Equals both band edges
    # when borrow fee and lending income are zero.
    theoretical_fair_value: float
    no_arbitrage_lower_bound: float      # Reverse cash-and-carry floor (short pays borrow)
    no_arbitrage_upper_bound: float      # Cash-and-carry ceiling (long earns lending income)
    mispricing_amount: float             # market_ssf_price - theoretical_fair_value
    mispricing_pct: float                # ... as a % of theoretical_fair_value
    # Signed gross edge beyond the *violated* band edge, before execution costs.
    # 0.0 when NEUTRAL. This, not `mispricing_pct`, is the tradeable number.
    gross_edge_pct: float
    arbitrage_signal: SSFArbitrageSignal
    initial_margin_ssf: float
    initial_margin_spot: float
    margin_basis: str                    # Where the margin percentages came from
    leverage_multiplier: float
    physical_delivery_at_expiry: bool
    pricing_model: str
    audit_notes: str
    excluded_dividends: int = 0          # Dividends dropped as outside (0, T]


@dataclass
class ExDividendAdjustmentResult:
    """Outcome of an exchange contract adjustment test for a dividend.

    ``is_adjusted`` is the load-bearing field: when it is ``False`` the exchange leaves
    the contract alone and ``adjusted_base_price`` equals the input price.
    """

    previous_settlement_price: float
    dividend_amount: float
    dividend_pct_of_market_price: Optional[float]
    is_adjusted: bool
    adjusted_base_price: float
    rationale: str


def _require_finite_number(value: object, name: str) -> float:
    """Reject NaN, Inf, booleans and non-numerics before they reach the arithmetic.

    NaN is the dangerous one: ``max(0.01, float('nan'))`` returns ``0.01`` and
    ``nan >= threshold`` is ``False``, so an unguarded NaN spot price produced a
    confident, wrong arbitrage signal rather than an error.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SSFInputError(f"{name} must be a real number, got {value!r}")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise SSFInputError(f"{name} must be finite, got {value!r}")
    return numeric


def _require_positive_price(value: object, name: str) -> float:
    price = _require_finite_number(value, name)
    if price <= 0.0:
        raise SSFInputError(f"{name} must be strictly positive, got {price}")
    return price


def _require_rate(value: object, name: str) -> float:
    rate = _require_finite_number(value, name)
    if not (MIN_PLAUSIBLE_RATE < rate < MAX_PLAUSIBLE_RATE):
        raise SSFInputError(
            f"{name}={rate} is outside the plausible range "
            f"({MIN_PLAUSIBLE_RATE}, {MAX_PLAUSIBLE_RATE}). Rates are decimals: "
            f"pass 0.06 for 6%, not 6."
        )
    return rate


def _validate_spec(spec: SSFContractSpec) -> None:
    if not isinstance(spec.lot_size, int) or isinstance(spec.lot_size, bool) or spec.lot_size <= 0:
        raise SSFInputError(f"lot_size must be a positive int, got {spec.lot_size!r}")
    if (
        not isinstance(spec.days_to_expiry, int)
        or isinstance(spec.days_to_expiry, bool)
        or spec.days_to_expiry < 0
    ):
        raise SSFInputError(
            f"days_to_expiry must be a non-negative int, got {spec.days_to_expiry!r}. "
            "An expired contract has no forward value to compute."
        )
    if spec.days_to_expiry > MAX_PLAUSIBLE_DAYS_TO_EXPIRY:
        raise SSFInputError(
            f"days_to_expiry={spec.days_to_expiry} exceeds "
            f"{MAX_PLAUSIBLE_DAYS_TO_EXPIRY}; no listed single stock future runs that long."
        )
    if not isinstance(spec.settlement_type, SSFSettlementType):
        raise SSFInputError(
            f"settlement_type must be an SSFSettlementType, got {spec.settlement_type!r}"
        )
    _require_rate(spec.risk_free_rate_annual, "risk_free_rate_annual")
    borrow = _require_rate(spec.short_borrow_rate_annual, "short_borrow_rate_annual")
    lending = _require_rate(spec.lending_income_rate_annual, "lending_income_rate_annual")
    if borrow < 0.0:
        raise SSFInputError(f"short_borrow_rate_annual must be >= 0, got {borrow}")
    if lending < 0.0:
        raise SSFInputError(f"lending_income_rate_annual must be >= 0, got {lending}")
    if lending > borrow:
        # The lender receives at most what the borrower pays. A lending income above the
        # borrow fee inverts the band (lower bound above upper bound) and would make
        # every price simultaneously too high and too low.
        raise SSFInputError(
            f"lending_income_rate_annual ({lending}) cannot exceed "
            f"short_borrow_rate_annual ({borrow}); the share lender cannot earn more "
            "than the borrower pays."
        )
    if spec.day_count_basis not in (DAY_COUNT_ACT_365, DAY_COUNT_ACT_360):
        raise SSFInputError(
            f"day_count_basis must be {DAY_COUNT_ACT_365} or {DAY_COUNT_ACT_360}, "
            f"got {spec.day_count_basis!r}"
        )


def _resolve_margin_percentages(
    spec: SSFContractSpec,
    ssf_margin_pct: Optional[float],
    spot_margin_pct: Optional[float],
) -> "tuple[float, float, str]":
    """Resolve the two margin percentages, or refuse to invent them.

    A flat percentage of notional is only a faithful model of a venue that publishes
    one. NSE Clearing (SPAN + 3.5% ELM) and Eurex Clearing (Prisma) both margin
    scenario-wise on the whole portfolio, so no constant reproduces their number and a
    leverage figure derived from a guess would be quoted downstream as if measured.
    """
    exchange = (spec.exchange or "").strip().upper()
    if ssf_margin_pct is None or spot_margin_pct is None:
        defaults = FLAT_MARGIN_VENUES.get(exchange)
        if defaults is None:
            raise SSFConfigError(
                f"No default margin percentages for exchange {spec.exchange!r}. "
                "NSE (SPAN + 3.5% ELM) and Eurex (Prisma) margin scenario-wise on the "
                "portfolio, so a flat percentage of notional is not their requirement. "
                "Pass ssf_margin_pct and spot_margin_pct measured from your own "
                "clearing member's figures."
            )
        default_ssf, default_spot = defaults
        ssf_margin_pct = default_ssf if ssf_margin_pct is None else ssf_margin_pct
        spot_margin_pct = default_spot if spot_margin_pct is None else spot_margin_pct
        basis = (
            f"{exchange}: statutory US minimums - security futures "
            f"{default_ssf:.0%} (CFTC Rule 41.45 / SEC Rule 403, eff. 2020-12-24), "
            f"spot equity {default_spot:.0%} (Reg T, 12 CFR 220.12). Minimums only; "
            "your broker may require more."
        )
    else:
        basis = f"{spec.exchange}: caller-supplied margin percentages."

    ssf_pct = _require_finite_number(ssf_margin_pct, "ssf_margin_pct")
    spot_pct = _require_finite_number(spot_margin_pct, "spot_margin_pct")
    if not (0.0 < ssf_pct <= 1.0):
        raise SSFConfigError(f"ssf_margin_pct must be in (0, 1], got {ssf_pct}")
    if not (0.0 < spot_pct <= 1.0):
        raise SSFConfigError(f"spot_margin_pct must be in (0, 1], got {spot_pct}")
    return ssf_pct, spot_pct, basis


class SingleStockFuturesEngine:
    """Prices single stock futures as a no-arbitrage *band* and screens for arbitrage.

    ``arbitrage_cost_threshold_pct`` is the round-trip execution cost, in percent of the
    violated band edge, that a mispricing must clear before it is called actionable. It
    is an **operator input, not a published threshold**: it has to cover commissions,
    exchange and clearing fees, bid-offer on both legs, market impact, funding spread,
    transaction taxes (STT and stamp duty in India), and the cost of carrying margin on
    both legs to expiry. The shipped 0.3% is a placeholder that lets the module run.
    """

    def __init__(self, arbitrage_cost_threshold_pct: float = 0.3):
        threshold = _require_finite_number(
            arbitrage_cost_threshold_pct, "arbitrage_cost_threshold_pct"
        )
        if threshold < 0.0:
            raise SSFConfigError(
                f"arbitrage_cost_threshold_pct must be >= 0, got {threshold}. A negative "
                "threshold would signal arbitrage on prices inside the no-arbitrage band."
            )
        self.arbitrage_cost_threshold_pct = threshold

    def _dividend_present_value(
        self, spec: SSFContractSpec, dividends: Optional[Sequence[DividendEvent]]
    ) -> "tuple[float, int]":
        """Present-value the cash dividends whose ex-date falls inside (0, T].

        Returns ``(pv, excluded_count)``. Dividends outside the window are excluded and
        logged rather than dropped silently: a schedule quietly ignored because its
        ex-dates were passed in the wrong unit produces a fair value that is too high
        with nothing in the output to say so.
        """
        if not dividends:
            return 0.0, 0

        pv = 0.0
        excluded = 0
        for index, div in enumerate(dividends):
            if not isinstance(div, DividendEvent):
                raise SSFInputError(f"dividends[{index}] must be a DividendEvent, got {div!r}")
            amount = _require_finite_number(
                div.amount_per_share, f"dividends[{index}].amount_per_share"
            )
            if amount < 0.0:
                raise SSFInputError(
                    f"dividends[{index}].amount_per_share must be >= 0, got {amount}. "
                    "A negative cash dividend is not a corporate action."
                )
            if not isinstance(div.ex_date_days, int) or isinstance(div.ex_date_days, bool):
                raise SSFInputError(
                    f"dividends[{index}].ex_date_days must be an int, got {div.ex_date_days!r}"
                )
            if not (0 <= div.ex_date_days <= spec.days_to_expiry):
                excluded += 1
                logger.warning(
                    "Excluding dividend %d (ex_date_days=%d, amount=%s) from PV: outside "
                    "the [0, %d] day window to expiry.",
                    index,
                    div.ex_date_days,
                    amount,
                    spec.days_to_expiry,
                )
                continue
            if div.ex_date_days == 0:
                logger.warning(
                    "Dividend %d has ex_date_days=0. If the quoted spot is already "
                    "ex-dividend this double-counts the drop; pass only dividends the "
                    "spot still carries.",
                    index,
                )
            t_div = div.ex_date_days / spec.day_count_basis
            pv += amount * math.exp(-spec.risk_free_rate_annual * t_div)
        return pv, excluded

    def compute_fair_value_and_arbitrage(
        self,
        spec: SSFContractSpec,
        spot_price: float,
        market_ssf_price: float,
        dividends: Optional[Sequence[DividendEvent]] = None,
        ssf_margin_pct: Optional[float] = None,
        spot_margin_pct: Optional[float] = None,
    ) -> SSFFairValueResult:
        """Price the no-arbitrage band and screen ``market_ssf_price`` against it.

        The band, over ``T = days_to_expiry / day_count_basis`` and
        ``base = spot - PV(dividends)``:

        * ceiling  ``base * exp((r - lending_income) * T)`` — the most a cash-and-carry
          can defend, since the long earns any contracted lending income;
        * floor    ``base * exp((r - short_borrow) * T)`` — the least a reverse
          cash-and-carry can defend, since the short pays the borrow fee.

        ``theoretical_fair_value`` is the zero-borrow-cost reference ``base * exp(r*T)``,
        reported for continuity; it is *not* the trigger. Signals fire on the band edges
        widened by ``arbitrage_cost_threshold_pct``, compared on unrounded values so a
        mispricing of 0.2996% cannot round up into a 0.30% trigger.

        Raises:
            SSFInputError: on non-finite, non-positive or implausible inputs.
            SSFConfigError: when margin percentages are neither supplied nor defensible
                as defaults for ``spec.exchange``.
        """
        _validate_spec(spec)
        spot = _require_positive_price(spot_price, "spot_price")
        market_price = _require_positive_price(market_ssf_price, "market_ssf_price")
        ssf_pct, spot_pct, margin_basis = _resolve_margin_percentages(
            spec, ssf_margin_pct, spot_margin_pct
        )

        t_years = spec.days_to_expiry / spec.day_count_basis
        div_pv, excluded = self._dividend_present_value(spec, dividends)

        base = spot - div_pv
        if base <= 0.0:
            raise SSFInputError(
                f"Present value of dividends ({div_pv:.4f}) is not less than the spot "
                f"price ({spot:.4f}). A dividend stream worth more than the share is a "
                "data error, not a forward with a negative price."
            )

        upper_bound = base * math.exp(
            (spec.risk_free_rate_annual - spec.lending_income_rate_annual) * t_years
        )
        lower_bound = base * math.exp(
            (spec.risk_free_rate_annual - spec.short_borrow_rate_annual) * t_years
        )
        carry_neutral = base * math.exp(spec.risk_free_rate_annual * t_years)

        # Signal determination on unrounded values.
        threshold_fraction = self.arbitrage_cost_threshold_pct / 100.0
        cash_and_carry_trigger = upper_bound * (1.0 + threshold_fraction)
        reverse_trigger = lower_bound * (1.0 - threshold_fraction)

        if market_price >= cash_and_carry_trigger:
            signal = SSFArbitrageSignal.CASH_AND_CARRY
            gross_edge_pct = (market_price / upper_bound - 1.0) * 100.0
        elif market_price <= reverse_trigger:
            signal = SSFArbitrageSignal.REVERSE_CASH_AND_CARRY
            gross_edge_pct = (market_price / lower_bound - 1.0) * 100.0
        else:
            signal = SSFArbitrageSignal.NEUTRAL
            gross_edge_pct = 0.0

        mispricing = market_price - carry_neutral
        mispricing_pct = (mispricing / carry_neutral) * 100.0

        # Margin and leverage. Notional uses the spot price because both legs of the
        # comparison are exposure to the same number of shares.
        notional_value = spec.lot_size * spot
        ssf_margin = notional_value * ssf_pct
        spot_margin = notional_value * spot_pct
        leverage_mult = spot_margin / ssf_margin

        physical = spec.settlement_type is SSFSettlementType.PHYSICAL_DELIVERY

        notes = (
            f"SSF BAND [{spec.symbol} @ {spec.exchange}, {spec.currency}]: "
            f"spot={spot:.4f}, market={market_price:.4f}, PV(div)={div_pv:.4f}, "
            f"band=[{lower_bound:.4f}, {upper_bound:.4f}] "
            f"(carry-neutral ref {carry_neutral:.4f}), signal={signal.value}, "
            f"gross edge={gross_edge_pct:.4f}% vs a {self.arbitrage_cost_threshold_pct}% "
            f"cost threshold, leverage={leverage_mult:.2f}x. {margin_basis}"
        )
        if physical:
            notes += (
                " PHYSICAL DELIVERY: an open leg at expiry becomes a delivery obligation "
                "for the full notional, not a cash difference."
            )
        if signal is SSFArbitrageSignal.REVERSE_CASH_AND_CARRY:
            notes += (
                " REVERSE LEG REQUIRES A LOCATED BORROW: the short spot leg is only "
                "executable if the shares can actually be borrowed at "
                f"{spec.short_borrow_rate_annual:.4f} for the full holding period."
            )
        logger.info(notes)

        return SSFFairValueResult(
            symbol=spec.symbol,
            exchange=spec.exchange,
            underlying_spot_price=spot,
            market_ssf_price=market_price,
            dividend_pv=round(div_pv, 4),
            theoretical_fair_value=round(carry_neutral, 4),
            no_arbitrage_lower_bound=round(lower_bound, 4),
            no_arbitrage_upper_bound=round(upper_bound, 4),
            mispricing_amount=round(mispricing, 4),
            mispricing_pct=round(mispricing_pct, 4),
            gross_edge_pct=round(gross_edge_pct, 4),
            arbitrage_signal=signal,
            initial_margin_ssf=round(ssf_margin, 2),
            initial_margin_spot=round(spot_margin, 2),
            margin_basis=margin_basis,
            leverage_multiplier=round(leverage_mult, 4),
            physical_delivery_at_expiry=physical,
            pricing_model=PRICING_MODEL_CARRY_BAND,
            audit_notes=notes,
            excluded_dividends=excluded,
        )

    def calculate_ex_dividend_price_adjustment(
        self,
        previous_settlement_price: float,
        dividend_amount: float,
        underlying_market_price: Optional[float] = None,
        extraordinary_threshold_pct: float = SEBI_EXTRAORDINARY_DIVIDEND_THRESHOLD_PCT,
    ) -> ExDividendAdjustmentResult:
        """Apply the SEBI/NSE extraordinary-dividend contract adjustment test.

        Under SEBI circular SEBI/HO/MRD2/MRD2_DCAP/P/CIR/2022/90 (28 June 2022), a
        dividend **below** ``extraordinary_threshold_pct`` of the underlying's market
        value is *ordinary* and **no contract adjustment is made**; the price drop is
        left to the market. Only at or above the threshold is the futures base price
        adjusted, by deducting the aggregate dividend from the contract's reference
        rate — which is the contract's own daily mark-to-market settlement price, hence
        ``previous_settlement_price``, not the spot.

        ``underlying_market_price`` is the closing price of the underlying on the day
        before the dividend announcement, which is what the threshold is measured
        against. **It is required**: without it the ordinary/extraordinary test cannot be
        performed, and the previous revision of this method simply assumed every dividend
        was extraordinary.

        This models the Indian rule. Eurex and CME publish their own corporate-action
        methodologies and neither is reproduced here — see ``references/standards.md``.

        Returns:
            ExDividendAdjustmentResult, whose ``is_adjusted`` says whether the exchange
            moves the contract at all. When ``False``, ``adjusted_base_price`` is the
            unchanged input price.
        """
        prev_price = _require_positive_price(
            previous_settlement_price, "previous_settlement_price"
        )
        dividend = _require_finite_number(dividend_amount, "dividend_amount")
        if dividend < 0.0:
            raise SSFInputError(f"dividend_amount must be >= 0, got {dividend}")
        threshold = _require_finite_number(
            extraordinary_threshold_pct, "extraordinary_threshold_pct"
        )
        if threshold < 0.0:
            raise SSFInputError(f"extraordinary_threshold_pct must be >= 0, got {threshold}")

        if underlying_market_price is None:
            raise SSFInputError(
                "underlying_market_price is required to classify the dividend as "
                "ordinary or extraordinary. Pass the underlying's closing price on the "
                "day before the dividend announcement."
            )
        market_price = _require_positive_price(underlying_market_price, "underlying_market_price")

        dividend_pct = (dividend / market_price) * 100.0
        is_adjusted = dividend_pct >= threshold

        if is_adjusted:
            adjusted = prev_price - dividend
            if adjusted <= 0.0:
                raise SSFInputError(
                    f"Adjusted base price would be {adjusted:.4f}: the dividend "
                    f"({dividend}) is not smaller than the previous settlement price "
                    f"({prev_price}). Check the units on both."
                )
            rationale = (
                f"Extraordinary dividend: {dividend_pct:.4f}% of market price "
                f"{market_price} is >= the {threshold}% threshold. Base price adjusted "
                f"to {prev_price} - {dividend} = {adjusted:.4f}."
            )
        else:
            adjusted = prev_price
            rationale = (
                f"Ordinary dividend: {dividend_pct:.4f}% of market price {market_price} "
                f"is below the {threshold}% threshold, so the exchange makes no contract "
                "adjustment. The base price is unchanged and the drop is absorbed by the "
                "market price."
            )

        logger.info("EX-DIVIDEND ADJUSTMENT [%s]", rationale)

        return ExDividendAdjustmentResult(
            previous_settlement_price=prev_price,
            dividend_amount=dividend,
            dividend_pct_of_market_price=round(dividend_pct, 6),
            is_adjusted=is_adjusted,
            adjusted_base_price=round(adjusted, 4),
            rationale=rationale,
        )
