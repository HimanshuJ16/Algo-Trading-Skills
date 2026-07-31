import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

@dataclass
class ExpiryOptionPosition:
    symbol: str
    underlying_symbol: str
    strike: float
    option_type: str                     # 'CALL', 'PUT'
    position_qty: int                    # + for long, - for short
    spot_price: float
    hours_to_expiry: float               # e.g. 1.5 hours remaining

@dataclass
class PinRiskPolicyConfig:
    pin_distance_pct: float = 1.0        # 1.0% distance to strike threshold
    pin_cutoff_hours: float = 2.0        # Evaluate pin risk when <= 2.0 hours to expiry
    auto_close_unhedged: bool = True

@dataclass
class PinRiskReport:
    symbol: str
    pin_distance_pct: float
    is_pin_risk_high: bool
    recommended_action: str              # 'CLOSE_POSITION_BEFORE_EXPIRY', 'ROLL_POSITION', 'HOLD_TO_EXPIRY', 'ISSUE_DNE'
    max_assigned_notional_usd: float
    status: str                          # 'HIGH_PIN_RISK_ACTION_REQUIRED', 'LOW_PIN_RISK_SAFE'
    audit_notes: str

class OptionsPinRiskManagementEngine:
    """
    Options pin risk management engine identifying strike proximity risk on expiration day,
    evaluating contrary exercise instruction liabilities, and triggering automated position closes prior to market close.
    """
    def __init__(self, config: Optional[PinRiskPolicyConfig] = None):
        self.config = config or PinRiskPolicyConfig()

    def audit_position_pin_risk(
        self, pos: ExpiryOptionPosition
    ) -> PinRiskReport:
        """
        Audits single expiring option position for pin risk and resolves action recommendations.
        """
        if pos.spot_price <= 0 or pos.strike <= 0:
            raise ValueError("Spot price and strike must be positive.")

        # Pin Distance % = |Spot - Strike| / Spot * 100
        dist_pct = (abs(pos.spot_price - pos.strike) / pos.spot_price) * 100.0

        is_close_to_strike = dist_pct <= self.config.pin_distance_pct
        is_near_expiry = pos.hours_to_expiry <= self.config.pin_cutoff_hours
        is_high_pin_risk = is_close_to_strike and is_near_expiry

        max_assigned_notional = abs(pos.position_qty) * 100.0 * pos.spot_price

        if is_high_pin_risk:
            status = "HIGH_PIN_RISK_ACTION_REQUIRED"
            if pos.position_qty < 0:      # Short Position (Assignment Liability)
                action = "CLOSE_POSITION_BEFORE_EXPIRY"
            else:                         # Long Position
                action = "ISSUE_DNE" if dist_pct <= 0.10 else "CLOSE_POSITION_BEFORE_EXPIRY"
        else:
            status = "LOW_PIN_RISK_SAFE"
            action = "HOLD_TO_EXPIRY"

        pos_type_str = "SHORT" if pos.position_qty < 0 else "LONG"
        notes = (
            f"PIN RISK AUDIT [{pos.symbol} ({pos_type_str}) - {status}]: Distance = {dist_pct:.2f}% "
            f"(threshold {self.config.pin_distance_pct}%), Hours to Expiry = {pos.hours_to_expiry:.1f}h. "
            f"Max Assigned Notional = ${max_assigned_notional:,.2f}. Recommended Action: '{action}'."
        )

        if is_high_pin_risk:
            logger.warning(f"HIGH PIN RISK ALERT: {notes}")
        else:
            logger.info(notes)

        return PinRiskReport(
            symbol=pos.symbol,
            pin_distance_pct=round(dist_pct, 2),
            is_pin_risk_high=is_high_pin_risk,
            recommended_action=action,
            max_assigned_notional_usd=round(max_assigned_notional, 2),
            status=status,
            audit_notes=notes
        )
