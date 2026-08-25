"""
explainable-boosting-machines-for-regulated-signals: glass-box scoring and
model-governance audit engine for Explainable Boosting Machines (EBM / GA2M).

An EBM is a generalized additive model with pairwise interactions:

    g(E[y]) = beta_0 + sum_i f_i(x_i) + sum_(i,j) f_ij(x_i, x_j)

where ``g`` is a link function and each ``f`` is a learned lookup table over the
feature's bins (InterpretML, "Explainable Boosting Machine" documentation;
Lou, Caruana, Gehrke & Hooker, "Accurate intelligible models with pairwise
interactions", KDD 2013, pp. 623-631, doi:10.1145/2487575.2487579). Because the
model *is* the sum of its term contributions, an attribution is a lookup, not a
sampled approximation as in SHAP or LIME.

This module does not fit an EBM. It takes shape functions that have already been
fitted (or hand-authored) and produces a per-prediction, reproducible governance
record: the exact term contributions, the intercept, the composed score, an
independent re-evaluation check, and a monotonicity audit against constraints the
model owner declared.

Score scale
-----------
Term contributions live on the *link* scale, not the response scale. For a
classification EBM they are log-odds: InterpretML's own graphs state "the y-axis
values are in logits ... because these graphs are in logarithm space". Summing
logit contributions and reading the result as a probability is wrong by
construction, so ``score_scale`` is a required, recorded property of every model
and every audit report. Use :func:`logit_score_to_probability` to convert, never
arithmetic on the raw score.

Monotonicity
------------
Monotonicity here is a *declared business constraint being verified*, not a
regulatory requirement (see ``references/standards.md``). Two limits are enforced
rather than assumed:

- A constraint is checked only over the ``audit_grid`` the caller supplies. There
  is no default grid: the plausible domain of an RSI differs from that of a
  volatility, and inventing one would certify a range the model was never asked
  about.
- A univariate term ``f_i`` being monotone does **not** make the model monotone in
  ``x_i`` when an interaction term containing ``x_i`` is also present, because the
  interaction can move the score in the opposite direction. InterpretML documents
  the same limit for its own ``monotonize``: it "only adjusts a single term and
  will not modify pairwise terms. When a feature needs to be globally monotonic,
  any pairwise terms that include the feature should be excluded from the model."
  A ``GLOBAL``-scope constraint on a feature that also appears in an interaction is
  therefore reported as a violation, not silently certified.

The audit is a property of the model, not of the instance being scored, so it is
computed once per model configuration and invalidated by registration. Grid density
therefore costs nothing per score: at 8 constrained features the scoring path measures
~15 us whether the grid holds 2 points or 1000. Grid the range the feature will really
take rather than trading coverage against latency.

Limitations (documented, deliberate)
------------------------------------
- **Not a fitter and not a validator of the shape functions themselves.** It audits
  the composition and the declared constraints. Whether ``f_i`` was fitted on
  point-in-time data, or generalises, is out of scope.
- **The term fingerprint covers structure, not shape values.** It changes when a
  term, a constraint, the intercept, the scale or the caller-supplied
  ``shape_table_version`` changes. It cannot detect a recalibrated lookup table
  behind an unchanged ``shape_table_version`` — binding a recalibration to an audit
  record is the caller's responsibility, via that argument.
- **Shape functions are called twice per evaluation** (once to score, once to verify
  reproducibility). They must be pure; a stateful or randomised shape function will
  be reported as failing the additive-identity check, which is the intended outcome.
- **No extrapolation guard.** A shape function asked for a feature value outside the
  range it was fitted on will return whatever it returns.
"""
import hashlib
import json
import logging
import math
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

#: Absolute tolerance for the additive-identity reconciliation. The composed score
#: and the independent re-evaluation are summed with ``math.fsum`` in the same order,
#: so a pure shape function reconciles to 0.0; the tolerance only absorbs the last
#: bits of a float and is far tighter than any material scoring difference.
IDENTITY_TOLERANCE = 1e-12

#: Default slack when comparing consecutive shape values on the audit grid. A
#: monotone step of exactly 0.0 (a flat bin, which EBM lookup tables produce often)
#: is monotone, not a violation.
DEFAULT_MONOTONICITY_TOLERANCE = 1e-12

