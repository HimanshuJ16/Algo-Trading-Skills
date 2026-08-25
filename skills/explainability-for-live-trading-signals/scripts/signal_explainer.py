"""
explainability-for-live-trading-signals: local feature-attribution reconciliation,
driver ranking, natural-language audit summary, and append-only compliance logging
for live ML trading signals.

Core contract - the additivity (local accuracy) gate
-----------------------------------------------------
An additive attribution method satisfies, for a single instance x:

    f(x) = phi_0 + sum_i phi_i

(Lundberg & Lee, "A Unified Approach to Interpreting Model Predictions", NeurIPS 30,
2017; the shap TreeExplainer docs state it as "the sum of the SHAP values plus the
``expected_value`` equals the model's output (in the specified output space)").

This module treats that identity as a **gate, not an assumption**. The caller must
supply the score the model actually emitted (``model_prediction``) alongside the
attribution vector. ``explain_signal`` recomputes ``base_value + sum(contributions)``
and compares it to that score. A previous version of this module *derived* the
prediction score from the contributions, which made the check structurally impossible:
a broken explainer (wrong background dataset, stale model version, mismatched feature
names, wrong output space) produced a plausible-looking audit record for a score the
model never emitted - and, worse, derived the BUY/SELL label from that reconstruction.

Output space is the trap that fires most often in production. For an XGBoost
``binary:logistic`` model, ``shap.TreeExplainer`` explains the **raw margin (log-odds)**
output by default, so the phi_i sum to log-odds, not to the probability returned by
``predict_proba``. ``base_value`` must be the ``expected_value`` of the *same* explainer
instance, in the *same* output space, as the contributions. Interventional and
tree-path-dependent perturbation also yield different expected values and different
phi_i for the same model. The reconciliation gate catches all of these.

Failure policy
--------------
``explain_signal`` never raises on a reconciliation mismatch. Raising would destroy the
evidence of the very failure a compliance log exists to capture. Instead it returns an
explanation with ``reconciled=False``, the signed ``reconciliation_error``, and a
summary string prefixed with an explicit UNRECONCILED banner so no human or agent
reading the log can mistake it for a valid explanation. The caller is responsible for
gating on ``explanation.reconciled`` before treating drivers as meaningful.

It *does* raise ``SignalExplainerError`` for inputs that make an explanation
meaningless or actively false: non-finite values, an empty attribution vector, or a
contribution naming a feature absent from the feature vector.

Limitations (documented, deliberate)
------------------------------------
- **Attribution is not causation.** phi_i quantifies a feature's contribution to *this
  model's* output under the chosen background distribution. It says nothing about
  whether the feature causes the price move, nor whether the model is right.
- **This module does not compute SHAP values.** It consumes an attribution vector
  produced elsewhere (shap, Captum, an EBM shape function, or a linear model's
  ``w_i * x_i``) and validates, ranks, narrates and records it. Attribution quality is
  the upstream explainer's responsibility.
- **The audit log is append-only, not immutable.** ``log_explainable_signal`` appends
  JSONL to a local file. Genuine immutability requires WORM storage, an append-only
  object store with object-lock, or a signed/hash-chained ledger. Do not describe the
  output of this function as an immutable record.
- **Not on the critical path by default.** Exact per-signal TreeSHAP costs real time.
  Where the explanation is for post-hoc audit rather than a pre-trade gate, compute and
  log it asynchronously; see ``model-inference-latency-budget-for-live-trading``.
"""
import datetime
import json
import logging
import math
import os
from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional

logger = logging.getLogger(__name__)

#: Default absolute tolerance for the additivity gate. Suitable for float64
#: attributions from an exact explainer (TreeSHAP, EBM shape functions, linear
#: contributions).
DEFAULT_RECONCILIATION_ABS_TOL = 1e-6

#: Default relative tolerance, applied against |model_prediction|.
#: For reference, shap's own TreeExplainer additivity assertion is far looser -
#: max(|sum - output| / (|sum| + 1e-2)) < 1e-2, i.e. ~1% relative - because it must
#: accommodate float32 tree ensembles. Callers explaining a float32 or GPU-trained
#: model should loosen these tolerances deliberately and record that they did so,
#: rather than disabling the gate.
DEFAULT_RECONCILIATION_REL_TOL = 1e-6

_VALID_ACTIONS = ("BUY", "SELL", "HOLD")


class SignalExplainerError(ValueError):
    """Raised when the attribution input is malformed, incomplete, or non-finite."""


