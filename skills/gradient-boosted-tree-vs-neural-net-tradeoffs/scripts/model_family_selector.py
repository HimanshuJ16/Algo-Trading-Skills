"""Model-family selection prior for financial ML: GBDT vs deep neural network.

This module does not measure anything. It combines evidence-tagged priors about
GBDT and deep-network behaviour with the caller's own constraints (modality,
latency budget, governance posture, available rows) into a reproducible,
auditable *starting hypothesis* for an empirical bake-off.

The distinction matters for how the output may be used. The recommendation is a
weighted score over documented priors, not a benchmark result on the caller's
data. Federal Reserve SR 26-2 names "benchmarking to other models" and "a
comparison of alternative assumptions and methodologies" as validation
activities; this engine pre-registers which alternative to benchmark first, and
`stated_limitations` on every report says so explicitly.

Because the engine is a deterministic weighted sum with no statistical,
economic, or financial theory underpinning it, it is itself outside the SR 26-2
definition of a "model" (which "excludes simple arithmetic calculations ... as
well as deterministic rule-based processes and software"). The models it
recommends are not.

See references/standards.md for the quoted sources behind every prior.
"""
import hashlib
import json
import logging
import math
from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Accepted vocabularies
# ---------------------------------------------------------------------------
MODALITY_TABULAR_ENGINEERED = "TABULAR_ENGINEERED"
MODALITY_RAW_HIGH_FREQUENCY_TICKS = "RAW_HIGH_FREQUENCY_TICKS"
VALID_MODALITIES = frozenset({
    MODALITY_TABULAR_ENGINEERED,
    MODALITY_RAW_HIGH_FREQUENCY_TICKS,
})

COMPLIANCE_STRICT_MODEL_GOVERNANCE = "STRICT_MODEL_GOVERNANCE"
COMPLIANCE_INTERNAL_RESEARCH = "INTERNAL_RESEARCH"
VALID_COMPLIANCE_LEVELS = frozenset({
    COMPLIANCE_STRICT_MODEL_GOVERNANCE,
    COMPLIANCE_INTERNAL_RESEARCH,
})

# 'STRICT_SR11_7_MIFID2' named guidance that no longer governs: SR 26-2
# (17 April 2026) "supersedes and replaces SR letter 11-7". It also merged a US
# banking-supervision reference with an EU investment-firm one. Still accepted
# so existing callers keep working, but canonicalised on construction.
DEPRECATED_COMPLIANCE_ALIASES: Dict[str, str] = {
    "STRICT_SR11_7_MIFID2": COMPLIANCE_STRICT_MODEL_GOVERNANCE,
}

RECOMMEND_GBDT = "RECOMMEND_LIGHTGBM_XGBOOST"
RECOMMEND_NEURAL_NET = "RECOMMEND_NEURAL_NETWORK_LSTM_TRANSFORMER"
RECOMMEND_HYBRID = "RECOMMEND_HYBRID_ENSEMBLE"

DATA_SUFFICIENT_FOR_DEEP_LEARNING = "SUFFICIENT_FOR_DEEP_LEARNING"
DATA_BELOW_DEEP_LEARNING_REFERENCE = "BELOW_DEEP_LEARNING_REFERENCE"


class ModelFamilySelectorError(ValueError):
    """Raised when a specification or engine configuration cannot be scored."""


@dataclass(frozen=True)
class DimensionPrior:
    """A per-dimension prior with the evidence it rests on.

    Scores are on a 0-10 scale and are *relative* judgements, not measurements.
    Where the published evidence shows no consistent winner, the two scores are
    deliberately equal: a dimension scored equally contributes identically to
    both totals and therefore cannot tilt the recommendation.
    """
    gbdt: float
    nn: float
    evidence: str


