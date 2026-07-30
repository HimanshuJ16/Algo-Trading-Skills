import math
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

@dataclass
class DiscreteDividendEvent:
    dividend_id: str
    amount_usd: float
    payment_time_years: float           # e.g. 0.25 years (3 months)
    withholding_tax_pct: float = 0.0    # e.g. 0.15 for 15% tax

@dataclass
class DividendForwardAuditReport:
    symbol: str
    spot_price: float
    time_to_maturity_years: float
    risk_free_rate_pct: float
    pv_dividends_usd: float
    fv_dividends_usd: float
    theoretical_forward_price: float
    fair_value_dividend_future_points: float
    market_forward_price: float
    mispricing_spread_usd: float
    arbitrage_opportunity: str          # 'NO_ARBITRAGE', 'ARBITRAGE_SHORT_FORWARD_LONG_SPOT', 'ARBITRAGE_LONG_FORWARD_SHORT_SPOT'
    estimated_gross_profit_usd: float

class DividendForwardModelingEngine:
    """
    Quantitative equity forward curve engine for modeling discrete dividends,
    present/future value calculations, fair value dividend futures pricing, and cash-and-carry arbitrage detection.
    """
    def __init__(self, arbitrage_cost_threshold_usd: float = 0.50):
        self.arbitrage_cost_threshold_usd = arbitrage_cost_threshold_usd

    def calculate_dividend_present_value(
        self,
        dividends: List[DiscreteDividendEvent],
        risk_free_rate: float,
        maturity_years: float
    ) -> Tuple[float, float, float]:
        """
        Calculates Present Value PV(D), Future Value FV(D), and Net Cumulative Dividend Points.
        PV(D) = sum( D_i * (1 - tax) * exp(-r * t_i) ) for t_i <= T
        """
        pv_div = 0.0
        fv_div = 0.0
        cumulative_div_points = 0.0

        for d in dividends:
            if d.payment_time_years <= maturity_years:
                net_amount = d.amount_usd * (1.0 - d.withholding_tax_pct)
                cumulative_div_points += net_amount

                # Discounting to t=0
                pv_i = net_amount * math.exp(-risk_free_rate * d.payment_time_years)
                pv_div += pv_i

                # Compounding to t=T
                fv_i = net_amount * math.exp(risk_free_rate * (maturity_years - d.payment_time_years))
                fv_div += fv_i

        return round(pv_div, 4), round(fv_div, 4), round(cumulative_div_points, 4)

    def calculate_theoretical_forward_price(
        self,
        spot_price: float,
        pv_dividends: float,
        risk_free_rate: float,
        maturity_years: float
    ) -> float:
        """
        Theoretical Forward Price F(0, T) = (S_0 - PV(D)) * exp(r * T)
        """
        f_price = (spot_price - pv_dividends) * math.exp(risk_free_rate * maturity_years)
        return round(f_price, 4)

    def audit_forward_arbitrage(
        self,
        symbol: str,
        spot_price: float,
        maturity_years: float,
        risk_free_rate: float,
        dividends: List[DiscreteDividendEvent],
        market_forward_price: float
    ) -> DividendForwardAuditReport:
        """
        Audits market forward price against theoretical forward price bounds to detect cash-and-carry arbitrage.
        """
        pv_div, fv_div, cum_div = self.calculate_dividend_present_value(dividends, risk_free_rate, maturity_years)
        f_theo = self.calculate_theoretical_forward_price(spot_price, pv_div, risk_free_rate, maturity_years)

        spread = round(market_forward_price - f_theo, 4)
        arb_type = "NO_ARBITRAGE"
        profit = 0.0

        if spread > self.arbitrage_cost_threshold_usd:
            arb_type = "ARBITRAGE_SHORT_FORWARD_LONG_SPOT"
            profit = round(spread - self.arbitrage_cost_threshold_usd, 2)
            logger.info(f"FORWARD ARBITRAGE DETECTED [{symbol}]: Market Forward ${market_forward_price:.2f} > Theo ${f_theo:.2f}. Short Forward / Long Spot! Profit=${profit}")
        elif spread < -self.arbitrage_cost_threshold_usd:
            arb_type = "ARBITRAGE_LONG_FORWARD_SHORT_SPOT"
            profit = round(abs(spread) - self.arbitrage_cost_threshold_usd, 2)
            logger.info(f"FORWARD ARBITRAGE DETECTED [{symbol}]: Market Forward ${market_forward_price:.2f} < Theo ${f_theo:.2f}. Long Forward / Short Spot! Profit=${profit}")

        return DividendForwardAuditReport(
            symbol=symbol,
            spot_price=spot_price,
            time_to_maturity_years=maturity_years,
            risk_free_rate_pct=round(risk_free_rate * 100.0, 2),
            pv_dividends_usd=pv_div,
            fv_dividends_usd=fv_div,
            theoretical_forward_price=f_theo,
            fair_value_dividend_future_points=cum_div,
            market_forward_price=market_forward_price,
            mispricing_spread_usd=spread,
            arbitrage_opportunity=arb_type,
            estimated_gross_profit_usd=profit
        )
