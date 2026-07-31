import logging
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any

logger = logging.getLogger(__name__)

def norm_cdf(x: float) -> float:
    """Standard normal cumulative distribution function."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

def norm_pdf(x: float) -> float:
    """Standard normal probability density function."""
    return (1.0 / math.sqrt(2.0 * math.pi)) * math.exp(-0.5 * x * x)

@dataclass
class RealTimeGreeksRecalculatorConfig:
    enabled: bool = True
    parameters: Dict[str, float] = field(default_factory=dict)
    full_recalc_threshold_pct: float = 0.005  # 0.5% spot price change threshold for full BS

@dataclass
class OptionPosition:
    symbol: str
    underlying_symbol: str
    option_type: str                     # 'CALL', 'PUT'
    strike: float
    expiry_years: float
    spot: float
    iv: float                            # Implied Volatility (e.g. 0.20 for 20%)
    position_size: float                 # e.g. +10 contracts (100 shares each)
    risk_free_rate: float = 0.05
    last_delta: float = 0.0
    last_gamma: float = 0.0
    last_vega: float = 0.0

@dataclass
class SingleOptionGreeksResult:
    symbol: str
    spot: float
    delta: float
    gamma: float
    vega: float
    recalc_method: str                   # 'TAYLOR_EXPANSION', 'FULL_BLACK_SCHOLES'

@dataclass
class RealTimeGreeksReport:
    underlying_symbol: str
    new_spot_price: float
    price_change_pct: float
    recalc_method: str                   # 'TAYLOR_EXPANSION_UPDATE', 'FULL_BS_RECALCULATION'
    portfolio_net_delta: float
    portfolio_net_gamma: float
    portfolio_net_vega: float
    option_results: List[SingleOptionGreeksResult]
    status: str                          # 'GREEKS_RECALCULATED_SUCCESS'
    audit_notes: str

class RealTimeGreeksRecalculator:
    """
    Event-driven options portfolio Greeks recalculation engine utilizing Taylor series expansion for micro-ticks
    and triggering full Black-Scholes revaluation upon price threshold breaches.
    """
    def __init__(self, config: RealTimeGreeksRecalculatorConfig):
        self.config = config

    def execute(self, data: Dict) -> bool:
        """
        Legacy execute method retained for backward compatibility.
        """
        if not self.config.enabled:
            return False
        return True

    def _calc_black_scholes_greeks(self, pos: OptionPosition, new_spot: float) -> Tuple[float, float, float]:
        """Calculates full Black-Scholes Delta, Gamma, Vega."""
        S = new_spot
        K = pos.strike
        T = max(0.0001, pos.expiry_years)
        r = pos.risk_free_rate
        sigma = max(0.001, pos.iv)

        d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))

        if pos.option_type.upper() == "CALL":
            delta = norm_cdf(d1)
        else: # PUT
            delta = norm_cdf(d1) - 1.0

        gamma = norm_pdf(d1) / (S * sigma * math.sqrt(T))
        vega = S * norm_pdf(d1) * math.sqrt(T) / 100.0 # Per 1% vol

        return delta, gamma, vega

    def recalculate_portfolio_greeks(
        self,
        underlying_symbol: str,
        new_spot_price: float,
        positions: List[OptionPosition]
    ) -> RealTimeGreeksReport:
        """
        Evaluates underlying spot movement, selecting Taylor Series for small ticks or Full BS for large moves.
        """
        if not self.config.enabled:
            return RealTimeGreeksReport(
                underlying_symbol=underlying_symbol,
                new_spot_price=new_spot_price,
                price_change_pct=0.0,
                recalc_method="DISABLED",
                portfolio_net_delta=0.0,
                portfolio_net_gamma=0.0,
                portfolio_net_vega=0.0,
                option_results=[],
                status="ENGINE_DISABLED",
                audit_notes="Greeks recalculator engine is disabled."
            )

        if not positions:
            return RealTimeGreeksReport(
                underlying_symbol=underlying_symbol,
                new_spot_price=new_spot_price,
                price_change_pct=0.0,
                recalc_method="NONE",
                portfolio_net_delta=0.0,
                portfolio_net_gamma=0.0,
                portfolio_net_vega=0.0,
                option_results=[],
                status="NO_POSITIONS",
                audit_notes="No positions provided for Greeks recalculation."
            )

        # Baseline spot from first position
        base_spot = positions[0].spot
        delta_S = new_spot_price - base_spot
        price_change_pct = abs(delta_S / max(0.0001, base_spot))

        use_full_bs = price_change_pct > self.config.full_recalc_threshold_pct
        recalc_method = "FULL_BS_RECALCULATION" if use_full_bs else "TAYLOR_EXPANSION_UPDATE"

        option_results: List[SingleOptionGreeksResult] = []
        net_delta = 0.0
        net_gamma = 0.0
        net_vega = 0.0

        for pos in positions:
            multiplier = pos.position_size * 100.0 # 100 shares per option contract

            if use_full_bs or pos.last_gamma == 0.0:
                delta, gamma, vega = self._calc_black_scholes_greeks(pos, new_spot_price)
                pos_method = "FULL_BLACK_SCHOLES"
            else:
                # Taylor Series Expansion: Delta_new = Delta_0 + Gamma_0 * delta_S
                delta = pos.last_delta + (pos.last_gamma * delta_S)
                gamma = pos.last_gamma
                vega = pos.last_vega
                pos_method = "TAYLOR_EXPANSION"

            # Update position state
            pos.last_delta = delta
            pos.last_gamma = gamma
            pos.last_vega = vega
            pos.spot = new_spot_price

            net_delta += delta * multiplier
            net_gamma += gamma * multiplier
            net_vega += vega * multiplier

            option_results.append(
                SingleOptionGreeksResult(
                    symbol=pos.symbol,
                    spot=new_spot_price,
                    delta=round(delta, 4),
                    gamma=round(gamma, 6),
                    vega=round(vega, 4),
                    recalc_method=pos_method
                )
            )

        notes = (
            f"REAL-TIME GREEKS RECALCULATION [{underlying_symbol} @ ${new_spot_price:.2f} ({recalc_method})]: "
            f"Spot Shift = {delta_S:+.2f} ({price_change_pct*100.0:.2f}%), "
            f"Net Delta = {net_delta:+.2f}, Net Gamma = {net_gamma:+.4f}, Net Vega = {net_vega:+.2f}."
        )

        logger.info(notes)

        return RealTimeGreeksReport(
            underlying_symbol=underlying_symbol,
            new_spot_price=new_spot_price,
            price_change_pct=round(price_change_pct, 6),
            recalc_method=recalc_method,
            portfolio_net_delta=round(net_delta, 2),
            portfolio_net_gamma=round(net_gamma, 4),
            portfolio_net_vega=round(net_vega, 2),
            option_results=option_results,
            status="GREEKS_RECALCULATED_SUCCESS",
            audit_notes=notes
        )
