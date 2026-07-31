import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any

logger = logging.getLogger(__name__)

@dataclass
class PortfolioLevelStopLossIndependentOfStrategyStopsConfig:
    enabled: bool = True
    max_daily_drawdown_pct: float = 0.05   # 5% max daily drawdown
    max_peak_drawdown_pct: float = 0.10    # 10% max peak-to-trough drawdown
    auto_flatten_on_breach: bool = True

@dataclass
class StrategyPosition:
    strategy_id: str
    symbol: str
    quantity: float
    current_price: float
    unrealized_pnl: float

@dataclass
class PortfolioState:
    start_of_day_equity: float
    peak_equity: float
    current_cash: float
    open_positions: List[StrategyPosition] = field(default_factory=list)

@dataclass
class PortfolioStopReport:
    current_nav: float
    daily_drawdown_pct: float
    peak_drawdown_pct: float
    is_daily_breached: bool
    is_peak_breached: bool
    is_trading_locked: bool
    positions_to_flatten_count: int
    status: str                          # 'PORTFOLIO_NAV_HEALTHY', 'DAILY_DRAWDOWN_BREACH_FLATTEN', 'PEAK_DRAWDOWN_BREACH_FLATTEN'
    audit_notes: str

class PortfolioLevelStopLossIndependentOfStrategyStops:
    """
    Independent portfolio-level stop-loss engine monitoring aggregated daily and peak-to-trough drawdowns,
    triggering emergency global position flattening independent of sub-strategy stops.
    """
    def __init__(
        self,
        config: Optional[PortfolioLevelStopLossIndependentOfStrategyStopsConfig] = None
    ):
        self.config = config or PortfolioLevelStopLossIndependentOfStrategyStopsConfig()

    def execute(self) -> bool:
        """
        Legacy execution method for backward compatibility.
        """
        return self.config.enabled

    def evaluate_portfolio_stop(
        self, state: PortfolioState
    ) -> PortfolioStopReport:
        """
        Evaluates portfolio NAV, daily drawdown %, and peak drawdown % to enforce independent stop-loss limits.
        """
        if not self.config.enabled:
            return PortfolioStopReport(
                current_nav=state.current_cash,
                daily_drawdown_pct=0.0,
                peak_drawdown_pct=0.0,
                is_daily_breached=False,
                is_peak_breached=False,
                is_trading_locked=False,
                positions_to_flatten_count=0,
                status="ENGINE_DISABLED",
                audit_notes="Engine is disabled."
            )

        # Calculate Aggregated Portfolio NAV
        position_market_val = sum(p.quantity * p.current_price for p in state.open_positions)
        current_nav = state.current_cash + position_market_val

        # Daily Drawdown = (Start_of_Day_Equity - Current_NAV) / Start_of_Day_Equity
        sod_eq = max(1.0, state.start_of_day_equity)
        daily_dd_pct = (sod_eq - current_nav) / sod_eq if current_nav < sod_eq else 0.0

        # Peak Drawdown = (Peak_Equity - Current_NAV) / Peak_Equity
        peak_eq = max(sod_eq, state.peak_equity)
        peak_dd_pct = (peak_eq - current_nav) / peak_eq if current_nav < peak_eq else 0.0

        is_daily_breached = daily_dd_pct >= self.config.max_daily_drawdown_pct
        is_peak_breached = peak_dd_pct >= self.config.max_peak_drawdown_pct

        is_trading_locked = is_daily_breached or is_peak_breached
        flatten_count = len(state.open_positions) if (is_trading_locked and self.config.auto_flatten_on_breach) else 0

        if is_daily_breached:
            status = "DAILY_DRAWDOWN_BREACH_FLATTEN"
        elif is_peak_breached:
            status = "PEAK_DRAWDOWN_BREACH_FLATTEN"
        else:
            status = "PORTFOLIO_NAV_HEALTHY"

        notes = (
            f"INDEPENDENT PORTFOLIO STOP EVALUATION [{status}]: Current NAV = ${current_nav:,.2f} "
            f"(SOD Equity = ${state.start_of_day_equity:,.2f}, Peak Equity = ${state.peak_equity:,.2f}). "
            f"Daily DD = {daily_dd_pct:.2%} (Limit = {self.config.max_daily_drawdown_pct:.2%}), "
            f"Peak DD = {peak_dd_pct:.2%} (Limit = {self.config.max_peak_drawdown_pct:.2%}). "
            f"Trading Lockout = {is_trading_locked}, Positions to Flatten = {flatten_count}."
        )

        if is_trading_locked:
            logger.critical(f"EMERGENCY PORTFOLIO STOP LOSS TRIGGERED: {notes}")
        else:
            logger.info(notes)

        return PortfolioStopReport(
            current_nav=round(current_nav, 2),
            daily_drawdown_pct=round(daily_dd_pct, 4),
            peak_drawdown_pct=round(peak_dd_pct, 4),
            is_daily_breached=is_daily_breached,
            is_peak_breached=is_peak_breached,
            is_trading_locked=is_trading_locked,
            positions_to_flatten_count=flatten_count,
            status=status,
            audit_notes=notes
        )
