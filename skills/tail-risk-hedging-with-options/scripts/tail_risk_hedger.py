"""
tail-risk-hedging-with-options: systematic out-of-the-money (OTM) index put overlay
sizer -- Black-Scholes pricing and Greeks, a carry budget that is actually annual,
a hedge-notional cap, and crash payoffs reported net of the premium paid.

What this module is for
-----------------------
Deciding *how many* OTM put contracts a portfolio can buy per roll cycle without
exceeding a stated annual premium budget, and showing what that position is worth
in a crash. It is a sizing and budgeting tool, not an execution engine and not a
surface calibrator.

Three things decide whether the answer is right
-----------------------------------------------
**1. The budget is annual; the purchase is per-tranche.** ``budget_pct`` is the
maximum premium spend per *year*. A 90-DTE put rolled at 30 DTE is held 60 days,
so the program buys ``365 / 60 = 6.08`` tranches a year. Each tranche may
therefore spend only ``budget_pct * 60 / 365`` of the portfolio. Spending the
full annual budget on every tranche -- the obvious and wrong implementation --
turns a stated 2% budget into roughly 12% of realised annual drag.

**2. ``volatility`` must be the implied volatility of the strike being bought,
not ATM vol.** Index put skew is not a refinement here, it is most of the price.
Since 1987 the index smile has been an asymmetric smirk with deep-OTM puts
carrying the highest implied volatilities (AQR, *Tail Risk Hedging: Contrasting
Put and Trend Strategies*, July 2020, p.4). At this module's reference point --
spot 400, 15% OTM, 90 DTE -- the put costs $61 per contract at 20% vol and $334
at 30% vol: **5.5x**. Feeding ATM vol to a deep-OTM strike therefore under-prices
the premium several-fold and over-allocates contracts by the same factor. There
is deliberately no default; calibrate the strike's own IV with
``options-implied-volatility-surface-construction`` and pass it in.

**3. A hedge cannot cover more shares than the portfolio owns.** Budget-only
sizing has no upper bound on contract count, so cheap deep-OTM puts silently
build a position larger than the thing being hedged -- at the defaults above,
394% of portfolio notional, which is a leveraged short with a premium cost, not
a hedge. ``Config.max_hedge_notional_pct`` caps hedged notional
(``contracts * multiplier * spot``) at a fraction of portfolio value, 100% by
default.

Expected return
---------------
Passive OTM index put buying has lost money in every decade for which index
option data exists, robust to maturity and moneyness, because implied
volatilities and implied negative skewness systematically exceed subsequent
realisations (AQR 2020, pp.3-5). This module sizes that cost deliberately; it
does not make it go away. See SKILL.md "When NOT to Use".

Scope
-----
European exercise, cash-settled index puts, a single flat volatility per call
(no smile, no term structure), terminal intrinsic value for stress payoffs. No
early exercise, no assignment, no bid/ask, no commissions, no margin.
"""
from dataclasses import dataclass, field
import logging
import math
from typing import Dict, Optional

logger = logging.getLogger(__name__)

#: Days per year used to convert DTE to year-fractions. Calendar-day convention,
#: matching how listed option DTE and IV term structure are conventionally quoted.
DAYS_PER_YEAR = 365.0

#: Which constraint bound the contract count, so the caller can tell "budget too
#: small" apart from "hedge would have exceeded portfolio notional".
BINDING_NONE = "NONE"
BINDING_BUDGET = "BUDGET"
BINDING_NOTIONAL_CAP = "NOTIONAL_CAP"


