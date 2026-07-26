import math
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

@dataclass
class CarryCostResult:
    spot_price: float
    futures_market_price: float
    theoretical_futures_price: float
    time_to_maturity_years: float
    implied_convenience_yield: float
    regime: str                           # 'CONTANGO' or 'BACKWARDATION'
    basis: float                          # Spot - Futures
    is_arbitrage_opportunity: bool
    arbitrage_type: Optional[str]         # 'CASH_AND_CARRY' or 'REVERSE_CASH_AND_CARRY'

class CommodityCarryCostModel:
    """
    Cost of Carry and Convenience Yield Pricing Engine for Commodity Futures.
    Formula: F_theoretical = Spot * exp((risk_free_rate + storage_cost - convenience_yield) * T)
    """
    def __init__(self, risk_free_rate: float = 0.05, storage_cost_rate: float = 0.02):
        self.risk_free_rate = risk_free_rate          # Annualized continuous rate (e.g. 5%)
        self.storage_cost_rate = storage_cost_rate    # Annualized continuous storage cost (e.g. 2%)

    def calculate_theoretical_futures_price(
        self, 
        spot_price: float, 
        time_to_maturity_years: float, 
        convenience_yield: float
    ) -> float:
        """
        Calculates theoretical futures price given spot, rates, storage, and convenience yield.
        """
        if spot_price <= 0 or time_to_maturity_years <= 0:
            raise ValueError("Spot price and time to maturity must be positive.")
            
        net_carry_rate = self.risk_free_rate + self.storage_cost_rate - convenience_yield
        return spot_price * math.exp(net_carry_rate * time_to_maturity_years)

    def extract_implied_convenience_yield(
        self, 
        spot_price: float, 
        futures_market_price: float, 
        time_to_maturity_years: float
    ) -> float:
        """
        Solves for implied convenience yield y: y = (r + c) - (1/T) * ln(F_market / Spot)
        """
        if spot_price <= 0 or futures_market_price <= 0 or time_to_maturity_years <= 0:
            raise ValueError("Spot price, futures price, and time to maturity must be positive.")
            
        ln_ratio = math.log(futures_market_price / spot_price)
        implied_y = (self.risk_free_rate + self.storage_cost_rate) - (ln_ratio / time_to_maturity_years)
        return float(implied_y)

    def evaluate_market(
        self, 
        spot_price: float, 
        futures_market_price: float, 
        time_to_maturity_years: float,
        expected_baseline_convenience_yield: float = 0.01,
        transaction_cost_pct: float = 0.005
    ) -> CarryCostResult:
        """
        Evaluates market regime (Contango/Backwardation) and checks for cash-and-carry arbitrage.
        """
        implied_y = self.extract_implied_convenience_yield(spot_price, futures_market_price, time_to_maturity_years)
        theoretical_f = self.calculate_theoretical_futures_price(spot_price, time_to_maturity_years, expected_baseline_convenience_yield)
        
        basis = spot_price - futures_market_price
        regime = "CONTANGO" if futures_market_price > spot_price else "BACKWARDATION"
        
        # Arbitrage check: compare market futures to fair theoretical futures
        price_diff_pct = (futures_market_price - theoretical_f) / theoretical_f
        is_arbitrage = False
        arb_type = None
        
        if price_diff_pct > transaction_cost_pct:
            is_arbitrage = True
            arb_type = "CASH_AND_CARRY"  # Futures overpriced: Buy Spot, Sell Futures
        elif price_diff_pct < -transaction_cost_pct:
            is_arbitrage = True
            arb_type = "REVERSE_CASH_AND_CARRY" # Futures underpriced: Sell Spot, Buy Futures

        return CarryCostResult(
            spot_price=round(spot_price, 4),
            futures_market_price=round(futures_market_price, 4),
            theoretical_futures_price=round(theoretical_f, 4),
            time_to_maturity_years=round(time_to_maturity_years, 4),
            implied_convenience_yield=round(implied_y, 6),
            regime=regime,
            basis=round(basis, 4),
            is_arbitrage_opportunity=is_arbitrage,
            arbitrage_type=arb_type
        )
