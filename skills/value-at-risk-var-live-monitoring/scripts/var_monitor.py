"""
value-at-risk-var-live-monitoring: Production-grade real-time Parametric VaR, Historical Simulation VaR,
Conditional VaR (CVaR / Expected Shortfall) monitor, and risk circuit breaker.
"""
from dataclasses import dataclass, field
import logging
import math
import statistics
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class VaRMonitorError(ValueError):
    """Raised when returns history or position data is invalid."""
    pass


@dataclass
class VaRMetrics:
    confidence_level: float
    parametric_var_usd: float
    parametric_var_pct: float
    historical_var_usd: float
    historical_var_pct: float
    cvar_usd: float
    cvar_pct: float
    is_breached: bool


@dataclass
class LiveRiskStatus:
    approved: bool
    var_metrics: VaRMetrics
    breach_reason: Optional[str]


class LiveValueAtRiskMonitor:
    """
    Computes real-time 1-day Parametric VaR, Historical Simulation VaR, and Conditional VaR (CVaR)
    across active portfolio holdings and triggers entry order vetoes upon VaR limit breaches.
    """

    def __init__(
        self,
        confidence_level: float = 0.99,
        var_limit_pct: float = 0.05,
    ):
        self.confidence_level = confidence_level
        self.var_limit_pct = var_limit_pct
        # Z-score lookup for common confidence levels
        self.z_table = {0.90: 1.282, 0.95: 1.645, 0.99: 2.326}

    def compute_var_metrics(
        self,
        positions: Dict[str, float],      # symbol -> quantity
        prices: Dict[str, float],         # symbol -> price USD
        returns_dict: Dict[str, List[float]],  # symbol -> daily return history
        portfolio_nav: float,
    ) -> VaRMetrics:
        """
        Calculates Parametric VaR, Historical VaR, and CVaR for current portfolio positions.
        """
        if portfolio_nav <= 0:
            raise VaRMonitorError(f"Invalid portfolio NAV: ${portfolio_nav}")

        symbols = [s for s, q in positions.items() if abs(q) > 0 and s in prices]
        if not symbols:
            return VaRMetrics(
                confidence_level=self.confidence_level,
                parametric_var_usd=0.0,
                parametric_var_pct=0.0,
                historical_var_usd=0.0,
                historical_var_pct=0.0,
                cvar_usd=0.0,
                cvar_pct=0.0,
                is_breached=False,
            )

        # 1. Compute position weights
        weights: Dict[str, float] = {}
        for s in symbols:
            val = positions[s] * prices[s]
            weights[s] = val / portfolio_nav

        # 2. Reconstruct historical portfolio return distribution
        n_history = min(len(returns_dict[s]) for s in symbols)
        if n_history < 10:
            raise VaRMonitorError("Insufficient return history for VaR estimation. Min 10 bars required.")

        port_returns: List[float] = []
        for t in range(n_history):
            r_t = sum(weights[s] * returns_dict[s][t] for s in symbols)
            port_returns.append(r_t)

        # 3. Parametric VaR Calculation
        mean_ret = statistics.mean(port_returns)
        std_ret = max(statistics.stdev(port_returns), 1e-6)
        z_val = self.z_table.get(self.confidence_level, 2.326)

        param_var_pct = max(z_val * std_ret - mean_ret, 0.0)
        param_var_usd = param_var_pct * portfolio_nav

        # 4. Historical Simulation VaR Calculation
        sorted_returns = sorted(port_returns)
        quantile_idx = int((1.0 - self.confidence_level) * len(sorted_returns))
        hist_var_pct = max(-sorted_returns[quantile_idx], 0.0)
        hist_var_usd = hist_var_pct * portfolio_nav

        # 5. Conditional VaR (CVaR / Expected Shortfall) Calculation
        tail_returns = sorted_returns[: quantile_idx + 1]
        cvar_pct = max(-statistics.mean(tail_returns) if tail_returns else hist_var_pct, 0.0)
        cvar_usd = cvar_pct * portfolio_nav

        is_breached = (param_var_pct >= self.var_limit_pct) or (hist_var_pct >= self.var_limit_pct)

        logger.info(
            f"Live VaR ({self.confidence_level:.0%}): Parametric: {param_var_pct:.2%} (${param_var_usd:,.2f}), "
            f"Hist: {hist_var_pct:.2%} (${hist_var_usd:,.2f}), CVaR: {cvar_pct:.2%} (${cvar_usd:,.2f}) | Breached: {is_breached}"
        )

        return VaRMetrics(
            confidence_level=self.confidence_level,
            parametric_var_usd=param_var_usd,
            parametric_var_pct=param_var_pct,
            historical_var_usd=hist_var_usd,
            historical_var_pct=hist_var_pct,
            cvar_usd=cvar_usd,
            cvar_pct=cvar_pct,
            is_breached=is_breached,
        )

    def evaluate_live_risk(
        self,
        positions: Dict[str, float],
        prices: Dict[str, float],
        returns_dict: Dict[str, List[float]],
        portfolio_nav: float,
    ) -> LiveRiskStatus:
        """Evaluates live portfolio VaR and returns approval status for new trades."""
        metrics = self.compute_var_metrics(positions, prices, returns_dict, portfolio_nav)

        if metrics.is_breached:
            reason = (
                f"LIVE VAR BREACH! 1-Day VaR ({metrics.parametric_var_pct:.2%}) >= limit ({self.var_limit_pct:.2%}). "
                f"Blocking new position entries."
            )
            logger.warning(reason)
            return LiveRiskStatus(approved=False, var_metrics=metrics, breach_reason=reason)

        return LiveRiskStatus(approved=True, var_metrics=metrics, breach_reason=None)
