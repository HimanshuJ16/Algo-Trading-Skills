import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class ShortOptionPosition:
    position_id: str
    symbol: str
    option_type: str                    # 'CALL' or 'PUT'
    exercise_style: str                 # 'AMERICAN' or 'EUROPEAN'
    strike: float
    option_market_price: float
    underlying_price: float
    contracts_qty: int                  # Number of short contracts (e.g. 10)
    days_to_expiry: float
    upcoming_dividend_usd: float = 0.0  # Dividend paid prior to expiry
    days_to_ex_div: float = 999.0       # Days remaining until ex-dividend date

@dataclass
class EarlyExerciseAuditReport:
    position_id: str
    symbol: str
    option_type: str
    exercise_style: str
    strike: float
    underlying_price: float
    intrinsic_value_usd: float
    extrinsic_value_usd: float
    upcoming_dividend_usd: float
    assignment_probability_pct: float
    risk_level: str                     # 'CRITICAL_ASSIGNMENT_RISK', 'HIGH_ASSIGNMENT_RISK', 'LOW_RISK'
    recommended_action: str             # 'CLOSE_OR_ROLL_SHORT_CALL', 'MONITOR', 'NO_ACTION_REQUIRED'
    risk_summary: str

class EarlyExerciseRiskEngine:
    """
    Quantitative options risk engine for detecting early exercise and assignment risk on short American calls/puts,
    evaluating ex-dividend dividend capture vs extrinsic value, and triggering position roll warnings.
    """
    def __init__(self, min_extrinsic_threshold_usd: float = 0.05):
        self.min_extrinsic_threshold_usd = min_extrinsic_threshold_usd

    def audit_short_position_assignment_risk(self, pos: ShortOptionPosition) -> EarlyExerciseAuditReport:
        """
        Audits short option position for early exercise & assignment risk.
        """
        opt_type = pos.option_type.upper()
        style = pos.exercise_style.upper()
        S = pos.underlying_price
        K = pos.strike
        P = pos.option_market_price

        # Intrinsic & Extrinsic calculation
        if opt_type == "CALL":
            intrinsic = max(0.0, S - K)
        else:
            intrinsic = max(0.0, K - S)

        extrinsic = round(max(0.0, P - intrinsic), 4)
        intrinsic = round(intrinsic, 4)

        # European options cannot be exercised early!
        if style == "EUROPEAN":
            return EarlyExerciseAuditReport(
                position_id=pos.position_id, symbol=pos.symbol, option_type=opt_type, exercise_style=style,
                strike=K, underlying_price=S, intrinsic_value_usd=intrinsic, extrinsic_value_usd=extrinsic,
                upcoming_dividend_usd=pos.upcoming_dividend_usd, assignment_probability_pct=0.0,
                risk_level="LOW_RISK", recommended_action="NO_ACTION_REQUIRED",
                risk_summary="European-style option; early assignment impossible prior to expiration."
            )

        risk_lvl = "LOW_RISK"
        action = "NO_ACTION_REQUIRED"
        prob_pct = 0.0
        summary = "Extrinsic value buffers early exercise risk."

        # Rule 1: Short American Call Ex-Dividend Capture Audit
        if opt_type == "CALL" and pos.upcoming_dividend_usd > 0 and pos.days_to_ex_div <= 1.0:
            # If Dividend > Extrinsic Value, long holder will rationally exercise early on ex-div eve!
            if pos.upcoming_dividend_usd > extrinsic:
                risk_lvl = "CRITICAL_ASSIGNMENT_RISK"
                action = "CLOSE_OR_ROLL_SHORT_CALL"
                prob_pct = 100.0
                summary = (
                    f"CRITICAL EX-DIVIDEND ASSIGNMENT RISK [{pos.symbol}]: Dividend (${pos.upcoming_dividend_usd:.2f}) > "
                    f"Extrinsic Value (${extrinsic:.2f}). Rational long call holders WILL exercise early today!"
                )
                logger.critical(summary)

        # Rule 2: Deep ITM Short Put Audit
        elif opt_type == "PUT" and intrinsic > 0 and extrinsic <= self.min_extrinsic_threshold_usd:
            risk_lvl = "HIGH_ASSIGNMENT_RISK"
            action = "CLOSE_OR_ROLL_SHORT_PUT"
            prob_pct = 85.0
            summary = f"DEEP ITM SHORT PUT ASSIGNMENT RISK [{pos.symbol}]: Extrinsic value (${extrinsic:.2f}) near zero."
            logger.warning(summary)

        return EarlyExerciseAuditReport(
            position_id=pos.position_id,
            symbol=pos.symbol,
            option_type=opt_type,
            exercise_style=style,
            strike=K,
            underlying_price=S,
            intrinsic_value_usd=intrinsic,
            extrinsic_value_usd=extrinsic,
            upcoming_dividend_usd=pos.upcoming_dividend_usd,
            assignment_probability_pct=prob_pct,
            risk_level=risk_lvl,
            recommended_action=action,
            risk_summary=summary
        )
