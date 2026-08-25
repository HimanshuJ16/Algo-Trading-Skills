"""
execution-cost-model-recalibration-cadence: audits a pre-trade execution cost model
against realized Implementation Shortfall (IS) and refits its impact coefficients by
ordinary least squares when the audit trips.

Cost model
----------
The audited model is the standard two-term pre-trade cost decomposition:

    IS_pred (bps) = eta * spread_bps  +  gamma * sigma_bps * sqrt(Q / ADV)

- ``eta * spread_bps`` is the spread-crossing term. eta ~ 0.5 corresponds to paying
  half the quoted spread.
- ``gamma * sigma_bps * sqrt(Q / ADV)`` is the square-root impact term. The
  square-root law I(Q) = Y * sigma * sqrt(Q/V) is reported with a dimensionless
  prefactor Y of order 0.5-1.0 (Toth, Lemperiere, Deremble, de Lataillade,
  Kockelkoren & Bouchaud, "Anomalous price impact and the critical nature of
  liquidity in financial markets", Phys. Rev. X 1, 021006 (2011); Almgren, Thum,
  Hauptmann & Li, "Direct estimation of equity market impact", Risk 18(7):58-62
  (2005)).

Units contract
--------------
The square-root law is dimensionally homogeneous: sigma must be expressed in the
*same relative unit* as the cost it predicts. This module works in basis points, so:

- ``spread_bps``           - quoted bid-ask spread, in bps.
- ``volatility_daily_pct`` - daily return volatility in **percent** (``1.5`` means
  1.5% per day), converted internally to bps via ``PCT_TO_BPS`` (1% = 100 bps).
- ``realized_is_bps``      - realized IS in bps, signed so that positive = cost.

Passing a decimal fraction (``0.015``) where percent (``1.5``) is required understates
the impact term by 100x and pushes essentially all fitted cost into the spread
coefficient. ``MAX_PLAUSIBLE_DAILY_VOL_PCT`` is only a coarse guard: it catches a bps
or annualized figure in this field, but it cannot detect a decimal fraction, which is
a valid - if tiny - percent. The contract is the caller's to honour.

Refitting
---------
``refit_model_parameters`` solves the two-regressor, no-intercept least-squares
problem in closed form via the normal equations:

    [S11 S12] [eta  ]   [T1]      S11 = sum x1^2, S12 = sum x1*x2, S22 = sum x2^2
    [S12 S22] [gamma] = [T2]      T1  = sum x1*y,  T2  = sum x2*y

with x1 = spread_bps and x2 = sigma_bps * sqrt(Q/ADV). No intercept is fitted: a
zero-size order in a zero-spread market has zero modelled cost, and a free intercept
would absorb impact into a constant that does not scale with order size.

Limitations (documented, deliberate)
------------------------------------
- **The fit is in-sample.** The refitted coefficients are scored on the same trades
  that triggered the recalibration, so ``post_refit_rmse_bps`` is an optimistic lower
  bound on live tracking error, not a forecast of it. Validate refitted parameters on
  a held-out or subsequent trade sample before promoting them to production.
- **Trigger arm only.** This engine implements the *trigger-based* arm of a
  recalibration cadence. The calendar arm (weekly/monthly review, and the annual
  validation floor RTS 6 Art. 9 imposes on EU algorithmic trading firms) is a
  scheduler concern and is not modelled here. See ``references/standards.md``.
- **No causal attribution.** A tripped threshold says the model no longer matches
  realized cost; it does not say why. Refitting a model whose error came from a venue
  outage, a fee-schedule change or a single outlier trade bakes that event into the
  coefficients. Screen the sample before refitting.
- **Ordering and selection are the caller's responsibility.** The engine treats
  ``trade_history`` as an unordered sample of *completed* executions. It does not
  deduplicate, window, or verify that realized IS was measured after execution
  completed; feeding partially-filled or still-working orders leaks incomplete
  outcomes into the fit.
- **Equal weighting.** Every trade contributes equally to the least-squares
  objective, so large orders (where the impact term matters most) do not dominate.
  Value- or size-weighted fitting is out of scope.
"""
import logging
import math
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

#: 1 percent = 100 basis points. Converts ``volatility_daily_pct`` into the bps unit
#: the rest of the model works in.
PCT_TO_BPS = 100.0