def _require_finite(value: float, label: str) -> float:
    """Rejects NaN/Inf before it can propagate into a compliance record."""
    try:
        as_float = float(value)
    except (TypeError, ValueError) as exc:
        raise SignalExplainerError(f"{label} must be a real number, got {value!r}.") from exc
    if not math.isfinite(as_float):
        raise SignalExplainerError(f"{label} must be finite, got {as_float!r}.")
    return as_float


@dataclass(frozen=True)
class FeatureContribution:
    """One feature's local attribution for a single signal instance."""

    feature_name: str
    feature_value: float
    contribution: float
    direction: str  # "BULLISH" (contribution > 0) or "BEARISH" (contribution < 0)


@dataclass
class SignalExplanation:
    """
    The complete audit record for one live signal.

    ``prediction_score`` is the score the **model actually emitted** - not a value
    reconstructed from the attributions. ``reconstructed_score`` is
    ``base_value + sum(all_contributions)``. When ``reconciled`` is False the two
    disagree beyond tolerance and the attribution vector does not explain the signal.
    """

    timestamp: float
    timestamp_utc: str
    symbol: str
    signal_action: str
    prediction_score: float
    reconstructed_score: float
    base_value: float
    reconciled: bool
    reconciliation_error: float          # reconstructed_score - prediction_score
    reconciliation_tolerance: float
    top_bullish_drivers: List[FeatureContribution]
    top_bearish_drivers: List[FeatureContribution]
    natural_language_summary: str
    all_contributions: Dict[str, float]
    residual_contribution: float         # attribution not shown in the listed drivers
    attribution_coverage: float          # |phi| share of the listed drivers, in [0, 1]
    unattributed_features: List[str] = field(default_factory=list)
    executed_action: Optional[str] = None
    action_mismatch: bool = False

    def to_audit_record(self) -> Dict[str, object]:
        """
        Canonical audit dictionary. Includes the **full** attribution vector, the base
        value, and the reconciliation verdict - a record holding only the top-N drivers
        cannot be re-verified by a reviewer and is not an adequate compliance artefact.
        """
        return {
            "timestamp": self.timestamp,
            "timestamp_utc": self.timestamp_utc,
            "symbol": self.symbol,
            "action": self.signal_action,
            "executed_action": self.executed_action,
            "action_mismatch": self.action_mismatch,
            "score": self.prediction_score,
            "reconstructed_score": self.reconstructed_score,
            "base_value": self.base_value,
            "reconciled": self.reconciled,
            "reconciliation_error": self.reconciliation_error,
            "reconciliation_tolerance": self.reconciliation_tolerance,
            "top_bullish": [
                {"feature": f.feature_name, "val": f.feature_value, "contrib": f.contribution}
                for f in self.top_bullish_drivers
            ],
            "top_bearish": [
                {"feature": f.feature_name, "val": f.feature_value, "contrib": f.contribution}
                for f in self.top_bearish_drivers
            ],
            "residual_contribution": self.residual_contribution,
            "attribution_coverage": self.attribution_coverage,
            "all_contributions": dict(self.all_contributions),
            "unattributed_features": list(self.unattributed_features),
            "summary": self.natural_language_summary,
        }

    def to_json_audit(self, indent: Optional[int] = 2) -> str:
        """Serializes :meth:`to_audit_record`. Pass ``indent=None`` for one-line JSONL."""
        return json.dumps(self.to_audit_record(), indent=indent, sort_keys=False)


def log_explainable_signal(
    explanation: SignalExplanation,
    log_path: str,
    encoding: str = "utf-8",
) -> str:
    """
    Appends one explanation to a JSONL compliance log and returns the line written.

    Unreconciled explanations are logged too, at ``ERROR``. Suppressing them would
    delete exactly the evidence a model-governance review needs; an explanation that
    failed the additivity gate is an incident to be recorded, not a record to be
    dropped.

    The file is opened in append mode and flushed + fsync'd per record so a crash
    cannot leave a torn line. This is append-only, **not** immutable - see the module
    docstring.
    """
    if not isinstance(explanation, SignalExplanation):
        raise SignalExplainerError(
            f"explanation must be a SignalExplanation, got {type(explanation).__name__}."
        )
    if not log_path:
        raise SignalExplainerError("log_path must be a non-empty path.")

    line = explanation.to_json_audit(indent=None)
    parent = os.path.dirname(os.path.abspath(log_path))
    os.makedirs(parent, exist_ok=True)
    with open(log_path, "a", encoding=encoding) as handle:
        handle.write(line + "\n")
        handle.flush()
        os.fsync(handle.fileno())

    if explanation.reconciled:
        logger.info(
            "Audit record written [%s %s] -> %s",
            explanation.symbol, explanation.signal_action, log_path,
        )
    else:
        logger.error(
            "UNRECONCILED audit record written [%s %s] error=%.6g tol=%.6g -> %s",
            explanation.symbol, explanation.signal_action,
            explanation.reconciliation_error, explanation.reconciliation_tolerance, log_path,
        )
    return line


