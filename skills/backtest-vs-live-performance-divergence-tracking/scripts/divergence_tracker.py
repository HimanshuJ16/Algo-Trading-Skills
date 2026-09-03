"""
backtest-vs-live-performance-divergence-tracking: Paired metric snapshot comparator,
divergence scorer, severity classifier, and structured alert generator.

Safety contract
---------------
This module's output gates whether a strategy keeps trading real capital, so the one
property it must never violate is: **a comparison that cannot be made is never reported
as ACCEPTABLE.** Four separate paths used to violate that -- a negative drawdown sign
convention, a zero slippage baseline, a NaN live metric, and a non-positive backtest
Sharpe all silently produced `ACCEPTABLE` with `is_suspension_recommended=False`.

Every undefined comparison now escalates to WARNING and carries a `notes` string saying
why. Non-finite inputs are rejected outright rather than classified.

Units
-----
Metrics are compared on three different bases, and `DivergenceMetric.comparison_basis`
records which one applies:

  * ``relative decay %``     Sharpe, win rate: (backtest - live) / backtest * 100
  * ``ratio to backtest``    drawdown, slippage: live / backtest, as a multiplier
  * ``percentage-point gap`` fill rate: backtest - live, in points

`comparison_value` is the quantity actually classified, on the same scale as
`threshold_warning` and `threshold_critical`. `divergence_pct` is a human-readable
percentage and is **not** on the threshold scale for the ratio metrics -- read
`comparison_value` when comparing against a threshold.

Thresholds are inclusive: a value equal to a threshold classifies at that level. The
classified value is rounded before comparison so that the number in the report and the
verdict beside it can never contradict each other.
"""
from dataclasses import dataclass, field
from enum import Enum
import logging
import math
from typing import List, Optional

logger = logging.getLogger(__name__)

#: Decimal places applied to a comparison value before it is classified, so that
#: floating-point noise cannot decide a threshold case. Without it, a Sharpe of 2.0
#: decaying to 1.6 computes as 19.999999999999996% and lands on the opposite side of a
#: 20% threshold from the 20.0 the report displays.
COMPARISON_PRECISION = 6


class DivergenceTrackerError(ValueError):
    """Raised on invalid tracker configuration or a malformed performance snapshot."""


class DivergenceSeverity(str, Enum):
    ACCEPTABLE = "ACCEPTABLE"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"

    def __str__(self) -> str:
        # Python 3.10 and 3.11 disagree on what a (str, Enum) member formats to:
        # 3.10 renders f"{member}" as the value but str(member) as "Class.MEMBER",
        # while 3.11 made both "Class.MEMBER". Pinning __str__ to the value keeps
        # str(), format() and .value identical on every version, which is what a
        # severity written into a report needs.
        return self.value


_SEVERITY_ORDER = {
    DivergenceSeverity.ACCEPTABLE: 0,
    DivergenceSeverity.WARNING: 1,
    DivergenceSeverity.CRITICAL: 2,
}


@dataclass
class PerformanceSnapshot:
    """One side of a paired comparison.

    Args:
        sharpe_ratio: Annualized Sharpe over the snapshot window.
        max_drawdown_pct: Maximum drawdown in percent. Either sign convention is
            accepted (-15.0 or 15.0) but **both snapshots must use the same one**;
            magnitudes are compared. Mixing conventions raises.
        win_rate_pct: Winning periods or trades as a percentage, 0-100.
        fill_rate_pct: Orders filled as a percentage, 0-100.
        avg_slippage_bps: Average slippage cost in basis points.
        observation_periods: Optional number of observations behind this snapshot. Supply
            it on both snapshots to let the tracker flag a live sample too short to
            support the verdict.
    """

    sharpe_ratio: float
    max_drawdown_pct: float
    win_rate_pct: float
    fill_rate_pct: float
    avg_slippage_bps: float
    observation_periods: Optional[int] = None


@dataclass
class DivergenceMetric:
    """One classified dimension of divergence.

    `comparison_value` is what was classified and shares the scale of the two
    thresholds. `divergence_pct` is for display. `notes` explains any comparison that
    could not be formed.
    """

    name: str
    backtest_value: float
    live_value: float
    divergence_pct: float
    threshold_warning: float
    threshold_critical: float
    severity: DivergenceSeverity
    comparison_value: float = 0.0
    comparison_basis: str = ""
    notes: str = ""