#: Sanity ceiling on daily volatility expressed in percent. A daily return volatility
#: above 200%/day is not a market observation; it is almost always a caller that
#: supplied a decimal fraction or an annualized figure in the wrong field.
MAX_PLAUSIBLE_DAILY_VOL_PCT = 200.0

#: Minimum acceptable conditioning of the 2x2 design matrix, expressed as
#: det / (S11 * S22). This quantity equals 1 - cos^2(angle between the two regressor
#: columns): 1.0 is orthogonal, 0.0 is perfectly collinear. Below this floor eta and
#: gamma are not separately identifiable from the sample.
DEFAULT_MIN_DESIGN_CONDITIONING = 1e-6

#: Governance default for the smallest trade sample the engine will refit on. Not an
#: industry standard - see ``references/standards.md``.
DEFAULT_MIN_RECALIBRATION_SAMPLE_SIZE = 50

#: Relative tolerance on the "least squares cannot increase in-sample RMSE" invariant.
#: Without it, an incumbent that already sits at the sample optimum can trip the check
#: on floating-point noise alone and raise a spurious manual-review escalation.
RMSE_INVARIANT_RELATIVE_TOLERANCE = 1e-9

STATUS_STABLE = "MODEL_PARAMETER_STABLE"
STATUS_RECALIBRATION_RECOMMENDED = "RECALIBRATION_RECOMMENDED"
STATUS_DEFERRED_INSUFFICIENT_SAMPLE = "RECALIBRATION_DEFERRED_INSUFFICIENT_SAMPLE"
STATUS_MANUAL_REVIEW = "RECALIBRATION_REQUIRED_MANUAL_REVIEW"


@dataclass
class TradeExecutionRecord:
    """One completed parent order with its realized execution cost.

    ``volatility_daily_pct`` is in **percent** (1.5 == 1.5% per day), not a decimal
    fraction. ``realized_is_bps`` is signed: positive means the execution cost money
    relative to the decision price, negative means it beat the benchmark.
    """
    trade_id: str
    symbol: str
    order_qty: int
    adv_shares: float
    spread_bps: float
    volatility_daily_pct: float
    realized_is_bps: float


@dataclass
class CostModelParameters:
    eta_spread_coefficient: float       # Coefficient on bid-ask spread (bps -> bps)
    gamma_impact_coefficient: float     # Coefficient on square-root volume impact


@dataclass
class CostModelFitResult:
    """Outcome of a least-squares refit, including why a refit was rejected.

    ``parameters`` may be populated even when ``is_well_posed`` is False - the
    economically inadmissible case returns the coefficients so they can be inspected.
    Never promote them on that basis: the only field safe to put into production is
    ``CostModelRecalibrationReport.recommended_parameters``, which is None unless the
    fit passed every gate.
    """
    parameters: Optional[CostModelParameters]
    observations: int
    design_conditioning: float          # det / (S11*S22); 1.0 orthogonal, 0.0 collinear
    is_well_posed: bool
    rejection_reason: Optional[str] = None


@dataclass
class CostModelRecalibrationReport:
    model_name: str
    asset_class: str
    total_trades_analyzed: int
    active_parameters: CostModelParameters
    tracking_error_rmse_bps: float
    mean_prediction_bias_bps: float
    is_recalibration_triggered: bool
    recommended_parameters: Optional[CostModelParameters]
    status: str                         # One of the STATUS_* constants
    audit_notes: str
    # Populated only when a refit was attempted and accepted; None otherwise.
    post_refit_rmse_bps: Optional[float] = None
    post_refit_bias_bps: Optional[float] = None
    fit_result: Optional[CostModelFitResult] = None


