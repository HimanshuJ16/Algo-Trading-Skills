"""
tail-correlation-between-strategies-under-stress: Production-grade lower tail dependence analyzer,
conditional exceedance correlation calculator, and multi-strategy stress breakdown auditor.
"""
from dataclasses import dataclass, field
import logging
import numpy as np
import pandas as pd
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class TailCorrelationError(ValueError):
    """Raised when strategy return series are insufficient or malformed."""
    pass


@dataclass
class Config:
    """Configuration container for tail correlation analysis."""
    enabled: bool = True
    tail_quantile: float = 0.10  # 10th percentile stress threshold
    breakdown_threshold: float = 0.70  # Lower tail correlation threshold for warning
    delta_breakdown_threshold: float = 0.40  # Max acceptable jump from unconditional to tail correlation


@dataclass
class TailCorrelationResult:
    strategy_a: str
    strategy_b: str
    unconditional_correlation: float
    lower_tail_correlation: float
    tail_correlation_delta: float
    empirical_tail_dependence: float  # P(R_B <= q_B | R_A <= q_A)
    joint_crash_probability: float  # P(R_A <= q_A AND R_B <= q_B)
    diversification_breakdown: bool
    details: List[str]


class TailCorrelationAnalyzerEngine:
    """
    Analyzes non-linear tail correlations and lower-tail dependence between trading strategies
    during extreme market downside stress events.
    """

    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config()

    def analyze_pair(
        self,
        returns_a: pd.Series,
        returns_b: pd.Series,
        strategy_a_name: str = "Strategy_A",
        strategy_b_name: str = "Strategy_B",
    ) -> TailCorrelationResult:
        """
        Computes unconditional vs lower-tail conditional correlation for a pair of return series.
        """
        if len(returns_a) != len(returns_b) or len(returns_a) < 20:
            raise TailCorrelationError("Return series must be of equal length with at least 20 observations.")

        df = pd.DataFrame({strategy_a_name: returns_a, strategy_b_name: returns_b}).dropna()
        if len(df) < 20:
            raise TailCorrelationError("Insufficient non-null overlapping observations.")

        # Unconditional Pearson correlation
        uncond_corr = float(df[strategy_a_name].corr(df[strategy_b_name]))

        # Calculate lower tail quantiles
        q_a = float(df[strategy_a_name].quantile(self.config.tail_quantile))
        q_b = float(df[strategy_b_name].quantile(self.config.tail_quantile))

        # Joint downside stress condition (either or both in tail)
        tail_mask_a = df[strategy_a_name] <= q_a
        tail_mask_b = df[strategy_b_name] <= q_b
        joint_tail_mask = tail_mask_a & tail_mask_b

        # Empirical lower tail dependence: P(B <= q_B | A <= q_A)
        count_a_tail = int(tail_mask_a.sum())
        count_joint_tail = int(joint_tail_mask.sum())
        empirical_tail_dep = count_joint_tail / max(count_a_tail, 1)

        # Joint crash probability in entire dataset
        joint_crash_prob = count_joint_tail / len(df)

        # Lower-tail conditional correlation (returns when A <= q_A OR B <= q_B)
        downside_regime_mask = tail_mask_a | tail_mask_b
        downside_df = df[downside_regime_mask]

        if len(downside_df) >= 5 and downside_df[strategy_a_name].std() > 1e-8 and downside_df[strategy_b_name].std() > 1e-8:
            lower_tail_corr = float(downside_df[strategy_a_name].corr(downside_df[strategy_b_name]))
        else:
            lower_tail_corr = uncond_corr

        if np.isnan(lower_tail_corr):
            lower_tail_corr = uncond_corr

        tail_corr_delta = lower_tail_corr - uncond_corr

        # Check breakdown
        is_breakdown = (
            lower_tail_corr >= self.config.breakdown_threshold
            or tail_corr_delta >= self.config.delta_breakdown_threshold
        )

        details = []
        if is_breakdown:
            details.append(
                f"DIVERSIFICATION BREAKDOWN WARNING: Lower tail corr ({lower_tail_corr:.2f}) exceeds threshold ({self.config.breakdown_threshold:.2f}) "
                f"or delta ({tail_corr_delta:+.2f}) exceeds max delta ({self.config.delta_breakdown_threshold:.2f})."
            )
        else:
            details.append("Tail diversification holds: strategies maintain acceptable independence during extreme stress.")

        return TailCorrelationResult(
            strategy_a=strategy_a_name,
            strategy_b=strategy_b_name,
            unconditional_correlation=uncond_corr,
            lower_tail_correlation=lower_tail_corr,
            tail_correlation_delta=tail_corr_delta,
            empirical_tail_dependence=empirical_tail_dep,
            joint_crash_probability=joint_crash_prob,
            diversification_breakdown=is_breakdown,
            details=details,
        )

    def analyze_portfolio_matrix(self, returns_df: pd.DataFrame) -> Dict[str, Any]:
        """
        Computes full unconditional and lower-tail correlation matrices for multiple strategies.
        """
        strategies = list(returns_df.columns)
        n = len(strategies)
        uncond_matrix = pd.DataFrame(np.eye(n), index=strategies, columns=strategies)
        tail_matrix = pd.DataFrame(np.eye(n), index=strategies, columns=strategies)

        breakdown_pairs = []

        for i in range(n):
            for j in range(i + 1, n):
                s1, s2 = strategies[i], strategies[j]
                res = self.analyze_pair(returns_df[s1], returns_df[s2], s1, s2)
                uncond_matrix.loc[s1, s2] = res.unconditional_correlation
                uncond_matrix.loc[s2, s1] = res.unconditional_correlation
                tail_matrix.loc[s1, s2] = res.lower_tail_correlation
                tail_matrix.loc[s2, s1] = res.lower_tail_correlation

                if res.diversification_breakdown:
                    breakdown_pairs.append((s1, s2, res.lower_tail_correlation))

        return {
            "unconditional_matrix": uncond_matrix,
            "lower_tail_matrix": tail_matrix,
            "breakdown_pairs": breakdown_pairs,
        }


class Engine:
    """Legacy Engine class wrapper."""

    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config()
        self.analyzer = TailCorrelationAnalyzerEngine(self.config)

    def run(self) -> bool:
        return self.config.enabled
