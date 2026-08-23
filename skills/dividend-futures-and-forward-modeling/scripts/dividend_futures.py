"""
dividend-futures-and-forward-modeling: equity forward curve engine for discrete
dividends -- present/future value of a dividend schedule, theoretical forward pricing,
dividend-futures fair value, and cash-and-carry arbitrage detection.

Two distinct dividend measures are computed, and they are NOT interchangeable:

  * Cash-and-carry forward price uses dividends **net of withholding tax**, because the
    arbitrageur holding the physical shares actually receives the net cash.
  * Dividend-futures fair value uses **gross ordinary** dividends, because that is what
    the underlying dividend-point indices accumulate:
      - Eurex EURO STOXX 50 Index Dividend Futures (product ID **FEXD**) settle on
        "the cumulative total of the relevant gross dividends of the constituents".
      - CME S&P 500 Annual Dividend Index futures (**SDA**) accumulate ordinary gross
        dividends of constituents going ex-dividend within the cumulation period, and
        exclude special/extraordinary dividends.

Eligibility is determined by the **ex-dividend date** (you must hold through the ex-date
to receive the dividend); the cash flow is discounted from the **payment date**. These
are different dates and the engine keeps them separate.
"""
import logging
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)


class ArbitrageSignal(str, Enum):
    """Cash-and-carry classification. Inherits `str`, so `== "NO_ARBITRAGE"` still works."""
    NO_ARBITRAGE = "NO_ARBITRAGE"
    ARBITRAGE_SHORT_FORWARD_LONG_SPOT = "ARBITRAGE_SHORT_FORWARD_LONG_SPOT"
    ARBITRAGE_LONG_FORWARD_SHORT_SPOT = "ARBITRAGE_LONG_FORWARD_SHORT_SPOT"


class DividendModelError(ValueError):
    """Raised for a dividend schedule or pricing input that cannot yield a valid forward."""


@dataclass
class DiscreteDividendEvent:
    dividend_id: str
    amount_usd: float                   # GROSS declared amount, per share / in index points
    payment_time_years: float           # cash settlement time, e.g. 0.25 years (3 months)
    withholding_tax_pct: float = 0.0    # e.g. 0.15 for 15% tax; must be in [0, 1)
    # --- default-valued: positional construction unchanged ---
    ex_time_years: Optional[float] = None  # eligibility date; defaults to payment_time_years
    is_special: bool = False               # special/extraordinary: excluded from index accrual

    @property
    def effective_ex_time_years(self) -> float:
        """Ex-date used for eligibility; falls back to the payment date when unspecified."""
        return self.payment_time_years if self.ex_time_years is None else self.ex_time_years


@dataclass
class DividendForwardAuditReport:
    symbol: str
    spot_price: float
    time_to_maturity_years: float
    risk_free_rate_pct: float
    pv_dividends_usd: float             # NET of withholding -- cash-and-carry measure
    fv_dividends_usd: float             # NET of withholding, compounded to T
    theoretical_forward_price: float
    fair_value_dividend_future_points: float  # GROSS ordinary accrual -- futures measure
    market_forward_price: float
    mispricing_spread_usd: float
    arbitrage_opportunity: str
    estimated_gross_profit_usd: float   # |spread| BEFORE costs (see estimated_net_profit_usd)
    # --- default-valued: positional construction unchanged ---
    estimated_net_profit_usd: float = 0.0     # |spread| minus the applicable cost threshold
    applied_cost_threshold_usd: float = 0.0   # direction-specific threshold actually used
    excluded_dividend_ids: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


def _safe_exp(exponent: float, context: str) -> float:
    """
    exp() that reports a range error as a modelling error rather than crashing.

    The usual cause is a unit mistake -- passing `5` for a 5% rate instead of `0.05`,
    which turns a 20-year discount factor into exp(100).
    """
    try:
        return math.exp(exponent)
    except OverflowError as exc:
        raise DividendModelError(
            f"{context}: exp({exponent}) overflowed. Check that risk_free_rate is a "
            f"decimal (0.05 for 5%), not a percentage, and that maturity_years is in years."
        ) from exc


