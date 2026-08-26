"""
multi-horizon-forecasting-architecture: horizon-scale normalizer, IC/decay weight
builder, composite alpha synthesizer, and short-vs-long conflict arbitrator.

Design notes
------------
* Return forecasts made over *different* horizons are not commensurable. A
  +20 bps forecast over the next 5 minutes and a +20 bps forecast over the next
  full session are very different statements, and a weighted average of the two
  raw numbers has no defined horizon at all. Every scheme here therefore
  rescales each forecast onto one `target_horizon_steps` basis *before*
  weighting -- see `HorizonScaling`. This is the failure mode the skill's
  "Naive Horizon Averaging" pitfall names.
* `SQRT_TIME` scaling assumes sigma(tau) proportional to sqrt(tau). That holds
  exactly only for zero-mean, homoscedastic, serially uncorrelated returns, and
  it understates risk under jumps and fat tails with the bias growing in the
  horizon (Danielsson & Zigrand, 2006). Where the difference matters, measure
  per-horizon volatilities and use `EXPLICIT_VOL`.
* Expressing a forecast in units of its own horizon's volatility is Grinold's
  (1994) `alpha = volatility x IC x score` identity read backwards:
  `score = alpha / volatility`. `composite_score` is that quantity.
* The weighting schemes are *marginal*: each judges one horizon in isolation and
  ignores that overlapping-horizon forecasts are mechanically correlated (the
  5-minute window sits inside the 60-minute window). Weights are therefore a
  heuristic blend, not a minimum-variance combination. Estimating the
  cross-horizon error covariance well enough to beat marginal weighting is hard
  in practice (Bates & Granger, 1969), which is why no covariance path is
  offered here rather than a poorly-estimated one.
* Nothing in this module is a regulatory or risk control. Position limits,
  exposure caps, and kill switches must live outside the signal path.
"""
from dataclasses import dataclass
from enum import Enum
import logging
import math
from typing import Dict, List, Optional, Sequence, Union

logger = logging.getLogger(__name__)

__all__ = [
    "MultiHorizonError",
    "WeightingScheme",
    "HorizonScaling",
    "ConflictPolicy",
    "ForecastStatus",
    "HorizonPrediction",
    "MultiHorizonConfig",
    "MultiHorizonForecastReport",
    "MultiHorizonForecasterEngine",
]

_MIN_VOLATILITY = 1e-12     # Floor below which a horizon volatility is unusable.
_MIN_TOTAL_WEIGHT = 1e-12   # Floor below which the weight vector is degenerate.


class MultiHorizonError(ValueError):
    """Raised on malformed multi-horizon inputs or infeasible configuration."""


class WeightingScheme(str, Enum):
    """How much each horizon's (already scale-normalized) forecast counts."""

    IC_WEIGHTED = "IC_WEIGHTED"                     # w = max(0, IC) * confidence
    INVERSE_HORIZON_SQRT = "INVERSE_HORIZON_SQRT"   # w = confidence / sqrt(tau)
    EQUAL = "EQUAL"                                 # w = 1 (the 1/N baseline)


class HorizonScaling(str, Enum):
    """How forecasts from different horizons are put on a common basis."""

    SQRT_TIME = "SQRT_TIME"        # sigma(tau) ~ sqrt(tau); needs no extra inputs
    EXPLICIT_VOL = "EXPLICIT_VOL"  # caller supplies measured per-horizon volatility
    NONE = "NONE"                  # forecasts already share a horizon; no rescaling


class ConflictPolicy(str, Enum):
    """What to do when the shortest and longest horizons disagree materially."""

    REPORT_ONLY = "REPORT_ONLY"    # flag it, leave the composite untouched
    DAMPEN = "DAMPEN"              # scale the composite by `conflict_damping`
    SUPPRESS = "SUPPRESS"          # zero the composite
    DEFER_LONG = "DEFER_LONG"      # follow the longest horizon alone


class ForecastStatus(str, Enum):
    SYNTHESIZED = "FORECAST_SYNTHESIZED_SUCCESS"
    DAMPENED_ON_CONFLICT = "FORECAST_DAMPENED_ON_CONFLICT"
    SUPPRESSED_ON_CONFLICT = "FORECAST_SUPPRESSED_ON_CONFLICT"
    DEFERRED_TO_LONG_HORIZON = "FORECAST_DEFERRED_TO_LONG_HORIZON"
    NO_VALID_HORIZON_WEIGHTS = "NO_VALID_HORIZON_WEIGHTS"