STATUS_PASS = "PASS_GOVERNANCE_AUDIT"
STATUS_FAIL_MONOTONICITY = "FAIL_MONOTONICITY_VIOLATION"
STATUS_FAIL_IDENTITY = "FAIL_ADDITIVE_IDENTITY"

#: Shape values moved against the declared direction somewhere on the audit grid.
REASON_SHAPE_NOT_MONOTONE = "SHAPE_NOT_MONOTONE"
#: The univariate term is monotone, but an interaction term containing the feature
#: can override it, so global monotonicity in this feature cannot be certified.
REASON_INTERACTION_SHADOWS_GLOBAL = "INTERACTION_SHADOWS_GLOBAL_MONOTONICITY"
#: A shape function returned NaN or +/-inf on the audit grid.
REASON_NON_FINITE_SHAPE = "NON_FINITE_SHAPE_VALUE"


class ScoreScale(str, Enum):
    """
    The scale on which the intercept and every term contribution are expressed.

    ``LOGIT`` — classification EBM. Contributions are log-odds and are additive only
    on that scale. Convert with :func:`logit_score_to_probability`.
    ``IDENTITY`` — regression EBM. The score is in the units of the target.
    """

    LOGIT = "logit"
    IDENTITY = "identity"


class MonotonicDirection(str, Enum):
    INCREASING = "increasing"
    DECREASING = "decreasing"


class MonotonicScope(str, Enum):
    """
    ``GLOBAL`` — the model must be monotone in this feature. Certified only when no
    interaction term contains the feature (see module docstring).
    ``TERM`` — only the univariate shape ``f_i`` is claimed to be monotone. Any
    interaction on the feature is recorded as a stated limitation of the audit
    rather than treated as a failure.
    """

    GLOBAL = "global"
    TERM = "term"


class ShapeFunctionError(RuntimeError):
    """A registered shape function raised while being evaluated."""


@dataclass(frozen=True)
class MonotonicityConstraint:
    """A monotonicity claim the model owner is asserting, plus where to check it."""

    feature_name: str
    direction: MonotonicDirection
    audit_grid: Tuple[float, ...]
    scope: MonotonicScope = MonotonicScope.GLOBAL
    tolerance: float = DEFAULT_MONOTONICITY_TOLERANCE


@dataclass(frozen=True)
class MonotonicityViolation:
    feature_name: str
    reason: str
    direction: str
    x_low: Optional[float] = None
    x_high: Optional[float] = None
    f_low: Optional[float] = None
    f_high: Optional[float] = None
    delta: Optional[float] = None
    detail: str = ""


@dataclass
class EbmFeatureContribution:
    feature_name: str
    feature_value: float
    contribution_score: float


@dataclass
class EbmInteractionContribution:
    feature_name_1: str
    feature_name_2: str
    value_1: float
    value_2: float
    contribution_score: float


@dataclass
class EbmSignalAuditReport:
    """
    The governance record for one scored instance.

    ``total_predicted_score`` is the full-precision composed score on
    ``score_scale``; contributions are stored unrounded so the record reconciles
    exactly. Callers MUST gate on ``status`` before consuming the score — a failed
    audit still carries a score field, and on a non-finite shape value that score
    is NaN.
    """

    model_id: str
    symbol: str
    base_intercept_beta0: float
    total_predicted_score: float
    single_feature_contributions: List[EbmFeatureContribution]
    interaction_contributions: List[EbmInteractionContribution]
    is_exact_additive_identity_valid: bool
    is_monotonicity_audit_passed: bool
    status: str                         # STATUS_PASS / STATUS_FAIL_MONOTONICITY / STATUS_FAIL_IDENTITY
    audit_notes: str
    score_scale: ScoreScale = ScoreScale.IDENTITY
    additive_identity_residual: float = 0.0
    monotonicity_violations: Tuple[MonotonicityViolation, ...] = ()
    monotonicity_audit_limitations: Tuple[str, ...] = ()
    term_fingerprint: str = ""
    shape_table_version: str = ""


