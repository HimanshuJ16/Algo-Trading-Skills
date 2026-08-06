"""
tail-risk-hedging-with-options: Production-grade systematic OTM put option tail risk hedger,
negative carry budgeting, Black-Scholes pricing, and crash payoff simulator.
"""
from dataclasses import dataclass, field
import math
from typing import Any, Dict, List, Optional, Tuple


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return (1.0 / math.sqrt(2.0 * math.pi)) * math.exp(-0.5 * x * x)


@dataclass
class Config:
    """Config container for tail risk hedger."""
    enabled: bool = True
    budget_pct: float = 0.02  # 2% annual portfolio premium budget limit
    otm_pct: float = 0.15      # 15% out-of-the-money put strike selection
    dte_target: int = 90       # Target 90 days to expiration roll frequency


@dataclass
class HedgingResult:
    hedged: bool
    options_bought: int
    cost: float
    option_price: float = 0.0
    strike_price: float = 0.0
    carry_cost_pct: float = 0.0
    crash_payout_20pct_drop: float = 0.0
    crash_payout_30pct_drop: float = 0.0


class TailRiskHedger:
    """
    Manages systematic out-of-the-money (OTM) put option tail risk hedges,
    calculates option Greeks, enforces negative carry budgets, and models convex crash payouts.
    """

    def __init__(self, budget_pct: float = 0.05, config: Optional[Config] = None):
        if config:
            self.config = config
        else:
            self.config = Config(budget_pct=budget_pct)
        self.budget_pct = self.config.budget_pct

    def black_scholes_put(
        self, S: float, K: float, T: float, r: float, sigma: float
    ) -> Dict[str, float]:
        """Calculates Black-Scholes put option price and Greeks."""
        if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
            return {"price": 0.0, "delta": 0.0, "gamma": 0.0, "vega": 0.0}

        d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)

        price = K * math.exp(-r * T) * _norm_cdf(-d2) - S * _norm_cdf(-d1)
        delta = _norm_cdf(-d1) - 1.0
        gamma = _norm_pdf(d1) / (S * sigma * math.sqrt(T))
        vega = S * math.sqrt(T) * _norm_pdf(d1) / 100.0  # per 1% vol

        return {"price": price, "delta": delta, "gamma": gamma, "vega": vega}

    def hedge(self, portfolio_value: float, option_price: float) -> HedgingResult:
        """
        Backward compatible basic hedge execution using direct option price input.
        """
        if option_price <= 0 or portfolio_value <= 0:
            return HedgingResult(False, 0, 0.0)

        max_spend = portfolio_value * self.budget_pct
        options_bought = int(max_spend // option_price)
        cost = options_bought * option_price
        carry_pct = (cost / portfolio_value) if portfolio_value > 0 else 0.0

        return HedgingResult(
            hedged=options_bought > 0,
            options_bought=options_bought,
            cost=cost,
            option_price=option_price,
            carry_cost_pct=carry_pct,
        )

    def plan_systematic_otm_put_hedge(
        self,
        portfolio_value: float,
        spot_price: float,
        volatility: float = 0.20,
        risk_free_rate: float = 0.04,
        contract_multiplier: int = 100,
    ) -> HedgingResult:
        """
        Plans systematic OTM put option hedge based on strike selection, Black-Scholes pricing,
        and portfolio premium budget constraints.
        """
        strike = spot_price * (1.0 - self.config.otm_pct)
        T = self.config.dte_target / 365.0

        bs = self.black_scholes_put(spot_price, strike, T, risk_free_rate, volatility)
        per_share_price = bs["price"]
        per_contract_price = per_share_price * contract_multiplier

        if per_contract_price <= 0:
            return HedgingResult(False, 0, 0.0)

        max_budget = portfolio_value * self.config.budget_pct
        contracts = int(max_budget // per_contract_price)
        total_cost = contracts * per_contract_price

        # Simulate intrinsic payoff at expiration for market crashes (-20%, -30%)
        spot_20_drop = spot_price * 0.80
        payoff_20 = max(0.0, strike - spot_20_drop) * contract_multiplier * contracts

        spot_30_drop = spot_price * 0.70
        payoff_30 = max(0.0, strike - spot_30_drop) * contract_multiplier * contracts

        return HedgingResult(
            hedged=contracts > 0,
            options_bought=contracts,
            cost=total_cost,
            option_price=per_contract_price,
            strike_price=strike,
            carry_cost_pct=total_cost / max(portfolio_value, 1.0),
            crash_payout_20pct_drop=payoff_20,
            crash_payout_30pct_drop=payoff_30,
        )


class Engine:
    """Legacy Engine class wrapper."""

    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config()
        self.hedger = TailRiskHedger(config=self.config)

    def run(self) -> bool:
        return self.config.enabled