@dataclass
class HorizonPrediction:
    """
    One model's return forecast for one forward horizon.

    Attributes:
        horizon_steps: Length of the forecast window, in base steps. The unit is
            the caller's (minutes, seconds, bars) but MUST be identical across
            every prediction in a call, because only the ratios
            `horizon_steps / target_horizon_steps` are used. Must be >= 1.
        predicted_return: Expected simple return realized *over that whole
            horizon* (0.0015 = 15 bps across `horizon_steps` steps), not a
            per-step rate.
        ic_score: Out-of-sample Information Coefficient -- the correlation
            between this model's forecasts and realized returns at this horizon.
            Being a correlation it must lie in [-1, 1].
        confidence: Model confidence multiplier in [0, 1].
        horizon_volatility: Measured standard deviation of the realized
            `horizon_steps`-step return. Required by `EXPLICIT_VOL`, ignored by
            the other scaling modes.
    """

    horizon_steps: int
    predicted_return: float
    ic_score: float = 0.05
    confidence: float = 1.0
    horizon_volatility: Optional[float] = None


@dataclass
class MultiHorizonConfig:
    """
    Args:
        symbol: Instrument identifier, for logging and the audit report.
        weighting_scheme: How much each scale-normalized forecast counts.
        scaling_mode: How forecasts are put on a common horizon basis. `NONE`
            reproduces a raw weighted average and is only correct when the
            caller has already normalized upstream.
        target_horizon_steps: Horizon the composite is expressed in. Defaults to
            the shortest horizon supplied -- normally the horizon actually traded
            and re-evaluated. Under `EXPLICIT_VOL` it must match one of the
            supplied horizons, since that is the only horizon whose volatility
            is known.
        conflict_threshold: Minimum absolute forecast, **in target-horizon return
            units**, for an endpoint to count toward a short-vs-long conflict.
            Set it to the smallest move worth acting on at the target horizon. A
            long-horizon view that is negligible once rescaled to the horizon
            being traded does not veto the trade: the position is re-evaluated
            long before that view can materialize.
        conflict_policy: Arbitration applied when a conflict is flagged.
        conflict_damping: Multiplier applied to the composite under `DAMPEN`, in
            [0, 1]. The 0.5 default is a policy choice, not a calibrated
            constant -- derive your own from conflict-conditional performance.
    """

    symbol: str
    weighting_scheme: Union[str, WeightingScheme] = WeightingScheme.IC_WEIGHTED
    scaling_mode: Union[str, HorizonScaling] = HorizonScaling.SQRT_TIME
    target_horizon_steps: Optional[int] = None
    conflict_threshold: float = 0.001
    conflict_policy: Union[str, ConflictPolicy] = ConflictPolicy.DAMPEN
    conflict_damping: float = 0.5


@dataclass
class MultiHorizonForecastReport:
    """
    Audit record of one synthesis.

    `composite_alpha` is an expected return over `target_horizon_steps`, NOT an
    average of the raw per-horizon forecasts. `normalized_predictions` holds each
    horizon's forecast rescaled onto that same target-horizon basis, so the
    composite is exactly the weighted mean of those values before any conflict
    arbitration is applied.
    """

    symbol: str
    composite_alpha: float
    directional_consensus_pct: float
    has_directional_conflict: bool
    horizon_weights: Dict[int, float]
    normalized_predictions: Dict[int, float]
    status: str
    audit_notes: str
    composite_score: float = 0.0
    target_horizon_steps: int = 0
    weighted_consensus_pct: float = 0.0
    scaling_mode: str = HorizonScaling.SQRT_TIME.value
    weighting_scheme: str = WeightingScheme.IC_WEIGHTED.value


def _coerce(enum_cls, value, label: str):
    """Coerces a str/enum config value, refusing unknown values outright."""
    if isinstance(value, enum_cls):
        return value
    try:
        return enum_cls(str(value).strip().upper())
    except ValueError:
        valid = ", ".join(m.value for m in enum_cls)
        raise MultiHorizonError(
            f"{label}: unknown value {value!r}. Valid values: {valid}."
        ) from None