class ExecutionCostModelRecalibrationEngine:
    """
    Audits an execution cost model's tracking error (RMSE) and systematic prediction
    bias against realized IS, and refits its impact coefficients by ordinary least
    squares when a governance threshold is breached.

    Thresholds are configuration, not industry standards: no regulator or standards
    body publishes a mandatory TCA tracking-error limit. Calibrate them against the
    firm's own cost-estimation tolerance and record the rationale.
    """

    def __init__(
        self,
        max_tracking_error_rmse_bps: float = 3.5,
        max_systematic_bias_bps: float = 1.5,
        min_recalibration_sample_size: int = DEFAULT_MIN_RECALIBRATION_SAMPLE_SIZE,
        min_design_conditioning: float = DEFAULT_MIN_DESIGN_CONDITIONING,
    ) -> None:
        if not math.isfinite(max_tracking_error_rmse_bps) or max_tracking_error_rmse_bps <= 0:
            raise ValueError("max_tracking_error_rmse_bps must be a finite positive number.")
        if not math.isfinite(max_systematic_bias_bps) or max_systematic_bias_bps <= 0:
            raise ValueError("max_systematic_bias_bps must be a finite positive number.")
        if min_recalibration_sample_size < 2:
            raise ValueError(
                "min_recalibration_sample_size must be at least 2: the model has two free "
                "coefficients and cannot be identified from fewer observations."
            )
        if not 0.0 < min_design_conditioning < 1.0:
            raise ValueError("min_design_conditioning must lie strictly between 0 and 1.")

        self.max_tracking_error_rmse_bps = max_tracking_error_rmse_bps
        self.max_systematic_bias_bps = max_systematic_bias_bps
        self.min_recalibration_sample_size = min_recalibration_sample_size
        self.min_design_conditioning = min_design_conditioning

    # ------------------------------------------------------------------ validation

    @staticmethod
    def _validate_record(record: TradeExecutionRecord, position: Optional[int] = None) -> None:
        """Reject a trade record that cannot produce a meaningful prediction.

        Non-finite values are rejected outright: a single NaN propagates through the
        mean into RMSE and bias, and ``nan > threshold`` is False, so an unvalidated
        corrupt sample reports the model as stable and suppresses the recalibration it
        should have triggered.
        """
        tag = (
            f"trade_history[{position}]" if position is not None else "trade record"
        ) + f" (trade_id={record.trade_id!r})"

        if record.order_qty <= 0:
            raise ValueError(f"{tag}: order_qty must be positive, got {record.order_qty}.")
        if not math.isfinite(record.adv_shares) or record.adv_shares <= 0:
            raise ValueError(
                f"{tag}: adv_shares must be finite and positive, got {record.adv_shares}."
            )
        if not math.isfinite(record.spread_bps) or record.spread_bps < 0:
            raise ValueError(
                f"{tag}: spread_bps must be finite and non-negative, got {record.spread_bps}."
            )
        if not math.isfinite(record.volatility_daily_pct) or record.volatility_daily_pct < 0:
            raise ValueError(
                f"{tag}: volatility_daily_pct must be finite and non-negative, "
                f"got {record.volatility_daily_pct}."
            )
        if record.volatility_daily_pct > MAX_PLAUSIBLE_DAILY_VOL_PCT:
            raise ValueError(
                f"{tag}: volatility_daily_pct={record.volatility_daily_pct} exceeds "
                f"{MAX_PLAUSIBLE_DAILY_VOL_PCT}%/day. This field is in percent "
                f"(1.5 == 1.5%/day), not a decimal fraction or an annualized figure."
            )
        if not math.isfinite(record.realized_is_bps):
            raise ValueError(
                f"{tag}: realized_is_bps must be finite, got {record.realized_is_bps}. "
                "Non-finite realized cost would propagate into RMSE and bias and "
                "silently report the model as stable."
            )

    def _validate_history(self, trade_history: Sequence[TradeExecutionRecord]) -> None:
        if not trade_history:
            raise ValueError("Trade execution history cannot be empty.")
        for position, record in enumerate(trade_history):
            self._validate_record(record, position)

    # ------------------------------------------------------------------- modelling

    @staticmethod
    def _regressors(record: TradeExecutionRecord) -> Tuple[float, float]:
        """Return (spread term, square-root impact term), both in bps.

        Caller must have validated the record: ``adv_shares > 0`` and ``order_qty > 0``
        are preconditions of the division and the square root.
        """
        participation = record.order_qty / record.adv_shares
        impact_regressor = record.volatility_daily_pct * PCT_TO_BPS * math.sqrt(participation)
        return record.spread_bps, impact_regressor

    def predict_slippage_bps(
        self, params: CostModelParameters, record: TradeExecutionRecord
    ) -> float:
        """Predicted Implementation Shortfall in bps.

            IS_pred = eta * spread_bps + gamma * sigma_bps * sqrt(qty / ADV)

        Returned unrounded: this value feeds residuals, the least-squares fit and the
        threshold comparisons, all of which would be quantised by rounding here.
        """
        self._validate_record(record)
        spread_regressor, impact_regressor = self._regressors(record)
        return (
            params.eta_spread_coefficient * spread_regressor
            + params.gamma_impact_coefficient * impact_regressor
        )

    def _error_metrics(
        self, params: CostModelParameters, trade_history: Sequence[TradeExecutionRecord]
    ) -> Tuple[float, float]:
        """Return (rmse_bps, mean_bias_bps), unrounded.

        Bias is ``mean(realized - predicted)``: positive means the model
        systematically *under*-predicts cost.
        """
        errors = []
        for record in trade_history:
            spread_regressor, impact_regressor = self._regressors(record)
            predicted = (
                params.eta_spread_coefficient * spread_regressor
                + params.gamma_impact_coefficient * impact_regressor
            )
            errors.append(record.realized_is_bps - predicted)

        n = len(errors)
        mse = sum(e * e for e in errors) / n
        return math.sqrt(mse), sum(errors) / n

    def refit_model_parameters(
        self, trade_history: Sequence[TradeExecutionRecord]
    ) -> CostModelFitResult:
        """Refit (eta, gamma) by ordinary least squares over ``trade_history``.

        Solves the no-intercept normal equations in closed form and reports whether the
        fit is usable. The fit is rejected - rather than silently returned - when the
        design is degenerate or near-collinear, because in that regime eta and gamma
        trade off against each other freely and the individual coefficients are
        arbitrary even where the combined prediction still looks reasonable.

        The active parameters are deliberately *not* an input: least squares finds the
        sample optimum, so seeding it with the incumbent values would only bias the
        result toward a model the audit has already rejected.
        """
        self._validate_history(trade_history)

        s11 = s12 = s22 = t1 = t2 = 0.0
        for record in trade_history:
            x1, x2 = self._regressors(record)
            y = record.realized_is_bps
            s11 += x1 * x1
            s12 += x1 * x2
            s22 += x2 * x2
            t1 += x1 * y
            t2 += x2 * y

        n = len(trade_history)

        if s11 <= 0.0 or s22 <= 0.0:
            reason = (
                "Degenerate design: every trade in the sample has zero spread."
                if s11 <= 0.0
                else "Degenerate design: every trade in the sample has zero volatility "
                     "or zero participation."
            )
            return CostModelFitResult(None, n, 0.0, False, reason)

        determinant = s11 * s22 - s12 * s12
        conditioning = determinant / (s11 * s22)

        if conditioning < self.min_design_conditioning:
            return CostModelFitResult(
                None, n, conditioning, False,
                f"Near-collinear design (conditioning {conditioning:.3e} < "
                f"{self.min_design_conditioning:.3e}): the spread and impact regressors do "
                "not vary independently across this sample, so eta and gamma are not "
                "separately identifiable. Widen the sample across order sizes and spread "
                "regimes."
            )

        eta = (s22 * t1 - s12 * t2) / determinant
        gamma = (s11 * t2 - s12 * t1) / determinant

        if not (math.isfinite(eta) and math.isfinite(gamma)):
            return CostModelFitResult(
                None, n, conditioning, False,
                "Least-squares solution is not finite; inspect the trade sample for "
                "extreme values."
            )

        if eta < 0.0 or gamma < 0.0:
            return CostModelFitResult(
                CostModelParameters(eta, gamma), n, conditioning, False,
                f"Fitted coefficients are not economically admissible (eta={eta:.4f}, "
                f"gamma={gamma:.4f}): a negative coefficient implies wider spreads or larger "
                "orders reduce cost. Do not promote to production; investigate the sample."
            )

        return CostModelFitResult(CostModelParameters(eta, gamma), n, conditioning, True)

    # ----------------------------------------------------------------------- audit

    def audit_and_recalibrate(
        self,
        model_name: str,
        asset_class: str,
        active_params: CostModelParameters,
        trade_history: List[TradeExecutionRecord],
    ) -> CostModelRecalibrationReport:
        """Audit the active model's tracking error and bias, refitting if a threshold trips.

        Thresholds are compared against the *unrounded* metrics; rounding happens only
        when the numbers are written to the report, so an RMSE of 3.504 bps against a
        3.50 bps limit triggers rather than rounding itself back inside the limit.
        """
        self._validate_history(trade_history)

        rmse_exact, bias_exact = self._error_metrics(active_params, trade_history)
        is_triggered = (
            rmse_exact > self.max_tracking_error_rmse_bps
            or abs(bias_exact) > self.max_systematic_bias_bps
        )

        rmse = round(rmse_exact, 2)
        bias = round(bias_exact, 2)
        n = len(trade_history)
        metrics = (
            f"RMSE {rmse:.2f}bps vs {self.max_tracking_error_rmse_bps:.2f}bps limit, "
            f"Bias {bias:+.2f}bps vs +/-{self.max_systematic_bias_bps:.2f}bps limit"
        )

        recommended: Optional[CostModelParameters] = None
        fit_result: Optional[CostModelFitResult] = None
        post_rmse: Optional[float] = None
        post_bias: Optional[float] = None

        if not is_triggered:
            status = STATUS_STABLE
            notes = (
                f"COST MODEL STABLE [{model_name} - {asset_class}]: {metrics} over {n} trades. "
                "Active parameters retained."
            )
            logger.info(notes)
        elif n < self.min_recalibration_sample_size:
            # The headline pitfall this skill exists to prevent: refitting on a sample
            # too small to distinguish a regime change from noise.
            status = STATUS_DEFERRED_INSUFFICIENT_SAMPLE
            notes = (
                f"RECALIBRATION DEFERRED [{model_name} - {asset_class}]: {metrics}, but only {n} "
                f"trades are available against a minimum sample of "
                f"{self.min_recalibration_sample_size}. Refitting on this sample would fit "
                "noise. Active parameters retained; re-audit once the sample is sufficient."
            )
            logger.warning(notes)
        else:
            fit_result = self.refit_model_parameters(trade_history)
            if not fit_result.is_well_posed:
                status = STATUS_MANUAL_REVIEW
                notes = (
                    f"RECALIBRATION REQUIRES MANUAL REVIEW [{model_name} - {asset_class}]: "
                    f"{metrics} over {n} trades, but the refit was rejected. "
                    f"{fit_result.rejection_reason} Active parameters retained."
                )
                logger.warning(notes)
            else:
                candidate = fit_result.parameters
                post_rmse_exact, post_bias_exact = self._error_metrics(candidate, trade_history)
                if post_rmse_exact > rmse_exact * (1.0 + RMSE_INVARIANT_RELATIVE_TOLERANCE):
                    # Least squares minimises in-sample SSE, so this branch is
                    # analytically unreachable; reaching it means the solve was
                    # numerically unsound and the result must not be promoted.
                    status = STATUS_MANUAL_REVIEW
                    notes = (
                        f"RECALIBRATION REQUIRES MANUAL REVIEW [{model_name} - {asset_class}]: "
                        f"refitted parameters increased in-sample RMSE "
                        f"({post_rmse_exact:.4f}bps > {rmse_exact:.4f}bps), indicating a "
                        "numerically unsound solve. Active parameters retained."
                    )
                    logger.error(notes)
                else:
                    status = STATUS_RECALIBRATION_RECOMMENDED
                    recommended = candidate
                    post_rmse = round(post_rmse_exact, 2)
                    post_bias = round(post_bias_exact, 2)
                    notes = (
                        f"COST MODEL RECALIBRATION TRIGGERED [{model_name} - {asset_class}]: "
                        f"{metrics} over {n} trades. Least-squares refit: "
                        f"Eta={candidate.eta_spread_coefficient:.4f}, "
                        f"Gamma={candidate.gamma_impact_coefficient:.4f} (in-sample RMSE "
                        f"{post_rmse:.2f}bps, bias {post_bias:+.2f}bps, design conditioning "
                        f"{fit_result.design_conditioning:.3e}). In-sample fit is optimistic: "
                        "validate on a held-out sample before promoting to production."
                    )
                    logger.warning(notes)

        return CostModelRecalibrationReport(
            model_name=model_name,
            asset_class=asset_class,
            total_trades_analyzed=n,
            active_parameters=active_params,
            tracking_error_rmse_bps=rmse,
            mean_prediction_bias_bps=bias,
            is_recalibration_triggered=is_triggered,
            recommended_parameters=recommended,
            status=status,
            audit_notes=notes,
            post_refit_rmse_bps=post_rmse,
            post_refit_bias_bps=post_bias,
            fit_result=fit_result,
        )
