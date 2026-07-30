import math
import statistics
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

@dataclass
class TradeExecutionRecord:
    trade_id: str
    symbol: str
    order_qty: int
    adv_shares: float
    spread_bps: float
    volatility_daily_pct: float
    realized_is_bps: float

@dataclass
class CostModelParameters:
    eta_spread_coefficient: float       # Coefficient on bid-ask spread
    gamma_impact_coefficient: float     # Coefficient on square-root volume impact

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
    status: str                         # 'MODEL_PARAMETER_STABLE', 'RECALIBRATION_RECOMMENDED'
    audit_notes: str

class ExecutionCostModelRecalibrationEngine:
    """
    Quantitative TCA engine for auditing execution transaction cost model (TCM) tracking error (RMSE),
    detecting systematic prediction bias, and executing recalibration cadences.
    """
    def __init__(
        self,
        max_tracking_error_rmse_bps: float = 3.5,
        max_systematic_bias_bps: float = 1.5
    ):
        self.max_tracking_error_rmse_bps = max_tracking_error_rmse_bps
        self.max_systematic_bias_bps = max_systematic_bias_bps

    def predict_slippage_bps(self, params: CostModelParameters, record: TradeExecutionRecord) -> float:
        """
        Predicts Implementation Shortfall in bps: IS_pred = eta * spread + gamma * volatility * sqrt(qty / ADV)
        """
        volume_ratio = math.sqrt(record.order_qty / float(record.adv_shares))
        pred = (params.eta_spread_coefficient * record.spread_bps) + (params.gamma_impact_coefficient * record.volatility_daily_pct * 100.0 * volume_ratio)
        return round(pred, 2)

    def refit_model_parameters(self, trade_history: List[TradeExecutionRecord]) -> CostModelParameters:
        """
        Refits model impact coefficients (eta, gamma) using simple least-squares adjustment over recent trade history.
        """
        # Calculate scaling ratios between realized IS and base components
        realized_list = [t.realized_is_bps for t in trade_history]
        avg_realized = statistics.mean(realized_list)

        # Baseline predictions at default eta=0.5, gamma=1.0
        base_params = CostModelParameters(0.5, 1.0)
        preds = [self.predict_slippage_bps(base_params, t) for t in trade_history]
        avg_pred = statistics.mean(preds)

        scale_factor = max(0.1, avg_realized / float(avg_pred)) if avg_pred > 0 else 1.0

        new_eta = round(base_params.eta_spread_coefficient * scale_factor, 4)
        new_gamma = round(base_params.gamma_impact_coefficient * scale_factor, 4)

        return CostModelParameters(eta_spread_coefficient=new_eta, gamma_impact_coefficient=new_gamma)

    def audit_and_recalibrate(
        self,
        model_name: str,
        asset_class: str,
        active_params: CostModelParameters,
        trade_history: List[TradeExecutionRecord]
    ) -> CostModelRecalibrationReport:
        """
        Audits active model tracking error & bias, triggering parameter recalibration if thresholds are breached.
        """
        if not trade_history:
            raise ValueError("Trade execution history cannot be empty.")

        errors: List[float] = []

        for trade in trade_history:
            pred_is = self.predict_slippage_bps(active_params, trade)
            err = trade.realized_is_bps - pred_is
            errors.append(err)

        # Compute RMSE = sqrt(mean(err^2))
        mse = statistics.mean([e ** 2 for e in errors])
        rmse = round(math.sqrt(mse), 2)

        # Compute Mean Systematic Bias = mean(err)
        bias = round(statistics.mean(errors), 2)

        # Audit Triggers
        is_triggered = (rmse > self.max_tracking_error_rmse_bps) or (abs(bias) > self.max_systematic_bias_bps)

        rec_params = None
        if is_triggered:
            status = "RECALIBRATION_RECOMMENDED"
            rec_params = self.refit_model_parameters(trade_history)
            notes = (
                f"COST MODEL RECALIBRATION TRIGGERED [{model_name} - {asset_class}]: RMSE {rmse:.2f}bps > {self.max_tracking_error_rmse_bps:.2f}bps limit "
                f"or Bias {bias:+.2f}bps > {self.max_systematic_bias_bps:.2f}bps limit. Refitted params: Eta={rec_params.eta_spread_coefficient:.4f}, Gamma={rec_params.gamma_impact_coefficient:.4f}."
            )
            logger.warning(notes)
        else:
            status = "MODEL_PARAMETER_STABLE"
            notes = f"COST MODEL STABLE [{model_name} - {asset_class}]: RMSE={rmse:.2f}bps, Bias={bias:+.2f}bps within SLA. Active parameters retained."
            logger.info(notes)

        return CostModelRecalibrationReport(
            model_name=model_name,
            asset_class=asset_class,
            total_trades_analyzed=len(trade_history),
            active_parameters=active_params,
            tracking_error_rmse_bps=rmse,
            mean_prediction_bias_bps=bias,
            is_recalibration_triggered=is_triggered,
            recommended_parameters=rec_params,
            status=status,
            audit_notes=notes
        )