class LiveSignalExplainer:
    """
    Validates a local feature-attribution vector against the model's actual output,
    ranks the drivers, and produces a compliance-ready natural-language summary.

    ``base_value`` must be the expected value of the *same* explainer instance that
    produced the contributions, in the *same* output space (see module docstring).
    """

    def __init__(
        self,
        base_value: float = 0.0,
        buy_threshold: float = 0.50,
        sell_threshold: float = -0.50,
        top_n_drivers: int = 3,
        materiality_threshold: float = 0.0,
        reconciliation_abs_tol: float = DEFAULT_RECONCILIATION_ABS_TOL,
        reconciliation_rel_tol: float = DEFAULT_RECONCILIATION_REL_TOL,
    ) -> None:
        self.base_value = _require_finite(base_value, "base_value")
        self.buy_threshold = _require_finite(buy_threshold, "buy_threshold")
        self.sell_threshold = _require_finite(sell_threshold, "sell_threshold")

        if self.sell_threshold >= self.buy_threshold:
            raise SignalExplainerError(
                f"sell_threshold ({self.sell_threshold}) must be strictly below "
                f"buy_threshold ({self.buy_threshold}); otherwise the BUY branch "
                f"shadows SELL and every score is labelled BUY."
            )
        if not isinstance(top_n_drivers, int) or isinstance(top_n_drivers, bool) or top_n_drivers < 1:
            raise SignalExplainerError(
                f"top_n_drivers must be an int >= 1, got {top_n_drivers!r}. "
                f"A negative value silently truncates the driver list instead of limiting it."
            )
        self.top_n_drivers = top_n_drivers

        self.materiality_threshold = _require_finite(materiality_threshold, "materiality_threshold")
        if self.materiality_threshold < 0.0:
            raise SignalExplainerError(
                f"materiality_threshold must be >= 0, got {self.materiality_threshold}."
            )
        self.reconciliation_abs_tol = _require_finite(
            reconciliation_abs_tol, "reconciliation_abs_tol")
        self.reconciliation_rel_tol = _require_finite(
            reconciliation_rel_tol, "reconciliation_rel_tol")
        if self.reconciliation_abs_tol < 0.0 or self.reconciliation_rel_tol < 0.0:
            raise SignalExplainerError("Reconciliation tolerances must be non-negative.")

    def classify_action(self, score: float) -> str:
        """Maps a model score to BUY / SELL / HOLD using the configured thresholds."""
        if score >= self.buy_threshold:
            return "BUY"
        if score <= self.sell_threshold:
            return "SELL"
        return "HOLD"

    def explain_signal(
        self,
        symbol: str,
        feature_dict: Mapping[str, float],
        contributions_dict: Mapping[str, float],
        model_prediction: float,
        timestamp: Optional[float] = None,
        executed_action: Optional[str] = None,
    ) -> SignalExplanation:
        """
        Reconciles an attribution vector against the model's actual output and builds
        the audit record.

        Args:
            symbol: Instrument identifier. Normalised to upper case everywhere.
            feature_dict: Feature values fed to the model for this instance. Must
                contain every key present in ``contributions_dict``.
            contributions_dict: Local attributions phi_i, in the same output space as
                ``base_value``. Must be non-empty.
            model_prediction: The score the model actually emitted for this instance,
                in the same output space. Required - it is the reference the additivity
                gate compares against.
            timestamp: POSIX timestamp. Defaults to now, in UTC.
            executed_action: What the strategy actually did, if known. Recorded and
                cross-checked against the threshold-derived action so the log can prove
                the explanation belongs to the decision that was taken.

        Returns:
            A :class:`SignalExplanation`. Check ``.reconciled`` before treating the
            drivers as a valid explanation.

        Raises:
            SignalExplainerError: on non-finite input, an empty attribution vector, a
                contribution naming a feature absent from ``feature_dict``, or an
                invalid ``executed_action``.
        """
        if not isinstance(symbol, str) or not symbol.strip():
            raise SignalExplainerError("symbol must be a non-empty string.")
        symbol = symbol.strip().upper()

        if not contributions_dict:
            raise SignalExplainerError(
                f"[{symbol}] contributions_dict is empty. An explanation with no "
                f"attributions explains nothing and must not be recorded as valid."
            )
        # Feature names must be strings: non-string keys break deterministic sorting,
        # and json.dumps would silently coerce them, so the persisted record would not
        # round-trip to the keys the caller passed.
        non_str = [k for k in contributions_dict if not isinstance(k, str)]
        if non_str:
            raise SignalExplainerError(
                f"[{symbol}] contribution keys must be strings, got "
                f"{[type(k).__name__ for k in non_str]}."
            )
        if executed_action is not None:
            if not isinstance(executed_action, str) or executed_action.upper() not in _VALID_ACTIONS:
                raise SignalExplainerError(
                    f"[{symbol}] executed_action must be one of {_VALID_ACTIONS}, got "
                    f"{executed_action!r}."
                )
            executed_action = executed_action.upper()

        model_prediction = _require_finite(model_prediction, f"[{symbol}] model_prediction")

        # A contribution for a feature the model was never shown is a name mismatch
        # between the explainer and the feature store. Defaulting the value to 0.0
        # (the previous behaviour) writes a *false* feature value into the audit log.
        missing = sorted(set(contributions_dict) - set(feature_dict))
        if missing:
            raise SignalExplainerError(
                f"[{symbol}] contributions reference features absent from feature_dict: "
                f"{missing}. This is a feature-name mismatch between the explainer and "
                f"the feature vector, not a zero-valued feature."
            )

        contributions: Dict[str, float] = {
            name: _require_finite(value, f"[{symbol}] contribution '{name}'")
            for name, value in contributions_dict.items()
        }
        feature_values: Dict[str, float] = {
            name: _require_finite(feature_dict[name], f"[{symbol}] feature '{name}'")
            for name in contributions
        }
        # Features supplied but never attributed: legitimate (the model may ignore
        # them) but worth recording, because it is also how a silently dropped
        # attribution looks.
        unattributed = sorted(set(feature_dict) - set(contributions))
        if unattributed:
            logger.warning(
                "[%s] %d feature(s) present in feature_dict with no attribution: %s",
                symbol, len(unattributed), unattributed,
            )

        if timestamp is None:
            now = datetime.datetime.now(datetime.timezone.utc)
        else:
            timestamp = _require_finite(timestamp, f"[{symbol}] timestamp")
            try:
                now = datetime.datetime.fromtimestamp(timestamp, tz=datetime.timezone.utc)
            except (OverflowError, OSError, ValueError) as exc:
                # The common cause is a millisecond epoch (~1.7e12) passed where POSIX
                # seconds are expected, which would otherwise escape as a raw OSError.
                raise SignalExplainerError(
                    f"[{symbol}] timestamp {timestamp!r} is not a representable POSIX "
                    f"timestamp in seconds (milliseconds passed by mistake?)."
                ) from exc
        # UTC throughout: a naive local-time stamp cannot be aligned with order records
        # kept under MiFID II RTS 25 clock-synchronisation rules, and silently shifts
        # across a DST boundary.
        ts_epoch = now.timestamp()
        ts_iso = now.isoformat().replace("+00:00", "Z")

        # --- Additivity gate -------------------------------------------------------
        sum_contrib = math.fsum(contributions.values())
        reconstructed = self.base_value + sum_contrib
        error = reconstructed - model_prediction
        tolerance = (
            self.reconciliation_abs_tol
            + self.reconciliation_rel_tol * abs(model_prediction)
        )
        reconciled = abs(error) <= tolerance

        # The action is derived from the model's ACTUAL output, never from the
        # reconstruction, so a broken explainer cannot relabel a trade direction.
        action = self.classify_action(model_prediction)
        action_mismatch = executed_action is not None and executed_action != action

        # --- Rank drivers ----------------------------------------------------------
        bullish: List[FeatureContribution] = []
        bearish: List[FeatureContribution] = []
        for name, contrib in contributions.items():
            if contrib > self.materiality_threshold:
                bullish.append(FeatureContribution(name, feature_values[name], contrib, "BULLISH"))
            elif contrib < -self.materiality_threshold:
                bearish.append(FeatureContribution(name, feature_values[name], contrib, "BEARISH"))

        # Deterministic ordering: magnitude first, then feature name, so equal
        # contributions never reorder between runs on the same input.
        bullish.sort(key=lambda f: (-f.contribution, f.feature_name))
        bearish.sort(key=lambda f: (f.contribution, f.feature_name))
        top_bull = bullish[: self.top_n_drivers]
        top_bear = bearish[: self.top_n_drivers]

        listed = top_bull + top_bear
        listed_sum = math.fsum(f.contribution for f in listed)
        residual = sum_contrib - listed_sum
        total_magnitude = math.fsum(abs(v) for v in contributions.values())
        listed_magnitude = math.fsum(abs(f.contribution) for f in listed)
        coverage = 1.0 if total_magnitude == 0.0 else listed_magnitude / total_magnitude

        summary = self._build_summary(
            symbol=symbol,
            action=action,
            model_prediction=model_prediction,
            reconstructed=reconstructed,
            reconciled=reconciled,
            error=error,
            tolerance=tolerance,
            top_bull=top_bull,
            top_bear=top_bear,
            coverage=coverage,
            n_features=len(contributions),
            action_mismatch=action_mismatch,
            executed_action=executed_action,
        )

        if reconciled:
            logger.info("Signal explained [%s]: %s", symbol, summary)
        else:
            logger.error(
                "[%s] additivity gate FAILED: base(%.10g) + sum(phi)(%.10g) = %.10g vs "
                "model output %.10g (error %.6g > tol %.6g). Check that base_value is the "
                "expected_value of the same explainer instance and that contributions and "
                "prediction are in the same output space (raw margin vs probability).",
                symbol, self.base_value, sum_contrib, reconstructed,
                model_prediction, error, tolerance,
            )
        if action_mismatch:
            logger.error(
                "[%s] executed action %s does not match the action implied by the model "
                "score (%s). The explanation may not correspond to the decision taken.",
                symbol, executed_action, action,
            )

        return SignalExplanation(
            timestamp=ts_epoch,
            timestamp_utc=ts_iso,
            symbol=symbol,
            signal_action=action,
            prediction_score=model_prediction,
            reconstructed_score=reconstructed,
            base_value=self.base_value,
            reconciled=reconciled,
            reconciliation_error=error,
            reconciliation_tolerance=tolerance,
            top_bullish_drivers=top_bull,
            top_bearish_drivers=top_bear,
            natural_language_summary=summary,
            # Copy: the caller must not be able to mutate a written audit record.
            all_contributions=dict(contributions),
            residual_contribution=residual,
            attribution_coverage=coverage,
            unattributed_features=unattributed,
            executed_action=executed_action,
            action_mismatch=action_mismatch,
        )

    def _build_summary(
        self,
        symbol: str,
        action: str,
        model_prediction: float,
        reconstructed: float,
        reconciled: bool,
        error: float,
        tolerance: float,
        top_bull: List[FeatureContribution],
        top_bear: List[FeatureContribution],
        coverage: float,
        n_features: int,
        action_mismatch: bool,
        executed_action: Optional[str],
    ) -> str:
        """
        Builds the human-readable audit string.

        Drivers are labelled by alignment with the signal direction, not by sign. On a
        SELL, the *negative* contributions are what drove it; the previous version
        described them as "offset by", which told a risk officer reading an incident
        log the exact opposite of what happened.
        """
        def render(items: List[FeatureContribution]) -> str:
            return ", ".join(f"{f.feature_name} ({f.contribution:+.4f})" for f in items)

        if action == "SELL":
            aligned, opposing = top_bear, top_bull
        else:  # BUY, and HOLD (where the positive side is still the "for" case)
            aligned, opposing = top_bull, top_bear

        head = (
            f"{action} signal for '{symbol}': model output {model_prediction:+.4f} "
            f"(base {self.base_value:+.4f} + attributions)"
        )
        clauses: List[str] = []
        if action == "HOLD":
            if aligned:
                clauses.append(f"largest positive: {render(aligned)}")
            if opposing:
                clauses.append(f"largest negative: {render(opposing)}")
        else:
            if aligned:
                clauses.append(f"driven by {render(aligned)}")
            if opposing:
                clauses.append(f"offset by {render(opposing)}")
        if not clauses:
            clauses.append("no material feature contributions")

        body = f"{head}; " + "; ".join(clauses) + "."
        body += (
            f" Listed drivers cover {coverage * 100:.1f}% of total attribution magnitude "
            f"across {n_features} attributed feature(s)."
        )
        if action_mismatch:
            body += (
                f" ACTION MISMATCH: strategy executed {executed_action} while the model "
                f"score implies {action}."
            )
        if not reconciled:
            body = (
                f"UNRECONCILED - attributions do not explain this signal "
                f"(reconstructed {reconstructed:+.6f} vs model output "
                f"{model_prediction:+.6f}, error {error:+.3g} > tolerance {tolerance:.3g}). "
                f"DO NOT rely on the drivers below. " + body
            )
        return body
