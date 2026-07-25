"""
multi-year-regime-coverage-requirement: Market regime classifier, multi-year coverage auditor,
and de-averaged regime performance breakdown engine.
"""
from dataclasses import dataclass, field
import logging
import math
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class MarketRegime(Enum):
    BULL_TREND = "BULL_TREND"
    BEAR_MARKET = "BEAR_MARKET"
    HIGH_VOLATILITY_CRASH = "HIGH_VOLATILITY_CRASH"
    LOW_VOLATILITY_RANGE = "LOW_VOLATILITY_RANGE"


@dataclass
class RegimePerformanceMetrics:
    regime: MarketRegime
    bars_count: int
    total_return_pct: float
    sharpe_ratio: float
    max_drawdown_pct: float
    win_rate_pct: float


@dataclass
class RegimeCoverageAuditReport:
    total_years: float
    unique_regimes_covered: List[MarketRegime]
    regime_metrics: Dict[str, RegimePerformanceMetrics]
    is_coverage_sufficient: bool
    is_promotable: bool
    message: str


class MarketRegimeCoverageEngine:
    """
    Classifies market regimes (Bull, Bear, High Vol Crash, Low Vol Range), audits multi-year
    regime coverage, and evaluates de-averaged performance metrics.
    """

    def __init__(
        self,
        min_required_years: float = 3.0,
        min_required_regimes: int = 3,
        max_allowed_regime_drawdown_pct: float = 25.0,
    ):
        self.min_required_years = min_required_years
        self.min_required_regimes = min_required_regimes
        self.max_allowed_regime_drawdown_pct = max_allowed_regime_drawdown_pct

    def classify_regimes(self, prices: List[float], window_size: int = 20) -> List[MarketRegime]:
        """
        Classifies each bar into a MarketRegime based on rolling return trend and volatility.
        """
        n = len(prices)
        if n < window_size:
            return [MarketRegime.LOW_VOLATILITY_RANGE] * n

        regimes: List[MarketRegime] = []

        for i in range(n):
            if i < window_size:
                regimes.append(MarketRegime.LOW_VOLATILITY_RANGE)
                continue

            sub = prices[i - window_size : i + 1]
            rets = [(sub[j] - sub[j - 1]) / sub[j - 1] for j in range(1, len(sub))]
            mean_r = sum(rets) / len(rets)
            variance = sum((r - mean_r) ** 2 for r in rets) / len(rets)
            std_r = math.sqrt(variance)
            ann_vol = std_r * math.sqrt(252)

            price_change_pct = (sub[-1] - sub[0]) / sub[0]

            if ann_vol > 0.35:
                regimes.append(MarketRegime.HIGH_VOLATILITY_CRASH)
            elif price_change_pct > 0.03:
                regimes.append(MarketRegime.BULL_TREND)
            elif price_change_pct < -0.03:
                regimes.append(MarketRegime.BEAR_MARKET)
            else:
                regimes.append(MarketRegime.LOW_VOLATILITY_RANGE)

        return regimes

    def audit_coverage(
        self,
        prices: List[float],
        strategy_returns: List[float],
    ) -> RegimeCoverageAuditReport:
        """
        Audits multi-year regime coverage and computes de-averaged performance metrics per regime.
        """
        n = len(prices)
        total_years = round(n / 252.0, 2)
        regimes = self.classify_regimes(prices)

        # Group strategy returns by regime
        regime_data: Dict[MarketRegime, List[float]] = {m: [] for m in MarketRegime}

        for i in range(min(n, len(strategy_returns))):
            reg = regimes[i]
            regime_data[reg].append(strategy_returns[i])

        metrics_map: Dict[str, RegimePerformanceMetrics] = {}
        unique_covered: List[MarketRegime] = []
        is_promotable = True

        for reg, rets in regime_data.items():
            if not rets:
                continue

            unique_covered.append(reg)
            bars_cnt = len(rets)
            cum_ret = sum(rets)
            wins = sum(1 for r in rets if r > 0)
            win_rate = (wins / float(bars_cnt)) * 100.0 if bars_cnt > 0 else 0.0

            mean_r = cum_ret / float(bars_cnt)
            std_r = math.sqrt(sum((r - mean_r) ** 2 for r in rets) / float(bars_cnt)) if bars_cnt > 1 else 0.0001
            sharpe = (mean_r / (std_r or 0.0001)) * math.sqrt(252)

            # Max drawdown calculation
            peak = 1.0
            equity = 1.0
            max_dd = 0.0
            for r in rets:
                equity *= (1.0 + r)
                if equity > peak:
                    peak = equity
                dd = (peak - equity) / peak
                if dd > max_dd:
                    max_dd = dd

            max_dd_pct = round(max_dd * 100.0, 2)
            if max_dd_pct > self.max_allowed_regime_drawdown_pct:
                is_promotable = False
                logger.warning(
                    f"REGIME VETO: Strategy max drawdown {max_dd_pct}% in '{reg.value}' "
                    f"exceeds limit {self.max_allowed_regime_drawdown_pct}%."
                )

            metrics_map[reg.value] = RegimePerformanceMetrics(
                regime=reg,
                bars_count=bars_cnt,
                total_return_pct=round(cum_ret * 100.0, 2),
                sharpe_ratio=round(sharpe, 2),
                max_drawdown_pct=max_dd_pct,
                win_rate_pct=round(win_rate, 2),
            )

        is_coverage_ok = (total_years >= self.min_required_years) and (len(unique_covered) >= self.min_required_regimes)

        reasons = []
        if not is_coverage_ok:
            is_promotable = False
            reasons.append(f"Insufficient coverage ({total_years} yrs, {len(unique_covered)} regimes).")
        if not is_promotable:
            reasons.append("REGIME VETO: Exceeded max drawdown threshold in one or more regimes.")

        if reasons:
            msg = " | ".join(reasons)
        else:
            msg = f"Regime coverage verified across {total_years} years and {len(unique_covered)} regimes. Strategy promotable!"
            logger.info(msg)

        return RegimeCoverageAuditReport(
            total_years=total_years,
            unique_regimes_covered=unique_covered,
            regime_metrics=metrics_map,
            is_coverage_sufficient=is_coverage_ok,
            is_promotable=is_promotable,
            message=msg,
        )
