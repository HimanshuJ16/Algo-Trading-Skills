import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Asset Classes: 'EQUITY', 'CRYPTO', 'FX', 'FUTURES', 'OPTION'

@dataclass
class PositionSpec:
    symbol: str                         # e.g. 'BTC-PERP', 'AAPL', 'EURUSD'
    asset_class: str                    # 'EQUITY', 'CRYPTO', 'FX', 'FUTURES'
    side: str                           # 'BUY' (Long) or 'SELL' (Short)
    notional_usd: float                 # Position Notional USD

@dataclass
class ProposedOrderSpec:
    symbol: str
    asset_class: str                    # 'EQUITY', 'CRYPTO', 'FX', 'FUTURES'
    side: str                           # 'BUY' or 'SELL'
    order_notional_usd: float

@dataclass
class LeverageEnforcementReport:
    current_gross_leverage: float
    current_net_leverage: float
    projected_gross_leverage: float
    projected_net_leverage: float
    projected_asset_class_leverage: float
    is_gross_limit_passed: bool
    is_net_limit_passed: bool
    is_asset_class_limit_passed: bool
    status: str                         # 'ORDER_LEVERAGE_APPROVED', 'REJECTED_GROSS_LEVERAGE_BREACH', 'REJECTED_NET_LEVERAGE_BREACH', 'REJECTED_ASSET_CLASS_LEVERAGE_BREACH'
    audit_notes: str

class LeverageLimitEnforcerEngine:
    """
    Pre-trade risk gateway enforcing global Gross Leverage, Net Directional Leverage,
    and asset-class specific leverage caps across multi-instrument portfolios (Equities, Crypto, FX, Futures).
    """
    def __init__(
        self,
        max_gross_leverage: float = 3.0,
        max_net_leverage: float = 1.5,
        asset_class_limits: Optional[Dict[str, float]] = None
    ):
        self.max_gross_leverage = max_gross_leverage
        self.max_net_leverage = max_net_leverage
        self.asset_class_limits = asset_class_limits or {
            "EQUITY": 2.0,      # Reg T 50% Margin limit
            "CRYPTO": 3.0,      # Crypto volatility cap
            "FX": 10.0,         # G10 FX pair cap
            "FUTURES": 5.0
        }

    def compute_exposures(
        self,
        equity_usd: float,
        positions: List[PositionSpec]
    ) -> Tuple[float, float, Dict[str, float]]:
        """
        Computes Gross Notional, Net Signed Notional, and per-asset-class notionals.
        """
        gross_notional = sum(abs(p.notional_usd) for p in positions)

        signed_notional = sum(
            p.notional_usd if p.side.upper() == "BUY" else -p.notional_usd
            for p in positions
        )
        net_notional = abs(signed_notional)

        asset_notionals: Dict[str, float] = {}
        for p in positions:
            ac = p.asset_class.upper()
            asset_notionals[ac] = asset_notionals.get(ac, 0.0) + abs(p.notional_usd)

        return gross_notional, net_notional, asset_notionals

    def audit_proposed_order(
        self,
        portfolio_equity_usd: float,
        current_positions: List[PositionSpec],
        proposed_order: ProposedOrderSpec
    ) -> LeverageEnforcementReport:
        """
        Projects resulting Gross and Net leverage ratios with proposed order and enforces pre-trade limits.
        """
        if portfolio_equity_usd <= 0.0:
            raise ValueError(f"Portfolio Equity (${portfolio_equity_usd:,.2f}) must be strictly positive.")

        # 1. Current Exposures
        cur_gross, cur_net, _ = self.compute_exposures(portfolio_equity_usd, current_positions)
        cur_gross_lev = round(cur_gross / portfolio_equity_usd, 2)
        cur_net_lev = round(cur_net / portfolio_equity_usd, 2)

        # 2. Simulate Projected Positions with Proposed Order
        simulated_positions = list(current_positions)
        simulated_positions.append(
            PositionSpec(
                symbol=proposed_order.symbol,
                asset_class=proposed_order.asset_class,
                side=proposed_order.side,
                notional_usd=proposed_order.order_notional_usd
            )
        )

        proj_gross, proj_net, proj_asset_notionals = self.compute_exposures(
            portfolio_equity_usd, simulated_positions
        )

        proj_gross_lev = round(proj_gross / portfolio_equity_usd, 2)
        proj_net_lev = round(proj_net / portfolio_equity_usd, 2)

        ac_target = proposed_order.asset_class.upper()
        ac_notional = proj_asset_notionals.get(ac_target, 0.0)
        proj_ac_lev = round(ac_notional / portfolio_equity_usd, 2)

        ac_max_limit = self.asset_class_limits.get(ac_target, 2.0)

        # 3. Enforce Pre-Trade Veto Rules
        is_gross_ok = (proj_gross_lev <= self.max_gross_leverage)
        is_net_ok = (proj_net_lev <= self.max_net_leverage)
        is_ac_ok = (proj_ac_lev <= ac_max_limit)

        if not is_gross_ok:
            status = "REJECTED_GROSS_LEVERAGE_BREACH"
            notes = (
                f"LEVERAGE VETO [{proposed_order.symbol}]: Projected Gross Leverage {proj_gross_lev:.2f}x "
                f"exceeds max limit ({self.max_gross_leverage:.2f}x)! Order rejected."
            )
            logger.warning(notes)
        elif not is_net_ok:
            status = "REJECTED_NET_LEVERAGE_BREACH"
            notes = (
                f"LEVERAGE VETO [{proposed_order.symbol}]: Projected Net Leverage {proj_net_lev:.2f}x "
                f"exceeds max limit ({self.max_net_leverage:.2f}x)! Order rejected."
            )
            logger.warning(notes)
        elif not is_ac_ok:
            status = "REJECTED_ASSET_CLASS_LEVERAGE_BREACH"
            notes = (
                f"LEVERAGE VETO [{proposed_order.symbol} ({ac_target})]: Projected {ac_target} Leverage {proj_ac_lev:.2f}x "
                f"exceeds asset class limit ({ac_max_limit:.2f}x)! Order rejected."
            )
            logger.warning(notes)
        else:
            status = "ORDER_LEVERAGE_APPROVED"
            notes = (
                f"LEVERAGE APPROVED [{proposed_order.symbol}]: Projected Gross Lev = {proj_gross_lev:.2f}x "
                f"(Limit {self.max_gross_leverage:.2f}x), Net Lev = {proj_net_lev:.2f}x (Limit {self.max_net_leverage:.2f}x), "
                f"{ac_target} Lev = {proj_ac_lev:.2f}x (Limit {ac_max_limit:.2f}x)."
            )
            logger.info(notes)

        return LeverageEnforcementReport(
            current_gross_leverage=cur_gross_lev,
            current_net_leverage=cur_net_lev,
            projected_gross_leverage=proj_gross_lev,
            projected_net_leverage=proj_net_lev,
            projected_asset_class_leverage=proj_ac_lev,
            is_gross_limit_passed=is_gross_ok,
            is_net_limit_passed=is_net_ok,
            is_asset_class_limit_passed=is_ac_ok,
            status=status,
            audit_notes=notes
        )