# Baseline priors. Every gap here traces to references/standards.md.
DEFAULT_DIMENSION_PRIORS: Dict[str, DimensionPrior] = {
    "tabular_data_fit": DimensionPrior(
        gbdt=9.5, nn=5.5,
        evidence="Grinsztajn et al. 2022 (tree-based models state-of-the-art on "
                 "medium-sized ~10K-sample tabular data); Shwartz-Ziv & Armon 2022 "
                 "(XGBoost outperforms the surveyed deep models and needs much less tuning).",
    ),
    "sequential_pattern_extraction": DimensionPrior(
        gbdt=3.0, nn=9.5,
        evidence="The tabular results above are explicitly scoped to tabular data; "
                 "Grinsztajn et al. open by conceding deep learning's 'tremendous "
                 "progress on text and image datasets'. Raw tick/order-book sequences "
                 "are that regime, not the tabular one.",
    ),
    "interpretability_compliance": DimensionPrior(
        gbdt=9.0, nn=4.0,
        evidence="TreeSHAP gives tree ensembles exact Shapley attributions in "
                 "polynomial time (Lundberg et al. 2019/2020); deep-network attribution "
                 "is sampling- or gradient-based and carries estimation variance a "
                 "reviewer can challenge. Note this is a tractability gap, not a "
                 "regulatory requirement - no regulator mandates SHAP.",
    ),
    "inference_speed_latency": DimensionPrior(
        gbdt=9.0, nn=5.0,
        evidence="Directional prior only: for comparable capacity a boosted-tree "
                 "ensemble evaluates fewer arithmetic operations per inference than a "
                 "multi-layer recurrent network on CPU. LOW CONFIDENCE - latency is "
                 "governed by model size, batch size and runtime, not by family. "
                 "Measure it; see skill model-inference-latency-budget-for-live-trading.",
    ),
    "regime_shift_robustness": DimensionPrior(
        gbdt=6.0, nn=6.0,
        evidence="Deliberately neutral, and therefore decision-neutral. TableShift "
                 "(Gardner et al., NeurIPS 2023) found 'no model consistently "
                 "outperforms the standard tabular baselines of XGBoost, LightGBM, or "
                 "CatBoost' out of distribution, that no technique eliminates shift "
                 "gaps, and that ID and OOD accuracy correlate at rho=0.81. Neither "
                 "family has a defensible robustness advantage. Countervailing point: "
                 "tree predictions are 'piecewise constant approximations, and "
                 "therefore they are not good at extrapolation' (scikit-learn), so a "
                 "feature moving outside its training range saturates a GBDT rather "
                 "than extrapolating.",
    ),
}

# Weight applied when a dimension is the caller's binding constraint, versus the
# residual weight it carries otherwise.
_WEIGHT_TABULAR_BINDING = 0.40
_WEIGHT_SEQUENTIAL_BINDING = 0.50
_WEIGHT_COMPLIANCE_BINDING = 0.25
_WEIGHT_LATENCY_BINDING = 0.25
_WEIGHT_REGIME = 0.15
_WEIGHT_RESIDUAL = 0.05

# Latency at or below which the latency dimension becomes a binding constraint.
LATENCY_BINDING_THRESHOLD_US = 500.0

# Reference row count below which a deep network is treated as unsupportable by
# the data. Anchored on Grinsztajn et al.'s "medium-sized data (~10K samples)"
# regime, in which tree-based models were state of the art. That figure is a
# *tabular* benchmark result; applying it to sequence models is an extrapolation,
# not a published threshold, which is why it is a constructor argument.
DEFAULT_DEEP_LEARNING_REFERENCE_ROWS = 10_000

# Score gap below which neither family is preferred outright.
DEFAULT_DECISION_MARGIN = 1.0

_UNIVERSAL_LIMITATIONS = (
    "This recommendation is a weighted prior, not a benchmark result. It says which "
    "family to try first; it does not say which will perform better on this dataset. "
    "Settle that with a walk-forward bake-off of both families under transaction costs.",
    "The latency dimension is a family-level prior, not a measurement. This engine "
    "does not certify that any model meets the stated latency budget.",
    "Switching model family in a deployed EU algorithmic trading system is a material "
    "change under ESMA's 2026 supervisory briefing (para. 31) - it must be retested, "
    "timestamped, approved and recorded before it goes live.",
)


