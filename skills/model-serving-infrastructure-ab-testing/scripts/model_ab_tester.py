import math
import hashlib
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

@dataclass
class ABTestConfig:
    experiment_id: str
    champion_model_id: str
    challenger_model_id: str
    traffic_split_ratio: float = 0.80   # 0.80 = 80% Champion, 20% Challenger
    test_mode: str = "LIVE_SPLIT"       # 'LIVE_SPLIT' or 'SHADOW'
    min_sample_size: int = 30
    significance_level_alpha: float = 0.05

@dataclass
class ModelExecutionResult:
    model_id: str
    timestamp_epoch: float
    symbol: str
    signal_return_bps: float            # Realized trade return in basis points
    latency_ms: float

@dataclass
class ABTestReport:
    experiment_id: str
    champion_model_id: str
    challenger_model_id: str
    champion_sample_count: int
    challenger_sample_count: int
    champion_mean_return_bps: float
    challenger_mean_return_bps: float
    mean_difference_bps: float
    welch_t_statistic: float
    degrees_of_freedom: float
    p_value: float
    is_statistically_significant: bool
    recommended_action: str             # 'PROMOTE_CHALLENGER_TO_CHAMPION', 'REJECT_CHALLENGER', 'CONTINUE_EXPERIMENT_INSUFFICIENT_SAMPLES', 'CONTINUE_EXPERIMENT_NO_SIGNIFICANT_DIFFERENCE'
    status: str                         # 'AB_TEST_COMPLETED'
    audit_notes: str

class ModelABTesterEngine:
    """
    Champion-Challenger model serving A/B testing engine,
    managing deterministic traffic routing, shadow execution, and Welch's t-test statistical significance testing for model promotion.
    """
    def __init__(self):
        pass

    def route_request(self, config: ABTestConfig, request_key: str) -> str:
        """
        Deterministically routes request key (e.g. 'AAPL', 'ACC_9921') to Champion or Challenger model.
        In SHADOW mode, always routes execution to Champion.
        """
        if config.test_mode == "SHADOW":
            return config.champion_model_id

        # Deterministic MD5 Hash to [0.0, 1.0] range
        hash_val = hashlib.md5(request_key.encode('utf-8')).hexdigest()
        score = int(hash_val[:8], 16) / 0xFFFFFFFF

        if score <= config.traffic_split_ratio:
            return config.champion_model_id
        else:
            return config.challenger_model_id

    def evaluate_ab_test_results(
        self,
        config: ABTestConfig,
        champion_results: List[ModelExecutionResult],
        challenger_results: List[ModelExecutionResult]
    ) -> ABTestReport:
        """
        Calculates Welch's Two-Sample t-test, p-value, and determines model promotion/demotion.
        """
        nA = len(champion_results)
        nB = len(challenger_results)

        if nA < config.min_sample_size or nB < config.min_sample_size:
            notes = (
                f"AB TEST INSUFFICIENT SAMPLES [{config.experiment_id}]: Champion N={nA}, Challenger N={nB} "
                f"(Min required = {config.min_sample_size}). Continue experiment."
            )
            logger.info(notes)
            return ABTestReport(
                experiment_id=config.experiment_id,
                champion_model_id=config.champion_model_id,
                challenger_model_id=config.challenger_model_id,
                champion_sample_count=nA,
                challenger_sample_count=nB,
                champion_mean_return_bps=0.0,
                challenger_mean_return_bps=0.0,
                mean_difference_bps=0.0,
                welch_t_statistic=0.0,
                degrees_of_freedom=0.0,
                p_value=1.0,
                is_statistically_significant=False,
                recommended_action="CONTINUE_EXPERIMENT_INSUFFICIENT_SAMPLES",
                status="AB_TEST_COMPLETED",
                audit_notes=notes
            )

        returnsA = [r.signal_return_bps for r in champion_results]
        returnsB = [r.signal_return_bps for r in challenger_results]

        meanA = sum(returnsA) / nA
        meanB = sum(returnsB) / nB
        diff_mean = meanB - meanA

        varA = sum((x - meanA) ** 2 for x in returnsA) / (nA - 1) if nA > 1 else 0.001
        varB = sum((x - meanB) ** 2 for x in returnsB) / (nB - 1) if nB > 1 else 0.001

        # Prevent zero variance division
        varA = max(varA, 1e-6)
        varB = max(varB, 1e-6)

        # Welch's t-statistic calculation: t = (meanB - meanA) / sqrt(varA/nA + varB/nB)
        se_diff = math.sqrt((varA / nA) + (varB / nB))
        t_stat = (meanB - meanA) / se_diff if se_diff > 0 else 0.0

        # Welch-Satterthwaite degrees of freedom
        numerator = ((varA / nA) + (varB / nB)) ** 2
        denominator = (((varA / nA) ** 2) / (nA - 1)) + (((varB / nB) ** 2) / (nB - 1))
        df = numerator / denominator if denominator > 0 else (nA + nB - 2)

        # Approximate p-value using normal CDF for large df, or standard t-approximation
        # p-value = 2 * (1 - NormalCDF(|t|))
        abs_t = abs(t_stat)
        p_val = round(2.0 * (1.0 - 0.5 * (1.0 + math.erf(abs_t / math.sqrt(2.0)))), 4)

        is_significant = (p_val < config.significance_level_alpha)

        # Recommendation Decision Logic
        if is_significant and diff_mean > 0.0:
            action = "PROMOTE_CHALLENGER_TO_CHAMPION"
            notes = (
                f"AB TEST PROMOTION [{config.experiment_id}]: Challenger '{config.challenger_model_id}' "
                f"significantly outperforms Champion '{config.champion_model_id}'! "
                f"Mean Diff = +{diff_mean:.2f} bps, t-stat = {t_stat:.3f}, p-val = {p_val:.4f} < {config.significance_level_alpha}."
            )
            logger.info(notes)
        elif is_significant and diff_mean < 0.0:
            action = "REJECT_CHALLENGER"
            notes = (
                f"AB TEST DEMOTION [{config.experiment_id}]: Challenger '{config.challenger_model_id}' "
                f"significantly underperforms Champion! Mean Diff = {diff_mean:.2f} bps, t-stat = {t_stat:.3f}, p-val = {p_val:.4f}."
            )
            logger.warning(notes)
        else:
            action = "CONTINUE_EXPERIMENT_NO_SIGNIFICANT_DIFFERENCE"
            notes = (
                f"AB TEST INCONCLUSIVE [{config.experiment_id}]: No statistically significant difference "
                f"detected yet (p-val = {p_val:.4f} >= {config.significance_level_alpha}). Continue testing."
            )
            logger.info(notes)

        return ABTestReport(
            experiment_id=config.experiment_id,
            champion_model_id=config.champion_model_id,
            challenger_model_id=config.challenger_model_id,
            champion_sample_count=nA,
            challenger_sample_count=nB,
            champion_mean_return_bps=round(meanA, 2),
            challenger_mean_return_bps=round(meanB, 2),
            mean_difference_bps=round(diff_mean, 2),
            welch_t_statistic=round(t_stat, 3),
            degrees_of_freedom=round(df, 2),
            p_value=p_val,
            is_statistically_significant=is_significant,
            recommended_action=action,
            status="AB_TEST_COMPLETED",
            audit_notes=notes
        )
