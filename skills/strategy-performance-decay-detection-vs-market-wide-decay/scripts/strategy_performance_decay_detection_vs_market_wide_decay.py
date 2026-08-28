"""
strategy-performance-decay-detection-vs-market-wide-decay: classifies a fall in a
strategy's realized Sharpe ratio as either idiosyncratic alpha decay (the strategy's
own edge died while its peer group did not) or a market-wide regime shift (the whole
peer group is impaired), so that remediation targets the right cause.

Conventions used throughout:
  * Returns are simple (arithmetic) per-period returns, not log returns, and both
    series must be stated on the same frequency as `periods_per_year`.
  * Sharpe ratios are annualized arithmetically: mean(excess) / std(excess) * sqrt(F).
    This scaling assumes serially uncorrelated returns. Lo (2002) shows the sqrt(F)
    rule is invalid under autocorrelation and overstates the Sharpe ratio of a
    positively autocorrelated series.
  * Statistical significance of the Sharpe *difference* is assessed with the
    Jobson-Korkie (1981) test as corrected by Memmel (2003):

        z = (Sh_t - Sh_p) / sqrt(theta)
        theta = (1/T) * [2 - 2*rho + 0.5*(Sh_t^2 + Sh_p^2 - 2*Sh_t*Sh_p*rho^2)]

    where Sh_t, Sh_p are the *per-period* Sharpe ratios of target and peer over the
    T-observation window and rho is the correlation between the two return series.
    Under i.i.d. bivariate normal returns z is asymptotically standard normal, which
    is what makes the -1.96 critical value mean what it claims to mean. The statistic
    is invariant to annualization because numerator and standard error scale together.
  * That null distribution is NOT valid for heavy-tailed or serially correlated
    returns; Ledoit and Wolf (2008) show the test then over-rejects and recommend a
    studentized time-series bootstrap instead. Treat a marginal z as a prompt to run
    that bootstrap, not as a decommissioning decision. See `references/standards.md`.
"""
import logging
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Sequence, Tuple, Union

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

SeriesLike = Union[pd.Series, Sequence[float], np.ndarray]

# Floating-point noise floor on a per-period return standard deviation. A series whose
# dispersion sits below this is constant to within machine resolution and has no
# risk-adjusted interpretation at all. Deliberately far below any real return series:
# a genuine low-volatility strategy at 1bp daily sigma is 1e-4, eight orders of
# magnitude above this floor, and is NOT treated as degenerate.
_DEGENERATE_STD_TOL = 1e-12

# Floor on the *dimensionless* variance bracket of the Sharpe difference, i.e. theta
# before dividing by the observation count. The bracket is 2 - 2*rho + (Sharpe terms),
# so it is O(1) -- around 2 -- for any genuinely distinct pair, which makes this
# absolute tolerance a relative one. It collapses only when the two series are the same
# series: rho = 1 with equal Sharpe ratios, where the difference is identically zero
# and no test exists. The tolerance must sit well above floating-point noise, because
# np.corrcoef(v, v) returns 0.9999999999999999 rather than 1.0 for roughly a quarter of
# real inputs; a tighter floor would make "strategy versus a copy of itself" classify
# HEALTHY or INCONCLUSIVE depending on the last bit of the correlation estimate.
_DEGENERATE_VARIANCE_BRACKET_TOL = 1e-12

# Below this many observations the Memmel-corrected Jobson-Korkie statistic is an
# asymptotic approximation with visibly liberal small-sample size; the result is still
# reported but carries a warning.
_SMALL_SAMPLE_OBS = 30


class DecayDiagnosticError(ValueError):
    """Raised when the supplied return series cannot support a valid decay diagnosis.

    Subclasses ValueError so that callers catching ValueError keep working.
    """


