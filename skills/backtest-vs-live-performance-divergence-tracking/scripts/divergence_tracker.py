"""
backtest-vs-live-performance-divergence-tracking: Paired metric snapshot comparator,
divergence scorer, severity classifier, and structured alert generator.
"""
from dataclasses import dataclass
import logging
from enum import Enum
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class DivergenceSeverity(Enum):
    ACCEPTABLE = "ACCEPTABLE"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


@dataclass
class PerformanceSnapshot:
    sharpe_ratio: float
    max_drawdown_pct: float
    win_rate_pct: float
    fill_rate_pct: float
    avg_slippage_bps: float


@dataclass
class DivergenceMetric:
    name: str
    backtest_value: float
    live_value: float
    divergence_pct: float
    threshold_warning: float
    threshold_critical: float
    severity: DivergenceSeverity


@dataclass
class DivergenceReport:
    strategy_name: str
    metrics: List[DivergenceMetric]
    overall_severity: DivergenceSeverity
    is_suspension_recommended: bool
    message: str


class BacktestLiveDivergenceTracker:
    """
    Tracks and classifies divergence between backtested and live trading performance
    across Sharpe, drawdown, fill rate, and slippage dimensions.
    """

    def __init__(
        self,
        sharpe_warning_pct: float = 20.0,
        sharpe_critical_pct: float = 50.0,
        drawdown_warning_multiplier: float = 1.5,
        drawdown_critical_multiplier: float = 2.0,
        win_rate_warning_decay_pct: float = 10.0,
        win_rate_critical_decay_pct: float = 25.0,
        fill_rate_warning_gap_pct: float = 5.0,
        fill_rate_critical_gap_pct: float = 15.0,
        slippage_warning_multiplier: float = 2.0,
        slippage_critical_multiplier: float = 4.0,
    ):
        self.sharpe_warn = sharpe_warning_pct
        self.sharpe_crit = sharpe_critical_pct
        self.dd_warn_mult = drawdown_warning_multiplier
        self.dd_crit_mult = drawdown_critical_multiplier
        self.wr_warn = win_rate_warning_decay_pct
        self.wr_crit = win_rate_critical_decay_pct
        self.fill_warn = fill_rate_warning_gap_pct
        self.fill_crit = fill_rate_critical_gap_pct
        self.slip_warn_mult = slippage_warning_multiplier
        self.slip_crit_mult = slippage_critical_multiplier

    def _classify(self, value: float, warn_thresh: float, crit_thresh: float) -> DivergenceSeverity:
        if value >= crit_thresh:
            return DivergenceSeverity.CRITICAL
        elif value >= warn_thresh:
            return DivergenceSeverity.WARNING
        return DivergenceSeverity.ACCEPTABLE

    def evaluate_divergence(
        self,
        strategy_name: str,
        backtest: PerformanceSnapshot,
        live: PerformanceSnapshot,
    ) -> DivergenceReport:
        """
        Compares backtest vs live snapshots and produces a classified divergence report.
        """
        metrics: List[DivergenceMetric] = []

        # 1. Sharpe Ratio Decay
        if backtest.sharpe_ratio > 0:
            sharpe_decay_pct = max(0.0, ((backtest.sharpe_ratio - live.sharpe_ratio) / backtest.sharpe_ratio) * 100.0)
        else:
            sharpe_decay_pct = 0.0
        severity = self._classify(sharpe_decay_pct, self.sharpe_warn, self.sharpe_crit)
        metrics.append(DivergenceMetric(
            name="Sharpe Decay",
            backtest_value=backtest.sharpe_ratio,
            live_value=live.sharpe_ratio,
            divergence_pct=round(sharpe_decay_pct, 2),
            threshold_warning=self.sharpe_warn,
            threshold_critical=self.sharpe_crit,
            severity=severity,
        ))

        # 2. Drawdown Blow-Up
        if backtest.max_drawdown_pct > 0:
            dd_ratio = live.max_drawdown_pct / backtest.max_drawdown_pct
        else:
            dd_ratio = 1.0
        dd_div_pct = (dd_ratio - 1.0) * 100.0
        severity = self._classify(dd_ratio, self.dd_warn_mult, self.dd_crit_mult)
        metrics.append(DivergenceMetric(
            name="Drawdown Blow-Up",
            backtest_value=backtest.max_drawdown_pct,
            live_value=live.max_drawdown_pct,
            divergence_pct=round(dd_div_pct, 2),
            threshold_warning=self.dd_warn_mult,
            threshold_critical=self.dd_crit_mult,
            severity=severity,
        ))

        # 2.5 Win Rate Decay (Concept Drift)
        if backtest.win_rate_pct > 0:
            wr_decay_pct = max(0.0, ((backtest.win_rate_pct - live.win_rate_pct) / backtest.win_rate_pct) * 100.0)
        else:
            wr_decay_pct = 0.0
        severity = self._classify(wr_decay_pct, self.wr_warn, self.wr_crit)
        metrics.append(DivergenceMetric(
            name="Win Rate Decay",
            backtest_value=backtest.win_rate_pct,
            live_value=live.win_rate_pct,
            divergence_pct=round(wr_decay_pct, 2),
            threshold_warning=self.wr_warn,
            threshold_critical=self.wr_crit,
            severity=severity,
        ))

        # 3. Fill Rate Gap
        fill_gap = max(0.0, backtest.fill_rate_pct - live.fill_rate_pct)
        severity = self._classify(fill_gap, self.fill_warn, self.fill_crit)
        metrics.append(DivergenceMetric(
            name="Fill Rate Gap",
            backtest_value=backtest.fill_rate_pct,
            live_value=live.fill_rate_pct,
            divergence_pct=round(fill_gap, 2),
            threshold_warning=self.fill_warn,
            threshold_critical=self.fill_crit,
            severity=severity,
        ))

        # 4. Slippage Amplification
        if backtest.avg_slippage_bps > 0:
            slip_ratio = live.avg_slippage_bps / backtest.avg_slippage_bps
        else:
            slip_ratio = 1.0
        slip_div_pct = (slip_ratio - 1.0) * 100.0
        severity = self._classify(slip_ratio, self.slip_warn_mult, self.slip_crit_mult)
        metrics.append(DivergenceMetric(
            name="Slippage Amplification",
            backtest_value=backtest.avg_slippage_bps,
            live_value=live.avg_slippage_bps,
            divergence_pct=round(slip_div_pct, 2),
            threshold_warning=self.slip_warn_mult,
            threshold_critical=self.slip_crit_mult,
            severity=severity,
        ))

        # Overall severity = worst individual severity
        severity_order = {DivergenceSeverity.ACCEPTABLE: 0, DivergenceSeverity.WARNING: 1, DivergenceSeverity.CRITICAL: 2}
        overall = max(metrics, key=lambda m: severity_order[m.severity]).severity
        suspend = (overall == DivergenceSeverity.CRITICAL)

        if suspend:
            msg = f"CRITICAL DIVERGENCE for '{strategy_name}': Strategy suspension recommended."
            logger.error(msg)
        elif overall == DivergenceSeverity.WARNING:
            msg = f"WARNING DIVERGENCE for '{strategy_name}': Review recommended."
            logger.warning(msg)
        else:
            msg = f"Divergence ACCEPTABLE for '{strategy_name}'."
            logger.info(msg)

        return DivergenceReport(
            strategy_name=strategy_name,
            metrics=metrics,
            overall_severity=overall,
            is_suspension_recommended=suspend,
            message=msg,
        )