@dataclass
class DatasetSpec:
    """Caller-supplied constraints for one model-family decision.

    Args:
        modality: one of ``VALID_MODALITIES``.
        sample_size_rows: labelled training rows (or sequences) actually
            available. Gates the deep-learning branch - see
            ``deep_learning_reference_rows``.
        feature_count: engineered feature count. **Recorded in the audit record
            only; it does not affect the score.** There is no defensible
            published threshold on feature count that separates the two
            families, and inventing one would make the report less honest, not
            more informative.
        latency_budget_us: inference budget in microseconds. Must be finite and
            strictly positive.
        regulatory_compliance: one of ``VALID_COMPLIANCE_LEVELS``. The legacy
            value ``'STRICT_SR11_7_MIFID2'`` is accepted and canonicalised.

    Raises:
        ModelFamilySelectorError: on any unrecognised or out-of-range value.
            An unrecognised modality is rejected rather than defaulted, because
            a defaulted modality previously produced an audit note asserting the
            opposite of the caller's actual data.
    """
    modality: str
    sample_size_rows: int
    feature_count: int
    latency_budget_us: float
    regulatory_compliance: str

    def __post_init__(self) -> None:
        canonical = DEPRECATED_COMPLIANCE_ALIASES.get(self.regulatory_compliance)
        if canonical is not None:
            logger.warning(
                "regulatory_compliance=%r is deprecated: SR 26-2 (17 April 2026) "
                "supersedes SR 11-7, and the value also conflated US and EU regimes. "
                "Canonicalised to %r.",
                self.regulatory_compliance, canonical,
            )
            self.regulatory_compliance = canonical

        if self.modality not in VALID_MODALITIES:
            raise ModelFamilySelectorError(
                f"modality {self.modality!r} is not recognised; expected one of "
                f"{sorted(VALID_MODALITIES)}. Refusing to guess - an unrecognised "
                f"modality cannot be scored, and defaulting it produces an audit "
                f"record that describes data the caller does not have."
            )
        if self.regulatory_compliance not in VALID_COMPLIANCE_LEVELS:
            raise ModelFamilySelectorError(
                f"regulatory_compliance {self.regulatory_compliance!r} is not "
                f"recognised; expected one of {sorted(VALID_COMPLIANCE_LEVELS)} "
                f"(or the deprecated {sorted(DEPRECATED_COMPLIANCE_ALIASES)})."
            )
        if not isinstance(self.sample_size_rows, int) or isinstance(self.sample_size_rows, bool):
            raise ModelFamilySelectorError(
                f"sample_size_rows must be an int, got {type(self.sample_size_rows).__name__}"
            )
        if self.sample_size_rows < 1:
            raise ModelFamilySelectorError(
                f"sample_size_rows must be >= 1, got {self.sample_size_rows}"
            )
        if not isinstance(self.feature_count, int) or isinstance(self.feature_count, bool):
            raise ModelFamilySelectorError(
                f"feature_count must be an int, got {type(self.feature_count).__name__}"
            )
        if self.feature_count < 1:
            raise ModelFamilySelectorError(
                f"feature_count must be >= 1, got {self.feature_count}"
            )
        try:
            budget = float(self.latency_budget_us)
        except (TypeError, ValueError) as exc:
            raise ModelFamilySelectorError(
                f"latency_budget_us must be numeric, got {self.latency_budget_us!r}"
            ) from exc
        if not math.isfinite(budget):
            raise ModelFamilySelectorError(
                f"latency_budget_us must be finite, got {self.latency_budget_us!r}. "
                f"A NaN budget silently compares False against every threshold and "
                f"would drop the latency constraint without saying so."
            )
        if budget <= 0.0:
            raise ModelFamilySelectorError(
                f"latency_budget_us must be > 0, got {budget}"
            )
        self.latency_budget_us = budget


