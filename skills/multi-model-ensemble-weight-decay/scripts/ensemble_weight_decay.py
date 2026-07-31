import math
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

@dataclass
class ModelTelemetry:
    model_id: str
    recent_loss: float                   # Current period MSE / LogLoss (lower is better)
    recent_ic: float                     # Information Coefficient (higher is better)
    days_active: int = 1
    previous_decayed_loss: Optional[float] = None
    previous_decayed_ic: Optional[float] = None

@dataclass
class EnsembleConfig:
    decay_factor_lambda: float = 0.95    # Recency memory decay factor [0, 1]
    temperature_beta: float = 2.0        # Softmax temperature scaling
    min_weight_floor: float = 0.05       # Minimum weight threshold for demotion
    weighting_method: str = "EXPONENTIAL_LOSS" # 'EXPONENTIAL_LOSS', 'IC_SOFTMAX'

@dataclass
class ModelWeightStatus:
    model_id: str
    raw_weight: float
    final_normalized_weight: float
    decayed_metric: float
    is_active: bool
    status: str                          # 'ACTIVE', 'DEMOTED_BELOW_FLOOR', 'DEMOTED_NEGATIVE_IC'

@dataclass
class EnsembleWeightReport:
    ensemble_id: str
    active_model_count: int
    demoted_model_count: int
    model_statuses: List[ModelWeightStatus]
    status: str                          # 'ENSEMBLE_REWEIGHTED_SUCCESS'
    audit_notes: str

class EnsembleWeightDecayEngine:
    """
    Multi-model ensemble weight decay engine, applying exponential recency memory decay,
    softmax dynamic reweighting, and model demotion circuit breakers.
    """
    def __init__(self):
        pass

    def reweight_ensemble(
        self, ensemble_id: str, cfg: EnsembleConfig, models: List[ModelTelemetry]
    ) -> EnsembleWeightReport:
        """
        Updates decayed performance metrics, computes softmax ensemble weights, demotes underperforming models,
        and normalizes active model weights.
        """
        if not models:
            raise ValueError("Model telemetry list cannot be empty.")

        lam = cfg.decay_factor_lambda
        beta = cfg.temperature_beta
        method_upper = cfg.weighting_method.upper()

        decayed_metrics: Dict[str, float] = {}
        raw_weights: Dict[str, float] = {}

        # 1. Update Exponential Memory Decay & Calculate Raw Softmax Exponentials
        for m in models:
            m_id = m.model_id
            if method_upper == "EXPONENTIAL_LOSS":
                prev = m.previous_decayed_loss if m.previous_decayed_loss is not None else m.recent_loss
                decayed_val = lam * prev + (1.0 - lam) * m.recent_loss
                decayed_metrics[m_id] = decayed_val
                # Lower loss -> higher weight exp(-beta * loss)
                exp_val = math.exp(-beta * decayed_val)
            else: # IC_SOFTMAX
                prev = m.previous_decayed_ic if m.previous_decayed_ic is not None else m.recent_ic
                decayed_val = lam * prev + (1.0 - lam) * m.recent_ic
                decayed_metrics[m_id] = decayed_val
                # Higher IC -> higher weight exp(+beta * IC)
                exp_val = math.exp(beta * decayed_val)

            raw_weights[m_id] = exp_val

        sum_raw = sum(raw_weights.values())
        raw_normalized: Dict[str, float] = {m_id: w / sum_raw for m_id, w in raw_weights.items()}

        # 2. Evaluate Demotion Circuit Breakers & Floor Thresholds
        active_weights: Dict[str, float] = {}
        statuses: List[ModelWeightStatus] = []
        demoted_count = 0

        for m in models:
            m_id = m.model_id
            raw_w = raw_normalized[m_id]
            decayed_val = decayed_metrics[m_id]

            is_active = True
            stat = "ACTIVE"

            if method_upper == "IC_SOFTMAX" and m.recent_ic <= 0.0:
                is_active = False
                stat = "DEMOTED_NEGATIVE_IC"
                demoted_count += 1
            elif raw_w < cfg.min_weight_floor:
                is_active = False
                stat = "DEMOTED_BELOW_FLOOR"
                demoted_count += 1

            if is_active:
                active_weights[m_id] = raw_w

            statuses.append(ModelWeightStatus(
                model_id=m_id,
                raw_weight=round(raw_w, 4),
                final_normalized_weight=0.0, # Will set after normalization
                decayed_metric=round(decayed_val, 4),
                is_active=is_active,
                status=stat
            ))

        # 3. Normalize Active Weights
        sum_active = sum(active_weights.values())
        if sum_active <= 0.0:
            # Fallback equal weighting for all models if all demoted
            equal_w = 1.0 / len(models)
            for s in statuses:
                s.is_active = True
                s.status = "ACTIVE_FALLBACK"
                s.final_normalized_weight = round(equal_w, 4)
            active_count = len(models)
            demoted_count = 0
        else:
            active_count = len(active_weights)
            for s in statuses:
                if s.is_active:
                    s.final_normalized_weight = round(active_weights[s.model_id] / sum_active, 4)
                else:
                    s.final_normalized_weight = 0.0

        notes = (
            f"ENSEMBLE REWEIGHTED [{ensemble_id}]: {active_count} Active Models, {demoted_count} Demoted Models. "
            f"Decay Lambda = {lam}, Softmax Beta = {beta}, Method = {method_upper}."
        )
        logger.info(notes)

        return EnsembleWeightReport(
            ensemble_id=ensemble_id,
            active_model_count=active_count,
            demoted_model_count=demoted_count,
            model_statuses=statuses,
            status="ENSEMBLE_REWEIGHTED_SUCCESS",
            audit_notes=notes
        )