class DecayClassification(str, Enum):
    HEALTHY = "HEALTHY"
    IDIOSYNCRATIC_ALPHA_DECAY = "IDIOSYNCRATIC_ALPHA_DECAY"  # Strategy edge dead; peers healthy
    MARKET_WIDE_REGIME_SHIFT = "MARKET_WIDE_REGIME_SHIFT"    # Entire strategy class suffering
    INCONCLUSIVE = "INCONCLUSIVE"


@dataclass
class StrategyDecayDiagnosticsReport:
    """Outcome of one decay diagnosis.

    `relative_sharpe_z_score`, `p_value` and `return_correlation` are `None` -- never
    0.0 -- when the statistic is not measurable. A zero z-score means "the target
    matched its peers exactly"; `None` means "no test was performed". Rendering the
    two identically is what lets an untested strategy read as a healthy one.

    Sharpe figures are annualized and reported unrounded so that the classification is
    reproducible from the report at the decision threshold.
    """
    strategy_id: str
    peer_benchmark_id: str
    classification: DecayClassification
    target_sharpe: float
    peer_benchmark_sharpe: float
    relative_excess_sharpe: float
    relative_sharpe_z_score: Optional[float]
    recommended_action: str
    audit_notes: str
    observations: int = 0
    return_correlation: Optional[float] = None
    p_value: Optional[float] = None
    warnings: List[str] = field(default_factory=list)