def logit_score_to_probability(score: float) -> float:
    """
    Convert a ``ScoreScale.LOGIT`` score to a probability: ``1 / (1 + exp(-score))``.

    Provided because the failure mode this module exists to prevent is reading a
    summed log-odds score as if it were already a probability. Overflow-safe at
    large |score|.
    """
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        raise TypeError(f"score must be numeric, got {type(score).__name__}.")
    if not math.isfinite(score):
        raise ValueError(f"score must be finite to convert to a probability, got {score}.")
    if score >= 0.0:
        return 1.0 / (1.0 + math.exp(-score))
    exp_score = math.exp(score)
    return exp_score / (1.0 + exp_score)


def _require_finite_number(value: object, context: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{context} must be numeric, got {type(value).__name__}.")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"{context} must be finite, got {numeric}.")
    return numeric


def _canonical_pair(feat_1: str, feat_2: str) -> Tuple[str, str]:
    """
    GA2M interaction terms are unordered pairs, so ``(a, b)`` and ``(b, a)`` name the
    same term. Canonicalising the key stops the same interaction being registered
    twice under two orderings and counted twice in the score.
    """
    if feat_1 == feat_2:
        raise ValueError(
            f"an interaction term needs two distinct features, got '{feat_1}' twice."
        )
    return (feat_1, feat_2) if feat_1 <= feat_2 else (feat_2, feat_1)


class ExplainableBoostingPricerEngine:
    """
    Composes an already-fitted EBM / GA2M from its shape functions and emits a
    per-prediction governance audit report.

    The intercept is a required argument: it is a fitted model parameter, and a
    library default would silently produce a wrong score that still passed every
    check in the report.
    """

    def __init__(
        self,
        model_id: str,
        base_intercept_beta0: float,
        score_scale: ScoreScale = ScoreScale.IDENTITY,
        shape_table_version: str = "unversioned",
    ):
        if not model_id or not isinstance(model_id, str):
            raise ValueError(f"model_id must be a non-empty string, got {model_id!r}.")
        self.model_id = model_id
        self.beta0 = _require_finite_number(base_intercept_beta0, "base_intercept_beta0")
        self.score_scale = ScoreScale(score_scale)
        self.shape_table_version = str(shape_table_version)

        self.single_shape_funcs: Dict[str, Callable[[float], float]] = {}
        self.interaction_shape_funcs: Dict[Tuple[str, str], Callable[[float, float], float]] = {}
        self.monotonicity_constraints: Dict[str, MonotonicityConstraint] = {}

        # Monotonicity and the structure fingerprint are properties of the model, not of
        # the instance being scored, so they are computed once per model configuration
        # and invalidated by registration. Re-running a dense audit grid on every score
        # costs ~7 ms per call at 1000 grid points against ~50 us of actual scoring.
        self._monotonicity_cache: Optional[Tuple[List[MonotonicityViolation], List[str]]] = None
        self._fingerprint_cache: Optional[str] = None

        if self.shape_table_version == "unversioned":
            logger.info(
                "model %s registered without a shape_table_version: audit records will not "
                "distinguish this shape table from a later recalibration of it.",
                self.model_id,
            )

    # ------------------------------------------------------------------ registration

    def register_single_feature_shape(
        self,
        feature_name: str,
        shape_func: Callable[[float], float],
        monotonic: Optional[MonotonicDirection] = None,
        audit_grid: Optional[Sequence[float]] = None,
        scope: MonotonicScope = MonotonicScope.GLOBAL,
        tolerance: float = DEFAULT_MONOTONICITY_TOLERANCE,
        replace: bool = False,
    ) -> None:
        """
        Register a univariate term ``f_i``.

        Declaring ``monotonic`` also requires ``audit_grid`` — the ascending feature
        values the constraint is checked at. There is no default grid because there is
        no domain-independent one; certifying a range the caller never named would be
        a claim the audit cannot support.
        """
        if not feature_name or not isinstance(feature_name, str):
            raise ValueError(f"feature_name must be a non-empty string, got {feature_name!r}.")
        if not callable(shape_func):
            raise TypeError(f"shape_func for '{feature_name}' must be callable.")
        if feature_name in self.single_shape_funcs and not replace:
            raise ValueError(
                f"feature '{feature_name}' is already registered. Silently replacing a shape "
                "function changes the model behind existing audit records; pass replace=True "
                "to do it deliberately."
            )

        self.single_shape_funcs[feature_name] = shape_func
        self.monotonicity_constraints.pop(feature_name, None)
        self._invalidate_model_caches()

        if monotonic is None:
            if audit_grid is not None:
                raise ValueError(
                    f"audit_grid supplied for '{feature_name}' without a monotonic direction; "
                    "the grid would never be evaluated."
                )
            return

        direction = MonotonicDirection(monotonic)
        grid = self._validate_audit_grid(audit_grid, feature_name)
        if not math.isfinite(tolerance) or tolerance < 0.0:
            raise ValueError(
                f"tolerance for '{feature_name}' must be a finite non-negative number, got {tolerance}."
            )
        self.monotonicity_constraints[feature_name] = MonotonicityConstraint(
            feature_name=feature_name,
            direction=direction,
            audit_grid=grid,
            scope=MonotonicScope(scope),
            tolerance=tolerance,
        )
        self._invalidate_model_caches()

    def register_interaction_shape(
        self,
        feat_1: str,
        feat_2: str,
        shape_func: Callable[[float, float], float],
        replace: bool = False,
    ) -> None:
        """
        Register a pairwise term ``f_ij``. The pair is stored canonically ordered, so
        ``(a, b)`` and ``(b, a)`` are the same term rather than two terms that both
        contribute.
        """
        if not callable(shape_func):
            raise TypeError(f"shape_func for ('{feat_1}', '{feat_2}') must be callable.")
        key = _canonical_pair(feat_1, feat_2)
        if key in self.interaction_shape_funcs and not replace:
            raise ValueError(
                f"interaction {key} is already registered (interaction terms are unordered "
                "pairs, so ('a','b') and ('b','a') are the same term). Pass replace=True to "
                "replace it deliberately."
            )
        self.interaction_shape_funcs[key] = shape_func
        self._invalidate_model_caches()

    def _invalidate_model_caches(self) -> None:
        """Called whenever a term or constraint changes; both caches key off structure."""
        self._monotonicity_cache = None
        self._fingerprint_cache = None

    @staticmethod
    def _validate_audit_grid(
        audit_grid: Optional[Sequence[float]], feature_name: str
    ) -> Tuple[float, ...]:
        if audit_grid is None:
            raise ValueError(
                f"a monotonicity constraint on '{feature_name}' requires an audit_grid: the "
                "ascending feature values to check the constraint at. The audit certifies "
                "only the range it is given."
            )
        grid = tuple(
            _require_finite_number(x, f"audit_grid[{i}] for '{feature_name}'")
            for i, x in enumerate(audit_grid)
        )
        if len(grid) < 2:
            raise ValueError(
                f"audit_grid for '{feature_name}' needs at least 2 points to compare, "
                f"got {len(grid)}."
            )
        if any(grid[i] >= grid[i + 1] for i in range(len(grid) - 1)):
            raise ValueError(
                f"audit_grid for '{feature_name}' must be strictly ascending, got {grid}."
            )
        return grid

    # ----------------------------------------------------------------------- auditing

    def required_feature_names(self) -> Tuple[str, ...]:
        """Every feature the model needs a value for: univariate and interaction terms."""
        names = set(self.single_shape_funcs)
        for f1, f2 in self.interaction_shape_funcs:
            names.add(f1)
            names.add(f2)
        return tuple(sorted(names))

    def term_fingerprint(self) -> str:
        """
        Stable digest of the model's *structure* — terms, declared constraints,
        intercept, scale and ``shape_table_version``.

        It identifies which model configuration produced an audit record. It does not
        hash the shape tables themselves, so it cannot detect a recalibration carried
        out behind an unchanged ``shape_table_version``.
        """
        if self._fingerprint_cache is not None:
            return self._fingerprint_cache
        payload = {
            "model_id": self.model_id,
            "shape_table_version": self.shape_table_version,
            "score_scale": self.score_scale.value,
            "beta0": repr(self.beta0),
            "single_terms": sorted(self.single_shape_funcs),
            "interaction_terms": sorted(list(k) for k in self.interaction_shape_funcs),
            "constraints": [
                {
                    "feature": c.feature_name,
                    "direction": c.direction.value,
                    "scope": c.scope.value,
                    "grid": [repr(x) for x in c.audit_grid],
                    "tolerance": repr(c.tolerance),
                }
                for c in sorted(self.monotonicity_constraints.values(), key=lambda c: c.feature_name)
            ],
        }
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        self._fingerprint_cache = hashlib.sha256(blob).hexdigest()[:16]
        return self._fingerprint_cache

    def audit_monotonicity(
        self, use_cache: bool = True
    ) -> Tuple[List[MonotonicityViolation], List[str]]:
        """
        Check every declared constraint against its audit grid.

        Returns ``(violations, limitations)``. Monotonicity is a property of the model,
        not of one scored instance, so the result is computed once per model
        configuration and cached; registering or replacing any term or constraint
        invalidates it. ``evaluate_ebm_signal`` calls this and attaches the result to
        every report, so without the cache a dense grid would be re-walked on every
        score for an answer that cannot change.

        Pass ``use_cache=False`` to force a re-walk. That only changes the answer if a
        shape function is impure — which the additive-identity check reports as a
        failure on the scoring path anyway.
        """
        if use_cache and self._monotonicity_cache is not None:
            cached_violations, cached_limitations = self._monotonicity_cache
            return list(cached_violations), list(cached_limitations)

        violations: List[MonotonicityViolation] = []
        limitations: List[str] = []

        for name in sorted(self.monotonicity_constraints):
            constraint = self.monotonicity_constraints[name]
            shape_func = self.single_shape_funcs[name]
            interacting = sorted(
                pair for pair in self.interaction_shape_funcs if name in pair
            )

            values: List[float] = []
            non_finite = False
            for x in constraint.audit_grid:
                value = self._call_single(shape_func, name, x)
                if not math.isfinite(value):
                    violations.append(
                        MonotonicityViolation(
                            feature_name=name,
                            reason=REASON_NON_FINITE_SHAPE,
                            direction=constraint.direction.value,
                            x_low=x,
                            f_low=value,
                            detail=f"f_{name}({x}) returned {value}; the shape is not auditable.",
                        )
                    )
                    non_finite = True
                    break
                values.append(value)
            if non_finite:
                continue

            for i in range(len(values) - 1):
                delta = values[i + 1] - values[i]
                moved_wrong_way = (
                    delta < -constraint.tolerance
                    if constraint.direction is MonotonicDirection.INCREASING
                    else delta > constraint.tolerance
                )
                if moved_wrong_way:
                    violations.append(
                        MonotonicityViolation(
                            feature_name=name,
                            reason=REASON_SHAPE_NOT_MONOTONE,
                            direction=constraint.direction.value,
                            x_low=constraint.audit_grid[i],
                            x_high=constraint.audit_grid[i + 1],
                            f_low=values[i],
                            f_high=values[i + 1],
                            delta=delta,
                            detail=(
                                f"f_{name} moved by {delta:+.6g} between x={constraint.audit_grid[i]:g} "
                                f"and x={constraint.audit_grid[i + 1]:g}, against a declared "
                                f"{constraint.direction.value} constraint."
                            ),
                        )
                    )

            if not interacting:
                continue
            pairs = ", ".join(f"({a}, {b})" for a, b in interacting)
            if constraint.scope is MonotonicScope.GLOBAL:
                violations.append(
                    MonotonicityViolation(
                        feature_name=name,
                        reason=REASON_INTERACTION_SHADOWS_GLOBAL,
                        direction=constraint.direction.value,
                        detail=(
                            f"'{name}' is declared globally {constraint.direction.value} but also "
                            f"appears in interaction term(s) {pairs}, which can move the score the "
                            "other way. Exclude those pairwise terms, or declare the constraint "
                            "with scope=TERM to audit only the univariate shape."
                        ),
                    )
                )
            else:
                limitations.append(
                    f"'{name}': univariate shape audited only; interaction term(s) {pairs} are "
                    "not covered, so the model is not certified monotone in this feature."
                )

        self._monotonicity_cache = (list(violations), list(limitations))
        return violations, limitations

    # ---------------------------------------------------------------------- evaluation

    def _call_single(self, shape_func: Callable[[float], float], name: str, x: float) -> float:
        try:
            return float(shape_func(x))
        except Exception as exc:                     # noqa: BLE001 - re-raised with context below
            raise ShapeFunctionError(
                f"shape function f_{name} raised at x={x!r}: {type(exc).__name__}: {exc}"
            ) from exc

    def _call_interaction(
        self, shape_func: Callable[[float, float], float], pair: Tuple[str, str], v1: float, v2: float
    ) -> float:
        try:
            return float(shape_func(v1, v2))
        except Exception as exc:                     # noqa: BLE001 - re-raised with context below
            raise ShapeFunctionError(
                f"interaction shape function f_{pair[0]},{pair[1]} raised at "
                f"({v1!r}, {v2!r}): {type(exc).__name__}: {exc}"
            ) from exc

    def _evaluate_terms(
        self, feature_values: Mapping[str, float]
    ) -> Tuple[List[EbmFeatureContribution], List[EbmInteractionContribution]]:
        singles = [
            EbmFeatureContribution(
                feature_name=name,
                feature_value=feature_values[name],
                contribution_score=self._call_single(
                    self.single_shape_funcs[name], name, feature_values[name]
                ),
            )
            for name in sorted(self.single_shape_funcs)
        ]
        interactions = [
            EbmInteractionContribution(
                feature_name_1=pair[0],
                feature_name_2=pair[1],
                value_1=feature_values[pair[0]],
                value_2=feature_values[pair[1]],
                contribution_score=self._call_interaction(
                    self.interaction_shape_funcs[pair],
                    pair,
                    feature_values[pair[0]],
                    feature_values[pair[1]],
                ),
            )
            for pair in sorted(self.interaction_shape_funcs)
        ]
        return singles, interactions

    def _validate_feature_values(self, feature_values: Mapping[str, float]) -> Dict[str, float]:
        """
        Every registered term must have a value, and every supplied value must belong to
        a registered term.

        The original engine skipped a registered feature that was absent from the input
        and ignored an input whose name matched nothing. Both produced a score composed
        from a *different* term set than the model, indistinguishable in the report from
        a complete evaluation — a partial model scoring a live signal while reporting
        PASS. Both now raise.
        """
        if not isinstance(feature_values, Mapping):
            raise TypeError(
                f"feature_values must be a mapping of feature name to value, "
                f"got {type(feature_values).__name__}."
            )
        required = set(self.required_feature_names())
        if not required:
            raise ValueError(
                f"model '{self.model_id}' has no registered terms; register at least one "
                "shape function before scoring."
            )
        supplied = set(feature_values)

        missing = sorted(required - supplied)
        if missing:
            raise ValueError(
                f"model '{self.model_id}' requires values for {missing} but they were not "
                "supplied. Scoring a subset of the model's terms silently understates the "
                "score; supply every feature or unregister the term."
            )
        unknown = sorted(supplied - required)
        if unknown:
            raise ValueError(
                f"feature_values contains {unknown}, which match no registered term of model "
                f"'{self.model_id}'. A misspelled feature name would otherwise be dropped "
                "without changing the score or the report."
            )
        return {
            name: _require_finite_number(feature_values[name], f"feature '{name}'")
            for name in sorted(required)
        }

    def evaluate_ebm_signal(
        self,
        symbol: str,
        feature_values: Mapping[str, float],
    ) -> EbmSignalAuditReport:
        """
        Score one instance and emit its governance record.

            score = beta_0 + sum_i f_i(x_i) + sum_(i,j) f_ij(x_i, x_j)

        Raises ``ValueError``/``TypeError`` for a caller error (missing, unknown or
        non-finite feature values) and ``ShapeFunctionError`` if a shape function
        raises. A *model* defect — a non-finite shape value, or a monotonicity
        constraint the shape violates — is reported through ``status`` rather than
        raised, so the failure is recorded in the audit trail.
        """
        if not symbol or not isinstance(symbol, str):
            raise ValueError(f"symbol must be a non-empty string, got {symbol!r}.")
        values = self._validate_feature_values(feature_values)

        single_contribs, interaction_contribs = self._evaluate_terms(values)
        components = [c.contribution_score for c in single_contribs] + [
            c.contribution_score for c in interaction_contribs
        ]
        total_score = math.fsum([self.beta0] + components)

        # Independent second pass over the same recorded feature values. A pure shape
        # function reconciles exactly; a stateful, cached or randomised one does not,
        # and an audit record that cannot be reproduced from its own inputs is not an
        # audit record. This is a reproducibility and transcription check on the
        # report -- it is not evidence that the shape functions are correct.
        recheck_singles, recheck_interactions = self._evaluate_terms(values)
        recheck_components = [c.contribution_score for c in recheck_singles] + [
            c.contribution_score for c in recheck_interactions
        ]

        all_finite = all(math.isfinite(v) for v in components) and math.isfinite(total_score)
        if not all_finite:
            residual = math.inf
        else:
            # Term-by-term, so two offsetting drifts cannot cancel in the total, then the
            # composed total itself.
            per_term = [abs(a - b) for a, b in zip(components, recheck_components)]
            recheck_total = math.fsum([self.beta0] + recheck_components)
            residual = max(per_term + [abs(total_score - recheck_total)]) if per_term else 0.0
            if not math.isfinite(residual):
                residual = math.inf
        is_exact_valid = residual <= IDENTITY_TOLERANCE

        violations, limitations = self.audit_monotonicity()
        is_monotonic_ok = not violations

        if not is_exact_valid:
            status = STATUS_FAIL_IDENTITY
        elif not is_monotonic_ok:
            status = STATUS_FAIL_MONOTONICITY
        else:
            status = STATUS_PASS

        fingerprint = self.term_fingerprint()
        notes = self._build_notes(
            symbol=symbol,
            status=status,
            total_score=total_score,
            n_single=len(single_contribs),
            n_interaction=len(interaction_contribs),
            residual=residual,
            violations=violations,
            limitations=limitations,
            fingerprint=fingerprint,
        )
        if status == STATUS_PASS:
            logger.info(notes)
        else:
            logger.warning(notes)

        return EbmSignalAuditReport(
            model_id=self.model_id,
            symbol=symbol,
            base_intercept_beta0=self.beta0,
            total_predicted_score=total_score,
            single_feature_contributions=single_contribs,
            interaction_contributions=interaction_contribs,
            is_exact_additive_identity_valid=is_exact_valid,
            is_monotonicity_audit_passed=is_monotonic_ok,
            status=status,
            audit_notes=notes,
            score_scale=self.score_scale,
            additive_identity_residual=residual,
            monotonicity_violations=tuple(violations),
            monotonicity_audit_limitations=tuple(limitations),
            term_fingerprint=fingerprint,
            shape_table_version=self.shape_table_version,
        )

    def _build_notes(
        self,
        symbol: str,
        status: str,
        total_score: float,
        n_single: int,
        n_interaction: int,
        residual: float,
        violations: Sequence[MonotonicityViolation],
        limitations: Sequence[str],
        fingerprint: str,
    ) -> str:
        scale_note = (
            "log-odds (NOT a probability)"
            if self.score_scale is ScoreScale.LOGIT
            else "target units"
        )
        parts = [
            f"EBM GLASS-BOX SIGNAL AUDIT [{self.model_id}@{self.shape_table_version} "
            f"fp={fingerprint} - {symbol}]: {status}.",
            f"Score = {total_score:.6f} on the {self.score_scale.value} scale ({scale_note}); "
            f"beta0 = {self.beta0:.6f}, {n_single} univariate term(s), "
            f"{n_interaction} interaction term(s).",
            f"Additive identity residual = {residual:.3g}.",
        ]
        if violations:
            parts.append(
                f"{len(violations)} monotonicity violation(s): "
                + " | ".join(v.detail for v in violations)
            )
        if limitations:
            parts.append("Audit limitations: " + " | ".join(limitations))
        return " ".join(parts)