@dataclass
class ModelFamilyTradeoffReport:
    """Audit record for one model-family decision.

    ``dimension_scores`` and ``applied_dimension_weights`` let a reviewer
    recompute both published scores from the record alone, to the two decimal
    places the record publishes. ``score_gap`` is the decision variable itself,
    taken from the unrounded scores, and ``decision_margin`` is the threshold it
    was compared against - so the recommendation follows from the record without
    re-deriving it from two rounded numbers. Read ``stated_limitations`` before
    acting on ``recommended_model_family``.
    """
    recommended_model_family: str
    gbdt_overall_score_0_to_10: float
    neural_net_overall_score_0_to_10: float
    dimension_scores: Dict[str, Dict[str, float]]
    primary_decision_factors: List[str]
    audit_notes: str
    applied_dimension_weights: Dict[str, float] = field(default_factory=dict)
    dimension_evidence: Dict[str, str] = field(default_factory=dict)
    score_gap: float = 0.0
    decision_margin: float = 0.0
    stated_limitations: List[str] = field(default_factory=list)
    data_sufficiency: str = ""
    config_fingerprint: str = ""


class ModelFamilySelectorEngine:
    """Scores GBDT against deep neural networks over five documented dimensions.

    Args:
        dimension_priors: override the baseline priors. Must supply exactly the
            keys of ``DEFAULT_DIMENSION_PRIORS``, with scores in [0, 10].
        decision_margin: score gap below which the result is
            ``RECOMMEND_HYBRID_ENSEMBLE``. The hybrid branch is not a hedge -
            Shwartz-Ziv & Armon found "an ensemble of deep models and XGBoost
            performs better on these datasets than XGBoost alone".
        deep_learning_reference_rows: row count below which the sequential
            dimension is demoted to residual weight regardless of modality,
            because a deep sequence model cannot be supported by the data.

    Raises:
        ModelFamilySelectorError: on invalid configuration.
    """

    def __init__(
        self,
        dimension_priors: Optional[Mapping[str, DimensionPrior]] = None,
        decision_margin: float = DEFAULT_DECISION_MARGIN,
        deep_learning_reference_rows: int = DEFAULT_DEEP_LEARNING_REFERENCE_ROWS,
    ) -> None:
        priors = dict(DEFAULT_DIMENSION_PRIORS if dimension_priors is None else dimension_priors)
        if set(priors) != set(DEFAULT_DIMENSION_PRIORS):
            missing = sorted(set(DEFAULT_DIMENSION_PRIORS) - set(priors))
            extra = sorted(set(priors) - set(DEFAULT_DIMENSION_PRIORS))
            raise ModelFamilySelectorError(
                f"dimension_priors must supply exactly the five scored dimensions; "
                f"missing={missing}, unexpected={extra}"
            )
        for name, prior in priors.items():
            for family, value in (("gbdt", prior.gbdt), ("nn", prior.nn)):
                if not math.isfinite(value) or not 0.0 <= value <= 10.0:
                    raise ModelFamilySelectorError(
                        f"prior {name}.{family} must be a finite score in [0, 10], got {value!r}"
                    )
        if not math.isfinite(decision_margin) or decision_margin < 0.0:
            raise ModelFamilySelectorError(
                f"decision_margin must be finite and >= 0, got {decision_margin!r}"
            )
        if not isinstance(deep_learning_reference_rows, int) or deep_learning_reference_rows < 1:
            raise ModelFamilySelectorError(
                f"deep_learning_reference_rows must be an int >= 1, "
                f"got {deep_learning_reference_rows!r}"
            )

        self._priors: Dict[str, DimensionPrior] = priors
        self._decision_margin = float(decision_margin)
        self._deep_learning_reference_rows = deep_learning_reference_rows
        self._config_fingerprint = self._fingerprint()

    @property
    def config_fingerprint(self) -> str:
        """Stable digest of the priors and thresholds that produce a score."""
        return self._config_fingerprint

    def _fingerprint(self) -> str:
        payload = {
            "priors": {
                name: [prior.gbdt, prior.nn]
                for name, prior in sorted(self._priors.items())
            },
            "decision_margin": self._decision_margin,
            "deep_learning_reference_rows": self._deep_learning_reference_rows,
            "latency_binding_threshold_us": LATENCY_BINDING_THRESHOLD_US,
            "weights": [
                _WEIGHT_TABULAR_BINDING, _WEIGHT_SEQUENTIAL_BINDING,
                _WEIGHT_COMPLIANCE_BINDING, _WEIGHT_LATENCY_BINDING,
                _WEIGHT_REGIME, _WEIGHT_RESIDUAL,
            ],
        }
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]

    def evaluate_model_family_tradeoffs(self, spec: DatasetSpec) -> ModelFamilyTradeoffReport:
        """Score GBDT against deep neural networks for one dataset specification.

        Args:
            spec: validated caller constraints.

        Returns:
            A ``ModelFamilyTradeoffReport`` whose scores are reproducible from
            its own ``dimension_scores`` and ``applied_dimension_weights``.

        Raises:
            ModelFamilySelectorError: if ``spec`` is not a ``DatasetSpec``, or
                was mutated into an invalid state after construction.
        """
        if not isinstance(spec, DatasetSpec):
            raise ModelFamilySelectorError(
                f"spec must be a DatasetSpec, got {type(spec).__name__}"
            )
        # Re-validate: DatasetSpec is mutable, so a caller may have changed a
        # field after construction and bypassed __post_init__.
        spec.__post_init__()

        is_tabular = spec.modality == MODALITY_TABULAR_ENGINEERED
        is_sequential = spec.modality == MODALITY_RAW_HIGH_FREQUENCY_TICKS
        is_strict_governance = spec.regulatory_compliance == COMPLIANCE_STRICT_MODEL_GOVERNANCE
        is_latency_binding = spec.latency_budget_us <= LATENCY_BINDING_THRESHOLD_US
        has_deep_learning_scale = spec.sample_size_rows >= self._deep_learning_reference_rows

        decision_factors: List[str] = []
        limitations: List[str] = list(_UNIVERSAL_LIMITATIONS)

        if is_tabular:
            decision_factors.append(
                "Engineered tabular dataset favours GBDT split rules (Grinsztajn et al. 2022)"
            )
        else:
            decision_factors.append(
                "Raw high-frequency tick sequence favours deep-network representation learning"
            )

        # Sample size gates the sequential branch: the representation-learning
        # advantage is only realisable if there is enough data to learn one.
        sequential_weight = _WEIGHT_SEQUENTIAL_BINDING if is_sequential else _WEIGHT_RESIDUAL
        if is_sequential and not has_deep_learning_scale:
            sequential_weight = _WEIGHT_RESIDUAL
            decision_factors.append(
                f"Sequential advantage withheld: {spec.sample_size_rows:,} rows is below the "
                f"{self._deep_learning_reference_rows:,}-row deep-learning reference point"
            )
            limitations.append(
                f"The dataset ({spec.sample_size_rows:,} rows) is below the configured "
                f"{self._deep_learning_reference_rows:,}-row reference point, so the sequential "
                f"dimension was demoted to residual weight. That reference point is anchored on "
                f"a tabular benchmark (Grinsztajn et al. 2022, ~10K samples) and extended to "
                f"sequence models by judgement, not by a published result. Override it via "
                f"deep_learning_reference_rows if you have a better figure for your data."
            )
            logger.warning(
                "Deep-learning branch gated: sample_size_rows=%d < reference=%d",
                spec.sample_size_rows, self._deep_learning_reference_rows,
            )

        if is_strict_governance:
            decision_factors.append(
                "Strict model-governance posture favours GBDT: TreeSHAP yields exact "
                "attributions in polynomial time (no regulator mandates SHAP - see standards.md)"
            )
        if is_latency_binding:
            decision_factors.append(
                f"Latency budget of {spec.latency_budget_us:g}us is at or below the "
                f"{LATENCY_BINDING_THRESHOLD_US:g}us binding threshold, favouring a "
                f"lightweight tree ensemble - subject to measurement"
            )

        weights: Dict[str, float] = {
            "tabular_data_fit": _WEIGHT_TABULAR_BINDING if is_tabular else _WEIGHT_RESIDUAL,
            "sequential_pattern_extraction": sequential_weight,
            "interpretability_compliance": (
                _WEIGHT_COMPLIANCE_BINDING if is_strict_governance else _WEIGHT_RESIDUAL
            ),
            "inference_speed_latency": (
                _WEIGHT_LATENCY_BINDING if is_latency_binding else _WEIGHT_RESIDUAL
            ),
            "regime_shift_robustness": _WEIGHT_REGIME,
        }
        total_weight = math.fsum(weights.values())
        weights = {name: w / total_weight for name, w in weights.items()}

        gbdt_final = math.fsum(
            self._priors[name].gbdt * weight for name, weight in weights.items()
        )
        nn_final = math.fsum(
            self._priors[name].nn * weight for name, weight in weights.items()
        )
        gbdt_score = round(gbdt_final, 2)
        nn_score = round(nn_final, 2)

        # Derive the decision variable from the unrounded scores, then publish
        # it as `score_gap`. Subtracting two independently-rounded scores would
        # add up to 0.01 of slack and would let a dimension scored equally for
        # both families shift the gap - which must never happen.
        diff = round(gbdt_final - nn_final, 2)
        if diff >= self._decision_margin:
            rec = RECOMMEND_GBDT
        elif diff <= -self._decision_margin:
            rec = RECOMMEND_NEURAL_NET
        else:
            rec = RECOMMEND_HYBRID
            limitations.append(
                f"Scores are within the {self._decision_margin:g}-point decision margin "
                f"(gap {diff:+.2f}). The hybrid result means the priors do not separate the "
                f"families for this specification - it is a signal to benchmark both, not a "
                f"finding that an ensemble is optimal."
            )

        data_sufficiency = (
            DATA_SUFFICIENT_FOR_DEEP_LEARNING if has_deep_learning_scale
            else DATA_BELOW_DEEP_LEARNING_REFERENCE
        )

        notes = (
            f"MODEL FAMILY PRIOR: Recommended '{rec}'. "
            f"GBDT Score = {gbdt_score:.2f}/10 vs Neural Net Score = {nn_score:.2f}/10 "
            f"(margin {self._decision_margin:g}, config {self._config_fingerprint}). "
            f"Dataset: {spec.modality}, {spec.sample_size_rows:,} rows, "
            f"{spec.feature_count} features (features recorded, not scored), "
            f"{spec.latency_budget_us:g}us budget, {spec.regulatory_compliance}. "
            f"Primary Factors: {'; '.join(decision_factors)}. "
            f"This is a prior for a bake-off, not a benchmark result."
        )
        logger.info(notes)

        return ModelFamilyTradeoffReport(
            recommended_model_family=rec,
            gbdt_overall_score_0_to_10=gbdt_score,
            neural_net_overall_score_0_to_10=nn_score,
            dimension_scores={
                name: {"gbdt": prior.gbdt, "nn": prior.nn}
                for name, prior in self._priors.items()
            },
            primary_decision_factors=decision_factors,
            audit_notes=notes,
            applied_dimension_weights={name: round(w, 6) for name, w in weights.items()},
            score_gap=diff,
            decision_margin=self._decision_margin,
            dimension_evidence={name: prior.evidence for name, prior in self._priors.items()},
            stated_limitations=limitations,
            data_sufficiency=data_sufficiency,
            config_fingerprint=self._config_fingerprint,
        )