def _require_finite(value: float, name: str) -> float:
    """Rejects NaN/Inf, which would otherwise propagate into a confident wrong verdict."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise DividendModelError(f"{name} must be a real number, got {value!r}.")
    if not math.isfinite(value):
        raise DividendModelError(
            f"{name} must be finite, got {value!r}. A NaN or Inf here would silently "
            f"produce a 'NO_ARBITRAGE' verdict, because every NaN comparison is False."
        )
    return float(value)


class DividendForwardModelingEngine:
    """
    Equity forward curve engine for discrete dividends, dividend-futures fair value, and
    cash-and-carry arbitrage detection.

    All prices, dividends, spreads, and profits are **per unit** of the underlying (per
    share, or per index point) in the underlying's own currency. They are NOT scaled by a
    contract multiplier -- apply the venue's multiplier downstream (Eurex FEXD is EUR 100
    per point; CME SDA is USD 250 per point).

    Args:
        arbitrage_cost_threshold_usd: round-trip cost for the *forward* cash-and-carry
            (short forward / long spot). The spread must strictly exceed it to signal.
        reverse_arbitrage_cost_threshold_usd: round-trip cost for the *reverse* trade
            (long forward / short spot). Defaults to the forward threshold, but the reverse
            leg is genuinely more expensive in practice: it requires borrowing the stock
            (borrow fee, recall risk, hard-to-borrow names) and the short pays *gross*
            manufactured dividends while a long holder receives them net of withholding.
            Set this explicitly rather than relying on the symmetric default.
    """

    def __init__(
        self,
        arbitrage_cost_threshold_usd: float = 0.50,
        reverse_arbitrage_cost_threshold_usd: Optional[float] = None,
    ) -> None:
        self.arbitrage_cost_threshold_usd = _require_finite(
            arbitrage_cost_threshold_usd, "arbitrage_cost_threshold_usd"
        )
        if self.arbitrage_cost_threshold_usd < 0.0:
            raise DividendModelError("arbitrage_cost_threshold_usd must be non-negative.")

        if reverse_arbitrage_cost_threshold_usd is None:
            self.reverse_arbitrage_cost_threshold_usd = self.arbitrage_cost_threshold_usd
        else:
            self.reverse_arbitrage_cost_threshold_usd = _require_finite(
                reverse_arbitrage_cost_threshold_usd, "reverse_arbitrage_cost_threshold_usd"
            )
            if self.reverse_arbitrage_cost_threshold_usd < 0.0:
                raise DividendModelError(
                    "reverse_arbitrage_cost_threshold_usd must be non-negative."
                )

    def _validate_dividend(self, d: DiscreteDividendEvent) -> None:
        _require_finite(d.amount_usd, f"dividend '{d.dividend_id}' amount_usd")
        _require_finite(d.payment_time_years, f"dividend '{d.dividend_id}' payment_time_years")
        _require_finite(
            d.effective_ex_time_years, f"dividend '{d.dividend_id}' ex_time_years"
        )
        _require_finite(
            d.withholding_tax_pct, f"dividend '{d.dividend_id}' withholding_tax_pct"
        )
        if d.amount_usd < 0.0:
            raise DividendModelError(
                f"dividend '{d.dividend_id}': amount_usd must be non-negative, got {d.amount_usd}."
            )
        if not (0.0 <= d.withholding_tax_pct < 1.0):
            raise DividendModelError(
                f"dividend '{d.dividend_id}': withholding_tax_pct must be in [0, 1), got "
                f"{d.withholding_tax_pct}. A value outside this range flips the sign of the "
                f"net dividend or inflates it above the declared gross amount."
            )
        if d.effective_ex_time_years > d.payment_time_years:
            raise DividendModelError(
                f"dividend '{d.dividend_id}': ex_time_years ({d.effective_ex_time_years}) is "
                f"after payment_time_years ({d.payment_time_years}); a dividend cannot pay "
                f"before it goes ex."
            )

    def calculate_dividend_present_value(
        self,
        dividends: List[DiscreteDividendEvent],
        risk_free_rate: float,
        maturity_years: float,
        accrual_start_years: float = 0.0,
    ) -> Tuple[float, float, float]:
        """
        Returns (PV_net, FV_net, gross_ordinary_accrual) for the accrual window
        ``accrual_start_years < ex_date <= maturity_years``.

        PV(D) = sum( D_i * (1 - tax_i) * exp(-r * payment_t_i) )
        FV(D) = sum( D_i * (1 - tax_i) * exp( r * (T - payment_t_i)) )

        Eligibility is filtered on the **ex-date** (you must hold through it to receive the
        dividend); the cash flow is discounted from the **payment date**, which may fall
        after T for a dividend that goes ex just before maturity.

        The third element is the **gross ordinary** accrual -- gross of withholding tax and
        excluding special dividends -- because that is what dividend-point indices settle
        on. It is deliberately not the same measure as PV/FV.
        """
        if dividends is None:
            raise DividendModelError(
                "dividends must be a list of DiscreteDividendEvent; pass [] for a "
                "non-dividend-paying underlying, not None."
            )

        pv_div = 0.0
        fv_div = 0.0
        gross_ordinary_accrual = 0.0

        for d in dividends:
            self._validate_dividend(d)
            ex_t = d.effective_ex_time_years
            if not (accrual_start_years < ex_t <= maturity_years):
                continue

            net_amount = d.amount_usd * (1.0 - d.withholding_tax_pct)
            pv_div += net_amount * _safe_exp(
                -risk_free_rate * d.payment_time_years, f"PV of dividend '{d.dividend_id}'"
            )
            fv_div += net_amount * _safe_exp(
                risk_free_rate * (maturity_years - d.payment_time_years),
                f"FV of dividend '{d.dividend_id}'",
            )
            if not d.is_special:
                gross_ordinary_accrual += d.amount_usd

        return round(pv_div, 4), round(fv_div, 4), round(gross_ordinary_accrual, 4)

    def calculate_theoretical_forward_price(
        self,
        spot_price: float,
        pv_dividends: float,
        risk_free_rate: float,
        maturity_years: float,
    ) -> float:
        """Theoretical Forward Price F(0, T) = (S_0 - PV(D)) * exp(r * T)."""
        f_price = (spot_price - pv_dividends) * _safe_exp(
            risk_free_rate * maturity_years, "theoretical forward price"
        )
        return round(f_price, 4)

    def audit_forward_arbitrage(
        self,
        symbol: str,
        spot_price: float,
        maturity_years: float,
        risk_free_rate: float,
        dividends: List[DiscreteDividendEvent],
        market_forward_price: float,
        accrual_start_years: float = 0.0,
    ) -> DividendForwardAuditReport:
        """
        Audits a market forward price against the theoretical cash-and-carry forward.

        Dividends whose ex-date lies outside ``(accrual_start_years, maturity_years]`` are
        excluded and listed in ``excluded_dividend_ids``. Already-ex dividends in particular
        must not enter PV(D): including one overstates PV(D), understates the theoretical
        forward, and manufactures a false ARBITRAGE_SHORT_FORWARD_LONG_SPOT signal.
        """
        spot_price = _require_finite(spot_price, "spot_price")
        maturity_years = _require_finite(maturity_years, "maturity_years")
        risk_free_rate = _require_finite(risk_free_rate, "risk_free_rate")
        market_forward_price = _require_finite(market_forward_price, "market_forward_price")
        accrual_start_years = _require_finite(accrual_start_years, "accrual_start_years")

        if spot_price <= 0.0:
            raise DividendModelError(f"spot_price must be positive, got {spot_price}.")
        if maturity_years <= 0.0:
            raise DividendModelError(
                f"maturity_years must be positive, got {maturity_years}. A forward audit at "
                f"or past expiry has no carry period."
            )
        if market_forward_price < 0.0:
            raise DividendModelError(
                f"market_forward_price must be non-negative, got {market_forward_price}."
            )
        if accrual_start_years >= maturity_years:
            raise DividendModelError(
                f"accrual_start_years ({accrual_start_years}) must be before maturity_years "
                f"({maturity_years})."
            )

        if dividends is None:
            raise DividendModelError(
                "dividends must be a list of DiscreteDividendEvent; pass [] for a "
                "non-dividend-paying underlying, not None."
            )

        warnings: List[str] = []
        excluded: List[str] = []
        for d in dividends:
            self._validate_dividend(d)
            if not (accrual_start_years < d.effective_ex_time_years <= maturity_years):
                excluded.append(d.dividend_id)
        if excluded:
            msg = (
                f"[{symbol}] {len(excluded)} dividend(s) excluded -- ex-date outside the "
                f"({accrual_start_years}, {maturity_years}] window: {', '.join(excluded)}."
            )
            warnings.append(msg)
            logger.warning(msg)

        pv_div, fv_div, gross_accrual = self.calculate_dividend_present_value(
            dividends, risk_free_rate, maturity_years, accrual_start_years
        )
        f_theo = self.calculate_theoretical_forward_price(
            spot_price, pv_div, risk_free_rate, maturity_years
        )

        if f_theo <= 0.0:
            msg = (
                f"[{symbol}] theoretical forward is non-positive ({f_theo}): PV(D)={pv_div} "
                f"is at or above spot {spot_price}. Check the dividend feed for duplicated, "
                f"mis-scaled, or wrong-currency entries before trading this signal."
            )
            warnings.append(msg)
            logger.warning(msg)

        spread = round(market_forward_price - f_theo, 4)
        arb_type = ArbitrageSignal.NO_ARBITRAGE
        gross_profit = 0.0
        net_profit = 0.0
        applied_threshold = 0.0

        if spread > self.arbitrage_cost_threshold_usd:
            arb_type = ArbitrageSignal.ARBITRAGE_SHORT_FORWARD_LONG_SPOT
            applied_threshold = self.arbitrage_cost_threshold_usd
            gross_profit = round(abs(spread), 2)
            net_profit = round(abs(spread) - applied_threshold, 2)
            logger.info(
                "FORWARD ARBITRAGE DETECTED [%s]: Market Forward %.2f > Theo %.2f. "
                "Short Forward / Long Spot. Gross=%.2f Net=%.2f per unit",
                symbol, market_forward_price, f_theo, gross_profit, net_profit,
            )
        elif spread < -self.reverse_arbitrage_cost_threshold_usd:
            arb_type = ArbitrageSignal.ARBITRAGE_LONG_FORWARD_SHORT_SPOT
            applied_threshold = self.reverse_arbitrage_cost_threshold_usd
            gross_profit = round(abs(spread), 2)
            net_profit = round(abs(spread) - applied_threshold, 2)
            logger.info(
                "FORWARD ARBITRAGE DETECTED [%s]: Market Forward %.2f < Theo %.2f. "
                "Long Forward / Short Spot -- requires stock borrow and pays gross "
                "manufactured dividends. Gross=%.2f Net=%.2f per unit",
                symbol, market_forward_price, f_theo, gross_profit, net_profit,
            )

        return DividendForwardAuditReport(
            symbol=symbol,
            spot_price=spot_price,
            time_to_maturity_years=maturity_years,
            risk_free_rate_pct=round(risk_free_rate * 100.0, 2),
            pv_dividends_usd=pv_div,
            fv_dividends_usd=fv_div,
            theoretical_forward_price=f_theo,
            fair_value_dividend_future_points=gross_accrual,
            market_forward_price=market_forward_price,
            mispricing_spread_usd=spread,
            arbitrage_opportunity=arb_type.value,
            estimated_gross_profit_usd=gross_profit,
            estimated_net_profit_usd=net_profit,
            applied_cost_threshold_usd=applied_threshold,
            excluded_dividend_ids=excluded,
            warnings=warnings,
        )