@dataclass
class DivergenceReport:
    strategy_name: str
    metrics: List[DivergenceMetric]
    overall_severity: DivergenceSeverity
    is_suspension_recommended: bool
    message: str
    driving_metrics: List[str] = field(default_factory=list)
    is_sample_adequate: bool = True


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
        min_live_observations: int = 0,
    ) -> None:
        """
        Every threshold is an implementation default with **no published basis** -- no
        authoritative source prescribes backtest-vs-live divergence limits. Calibrate
        them against your own strategy population before wiring this to a suspension
        workflow. See `references/standards.md`.

        Args:
            min_live_observations: When above 0 and both snapshots carry
                `observation_periods`, a live sample smaller than this sets
                `is_sample_adequate=False` on the report. Severity is not altered:
                a short sample makes a verdict unreliable in *both* directions.

        Raises:
            DivergenceTrackerError: On a non-finite threshold, a negative threshold, or
                a warning threshold above its critical counterpart, which would invert
                the severity ladder.
        """
        pairs = (
            ("sharpe", sharpe_warning_pct, sharpe_critical_pct),
            ("drawdown", drawdown_warning_multiplier, drawdown_critical_multiplier),
            ("win_rate", win_rate_warning_decay_pct, win_rate_critical_decay_pct),
            ("fill_rate", fill_rate_warning_gap_pct, fill_rate_critical_gap_pct),
            ("slippage", slippage_warning_multiplier, slippage_critical_multiplier),
        )
        for label, warn, crit in pairs:
            for name, value in ((f"{label} warning", warn), (f"{label} critical", crit)):
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise DivergenceTrackerError(
                        f"{name} threshold must be a real number, got {type(value).__name__}."
                    )
                if not math.isfinite(value) or value < 0:
                    raise DivergenceTrackerError(
                        f"{name} threshold must be finite and non-negative, got {value}."
                    )
            if warn > crit:
                raise DivergenceTrackerError(
                    f"{label} warning threshold ({warn}) exceeds its critical threshold "
                    f"({crit}), which inverts the severity ladder: a mild divergence would "
                    f"classify CRITICAL and a severe one WARNING."
                )

        if not isinstance(min_live_observations, int) or isinstance(min_live_observations, bool):
            raise DivergenceTrackerError(
                f"min_live_observations must be an int, got {type(min_live_observations).__name__}."
            )
        if min_live_observations < 0:
            raise DivergenceTrackerError(
                f"min_live_observations must be non-negative, got {min_live_observations}."
            )

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
        self.min_live_observations = min_live_observations

    def _classify(self, value: float, warn_thresh: float, crit_thresh: float) -> DivergenceSeverity:
        """Classifies a comparison value. Thresholds are inclusive.

        The value is rounded first: without it, floating-point noise decides threshold
        cases and the report can display a divergence equal to its own warning threshold
        while calling it ACCEPTABLE.
        """
        rounded = round(value, COMPARISON_PRECISION)
        if rounded >= crit_thresh:
            return DivergenceSeverity.CRITICAL
        if rounded >= warn_thresh:
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

        Raises:
            DivergenceTrackerError: If either snapshot is malformed -- non-finite values,
                percentages outside 0-100, or drawdowns recorded under opposite sign
                conventions.
        """
        if not isinstance(strategy_name, str) or not strategy_name.strip():
            raise DivergenceTrackerError("strategy_name must be a non-empty string.")
        self._validate_snapshot("backtest", backtest)
        self._validate_snapshot("live", live)
        self._validate_drawdown_conventions(backtest, live)

        metrics: List[DivergenceMetric] = [
            self._decay_metric(
                "Sharpe Decay", backtest.sharpe_ratio, live.sharpe_ratio,
                self.sharpe_warn, self.sharpe_crit,
                undefined_note=(
                    "Backtest Sharpe is not positive, so relative decay is undefined. A "
                    "strategy promoted on a non-positive backtest Sharpe needs review "
                    "regardless of the live result."
                ),
            ),
            self._ratio_metric(
                "Drawdown Blow-Up", abs(backtest.max_drawdown_pct), abs(live.max_drawdown_pct),
                self.dd_warn_mult, self.dd_crit_mult,
                undefined_note=(
                    "Backtest max drawdown is zero, so no blow-up ratio can be formed. Any "
                    "live drawdown is entirely unmodelled."
                ),
            ),
            self._decay_metric(
                "Win Rate Decay", backtest.win_rate_pct, live.win_rate_pct,
                self.wr_warn, self.wr_crit,
                undefined_note="Backtest win rate is zero, so relative decay is undefined.",
            ),
            self._gap_metric(
                "Fill Rate Gap", backtest.fill_rate_pct, live.fill_rate_pct,
                self.fill_warn, self.fill_crit,
            ),
            self._ratio_metric(
                "Slippage Amplification", backtest.avg_slippage_bps, live.avg_slippage_bps,
                self.slip_warn_mult, self.slip_crit_mult,
                undefined_note=(
                    "Backtest assumed zero or negative slippage, so no amplification ratio "
                    "can be formed. Live execution cost is entirely unmodelled -- the most "
                    "common reason a promoted strategy underperforms its backtest."
                ),
            ),
        ]

        overall = max(metrics, key=lambda m: _SEVERITY_ORDER[m.severity]).severity
        suspend = overall == DivergenceSeverity.CRITICAL
        driving = [m.name for m in metrics if m.severity is overall] if overall != DivergenceSeverity.ACCEPTABLE else []

        sample_adequate = self._is_sample_adequate(live)
        sample_prefix = ""
        if not sample_adequate:
            sample_prefix = (
                f"[SHORT LIVE SAMPLE: {live.observation_periods} < "
                f"{self.min_live_observations} observations; verdict is noise-dominated in "
                f"either direction] "
            )

        if suspend:
            msg = (
                f"{sample_prefix}CRITICAL DIVERGENCE for '{strategy_name}': Strategy suspension "
                f"recommended. Driven by: {', '.join(driving)}."
            )
            logger.error(msg)
        elif overall == DivergenceSeverity.WARNING:
            msg = (
                f"{sample_prefix}WARNING DIVERGENCE for '{strategy_name}': Review recommended. "
                f"Driven by: {', '.join(driving)}."
            )
            logger.warning(msg)
        else:
            msg = f"{sample_prefix}Divergence ACCEPTABLE for '{strategy_name}'."
            logger.info(msg)

        return DivergenceReport(
            strategy_name=strategy_name,
            metrics=metrics,
            overall_severity=overall,
            is_suspension_recommended=suspend,
            message=msg,
            driving_metrics=driving,
            is_sample_adequate=sample_adequate,
        )

    # ------------------------------------------------------------------ internals

    @staticmethod
    def _validate_snapshot(label: str, snap: PerformanceSnapshot) -> None:
        """Rejects snapshots that would silently classify as ACCEPTABLE.

        NaN is the dangerous case: `max(0.0, nan)` returns 0.0 and `nan >= threshold` is
        False, so an unguarded NaN produced ACCEPTABLE on every metric it touched.
        """
        if not isinstance(snap, PerformanceSnapshot):
            raise DivergenceTrackerError(
                f"{label} must be a PerformanceSnapshot, got {type(snap).__name__}."
            )
        numeric_fields = (
            "sharpe_ratio", "max_drawdown_pct", "win_rate_pct",
            "fill_rate_pct", "avg_slippage_bps",
        )
        for name in numeric_fields:
            value = getattr(snap, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise DivergenceTrackerError(
                    f"{label}.{name} must be a real number, got {type(value).__name__}."
                )
            if not math.isfinite(value):
                raise DivergenceTrackerError(
                    f"{label}.{name} is non-finite ({value}). Every comparison against NaN is "
                    f"False, so leaving it in would report the strategy as ACCEPTABLE."
                )
        for name in ("win_rate_pct", "fill_rate_pct"):
            value = getattr(snap, name)
            if not 0.0 <= value <= 100.0:
                raise DivergenceTrackerError(
                    f"{label}.{name} must be a percentage in [0, 100], got {value}. A fraction "
                    f"such as 0.55 would be read as 0.55%."
                )
        if snap.observation_periods is not None:
            if not isinstance(snap.observation_periods, int) or isinstance(snap.observation_periods, bool):
                raise DivergenceTrackerError(
                    f"{label}.observation_periods must be an int or None, got "
                    f"{type(snap.observation_periods).__name__}."
                )
            if snap.observation_periods <= 0:
                raise DivergenceTrackerError(
                    f"{label}.observation_periods must be positive, got {snap.observation_periods}."
                )

    @staticmethod
    def _validate_drawdown_conventions(
        backtest: PerformanceSnapshot, live: PerformanceSnapshot
    ) -> None:
        """Blocks a mixed sign convention between the two snapshots.

        Magnitudes are compared, so either convention works on its own. Mixing them means
        one snapshot came from a different source than the other, which is a data-wiring
        error worth failing on rather than silently normalising away.
        """
        bt, lv = backtest.max_drawdown_pct, live.max_drawdown_pct
        if bt * lv < 0:
            raise DivergenceTrackerError(
                f"max_drawdown_pct sign conventions differ between snapshots (backtest {bt}, "
                f"live {lv}). Use one convention for both; magnitudes are compared."
            )

    def _is_sample_adequate(self, live: PerformanceSnapshot) -> bool:
        if self.min_live_observations <= 0 or live.observation_periods is None:
            return True
        return live.observation_periods >= self.min_live_observations

    def _decay_metric(
        self, name: str, backtest_value: float, live_value: float,
        warn: float, crit: float, undefined_note: str,
    ) -> DivergenceMetric:
        """Relative decay in percent: (backtest - live) / backtest * 100, floored at 0."""
        if backtest_value > 0:
            decay = max(0.0, ((backtest_value - live_value) / backtest_value) * 100.0)
            severity = self._classify(decay, warn, crit)
            notes = ""
        elif live_value == backtest_value:
            decay, severity, notes = 0.0, DivergenceSeverity.ACCEPTABLE, ""
        else:
            decay, severity, notes = 0.0, DivergenceSeverity.WARNING, undefined_note
        return DivergenceMetric(
            name=name,
            backtest_value=backtest_value,
            live_value=live_value,
            divergence_pct=round(decay, 2),
            threshold_warning=warn,
            threshold_critical=crit,
            severity=severity,
            comparison_value=round(decay, COMPARISON_PRECISION),
            comparison_basis="relative decay %",
            notes=notes,
        )

    def _ratio_metric(
        self, name: str, backtest_value: float, live_value: float,
        warn: float, crit: float, undefined_note: str,
    ) -> DivergenceMetric:
        """Live-over-backtest multiplier. A non-positive baseline has no ratio."""
        if backtest_value > 0:
            ratio = live_value / backtest_value
            severity = self._classify(ratio, warn, crit)
            notes = ""
        elif live_value == backtest_value:
            ratio, severity, notes = 1.0, DivergenceSeverity.ACCEPTABLE, ""
        else:
            # Never ACCEPTABLE: an unformable ratio is unassessed, not benign.
            ratio, severity, notes = math.inf, DivergenceSeverity.WARNING, undefined_note
        div_pct = (ratio - 1.0) * 100.0 if math.isfinite(ratio) else math.inf
        return DivergenceMetric(
            name=name,
            backtest_value=backtest_value,
            live_value=live_value,
            divergence_pct=round(div_pct, 2) if math.isfinite(div_pct) else div_pct,
            threshold_warning=warn,
            threshold_critical=crit,
            severity=severity,
            comparison_value=round(ratio, COMPARISON_PRECISION) if math.isfinite(ratio) else ratio,
            comparison_basis="ratio to backtest",
            notes=notes,
        )

    def _gap_metric(
        self, name: str, backtest_value: float, live_value: float, warn: float, crit: float,
    ) -> DivergenceMetric:
        """Absolute percentage-point shortfall, floored at 0."""
        gap = max(0.0, backtest_value - live_value)
        return DivergenceMetric(
            name=name,
            backtest_value=backtest_value,
            live_value=live_value,
            divergence_pct=round(gap, 2),
            threshold_warning=warn,
            threshold_critical=crit,
            severity=self._classify(gap, warn, crit),
            comparison_value=round(gap, COMPARISON_PRECISION),
            comparison_basis="percentage-point gap",
            notes="",
        )
