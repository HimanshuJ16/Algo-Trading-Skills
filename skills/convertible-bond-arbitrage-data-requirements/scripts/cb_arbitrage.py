import math
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class ConvertibleBondData:
    bond_id: str
    symbol: str
    clean_price: float                # Market price per $100 par (e.g. 98.50)
    par_value: float                  # e.g. $1,000
    conversion_ratio: float           # Number of shares per bond (e.g. 20.0)
    coupon_rate_annual: float         # e.g. 0.035 (3.5%)
    years_to_maturity: float

@dataclass
class EquityMarketData:
    symbol: str
    stock_price: float
    annual_borrow_fee_pct: float      # Hard-to-borrow fee (e.g. 0.015 = 1.5%)
    historical_volatility: float      # Annualized historical vol (e.g. 0.30)

@dataclass
class ConvertibleArbitrageMetrics:
    parity_conversion_value: float
    conversion_premium_pct: float
    option_delta: float
    optimal_short_stock_shares: int
    net_carry_rate_pct: float
    is_arbitrage_attractive: bool
    rationale: str

class ConvertibleBondArbitrageEngine:
    """
    Quantitative engine for validating data completeness, computing conversion parity,
    calculating delta-hedge short stock ratios, and evaluating convertible arbitrage trades.
    """
    def __init__(self, risk_free_rate: float = 0.04, repo_financing_rate: float = 0.045):
        self.risk_free_rate = risk_free_rate
        self.repo_financing_rate = repo_financing_rate

    def calculate_parity(self, conversion_ratio: float, stock_price: float) -> float:
        """
        Calculates Parity (Conversion Value): Parity = Conversion Ratio * Stock Price
        """
        if conversion_ratio <= 0 or stock_price <= 0:
            raise ValueError("Conversion ratio and stock price must be positive.")
        return conversion_ratio * stock_price

    def calculate_conversion_premium_pct(self, cb_market_price: float, parity: float) -> float:
        """
        Calculates Conversion Premium (%): ((CB Market Price - Parity) / Parity) * 100
        """
        if parity <= 0:
            raise ValueError("Parity must be positive.")
        return ((cb_market_price - parity) / parity) * 100.0

    def calculate_delta_hedge_quantity(
        self, 
        cb_quantity: int, 
        conversion_ratio: float, 
        delta: float
    ) -> int:
        """
        Calculates optimal short stock shares: Short Shares = CB Quantity * Conversion Ratio * Delta
        """
        if delta < 0.0 or delta > 1.0:
            raise ValueError("Delta must be in range [0.0, 1.0].")
        return int(round(cb_quantity * conversion_ratio * delta))

    def evaluate_arbitrage(
        self,
        bond: ConvertibleBondData,
        equity: EquityMarketData,
        cb_quantity: int,
        implied_volatility: float,
        estimated_delta: float
    ) -> ConvertibleArbitrageMetrics:
        """
        Audits data completeness and evaluates CB Arbitrage opportunity metrics.
        """
        # Data Integrity Audit
        if bond.clean_price <= 0 or equity.stock_price <= 0 or bond.conversion_ratio <= 0:
            raise ValueError("Invalid market data inputs.")

        cb_market_price = (bond.clean_price / 100.0) * bond.par_value
        parity = self.calculate_parity(bond.conversion_ratio, equity.stock_price)
        premium_pct = self.calculate_conversion_premium_pct(cb_market_price, parity)
        
        short_shares = self.calculate_delta_hedge_quantity(cb_quantity, bond.conversion_ratio, estimated_delta)
        
        # Net Carry Calculation: Coupon Yield - Repo Financing - Stock Borrow Fee
        cb_yield = (bond.coupon_rate_annual * bond.par_value) / cb_market_price
        net_carry = cb_yield - self.repo_financing_rate - equity.annual_borrow_fee_pct
        
        # Arbitrage Attractiveness Evaluation:
        # Attractive if Implied Volatility is cheap (IV < HV) AND Conversion Premium is reasonable (< 20%) AND Net Carry is positive/acceptable
        vol_discount = equity.historical_volatility - implied_volatility
        is_attractive = (vol_discount > 0.03) and (premium_pct < 25.0) and (net_carry > -0.02)
        
        rationale = (
            f"Parity=${parity:.2f}, Premium={premium_pct:.2f}%, NetCarry={net_carry*100:.2f}%, "
            f"IV={implied_volatility*100:.1f}% vs HV={equity.historical_volatility*100:.1f}%"
        )

        return ConvertibleArbitrageMetrics(
            parity_conversion_value=round(parity, 2),
            conversion_premium_pct=round(premium_pct, 2),
            option_delta=round(estimated_delta, 4),
            optimal_short_stock_shares=short_shares,
            net_carry_rate_pct=round(net_carry * 100, 2),
            is_arbitrage_attractive=is_attractive,
            rationale=rationale
        )
