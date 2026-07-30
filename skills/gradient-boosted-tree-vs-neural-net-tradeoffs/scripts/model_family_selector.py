import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class DatasetSpec:
    modality: str                       # 'TABULAR_ENGINEERED' or 'RAW_HIGH_FREQUENCY_TICKS'
    sample_size_rows: int
    feature_count: int
    latency_budget_us: float            # Microseconds allowed for inference (e.g. 200 us vs 20,000 us)
    regulatory_compliance: str          # 'STRICT_SR11_7_MIFID2' or 'INTERNAL_RESEARCH'

@dataclass
class ModelFamilyTradeoffReport:
    recommended_model_family: str       # 'RECOMMEND_LIGHTGBM_XGBOOST', 'RECOMMEND_NEURAL_NETWORK_LSTM_TRANSFORMER', 'RECOMMEND_HYBRID_ENSEMBLE'
    gbdt_overall_score_0_to_10: float
    neural_net_overall_score_0_to_10: float
    dimension_scores: Dict[str, Dict[str, float]] # e.g. {'tabular_fit': {'gbdt': 9.5, 'nn': 5.5}}
    primary_decision_factors: List[str]
    audit_notes: str

class ModelFamilySelectorEngine:
    """
    Quantitative model architecture selection engine for evaluating GBDTs (LightGBM/XGBoost) vs Deep Neural Networks (LSTM/Transformers)
    across accuracy, latency, interpretability, and regime-shift robustness.
    """
    def __init__(self):
        pass

    def evaluate_model_family_tradeoffs(self, spec: DatasetSpec) -> ModelFamilyTradeoffReport:
        """
        Evaluates GBDT vs Neural Network trade-offs across 5 quantitative dimensions.
        """
        # Baseline scores
        scores = {
            "tabular_data_fit": {"gbdt": 9.5, "nn": 5.5},
            "sequential_pattern_extraction": {"gbdt": 3.0, "nn": 9.5}, # NN strongly dominates raw sequences
            "interpretability_compliance": {"gbdt": 9.0, "nn": 4.0},
            "inference_speed_latency": {"gbdt": 9.0, "nn": 5.0},
            "regime_shift_robustness": {"gbdt": 8.5, "nn": 4.5}
        }

        # Weighting factors based on spec
        w_tabular = 0.40 if spec.modality == "TABULAR_ENGINEERED" else 0.05
        w_sequential = 0.50 if spec.modality == "RAW_HIGH_FREQUENCY_TICKS" else 0.05
        w_compliance = 0.25 if spec.regulatory_compliance == "STRICT_SR11_7_MIFID2" else 0.05
        w_latency = 0.25 if spec.latency_budget_us <= 500.0 else 0.05
        w_regime = 0.15

        # Normalize weights
        total_w = w_tabular + w_sequential + w_compliance + w_latency + w_regime
        w_tabular /= total_w
        w_sequential /= total_w
        w_compliance /= total_w
        w_latency /= total_w
        w_regime /= total_w

        gbdt_final = (
            scores["tabular_data_fit"]["gbdt"] * w_tabular +
            scores["sequential_pattern_extraction"]["gbdt"] * w_sequential +
            scores["interpretability_compliance"]["gbdt"] * w_compliance +
            scores["inference_speed_latency"]["gbdt"] * w_latency +
            scores["regime_shift_robustness"]["gbdt"] * w_regime
        )

        nn_final = (
            scores["tabular_data_fit"]["nn"] * w_tabular +
            scores["sequential_pattern_extraction"]["nn"] * w_sequential +
            scores["interpretability_compliance"]["nn"] * w_compliance +
            scores["inference_speed_latency"]["nn"] * w_latency +
            scores["regime_shift_robustness"]["nn"] * w_regime
        )

        gbdt_score_clean = round(gbdt_final, 2)
        nn_score_clean = round(nn_final, 2)

        decision_factors: List[str] = []
        if spec.modality == "TABULAR_ENGINEERED":
            decision_factors.append("Engineered tabular dataset strongly favors GBDT tree-based split rules")
        else:
            decision_factors.append("Raw high-frequency tick sequence favors Deep Neural Network representation learning")

        if spec.regulatory_compliance == "STRICT_SR11_7_MIFID2":
            decision_factors.append("Strict SR 11-7 / MiFID II compliance requires native GBDT SHAP feature attributions")

        if spec.latency_budget_us <= 500.0:
            decision_factors.append(f"Sub-500us latency budget ({spec.latency_budget_us}us) favors lightweight GBDT C++ inference")

        diff = gbdt_score_clean - nn_score_clean
        if diff >= 1.0:
            rec = "RECOMMEND_LIGHTGBM_XGBOOST"
        elif diff <= -1.0:
            rec = "RECOMMEND_NEURAL_NETWORK_LSTM_TRANSFORMER"
        else:
            rec = "RECOMMEND_HYBRID_ENSEMBLE"

        notes = (
            f"MODEL FAMILY SELECTION: Recommended '{rec}'. "
            f"GBDT Score = {gbdt_score_clean:.2f}/10 vs Neural Net Score = {nn_score_clean:.2f}/10. "
            f"Primary Factors: {'; '.join(decision_factors)}."
        )
        logger.info(notes)

        return ModelFamilyTradeoffReport(
            recommended_model_family=rec,
            gbdt_overall_score_0_to_10=gbdt_score_clean,
            neural_net_overall_score_0_to_10=nn_score_clean,
            dimension_scores=scores,
            primary_decision_factors=decision_factors,
            audit_notes=notes
        )