class StrategyPerformanceDecayDiagnosticEngine:
    """
    Performance decay diagnostic engine separating idiosyncratic alpha decay from a
    market-wide regime shift by testing the target strategy's Sharpe ratio against a
    peer benchmark index over a trailing evaluation window.
    """

    def __init__(
        self,
        rolling_window_days: int = 60,
        idiosyncratic_z_threshold: float = -1.96,
        market_wide_sharpe_threshold: float = 0.50,
        periods_per_year: int = 252,
    ) -> None:
        """
        Args:
            rolling_window_days: number of trailing observations the diagnosis is run
                on. Also the minimum aligned history required.
            idiosyncratic_z_threshold: one-sided critical value for the Sharpe
                difference test. Must be negative -- the test only fires on the target
                *underperforming*. -1.96 is the 2.5th percentile of the standard
                normal, i.e. a 2.5% one-sided false-positive rate under the test's
                i.i.d. normal assumptions.
            market_wide_sharpe_threshold: annualized Sharpe below which a series is
                treated as impaired. Applied to the target *and* the peer benchmark.
                A house default, not an external standard.
            periods_per_year: observations per year for annualization. 252 for daily
                bars. Frequency cannot be inferred from a return series -- set it.
        """
        window = self._require_positive_int(rolling_window_days, "rolling_window_days")
        if window < 2:
            raise DecayDiagnosticError(
                "rolling_window_days must be at least 2 to estimate a standard deviation, "
                f"got {window}."
            )
        if not math.isfinite(idiosyncratic_z_threshold) or idiosyncratic_z_threshold >= 0:
            raise DecayDiagnosticError(
                "idiosyncratic_z_threshold must be a finite negative number (the test fires on "
                f"underperformance only), got {idiosyncratic_z_threshold!r}."
            )
        if not math.isfinite(market_wide_sharpe_threshold):
            raise DecayDiagnosticError(
                f"market_wide_sharpe_threshold must be finite, got {market_wide_sharpe_threshold!r}."
            )

        self.rolling_window = window
        self.z_threshold = float(idiosyncratic_z_threshold)
        self.peer_sharpe_threshold = float(market_wide_sharpe_threshold)
        self.periods_per_year = self._require_positive_int(periods_per_year, "periods_per_year")

    @staticmethod
    def _require_positive_int(value: int, label: str) -> int:
        """Rejects non-integral or non-positive values rather than silently truncating."""
        try:
            as_int = int(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise DecayDiagnosticError(f"{label} must be a positive integer, got {value!r}.") from exc
        if as_int != value or as_int <= 0:
            raise DecayDiagnosticError(f"{label} must be a positive integer, got {value!r}.")
        return as_int

    @staticmethod
    def _as_series(values: SeriesLike, label: str) -> pd.Series:
        """Coerces input to a 1D float Series and rejects structurally unusable input."""
        if isinstance(values, pd.Series):
            series = values
        else:
            try:
                array = np.asarray(values, dtype=float)
            except (TypeError, ValueError) as exc:
                raise DecayDiagnosticError(f"{label} must be numeric: {exc}") from exc
            if array.ndim != 1:
                raise DecayDiagnosticError(
                    f"{label} must be a 1D series of per-period returns, got {array.ndim}D "
                    f"with shape {array.shape}."
                )
            series = pd.Series(array)

        try:
            series = series.astype(float)
        except (TypeError, ValueError) as exc:
            raise DecayDiagnosticError(f"{label} must be numeric: {exc}") from exc

        if not series.index.is_unique:
            # A duplicated label turns the target/peer join into a partial cartesian
            # product, silently inflating the observation count and the test statistic.
            raise DecayDiagnosticError(
                f"{label} has duplicate index labels; deduplicate before diagnosing."
            )
        if not series.index.is_monotonic_increasing:
            # The diagnosis reads the *last* `rolling_window` rows as the most recent
            # window. Out-of-order rows make that the wrong window entirely.
            raise DecayDiagnosticError(
                f"{label} index must be sorted ascending; the trailing window is taken "
                "positionally and an unsorted index selects the wrong observations."
            )
        return series

    def _align(
        self, strategy_returns: SeriesLike, peer_returns: SeriesLike
    ) -> Tuple[pd.DataFrame, List[str]]:
        """Aligns both series on their shared index and rejects unusable observations."""
        target = self._as_series(strategy_returns, "strategy_returns")
        peer = self._as_series(peer_returns, "peer_returns")

        shared_index = target.index.intersection(peer.index)
        if len(shared_index) == 0:
            raise DecayDiagnosticError(
                "strategy_returns and peer_returns share no aligned observations. Both series "
                "must be indexed on the same dates/periods."
            )
        shared = pd.DataFrame(
            {"target": target.loc[shared_index], "peer": peer.loc[shared_index]}
        )

        # Only *unmatched* index labels may be dropped. A NaN sitting inside the shared
        # window is a data-quality failure, not an alignment artifact: dropping it makes
        # non-adjacent observations adjacent and silently shrinks the sample.
        values = shared.to_numpy()
        non_finite = int(np.count_nonzero(~np.isfinite(values).all(axis=1)))
        if non_finite:
            raise DecayDiagnosticError(
                f"{non_finite} aligned observation(s) contain NaN/Inf. Clean both series before "
                "diagnosing -- dropping interior gaps treats non-adjacent periods as consecutive "
                "and reports a diagnosis from a silently truncated sample."
            )

        if np.any(values <= -1.0):
            raise DecayDiagnosticError(
                "Return series contains a value at or below -100%. Simple per-period returns "
                "cannot be <= -1.0; the series is either log returns or corrupt."
            )

        warnings: List[str] = []
        unmatched = len(target.index.union(peer.index)) - len(shared_index)
        if unmatched:
            warnings.append(
                f"{unmatched} observation(s) present in only one series were dropped during "
                f"alignment; {len(shared_index)} aligned observation(s) remain."
            )
        return shared, warnings

    def evaluate_decay_cause(
        self,
        strategy_id: str,
        peer_benchmark_id: str,
        strategy_returns: SeriesLike,
        peer_returns: SeriesLike,
        risk_free_rate_annual_pct: float = 2.0,
    ) -> StrategyDecayDiagnosticsReport:
        """
        Distinguishes idiosyncratic alpha decay from a market-wide regime shift.

        1. Aligns the two return series and takes the trailing `rolling_window` window.
        2. Computes annualized Sharpe ratios for target and peer over that window.
        3. Tests H0: Sh_target == Sh_peer with the Memmel-corrected Jobson-Korkie
           statistic, which accounts for the correlation between the two series.
        4. Classifies on the pair (statistical significance, absolute peer health).

        Raises:
            DecayDiagnosticError: on unusable input (misaligned, non-finite, too short,
                out-of-order, duplicated, or impossible returns).
        """
        if not math.isfinite(risk_free_rate_annual_pct):
            raise DecayDiagnosticError(
                f"risk_free_rate_annual_pct must be finite, got {risk_free_rate_annual_pct!r}."
            )

        df, warnings = self._align(strategy_returns, peer_returns)
        if len(df) < self.rolling_window:
            raise DecayDiagnosticError(
                f"Insufficient aligned return history ({len(df)} observations). "
                f"Required window = {self.rolling_window}."
            )

        window = df.iloc[-self.rolling_window:]
        observations = len(window)
        if observations < _SMALL_SAMPLE_OBS:
            warnings.append(
                f"Only {observations} observations: the Sharpe-difference test is asymptotic and "
                f"rejects more often than its nominal rate below ~{_SMALL_SAMPLE_OBS} observations."
            )

        rf_per_period = (risk_free_rate_annual_pct / 100.0) / self.periods_per_year
        target_excess = window["target"].to_numpy() - rf_per_period
        peer_excess = window["peer"].to_numpy() - rf_per_period

        annualization = math.sqrt(self.periods_per_year)
        target_std = float(np.std(target_excess, ddof=1))
        peer_std = float(np.std(peer_excess, ddof=1))

        degenerate = [
            label
            for label, std in (("strategy", target_std), ("peer benchmark", peer_std))
            if std <= _DEGENERATE_STD_TOL
        ]
        if degenerate:
            # A constant series has no risk-adjusted interpretation. Reporting Sharpe = 0.0
            # would classify a zero-volatility positive-return strategy as impaired.
            warnings.append(
                f"{' and '.join(degenerate)} return series is constant to floating-point "
                "resolution; the Sharpe ratio is undefined, not zero, so no diagnosis is possible."
            )
            return self._build_report(
                strategy_id,
                peer_benchmark_id,
                DecayClassification.INCONCLUSIVE,
                target_sharpe=float("nan"),
                peer_sharpe=float("nan"),
                excess_sharpe=float("nan"),
                z_score=None,
                p_value=None,
                correlation=None,
                observations=observations,
                action=(
                    "MONITOR_CLOSELY: Sharpe ratio undefined for a constant return series; "
                    "no decay diagnosis was performed."
                ),
                warnings=warnings,
            )

        target_sharpe_pp = float(np.mean(target_excess)) / target_std
        peer_sharpe_pp = float(np.mean(peer_excess)) / peer_std
        target_sharpe = target_sharpe_pp * annualization
        peer_sharpe = peer_sharpe_pp * annualization
        excess_sharpe = target_sharpe - peer_sharpe

        correlation = float(np.corrcoef(target_excess, peer_excess)[0, 1])

        # Memmel (2003) correction of Jobson & Korkie (1981). Computed on per-period
        # Sharpe ratios; the resulting z is identical under annualization because the
        # numerator and the standard error both scale by sqrt(periods_per_year).
        variance_bracket = (
            2.0
            - 2.0 * correlation
            + 0.5
            * (
                target_sharpe_pp ** 2
                + peer_sharpe_pp ** 2
                - 2.0 * target_sharpe_pp * peer_sharpe_pp * correlation ** 2
            )
        )
        theta = variance_bracket / observations

        z_score: Optional[float]
        p_value: Optional[float]
        if variance_bracket <= _DEGENERATE_VARIANCE_BRACKET_TOL:
            z_score = None
            p_value = None
            warnings.append(
                "Sharpe-difference variance collapsed to zero (the two series are effectively "
                "identical); no significance test is defined."
            )
        else:
            z_score = (target_sharpe_pp - peer_sharpe_pp) / math.sqrt(theta)
            # One-sided p-value for H1: Sh_target < Sh_peer.
            p_value = 0.5 * math.erfc(-z_score / math.sqrt(2.0))

        target_impaired = target_sharpe < self.peer_sharpe_threshold
        peer_impaired = peer_sharpe < self.peer_sharpe_threshold
        significant = z_score is not None and z_score <= self.z_threshold

        if z_score is None:
            classification = DecayClassification.INCONCLUSIVE
            action = (
                "MONITOR_CLOSELY: relative Sharpe significance test not measurable on this sample; "
                "classify only on absolute performance and re-run with clean history."
            )
        elif significant and not peer_impaired and target_impaired:
            classification = DecayClassification.IDIOSYNCRATIC_ALPHA_DECAY
            action = (
                "DECOMMISSION_OR_RECODE: Strategy alpha is dead while peer benchmark is healthy. "
                "Initiate decommissioning."
            )
        elif target_impaired and peer_impaired:
            classification = DecayClassification.MARKET_WIDE_REGIME_SHIFT
            action = (
                "PAUSE_OR_REDUCE_RISK: Entire asset class/strategy peer group is suffering from "
                "market-wide regime shift. Reduce capital allocation temporarily."
            )
        elif not target_impaired:
            classification = DecayClassification.HEALTHY
            action = (
                "MAINTAIN_TRADING: Strategy risk-adjusted performance is healthy relative to "
                "peer group."
            )
            if significant:
                # Relative underperformance against faster-improving peers is a capital
                # allocation question, not a decommissioning trigger. Retiring a strategy
                # whose own Sharpe is above the health threshold destroys working capacity.
                warnings.append(
                    f"Target underperforms peers significantly (z = {z_score:.2f}) but its own "
                    f"annualized Sharpe of {target_sharpe:.2f} is at or above the "
                    f"{self.peer_sharpe_threshold:.2f} health threshold. This is a "
                    "capital-allocation question, not alpha decay."
                )
        else:
            classification = DecayClassification.INCONCLUSIVE
            action = "MONITOR_CLOSELY: Mixed quantitative signals; monitor rolling performance."

        return self._build_report(
            strategy_id,
            peer_benchmark_id,
            classification,
            target_sharpe=target_sharpe,
            peer_sharpe=peer_sharpe,
            excess_sharpe=excess_sharpe,
            z_score=z_score,
            p_value=p_value,
            correlation=correlation,
            observations=observations,
            action=action,
            warnings=warnings,
        )

    @staticmethod
    def _build_report(
        strategy_id: str,
        peer_benchmark_id: str,
        classification: DecayClassification,
        *,
        target_sharpe: float,
        peer_sharpe: float,
        excess_sharpe: float,
        z_score: Optional[float],
        p_value: Optional[float],
        correlation: Optional[float],
        observations: int,
        action: str,
        warnings: List[str],
    ) -> StrategyDecayDiagnosticsReport:
        """Assembles the report and emits the audit line. Rounding is presentational only."""
        z_text = "n/a" if z_score is None else f"{z_score:.2f}"
        notes = (
            f"DECAY DIAGNOSIS [{classification.value}] ({strategy_id} vs {peer_benchmark_id}): "
            f"Target Sharpe = {target_sharpe:.2f}, Peer Sharpe = {peer_sharpe:.2f}, "
            f"Relative Z-Score = {z_text} over {observations} observations. Action = {action}"
        )
        if warnings:
            notes = f"{notes} Warnings: {' | '.join(warnings)}"
        logger.info(notes)

        return StrategyDecayDiagnosticsReport(
            strategy_id=strategy_id,
            peer_benchmark_id=peer_benchmark_id,
            classification=classification,
            target_sharpe=target_sharpe,
            peer_benchmark_sharpe=peer_sharpe,
            relative_excess_sharpe=excess_sharpe,
            relative_sharpe_z_score=z_score,
            recommended_action=action,
            audit_notes=notes,
            observations=observations,
            return_correlation=correlation,
            p_value=p_value,
            warnings=warnings,
        )
