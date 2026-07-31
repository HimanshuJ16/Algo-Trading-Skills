import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

@dataclass
class Configuration:
    enabled: bool = True
    threshold: float = 0.5

@dataclass
class OptionPosition:
    symbol: str
    underlying_symbol: str
    position_qty: int                    # +10 for long 10 contracts, -10 for short 10 contracts
    multiplier: int = 100                # Contract multiplier (100 for equity options)
    spot_price: float = 100.0
    delta: float = 0.50
    gamma: float = 0.02
    theta: float = -0.05
    vega: float = 0.10

@dataclass
class PortfolioGreeksLimits:
    max_dollar_delta_usd: float = 500000.0
    max_negative_theta_usd: float = -5000.0
    max_vega_usd: float = 10000.0

@dataclass
class PortfolioGreeksReport:
    total_positions: int
    net_delta_shares: float
    net_dollar_delta_usd: float
    net_gamma: float
    net_theta_daily_usd: float
    net_vega_usd: float
    by_underlying: Dict[str, Dict[str, float]]
    status: str                          # 'PORTFOLIO_GREEKS_HEALTHY', 'DOLLAR_DELTA_BREACH', 'THETA_LIMIT_BREACH', 'VEGA_LIMIT_BREACH'
    audit_notes: str

class OptionsGreeksRealTimePortfolioAggregationEngine:
    """
    Real-time options portfolio Greeks aggregation engine computing net portfolio Delta, Dollar Delta, Gamma, Theta,
    and Vega across multi-leg positions and auditing risk exposure limits.
    """
    def __init__(
        self,
        config: Optional[Configuration] = None,
        limits: Optional[PortfolioGreeksLimits] = None
    ):
        self.config = config or Configuration()
        self.limits = limits or PortfolioGreeksLimits()

    def execute(self, data: dict) -> dict:
        """
        Legacy execution method for backward compatibility.
        """
        if not self.config.enabled:
            return {"status": "disabled"}
        return {
            "status": "success",
            "processed": True,
            "value": data.get("value", 0) * self.config.threshold
        }

    def aggregate_portfolio_greeks(
        self, positions: List[OptionPosition]
    ) -> PortfolioGreeksReport:
        """
        Aggregates net portfolio Greeks, computes Dollar Delta, and audits risk limits.
        """
        if not self.config.enabled:
            return PortfolioGreeksReport(
                total_positions=0,
                net_delta_shares=0.0,
                net_dollar_delta_usd=0.0,
                net_gamma=0.0,
                net_theta_daily_usd=0.0,
                net_vega_usd=0.0,
                by_underlying={},
                status="ENGINE_DISABLED",
                audit_notes="Engine is disabled."
            )

        net_delta = 0.0
        net_dollar_delta = 0.0
        net_gamma = 0.0
        net_theta = 0.0
        net_vega = 0.0

        by_underlying: Dict[str, Dict[str, float]] = {}

        for pos in positions:
            scaled_qty = pos.position_qty * pos.multiplier
            pos_delta = scaled_qty * pos.delta
            pos_dollar_delta = pos_delta * pos.spot_price
            pos_gamma = scaled_qty * pos.gamma
            pos_theta = scaled_qty * pos.theta
            pos_vega = scaled_qty * pos.vega

            net_delta += pos_delta
            net_dollar_delta += pos_dollar_delta
            net_gamma += pos_gamma
            net_theta += pos_theta
            net_vega += pos_vega

            und = pos.underlying_symbol.upper()
            if und not in by_underlying:
                by_underlying[und] = {
                    "net_delta": 0.0, "net_dollar_delta": 0.0,
                    "net_gamma": 0.0, "net_theta": 0.0, "net_vega": 0.0
                }

            by_underlying[und]["net_delta"] += pos_delta
            by_underlying[und]["net_dollar_delta"] += pos_dollar_delta
            by_underlying[und]["net_gamma"] += pos_gamma
            by_underlying[und]["net_theta"] += pos_theta
            by_underlying[und]["net_vega"] += pos_vega

        # Round underlying aggregations
        for und in by_underlying:
            for k in by_underlying[und]:
                by_underlying[und][k] = round(by_underlying[und][k], 2)

        # Audit Risk Limits
        status = "PORTFOLIO_GREEKS_HEALTHY"
        if abs(net_dollar_delta) > self.limits.max_dollar_delta_usd:
            status = "DOLLAR_DELTA_BREACH"
        elif net_theta < self.limits.max_negative_theta_usd:
            status = "THETA_LIMIT_BREACH"
        elif abs(net_vega) > self.limits.max_vega_usd:
            status = "VEGA_LIMIT_BREACH"

        notes = (
            f"PORTFOLIO GREEKS AGGREGATION [{status}]: Net Delta = {net_delta:+,.1f} shares, "
            f"Dollar Delta = ${net_dollar_delta:+,.2f}, Net Gamma = {net_gamma:+.2f}, "
            f"Daily Theta = ${net_theta:+,.2f}/day, Net Vega = ${net_vega:+,.2f}/vol pt. "
            f"Positions = {len(positions)} across {len(by_underlying)} underlyings."
        )

        if status != "PORTFOLIO_GREEKS_HEALTHY":
            logger.warning(f"GREEKS LIMIT BREACH: {notes}")
        else:
            logger.info(notes)

        return PortfolioGreeksReport(
            total_positions=len(positions),
            net_delta_shares=round(net_delta, 2),
            net_dollar_delta_usd=round(net_dollar_delta, 2),
            net_gamma=round(net_gamma, 4),
            net_theta_daily_usd=round(net_theta, 2),
            net_vega_usd=round(net_vega, 2),
            by_underlying=by_underlying,
            status=status,
            audit_notes=notes
        )
