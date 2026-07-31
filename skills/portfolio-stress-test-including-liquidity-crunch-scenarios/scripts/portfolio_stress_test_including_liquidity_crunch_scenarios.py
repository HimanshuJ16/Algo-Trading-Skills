import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any

logger = logging.getLogger(__name__)

@dataclass
class Config:
    name: str = "Stress_Tester"
    max_allowed_dtl_days: float = 5.0    # Max 5 days to liquidate threshold

@dataclass
class PortfolioPosition:
    symbol: str
    quantity: float                      # + for Long, - for Short
    current_price: float
    adv_shares: float                    # Average Daily Volume in shares
    spread_bps: float = 5.0              # Normal bid-ask spread in bps

@dataclass
class StressScenario:
    scenario_name: str
    price_shock_pct: Dict[str, float]    # e.g. {'AAPL': -0.20, 'MSFT': -0.15}
    liquidity_drop_pct: float = 0.50     # 50% drop in market volume
    spread_expansion_factor: float = 5.0 # 5x spread widening

@dataclass
class StressTestReport:
    scenario_name: str
    total_portfolio_value_usd: float
    price_shock_loss_usd: float
    liquidity_slippage_haircut_usd: float
    total_stressed_loss_usd: float
    max_dtl_days: float
    illiquid_symbols: List[str]
    status: str                          # 'STRESS_TEST_PASSED', 'LIQUIDITY_CRUNCH_ILLIQUID_WARNING'
    audit_notes: str

class Engine:
    """
    Legacy Engine class retained for backward compatibility.
    """
    def __init__(self, config: Config):
        self.config = config

    def run(self) -> bool:
        return True

class PortfolioStressTestEngine:
    """
    Portfolio stress testing engine simulating macro price shocks combined with liquidity crunch scenarios,
    calculating Days-to-Liquidate (DTL) and liquidity slippage haircuts.
    """
    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config()

    def run_stress_test(
        self, positions: List[PortfolioPosition], scenario: StressScenario
    ) -> StressTestReport:
        """
        Executes stress test evaluating price shock losses, Days-to-Liquidate (DTL), and liquidity haircuts.
        """
        total_val = sum(abs(p.quantity) * p.current_price for p in positions)
        price_shock_loss = 0.0
        slippage_haircut = 0.0
        max_dtl = 0.0
        illiquid_symbols: List[str] = []

        for p in positions:
            abs_qty = abs(p.quantity)
            pos_val = abs_qty * p.current_price

            # 1. Price Shock Loss Calculation
            shock_pct = scenario.price_shock_pct.get(p.symbol, scenario.price_shock_pct.get("DEFAULT", -0.20))
            if p.quantity > 0:
                p_loss = pos_val * abs(shock_pct)
            else:
                p_loss = pos_val * abs(shock_pct) if shock_pct > 0 else 0.0
            price_shock_loss += p_loss

            # 2. Days-to-Liquidate (DTL) Calculation
            stressed_adv = max(1.0, p.adv_shares * (1.0 - scenario.liquidity_drop_pct))
            daily_trading_cap = 0.10 * stressed_adv  # Max 10% of volume per day
            dtl_days = abs_qty / daily_trading_cap
            max_dtl = max(max_dtl, dtl_days)

            if dtl_days > self.config.max_allowed_dtl_days:
                illiquid_symbols.append(p.symbol)

            # 3. Liquidity Slippage Haircut Calculation
            expanded_spread_pct = (p.spread_bps * scenario.spread_expansion_factor) / 10000.0
            haircut = pos_val * expanded_spread_pct * min(10.0, dtl_days)
            slippage_haircut += haircut

        total_stressed_loss = price_shock_loss + slippage_haircut
        is_illiquid_warning = len(illiquid_symbols) > 0 or max_dtl > self.config.max_allowed_dtl_days

        status = "LIQUIDITY_CRUNCH_ILLIQUID_WARNING" if is_illiquid_warning else "STRESS_TEST_PASSED"
        notes = (
            f"PORTFOLIO STRESS TEST [{scenario.scenario_name} - {status}]: Portfolio Val = ${total_val:,.2f}, "
            f"Price Shock Loss = ${price_shock_loss:,.2f}, Liquidity Slippage Haircut = ${slippage_haircut:,.2f}, "
            f"Total Stressed Loss = ${total_stressed_loss:,.2f} ({total_stressed_loss / max(1.0, total_val):.2%}). "
            f"Max DTL = {max_dtl:.1f} days, Illiquid Symbols = {illiquid_symbols}."
        )

        if is_illiquid_warning:
            logger.warning(f"LIQUIDITY CRUNCH WARNING: {notes}")
        else:
            logger.info(notes)

        return StressTestReport(
            scenario_name=scenario.scenario_name,
            total_portfolio_value_usd=round(total_val, 2),
            price_shock_loss_usd=round(price_shock_loss, 2),
            liquidity_slippage_haircut_usd=round(slippage_haircut, 2),
            total_stressed_loss_usd=round(total_stressed_loss, 2),
            max_dtl_days=round(max_dtl, 2),
            illiquid_symbols=illiquid_symbols,
            status=status,
            audit_notes=notes
        )
