import logging
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

@dataclass
class Result:
    """Legacy Result container for backward compatibility."""
    success: bool
    message: str

class BaselZone(str, Enum):
    GREEN = "GREEN"      # 0 to 4 exceptions in 250 observations -> Model Valid
    YELLOW = "YELLOW"    # 5 to 9 exceptions in 250 observations -> Increased Capital Add-on
    RED = "RED"          # >= 10 exceptions in 250 observations -> Model Rejected

@dataclass
class DailyRiskObservation:
    date_iso: str
    realized_pnl_usd: float
    forecast_var_usd: float              # Positive number representing 1-day VaR limit (e.g. 100,000)
    confidence_level: float = 0.99       # 0.99 for 99% VaR

@dataclass
class VaRException:
    date_iso: str
    realized_pnl_usd: float
    forecast_var_usd: float
    breach_amount_usd: float

@dataclass
class RiskModelBacktestReport:
    total_observations: int
    expected_exceptions: float
    actual_exceptions: int
    exception_rate_pct: float
    kupiec_lr_stat: float
    kupiec_p_value: float
    basel_zone: BaselZone
    exceptions: List[VaRException]
    is_model_accepted: bool
    audit_notes: str

class RiskModelBacktesterEngine:
    """
    Production-grade risk model backtesting engine evaluating forecast VaR against realized PnL outcomes
    using Kupiec's POF test and Basel Traffic Light Framework.
    """
    def __init__(self, confidence_level: float = 0.99):
        self.confidence_level = confidence_level
        self.alpha = 1.0 - confidence_level  # e.g., 0.01 for 99% VaR

    def execute(self, param: bool) -> Result:
        """Legacy execute method retained for backward compatibility."""
        if param:
            return Result(True, "Success")
        return Result(False, "Failure")

    def _kupiec_pof_test(self, N: int, x: int, p: float) -> Tuple[float, float]:
        """
        Kupiec's Proportion of Failures (POF) Likelihood Ratio test.
        H0: Actual exception rate == p (e.g., 0.01)
        LR = -2 * ln[ (1-p)^(N-x) * p^x / ( (1 - x/N)^(N-x) * (x/N)^x ) ]
        Follows Chi-Square distribution with 1 degree of freedom (critical value 3.841 at 95% conf).
        """
        if x == 0:
            # Handle edge case x = 0
            lr = -2.0 * (N * math.log(1.0 - p))
        else:
            p_hat = x / N
            if p_hat >= 1.0:
                p_hat = 0.9999
            term1 = (N - x) * math.log(1.0 - p) + x * math.log(p)
            term2 = (N - x) * math.log(1.0 - p_hat) + x * math.log(p_hat)
            lr = -2.0 * (term1 - term2)

        lr = max(0.0, lr)
        # Approximate p-value using chi2(1) survival function
        # Simple approximation for chi2(1) p-value: erfc(sqrt(lr/2))
        p_val = math.erfc(math.sqrt(lr / 2.0))
        return round(lr, 4), round(p_val, 4)

    def backtest_var_model(
        self,
        observations: List[DailyRiskObservation]
    ) -> RiskModelBacktestReport:
        """
        Backtests VaR model against realized daily PnL outcomes.
        Counts exceptions (where realized PnL < -forecast VaR), computes Kupiec LR, and assigns Basel Zone.
        """
        N = len(observations)
        if N < 20:
            raise ValueError(f"Minimum 20 daily observations required for VaR backtesting, got {N}")

        exceptions: List[VaRException] = []
        for obs in observations:
            # Exception occurs if loss exceeds forecast VaR (realized_pnl < -forecast_var)
            if obs.realized_pnl_usd < -abs(obs.forecast_var_usd):
                breach = abs(obs.realized_pnl_usd) - abs(obs.forecast_var_usd)
                exceptions.append(VaRException(
                    date_iso=obs.date_iso,
                    realized_pnl_usd=obs.realized_pnl_usd,
                    forecast_var_usd=obs.forecast_var_usd,
                    breach_amount_usd=round(breach, 2)
                ))

        x = len(exceptions)
        p = self.alpha
        exp_exceptions = round(N * p, 2)
        exc_rate_pct = round((x / N) * 100.0, 2)

        # Kupiec POF Test
        lr_stat, p_val = self._kupiec_pof_test(N, x, p)

        # Basel Traffic Light Zone (scaled for 250 observations equivalent)
        scaled_x = int(round(x * (250.0 / N))) if N != 250 else x

        if scaled_x <= 4:
            zone = BaselZone.GREEN
            is_accepted = True
        elif scaled_x <= 9:
            zone = BaselZone.YELLOW
            is_accepted = True  # Yellow zone is accepted with capital add-on
        else:
            zone = BaselZone.RED
            is_accepted = False

        notes = (
            f"VaR BACKTEST [{zone.value}]: N = {N}, Exceptions = {x} (Expected = {exp_exceptions:.1f}), "
            f"Exception Rate = {exc_rate_pct}%, Kupiec LR = {lr_stat} (p-val = {p_val}). "
            f"Model Accepted = {is_accepted}."
        )

        if zone == BaselZone.RED:
            logger.error(notes)
        elif zone == BaselZone.YELLOW:
            logger.warning(notes)
        else:
            logger.info(notes)

        return RiskModelBacktestReport(
            total_observations=N,
            expected_exceptions=exp_exceptions,
            actual_exceptions=x,
            exception_rate_pct=exc_rate_pct,
            kupiec_lr_stat=lr_stat,
            kupiec_p_value=p_val,
            basel_zone=zone,
            exceptions=exceptions,
            is_model_accepted=is_accepted,
            audit_notes=notes
        )