def _norm_cdf(x: float) -> float:
    """Standard normal cumulative distribution function."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    """Standard normal probability density function."""
    return (1.0 / math.sqrt(2.0 * math.pi)) * math.exp(-0.5 * x * x)


def _require_finite(name: str, value: float) -> float:
    """
    Rejects NaN/Inf before it reaches the model.

    Every comparison against NaN is False, so a plain ``if value <= 0`` guard
    passes NaN straight through. The previous implementation did exactly that: a
    NaN volatility produced a NaN option price, survived the
    ``per_contract_price <= 0`` check, and then raised from inside
    ``int(budget // nan)`` half-way through sizing.
    """
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"{name} must be a finite number, got {value!r}")
    return numeric


def _require_positive(name: str, value: float) -> float:
    """Rejects non-positive values where the model takes a log of or divides by them."""
    numeric = _require_finite(name, value)
    if numeric <= 0.0:
        raise ValueError(f"{name} must be strictly positive, got {value!r}")
    return numeric


def _require_non_negative(name: str, value: float) -> float:
    """Rejects negative values for quantities that are magnitudes or rates."""
    numeric = _require_finite(name, value)
    if numeric < 0.0:
        raise ValueError(f"{name} must be non-negative, got {value!r}")
    return numeric


@dataclass
class Config:
    """
    Overlay policy parameters.

    Attributes:
        enabled: Master on/off switch consumed by :class:`Engine`.
        budget_pct: Maximum option premium spend per **year**, as a fraction of
            portfolio value. This is not the per-tranche spend -- see
            ``holding_days`` and ``rolls_per_year``.
        otm_pct: Strike moneyness, as a fraction below spot. 0.15 selects a
            strike at ``0.85 * spot``.
        dte_target: Days to expiration at purchase.
        roll_dte: Days to expiration at which the tranche is rolled into a new
            ``dte_target`` contract. The tranche is therefore held
            ``dte_target - roll_dte`` days, and that holding period -- not
            ``dte_target`` -- sets how many tranches a year the budget funds.
        max_hedge_notional_pct: Cap on hedged notional
            (``contracts * multiplier * spot``) as a fraction of portfolio value.
            1.0 means the overlay may never cover more shares than the portfolio
            holds. Above 1.0 the position is directionally short beyond the
            portfolio and is no longer a hedge.
    """

    enabled: bool = True
    budget_pct: float = 0.02
    otm_pct: float = 0.15
    dte_target: int = 90
    roll_dte: int = 30
    max_hedge_notional_pct: float = 1.0

    def __post_init__(self) -> None:
        self.budget_pct = _require_non_negative("budget_pct", self.budget_pct)
        self.otm_pct = _require_finite("otm_pct", self.otm_pct)
        if not 0.0 < self.otm_pct < 1.0:
            raise ValueError(
                f"otm_pct must lie in (0, 1) for an OTM put strike, got {self.otm_pct!r}"
            )
        if self.dte_target <= 0:
            raise ValueError(f"dte_target must be positive, got {self.dte_target!r}")
        if self.roll_dte < 0:
            raise ValueError(f"roll_dte must be non-negative, got {self.roll_dte!r}")
        if self.roll_dte >= self.dte_target:
            raise ValueError(
                f"roll_dte ({self.roll_dte}) must be less than dte_target "
                f"({self.dte_target}); otherwise the tranche is rolled at or before "
                "purchase and the holding period is not positive"
            )
        self.max_hedge_notional_pct = _require_non_negative(
            "max_hedge_notional_pct", self.max_hedge_notional_pct
        )

    @property
    def holding_days(self) -> int:
        """Days a tranche is held: bought at ``dte_target``, rolled at ``roll_dte``."""
        return self.dte_target - self.roll_dte

    @property
    def rolls_per_year(self) -> float:
        """Tranches purchased per year under this roll schedule."""
        return DAYS_PER_YEAR / self.holding_days


@dataclass
class StressScenario:
    """Terminal-intrinsic outcome of the overlay under one spot shock."""

    spot_drop_pct: float
    terminal_spot: float
    gross_payout: float
    net_payout: float
    portfolio_loss: float
    #: ``net_payout / portfolio_loss``. 1.0 means the overlay exactly offsets the
    #: portfolio's mark-to-market loss at this shock; 0.0 means no offset.
    net_coverage_ratio: float


@dataclass
class HedgingResult:
    """
    Outcome of a sizing run.

    ``cost`` and ``carry_cost_pct`` describe a **single tranche**;
    ``annualized_carry_pct`` projects that spend across ``rolls_per_year`` and is
    the number to compare against ``Config.budget_pct``.
    """

    hedged: bool
    options_bought: int
    cost: float
    option_price: float = 0.0
    strike_price: float = 0.0
    carry_cost_pct: float = 0.0
    crash_payout_20pct_drop: float = 0.0
    crash_payout_30pct_drop: float = 0.0
    tranche_budget: float = 0.0
    annualized_carry_pct: float = 0.0
    rolls_per_year: float = 0.0
    hedged_notional: float = 0.0
    notional_coverage_ratio: float = 0.0
    binding_constraint: str = BINDING_NONE
    greeks: Dict[str, float] = field(default_factory=dict)
    stress_scenarios: Dict[str, StressScenario] = field(default_factory=dict)


class TailRiskHedger:
    """
    Sizes a systematic OTM put overlay against an annual premium budget and a
    hedge-notional cap, and prices it with Black-Scholes.
    """

    def __init__(self, budget_pct: float = 0.05, config: Optional[Config] = None):
        self.config = config if config is not None else Config(budget_pct=budget_pct)
        self.budget_pct = self.config.budget_pct

    def black_scholes_put(
        self,
        S: float,
        K: float,
        T: float,
        r: float,
        sigma: float,
        dividend_yield: float = 0.0,
    ) -> Dict[str, float]:
        """
        Prices a European put and returns its Greeks, per share of the underlying.

        Args:
            S: Spot price of the underlying.
            K: Strike price.
            T: Time to expiration in years.
            r: Continuously compounded risk-free rate, as a decimal.
            sigma: Implied volatility **of this strike**, as a decimal. For an OTM
                index put this is materially above ATM vol; see the module
                docstring.
            dividend_yield: Continuous dividend (or carry) yield ``q`` of the
                underlying, as a decimal. Omitting it on a dividend-paying index
                under-prices the put -- about 5.6% at SPX's ~1.3% yield for a
                15% OTM 90-day contract.

        Returns:
            ``price``, ``delta``, ``gamma``, ``vega`` (per 1 volatility point),
            ``theta`` (per calendar day), ``d1``, ``d2``.

        Raises:
            ValueError: On non-finite input, or non-positive ``S``, ``K``, ``T``
                or ``sigma``. It does not return a zero price for bad input: a
                zero-priced put reads as a free hedge and, downstream, as an
                unbounded contract count.
        """
        S = _require_positive("S", S)
        K = _require_positive("K", K)
        T = _require_positive("T", T)
        sigma = _require_positive("sigma", sigma)
        r = _require_finite("r", r)
        q = _require_finite("dividend_yield", dividend_yield)

        sqrt_T = math.sqrt(T)
        d1 = (math.log(S / K) + (r - q + 0.5 * sigma**2) * T) / (sigma * sqrt_T)
        d2 = d1 - sigma * sqrt_T

        disc_r = math.exp(-r * T)
        disc_q = math.exp(-q * T)

        price = K * disc_r * _norm_cdf(-d2) - S * disc_q * _norm_cdf(-d1)

        # Put delta is N(d1) - 1 (equivalently -N(-d1)), carry-discounted. Writing
        # it as N(-d1) - 1 is a different and much larger number: at this skill's
        # own reference point (S=100, K=85, T=0.25, sigma=0.25) the true delta is
        # -0.0746 and that expression gives -0.9254, 12.4x too large. A sizer fed
        # a near-1 delta for a 15% OTM put believes it is holding a synthetic
        # short rather than convexity.
        delta = disc_q * (_norm_cdf(d1) - 1.0)
        gamma = disc_q * _norm_pdf(d1) / (S * sigma * sqrt_T)
        vega = S * disc_q * sqrt_T * _norm_pdf(d1) / 100.0  # per 1 vol point

        # Per-year theta, then per calendar day. The first term is the decay of
        # extrinsic value; the other two are carry on the strike and on spot.
        theta_annual = (
            -S * disc_q * _norm_pdf(d1) * sigma / (2.0 * sqrt_T)
            + r * K * disc_r * _norm_cdf(-d2)
            - q * S * disc_q * _norm_cdf(-d1)
        )

        return {
            "price": price,
            "delta": delta,
            "gamma": gamma,
            "vega": vega,
            "theta": theta_annual / DAYS_PER_YEAR,
            "d1": d1,
            "d2": d2,
        }

    def hedge(self, portfolio_value: float, option_price: float) -> HedgingResult:
        """
        One-shot sizer against a pre-quoted premium, retained for callers that
        already have a price and want nothing else.

        ``option_price`` is the **all-in cost of one unit** the caller intends to
        buy -- if that unit is a 100-share contract, pass the per-contract
        premium, not the per-share quote.

        This entry point has no expiration and no roll schedule, so it cannot
        annualise anything: it spends ``budget_pct`` of portfolio value once, and
        the caller owns the question of how often that recurs. For a budget that
        is genuinely annual, use :meth:`plan_systematic_otm_put_hedge`.
        """
        portfolio_value = _require_finite("portfolio_value", portfolio_value)
        option_price = _require_finite("option_price", option_price)
        if option_price <= 0 or portfolio_value <= 0:
            return HedgingResult(False, 0, 0.0)

        max_spend = portfolio_value * self.budget_pct
        options_bought = int(max_spend // option_price)
        cost = options_bought * option_price

        return HedgingResult(
            hedged=options_bought > 0,
            options_bought=options_bought,
            cost=cost,
            option_price=option_price,
            carry_cost_pct=cost / portfolio_value,
            tranche_budget=max_spend,
            binding_constraint=BINDING_BUDGET,
        )

    def plan_systematic_otm_put_hedge(
        self,
        portfolio_value: float,
        spot_price: float,
        volatility: float,
        risk_free_rate: float = 0.04,
        contract_multiplier: int = 100,
        dividend_yield: float = 0.0,
    ) -> HedgingResult:
        """
        Sizes one tranche of the overlay.

        The contract count is the smaller of what the tranche's share of the
        annual premium budget affords and what the hedge-notional cap permits;
        ``binding_constraint`` reports which one bound.

        Args:
            portfolio_value: Portfolio value the overlay protects, in the same
                currency as ``spot_price``.
            spot_price: Current level of the hedged underlying.
            volatility: Implied volatility **of the selected OTM strike**. There
                is no default: an ATM-vol stand-in under-prices a 15% OTM 90-day
                put several-fold and over-allocates contracts by the same factor.
            risk_free_rate: Continuously compounded discount rate. The 0.04
                default is a placeholder, not a market observation.
            contract_multiplier: Shares (or index units) per contract. 100 is the
                OCC standard for listed US equity options and the CBOE SPX
                multiplier, but corporate-action-adjusted contracts can deliver a
                non-standard amount -- read the contract, do not assume.
            dividend_yield: Continuous dividend/carry yield of the underlying.

        Returns:
            A :class:`HedgingResult`. ``hedged`` is False when no whole contract
            fits; inspect ``binding_constraint`` and ``tranche_budget`` to see why.

        Raises:
            ValueError: On non-finite or non-positive pricing inputs.
        """
        portfolio_value = _require_finite("portfolio_value", portfolio_value)
        spot_price = _require_positive("spot_price", spot_price)
        if contract_multiplier <= 0:
            raise ValueError(
                f"contract_multiplier must be positive, got {contract_multiplier!r}"
            )
        if portfolio_value <= 0:
            return HedgingResult(False, 0, 0.0)

        cfg = self.config
        strike = spot_price * (1.0 - cfg.otm_pct)
        T = cfg.dte_target / DAYS_PER_YEAR

        greeks = self.black_scholes_put(
            spot_price, strike, T, risk_free_rate, volatility, dividend_yield
        )
        per_contract_price = greeks["price"] * contract_multiplier
        if per_contract_price <= 0.0:
            # Reachable only when the strike is so far OTM that the price
            # underflows to zero; treat as "no meaningful hedge available".
            logger.warning(
                "put premium underflowed to zero (spot=%.4f strike=%.4f sigma=%.4f "
                "T=%.4f); no hedge sized",
                spot_price, strike, volatility, T,
            )
            return HedgingResult(False, 0, 0.0, strike_price=strike, greeks=greeks)

        # The budget is annual; this tranche is held holding_days, so it gets
        # holding_days / 365 of it.
        tranche_budget = portfolio_value * cfg.budget_pct / cfg.rolls_per_year
        contracts_by_budget = int(tranche_budget // per_contract_price)

        # A hedge cannot cover more shares than the portfolio owns.
        notional_per_contract = spot_price * contract_multiplier
        max_notional = portfolio_value * cfg.max_hedge_notional_pct
        contracts_by_notional = int(max_notional // notional_per_contract)

        contracts = min(contracts_by_budget, contracts_by_notional)
        if contracts_by_notional < contracts_by_budget:
            binding = BINDING_NOTIONAL_CAP
            logger.info(
                "hedge notional cap bound contract count to %d (budget allowed %d) "
                "at max_hedge_notional_pct=%.2f",
                contracts_by_notional, contracts_by_budget, cfg.max_hedge_notional_pct,
            )
        else:
            binding = BINDING_BUDGET

        total_cost = contracts * per_contract_price
        hedged_notional = contracts * notional_per_contract

        scenarios: Dict[str, StressScenario] = {}
        for drop in (0.10, 0.20, 0.30, 0.40):
            terminal_spot = spot_price * (1.0 - drop)
            gross = max(0.0, strike - terminal_spot) * contract_multiplier * contracts
            loss = portfolio_value * drop
            net = gross - total_cost
            scenarios[f"drop_{int(round(drop * 100))}pct"] = StressScenario(
                spot_drop_pct=drop,
                terminal_spot=terminal_spot,
                gross_payout=gross,
                net_payout=net,
                portfolio_loss=loss,
                net_coverage_ratio=net / loss if loss > 0 else 0.0,
            )

        return HedgingResult(
            hedged=contracts > 0,
            options_bought=contracts,
            cost=total_cost,
            option_price=per_contract_price,
            strike_price=strike,
            carry_cost_pct=total_cost / portfolio_value,
            crash_payout_20pct_drop=scenarios["drop_20pct"].gross_payout,
            crash_payout_30pct_drop=scenarios["drop_30pct"].gross_payout,
            tranche_budget=tranche_budget,
            annualized_carry_pct=(total_cost * cfg.rolls_per_year) / portfolio_value,
            rolls_per_year=cfg.rolls_per_year,
            hedged_notional=hedged_notional,
            notional_coverage_ratio=hedged_notional / portfolio_value,
            # Reported even when the count is zero: "the cap allowed nothing" and
            # "the budget allowed nothing" are different problems with different
            # fixes, and collapsing them into BUDGET hides a mis-set cap.
            binding_constraint=binding,
            greeks=greeks,
            stress_scenarios=scenarios,
        )


class Engine:
    """Thin enable/disable wrapper retained for callers that construct it."""

    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config()
        self.hedger = TailRiskHedger(config=self.config)

    def run(self) -> bool:
        return self.config.enabled
