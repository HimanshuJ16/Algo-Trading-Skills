import math
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class ModelConfig:
    """Legacy ModelConfig container for backward compatibility."""
    name: str

class MainEngine:
    """Legacy MainEngine class for backward compatibility."""
    def __init__(self, config: ModelConfig):
        self.config = config

    def execute(self) -> bool:
        return True

class SSFSettlementType(str, Enum):
    CASH_SETTLED = "CASH_SETTLED"       # e.g., NSE India Futures, Eurex Cash SSF
    PHYSICAL_DELIVERY = "PHYSICAL_DELIVERY" # Physical delivery of underlying equity

class SSFArbitrageSignal(str, Enum):
    CASH_AND_CARRY = "CASH_AND_CARRY"   # Buy Spot, Sell SSF (SSF Overvalued)
    REVERSE_CASH_AND_CARRY = "REVERSE_CASH_AND_CARRY" # Sell Spot / Short, Buy SSF (SSF Undervalued)
    NEUTRAL = "NEUTRAL"

@dataclass
class SSFContractSpec:
    symbol: str                          # e.g., 'RELIANCE.NS-FUT', 'SIE.DE-FUT'
    underlying_spot_symbol: str          # e.g., 'RELIANCE.NS', 'SIE.DE'
    exchange: str                        # 'NSE', 'EUREX', 'EURONEXT'
    lot_size: int                        # Contract multiplier (e.g. 250 shares)
    days_to_expiry: int
    settlement_type: SSFSettlementType
    risk_free_rate_annual: float = 0.05
    short_borrow_rate_annual: float = 0.005 # Short borrow fee (0.5%)

@dataclass
class DividendEvent:
    ex_date_days: int                    # Days until ex-dividend date
    amount_per_share: float

@dataclass
class SSFFairValueResult:
    symbol: str
    underlying_spot_price: float
    market_ssf_price: float
    theoretical_fair_value: float
    mispricing_amount: float             # market_price - fair_value
    mispricing_pct: float
    arbitrage_signal: SSFArbitrageSignal
    initial_margin_ssf_usd: float
    initial_margin_spot_usd: float
    leverage_multiplier: float
    audit_notes: str

class SingleStockFuturesEngine:
    """
    Production-grade Single Stock Futures (SSF) engine evaluating theoretical fair value,
    cash-and-carry arbitrage opportunities, ex-dividend price adjustments, and margin leverage efficiency.
    """
    def __init__(self, arbitrage_cost_threshold_pct: float = 0.3):
        self.arbitrage_cost_threshold_pct = arbitrage_cost_threshold_pct

    def compute_fair_value_and_arbitrage(
        self,
        spec: SSFContractSpec,
        spot_price: float,
        market_ssf_price: float,
        dividends: Optional[List[DividendEvent]] = None,
        ssf_margin_pct: float = 0.15,      # 15% SSF margin requirement
        spot_margin_pct: float = 0.50      # 50% Regulation T spot margin
    ) -> SSFFairValueResult:
        """
        Calculates theoretical SSF fair value:
        F_fair = (Spot - PV(Dividends)) * e^{(r - q) * T}
        Identifies Cash-and-Carry vs Reverse Cash-and-Carry opportunities and quantifies margin efficiency.
        """
        t_years = spec.days_to_expiry / 365.0
        div_pv = 0.0

        if dividends:
            for div in dividends:
                if 0 <= div.ex_date_days <= spec.days_to_expiry:
                    # Discount dividend back to present
                    t_div = div.ex_date_days / 365.0
                    div_pv += div.amount_per_share * math.exp(-spec.risk_free_rate_annual * t_div)

        adjusted_spot = max(0.01, spot_price - div_pv)
        net_carry_rate = spec.risk_free_rate_annual - spec.short_borrow_rate_annual
        theoretical_fair_val = round(adjusted_spot * math.exp(net_carry_rate * t_years), 2)

        mispricing = round(market_ssf_price - theoretical_fair_val, 2)
        mispricing_pct = round((mispricing / theoretical_fair_val) * 100.0, 2)

        # Arbitrage Signal Determination
        if mispricing_pct >= self.arbitrage_cost_threshold_pct:
            signal = SSFArbitrageSignal.CASH_AND_CARRY
        elif mispricing_pct <= -self.arbitrage_cost_threshold_pct:
            signal = SSFArbitrageSignal.REVERSE_CASH_AND_CARRY
        else:
            signal = SSFArbitrageSignal.NEUTRAL

        # Margin & Leverage Efficiency
        notional_value = spec.lot_size * spot_price
        ssf_margin_usd = round(notional_value * ssf_margin_pct, 2)
        spot_margin_usd = round(notional_value * spot_margin_pct, 2)
        leverage_mult = round(spot_margin_usd / ssf_margin_usd, 2) if ssf_margin_usd > 0 else 1.0

        notes = (
            f"SSF FAIR VALUE [{spec.symbol}]: Spot = ${spot_price}, Market SSF = ${market_ssf_price}, "
            f"Fair Value = ${theoretical_fair_val}, Mispricing = {mispricing_pct}%, Signal = {signal.value}, "
            f"Leverage = {leverage_mult}x."
        )

        logger.info(notes)

        return SSFFairValueResult(
            symbol=spec.symbol,
            underlying_spot_price=spot_price,
            market_ssf_price=market_ssf_price,
            theoretical_fair_value=theoretical_fair_val,
            mispricing_amount=mispricing,
            mispricing_pct=mispricing_pct,
            arbitrage_signal=signal,
            initial_margin_ssf_usd=ssf_margin_usd,
            initial_margin_spot_usd=spot_margin_usd,
            leverage_multiplier=leverage_mult,
            audit_notes=notes
        )

    def calculate_ex_dividend_price_adjustment(
        self,
        previous_settlement_price: float,
        dividend_amount: float
    ) -> float:
        """
        Calculates adjusted SSF base price on ex-dividend date (NSE / Eurex corporate action rules).
        Adjusted Base Price = Previous Settlement Price - Dividend Amount
        """
        adjusted_price = round(max(0.01, previous_settlement_price - dividend_amount), 2)
        logger.info(f"EX-DIVIDEND ADJUSTMENT: Prev Settlement ${previous_settlement_price} - Div ${dividend_amount} = ${adjusted_price}.")
        return adjusted_price