def _require_finite(value, label: str) -> float:
    """Rejects NaN/Inf/non-numeric input before it can propagate into an alpha."""
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
    ):
        raise MultiHorizonError(f"{label}: non-finite or non-numeric value {value!r}.")
    return float(value)


def _sign(x: float) -> int:
    return 1 if x > 0.0 else (-1 if x < 0.0 else 0)


class MultiHorizonForecasterEngine:
    """
    Combines return forecasts made over different forward horizons into one
    composite alpha expressed at a single target horizon, and arbitrates
    short-vs-long directional conflicts.

    The engine is stateless: every call is fully determined by its arguments.
    """

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------
    @staticmethod
    def _validate_predictions(
        predictions: Sequence[HorizonPrediction],
    ) -> List[HorizonPrediction]:
        if not predictions:
            raise MultiHorizonError("Predictions list cannot be empty.")

        seen: Dict[int, int] = {}
        for idx, p in enumerate(predictions):
            label = f"prediction[{idx}]"
            if isinstance(p.horizon_steps, bool) or not isinstance(p.horizon_steps, int):
                raise MultiHorizonError(
                    f"{label}.horizon_steps must be an int, got {p.horizon_steps!r}."
                )
            if p.horizon_steps < 1:
                raise MultiHorizonError(
                    f"{label}.horizon_steps must be >= 1, got {p.horizon_steps}."
                )
            if p.horizon_steps in seen:
                raise MultiHorizonError(
                    f"{label}: duplicate horizon_steps={p.horizon_steps} (also at index "
                    f"{seen[p.horizon_steps]}). One forecast per horizon: aggregate "
                    "competing models for the same horizon before calling."
                )
            seen[p.horizon_steps] = idx

            _require_finite(p.predicted_return, f"{label}.predicted_return")
            ic = _require_finite(p.ic_score, f"{label}.ic_score")
            if not -1.0 <= ic <= 1.0:
                raise MultiHorizonError(
                    f"{label}.ic_score must be in [-1, 1] (it is a correlation), got {ic}."
                )
            conf = _require_finite(p.confidence, f"{label}.confidence")
            if not 0.0 <= conf <= 1.0:
                raise MultiHorizonError(
                    f"{label}.confidence must be in [0, 1], got {conf}."
                )
            if p.horizon_volatility is not None:
                vol = _require_finite(p.horizon_volatility, f"{label}.horizon_volatility")
                if vol <= _MIN_VOLATILITY:
                    raise MultiHorizonError(
                        f"{label}.horizon_volatility must be > {_MIN_VOLATILITY}, got {vol}."
                    )

        return sorted(predictions, key=lambda x: x.horizon_steps)

    @staticmethod
    def _validate_config(cfg: MultiHorizonConfig):
        scheme = _coerce(WeightingScheme, cfg.weighting_scheme, "weighting_scheme")
        scaling = _coerce(HorizonScaling, cfg.scaling_mode, "scaling_mode")
        policy = _coerce(ConflictPolicy, cfg.conflict_policy, "conflict_policy")

        threshold = _require_finite(cfg.conflict_threshold, "conflict_threshold")
        if threshold < 0.0:
            raise MultiHorizonError(f"conflict_threshold must be >= 0, got {threshold}.")
        damping = _require_finite(cfg.conflict_damping, "conflict_damping")
        if not 0.0 <= damping <= 1.0:
            raise MultiHorizonError(f"conflict_damping must be in [0, 1], got {damping}.")
        if cfg.target_horizon_steps is not None:
            if isinstance(cfg.target_horizon_steps, bool) or not isinstance(
                cfg.target_horizon_steps, int
            ):
                raise MultiHorizonError(
                    "target_horizon_steps must be an int or None, got "
                    f"{cfg.target_horizon_steps!r}."
                )
            if cfg.target_horizon_steps < 1:
                raise MultiHorizonError(
                    f"target_horizon_steps must be >= 1, got {cfg.target_horizon_steps}."
                )
        return scheme, scaling, policy, threshold, damping

    # ------------------------------------------------------------------
    # Horizon scale normalization
    # ------------------------------------------------------------------
    @staticmethod
    def _scale_factors(
        preds: Sequence[HorizonPrediction],
        scaling: HorizonScaling,
        target_horizon: int,
    ) -> Dict[int, float]:
        """
        Returns `sigma(target) / sigma(tau_k)` for each horizon.

        Multiplying a horizon's forecast by this factor restates it as an
        expected return over `target_horizon` steps, which is what makes
        forecasts from different horizons addable at all.
        """
        if scaling is HorizonScaling.NONE:
            return {p.horizon_steps: 1.0 for p in preds}

        if scaling is HorizonScaling.SQRT_TIME:
            return {
                p.horizon_steps: math.sqrt(target_horizon / p.horizon_steps)
                for p in preds
            }

        # EXPLICIT_VOL
        missing = [p.horizon_steps for p in preds if p.horizon_volatility is None]
        if missing:
            raise MultiHorizonError(
                "scaling_mode=EXPLICIT_VOL requires horizon_volatility on every "
                f"prediction; missing for horizon(s) {missing}."
            )
        target_vol = next(
            (p.horizon_volatility for p in preds if p.horizon_steps == target_horizon),
            None,
        )
        if target_vol is None:
            raise MultiHorizonError(
                f"scaling_mode=EXPLICIT_VOL: target_horizon_steps={target_horizon} does "
                "not match any supplied prediction, so its volatility is unknown. Supply "
                "a prediction at that horizon or choose one of "
                f"{[p.horizon_steps for p in preds]}."
            )
        return {p.horizon_steps: target_vol / p.horizon_volatility for p in preds}

    # ------------------------------------------------------------------
    # Weighting
    # ------------------------------------------------------------------
    @staticmethod
    def _raw_weights(
        preds: Sequence[HorizonPrediction], scheme: WeightingScheme
    ) -> Dict[int, float]:
        raw: Dict[int, float] = {}
        for p in preds:
            if scheme is WeightingScheme.IC_WEIGHTED:
                w = max(0.0, p.ic_score) * p.confidence
            elif scheme is WeightingScheme.INVERSE_HORIZON_SQRT:
                w = p.confidence / math.sqrt(p.horizon_steps)
            else:  # EQUAL -- the 1/N baseline; deliberately ignores IC and confidence
                w = 1.0
            raw[p.horizon_steps] = w
        return raw

    # ------------------------------------------------------------------
    # Synthesis
    # ------------------------------------------------------------------
    def synthesize_forecast(
        self, cfg: MultiHorizonConfig, predictions: List[HorizonPrediction]
    ) -> MultiHorizonForecastReport:
        """
        Rescales every horizon's forecast onto `target_horizon_steps`, blends them
        with the configured weighting scheme, and arbitrates short-vs-long
        directional conflicts.

        Raises:
            MultiHorizonError: on empty input, duplicate horizons, non-finite or
                out-of-range values, an unknown scheme/mode/policy, or an
                `EXPLICIT_VOL` request the supplied data cannot satisfy.
        """
        scheme, scaling, policy, threshold, damping = self._validate_config(cfg)
        preds = self._validate_predictions(predictions)

        target_horizon = (
            cfg.target_horizon_steps
            if cfg.target_horizon_steps is not None
            else preds[0].horizon_steps
        )
        if scaling is HorizonScaling.SQRT_TIME and all(
            p.horizon_volatility is not None for p in preds
        ):
            logger.warning(
                "[%s] Every prediction supplies horizon_volatility but scaling_mode is "
                "SQRT_TIME, so those measurements are ignored. Set "
                "scaling_mode=EXPLICIT_VOL to use them.",
                cfg.symbol,
            )

        scale = self._scale_factors(preds, scaling, target_horizon)
        scaled_preds = {
            p.horizon_steps: p.predicted_return * scale[p.horizon_steps] for p in preds
        }

        # --- Weights --------------------------------------------------
        raw_weights = self._raw_weights(preds, scheme)
        total_w = sum(raw_weights.values())
        degenerate = total_w <= _MIN_TOTAL_WEIGHT

        if degenerate:
            # Every horizon scored zero weight -- under IC_WEIGHTED that means no
            # horizon has non-negative measured skill. Falling back to equal
            # weighting here would emit a full-strength signal from a model set
            # that has demonstrated none, so the composite is zeroed instead.
            norm_weights = {h: 0.0 for h in raw_weights}
            composite_alpha = 0.0
        else:
            norm_weights = {h: w / total_w for h, w in raw_weights.items()}
            composite_alpha = sum(norm_weights[h] * scaled_preds[h] for h in norm_weights)

        # --- Directional consensus ------------------------------------
        signs = {h: _sign(v) for h, v in scaled_preds.items()}
        non_zero = [h for h, s in signs.items() if s != 0]
        if non_zero:
            pos = sum(1 for h in non_zero if signs[h] > 0)
            consensus_pct = (max(pos, len(non_zero) - pos) / len(non_zero)) * 100.0
            pos_w = sum(norm_weights[h] for h in non_zero if signs[h] > 0)
            neg_w = sum(norm_weights[h] for h in non_zero if signs[h] < 0)
            live_w = pos_w + neg_w
            weighted_consensus_pct = (
                max(pos_w, neg_w) / live_w * 100.0 if live_w > 0.0 else 0.0
            )
        else:
            # No horizon has a directional view. Reporting 100% "consensus" here
            # would read downstream as unanimous conviction on a flat forecast.
            consensus_pct = 0.0
            weighted_consensus_pct = 0.0

        # --- Short-vs-long conflict -----------------------------------
        short_h, long_h = preds[0].horizon_steps, preds[-1].horizon_steps
        short_scaled, long_scaled = scaled_preds[short_h], scaled_preds[long_h]
        has_conflict = (
            short_h != long_h
            and _sign(short_scaled) * _sign(long_scaled) < 0
            and abs(short_scaled) >= threshold
            and abs(long_scaled) >= threshold
        )

        # --- Arbitration ----------------------------------------------
        status = (
            ForecastStatus.NO_VALID_HORIZON_WEIGHTS
            if degenerate
            else ForecastStatus.SYNTHESIZED
        )
        if has_conflict and not degenerate:
            if policy is ConflictPolicy.DAMPEN:
                composite_alpha *= damping
                status = ForecastStatus.DAMPENED_ON_CONFLICT
            elif policy is ConflictPolicy.SUPPRESS:
                composite_alpha = 0.0
                status = ForecastStatus.SUPPRESSED_ON_CONFLICT
            elif policy is ConflictPolicy.DEFER_LONG:
                composite_alpha = long_scaled
                status = ForecastStatus.DEFERRED_TO_LONG_HORIZON

        # `composite_score` restates the composite in units of the target
        # horizon's volatility (Grinold's score). Under SQRT_TIME the base-step
        # volatility is unknown, so the divisor is sqrt(target) and the score is
        # only comparable across calls sharing the same base step.
        if scaling is HorizonScaling.SQRT_TIME:
            target_scale = math.sqrt(target_horizon)
        elif scaling is HorizonScaling.EXPLICIT_VOL:
            target_scale = next(
                p.horizon_volatility
                for p in preds
                if p.horizon_steps == target_horizon
            )
        else:
            target_scale = 1.0
        composite_score = composite_alpha / target_scale

        notes = (
            f"MULTI-HORIZON FORECAST [{cfg.symbol}]: Composite Alpha = "
            f"{composite_alpha * 10000:.2f} bps over {target_horizon} step(s). "
            f"Consensus = {consensus_pct:.0f}% (weighted {weighted_consensus_pct:.0f}%), "
            f"Conflict = {has_conflict}. Scheme = {scheme.value}, "
            f"Scaling = {scaling.value}, Policy = {policy.value}, "
            f"Status = {status.value}."
        )
        if degenerate:
            logger.warning(
                "%s No horizon carried positive weight under %s; composite zeroed.",
                notes,
                scheme.value,
            )
        elif has_conflict:
            logger.warning("%s", notes)
        else:
            logger.info("%s", notes)

        return MultiHorizonForecastReport(
            symbol=cfg.symbol,
            composite_alpha=round(composite_alpha, 8),
            directional_consensus_pct=round(consensus_pct, 1),
            has_directional_conflict=has_conflict,
            # Deliberately unrounded: the sum-to-one invariant is what downstream
            # sign-off checks, and rounding a K-vector can break it at the margin.
            horizon_weights=dict(norm_weights),
            normalized_predictions={h: round(v, 8) for h, v in scaled_preds.items()},
            status=status.value,
            audit_notes=notes,
            composite_score=round(composite_score, 8),
            target_horizon_steps=target_horizon,
            weighted_consensus_pct=round(weighted_consensus_pct, 1),
            scaling_mode=scaling.value,
            weighting_scheme=scheme.value,
        )
