import math
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class HorizonPrediction:
    horizon_steps: int                   # e.g. 5 (min), 15, 60, 240
    predicted_return: float              # Expected return (e.g. 0.0015 = 15 bps)
    ic_score: float = 0.05               # Out-of-sample Information Coefficient
    confidence: float = 1.0              # Model confidence [0, 1]

@dataclass
class MultiHorizonConfig:
    symbol: str
    weighting_scheme: str = "IC_WEIGHTED" # 'IC_WEIGHTED', 'INVERSE_HORIZON_SQRT', 'EQUAL'
    conflict_threshold: float = 0.001     # Threshold to flag directional conflict

@dataclass
class MultiHorizonForecastReport:
    symbol: str
    composite_alpha: float
    directional_consensus_pct: float
    has_directional_conflict: bool
    horizon_weights: Dict[int, float]
    normalized_predictions: Dict[int, float]
    status: str                         # 'FORECAST_SYNTHESIZED_SUCCESS'
    audit_notes: str

class MultiHorizonForecasterEngine:
    """
    Multi-horizon ML return forecasting engine, combining predictions across multiple time horizons
    with IC-weighted decay and short-vs-long conflict arbitration.
    """
    def __init__(self):
        pass

    def synthesize_forecast(
        self, cfg: MultiHorizonConfig, predictions: List[HorizonPrediction]
    ) -> MultiHorizonForecastReport:
        """
        Combines multi-horizon return predictions into a unified alpha signal and audits directional conflicts.
        """
        if not predictions:
            raise ValueError("Predictions list cannot be empty.")

        # Sort predictions by horizon length ascending
        preds_sorted = sorted(predictions, key=lambda x: x.horizon_steps)
        scheme_upper = cfg.weighting_scheme.upper()

        # 1. Compute Raw Horizon Weights
        raw_weights: Dict[int, float] = {}
        for p in preds_sorted:
            h = p.horizon_steps
            if scheme_upper == "INVERSE_HORIZON_SQRT":
                w = (1.0 / math.sqrt(h)) * max(0.0, p.confidence)
            elif scheme_upper == "IC_WEIGHTED":
                w = max(0.0, p.ic_score) * max(0.0, p.confidence)
            elif scheme_upper == "EQUAL":
                w = 1.0
            else:
                w = 1.0
            raw_weights[h] = w

        total_w = sum(raw_weights.values())
        if total_w <= 0.0:
            # Fallback equal weighting
            total_w = float(len(preds_sorted))
            norm_weights = {p.horizon_steps: 1.0 / total_w for p in preds_sorted}
        else:
            norm_weights = {h: w / total_w for h, w in raw_weights.items()}

        # 2. Synthesize Composite Alpha Signal
        composite_alpha = sum(norm_weights[p.horizon_steps] * p.predicted_return for p in preds_sorted)

        # 3. Directional Consensus & Conflict Arbitration
        signs = [1 if p.predicted_return > 0 else (-1 if p.predicted_return < 0 else 0) for p in preds_sorted]
        non_zero_signs = [s for s in signs if s != 0]

        if non_zero_signs:
            pos_count = sum(1 for s in non_zero_signs if s > 0)
            neg_count = sum(1 for s in non_zero_signs if s < 0)
            consensus_pct = (max(pos_count, neg_count) / len(non_zero_signs)) * 100.0
        else:
            consensus_pct = 100.0

        # Short-term (shortest horizon) vs Long-term (longest horizon) conflict check
        short_pred = preds_sorted[0]
        long_pred = preds_sorted[-1]
        has_conflict = False

        if (short_pred.predicted_return * long_pred.predicted_return < 0) and \
           (abs(short_pred.predicted_return) >= cfg.conflict_threshold) and \
           (abs(long_pred.predicted_return) >= cfg.conflict_threshold):
            has_conflict = True

        norm_preds_dict = {p.horizon_steps: p.predicted_return for p in preds_sorted}

        notes = (
            f"MULTI-HORIZON FORECAST SYNTHESIZED [{cfg.symbol}]: Composite Alpha = {composite_alpha * 10000:.1f} bps. "
            f"Consensus = {consensus_pct:.0f}%, Conflict = {has_conflict}. Scheme = {scheme_upper}."
        )
        logger.info(notes)

        return MultiHorizonForecastReport(
            symbol=cfg.symbol,
            composite_alpha=round(composite_alpha, 6),
            directional_consensus_pct=round(consensus_pct, 1),
            has_directional_conflict=has_conflict,
            horizon_weights={h: round(w, 4) for h, w in norm_weights.items()},
            normalized_predictions=norm_preds_dict,
            status="FORECAST_SYNTHESIZED_SUCCESS",
            audit_notes=notes
        )
