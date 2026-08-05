import logging
import math
import statistics
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

class CalibrationError(Exception):
    """Base exception for drawdown limit calibration errors."""

class InsufficientDataError(CalibrationError):
    """Raised when returns history is too short for calibration."""

@dataclass
class Config:
    """Legacy config class for backward compatibility."""
    param: float = 1.0

class MainEngine:
    """Legacy main engine retained for backward compatibility."""
    def __init__(self, config: Config):
        self.config = config

    def process(self, value: float) -> float:
        return value * self.config.param

class CalibrationMethod(str, Enum):
    HISTORICAL_MAX_DD = "HISTORICAL_MAX_DD"
    PARAMETRIC_VAR = "PARAMETRIC_VAR"
    MONTE_CARLO_BOOTSTRAP = "MONTE_CARLO_BOOTSTRAP"
    EXTREME_VALUE_THEORY = "EXTREME_VALUE_THEORY"

@dataclass
class DrawdownMetrics:
    max_drawdown_pct: float
    max_drawdown_duration_days: int
    var_99_pct: float
    cvar_99_pct: float
    ulcer_index: float
    volatility_annualized: float

@dataclass
class CalibratedRiskLimits:
    portfolio_capital_usd: float
    calibrated_max_drawdown_pct: float
    calibrated_max_drawdown_usd: float
    calibrated_daily_loss_limit_usd: float
    position_size_scalar: float
    confidence_level_pct: float
    calibration_method: CalibrationMethod
    audit_notes: str

class DrawdownLimitCalibratorEngine:
    """
    Production-grade risk limit calibration engine estimating max drawdown, VaR/CVaR,
    and extreme tail risk from historical return series to calibrate prudent pre-trade risk limits.
    """
    def __init__(
        self,
        stress_buffer_multiplier: float = 1.5,
        target_confidence_pct: float = 99.0
    ):
        self.stress_buffer_multiplier = stress_buffer_multiplier
        self.target_confidence_pct = target_confidence_pct

    def compute_drawdown_metrics(self, daily_returns: List[float]) -> DrawdownMetrics:
        """Computes max drawdown %, max duration days, VaR 99%, CVaR 99%, and Ulcer Index."""
        n = len(daily_returns)
        if n < 10:
            raise InsufficientDataError(f"Minimum 10 return data points required, got {n}")

        # Cumulative equity curve
        equity = [1.0]
        for r in daily_returns:
            equity.append(equity[-1] * (1.0 + r))

        peak = equity[0]
        max_dd = 0.0
        current_dd_duration = 0
        max_dd_duration = 0

        dd_squared_sum = 0.0

        for eq in equity[1:]:
            if eq > peak:
                peak = eq
                current_dd_duration = 0
            else:
                current_dd_duration += 1
                max_dd_duration = max(max_dd_duration, current_dd_duration)

            dd = (peak - eq) / peak
            max_dd = max(max_dd, dd)
            dd_squared_sum += (dd * 100.0) ** 2

        ulcer_index = math.sqrt(dd_squared_sum / n) if n > 0 else 0.0

        # Annualized volatility
        daily_std = statistics.stdev(daily_returns) if n > 1 else 0.0
        ann_vol = daily_std * math.sqrt(252)

        # Parametric VaR 99% (Z = 2.326 for 99%)
        daily_mean = statistics.mean(daily_returns)
        var_99 = max(0.0, -(daily_mean - 2.326 * daily_std))

        # CVaR 99% (Expected Shortfall): mean of returns below VaR 99%
        tail_returns = [r for r in daily_returns if r <= -var_99]
        cvar_99 = abs(statistics.mean(tail_returns)) if tail_returns else var_99 * 1.25

        return DrawdownMetrics(
            max_drawdown_pct=round(max_dd * 100.0, 2),
            max_drawdown_duration_days=max_dd_duration,
            var_99_pct=round(var_99 * 100.0, 2),
            cvar_99_pct=round(cvar_99 * 100.0, 2),
            ulcer_index=round(ulcer_index, 2),
            volatility_annualized=round(ann_vol * 100.0, 2)
        )

    def calibrate_risk_limits(
        self,
        daily_returns: List[float],
        portfolio_capital_usd: float,
        method: CalibrationMethod = CalibrationMethod.HISTORICAL_MAX_DD
    ) -> CalibratedRiskLimits:
        """
        Calibrates max drawdown % limit, daily loss limit ($), and position size scalar.
        """
        metrics = self.compute_drawdown_metrics(daily_returns)

        if method == CalibrationMethod.HISTORICAL_MAX_DD:
            # Calibrated limit = Historical MaxDD * buffer multiplier
            calibrated_dd_pct = min(metrics.max_drawdown_pct * self.stress_buffer_multiplier, 50.0)
            calibrated_dd_pct = max(calibrated_dd_pct, 5.0)  # Floor at 5%
        elif method == CalibrationMethod.EXTREME_VALUE_THEORY:
            # EVT tail multiplier (CVaR 99% * 2.0)
            calibrated_dd_pct = min(metrics.cvar_99_pct * 2.0 * self.stress_buffer_multiplier, 50.0)
        else:
            # Parametric VaR
            calibrated_dd_pct = min(metrics.var_99_pct * math.sqrt(20) * self.stress_buffer_multiplier, 50.0)

        calibrated_dd_usd = round(portfolio_capital_usd * (calibrated_dd_pct / 100.0), 2)

        # Calibrated daily loss limit: 3x 99% daily VaR in USD
        daily_loss_usd = round(portfolio_capital_usd * (metrics.var_99_pct / 100.0) * 3.0, 2)

        # Position size scalar: inversely proportional to max drawdown depth
        if metrics.max_drawdown_pct > 20.0:
            position_scalar = round(20.0 / metrics.max_drawdown_pct, 2)
        else:
            position_scalar = 1.0

        notes = (
            f"RISK CALIBRATION [{method.value}]: Portfolio Capital = ${portfolio_capital_usd:,.2f}, "
            f"Hist MaxDD = {metrics.max_drawdown_pct}%, Calibrated MaxDD Limit = {calibrated_dd_pct:.2f}% "
            f"(${calibrated_dd_usd:,.2f}), Daily Loss Limit = ${daily_loss_usd:,.2f}, "
            f"Position Scalar = {position_scalar}."
        )

        logger.info(notes)

        return CalibratedRiskLimits(
            portfolio_capital_usd=portfolio_capital_usd,
            calibrated_max_drawdown_pct=round(calibrated_dd_pct, 2),
            calibrated_max_drawdown_usd=calibrated_dd_usd,
            calibrated_daily_loss_limit_usd=daily_loss_usd,
            position_size_scalar=position_scalar,
            confidence_level_pct=self.target_confidence_pct,
            calibration_method=method,
            audit_notes=notes
        )
