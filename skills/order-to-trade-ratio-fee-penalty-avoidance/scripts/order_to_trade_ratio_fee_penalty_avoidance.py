import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

@dataclass
class OTRMarketSession:
    venue: str
    orders_count: int
    cancels_count: int
    modifies_count: int
    trades_count: int
    ordered_volume: float
    traded_volume: float

@dataclass
class OTRThresholdPolicy:
    max_count_otr: float = 50.0          # Max allowable messages per trade (e.g. 50:1)
    max_volume_otr: float = 100.0        # Max allowable volume ordered per trade volume
    warning_threshold_pct: float = 0.80  # Trigger warning throttle at 80% (40:1)
    penalty_fee_per_excess_order: float = 0.05  # $0.05 / €0.05 per excess message

@dataclass
class OTRReport:
    venue: str
    total_messages: int
    count_otr: float
    volume_otr: float
    excess_messages: int
    penalty_fee_accrued_usd: float
    recommended_action: str              # 'ALLOW_ORDER', 'THROTTLE_ORDER_MODIFICATIONS', 'FREEZE_ORDER_MODIFICATIONS_REQUIRE_TAKER_FILL'
    status: str                          # 'OTR_COMPLIANT_SAFE', 'OTR_WARNING_THROTTLE_ACTIVE', 'OTR_BREACH_PENALTY_ACTIVE'
    audit_notes: str

class OrderToTradeRatioFeePenaltyEngine:
    """
    Order-to-Trade Ratio (OTR) fee penalty avoidance engine monitoring count and volume OTR,
    calculating exchange surcharge penalties, and enforcing defensive order throttling.
    """
    def __init__(self, policy: Optional[OTRThresholdPolicy] = None):
        self.policy = policy or OTRThresholdPolicy()

    def audit_session_otr(
        self, session: OTRMarketSession
    ) -> OTRReport:
        """
        Audits current session order-to-trade ratio, calculates accrued penalties, and determines throttling state.
        """
        total_messages = session.orders_count + session.cancels_count + session.modifies_count
        effective_trades = max(1, session.trades_count)
        effective_traded_vol = max(1.0, session.traded_volume)

        count_otr = total_messages / float(effective_trades)
        volume_otr = session.ordered_volume / float(effective_traded_vol)

        max_allowable_messages = int(effective_trades * self.policy.max_count_otr)
        excess_messages = max(0, total_messages - max_allowable_messages)
        penalty_fee = excess_messages * self.policy.penalty_fee_per_excess_order

        warning_ratio = self.policy.max_count_otr * self.policy.warning_threshold_pct

        if count_otr >= self.policy.max_count_otr:
            status = "OTR_BREACH_PENALTY_ACTIVE"
            action = "FREEZE_ORDER_MODIFICATIONS_REQUIRE_TAKER_FILL"
        elif count_otr >= warning_ratio:
            status = "OTR_WARNING_THROTTLE_ACTIVE"
            action = "THROTTLE_ORDER_MODIFICATIONS"
        else:
            status = "OTR_COMPLIANT_SAFE"
            action = "ALLOW_ORDER"

        notes = (
            f"OTR SESSION AUDIT [{session.venue} - {status}]: Total Messages = {total_messages} "
            f"(Orders={session.orders_count}, Cancels={session.cancels_count}, Modifies={session.modifies_count}), "
            f"Trades = {session.trades_count}. Count OTR = {count_otr:.1f}:1 (Max Limit {self.policy.max_count_otr:.1f}:1). "
            f"Excess Messages = {excess_messages}, Accrued Penalty Fee = ${penalty_fee:,.2f}. Action: '{action}'."
        )

        if status == "OTR_BREACH_PENALTY_ACTIVE":
            logger.error(f"OTR BREACH PENALTY: {notes}")
        elif status == "OTR_WARNING_THROTTLE_ACTIVE":
            logger.warning(f"OTR WARNING: {notes}")
        else:
            logger.info(notes)

        return OTRReport(
            venue=session.venue,
            total_messages=total_messages,
            count_otr=round(count_otr, 2),
            volume_otr=round(volume_otr, 2),
            excess_messages=excess_messages,
            penalty_fee_accrued_usd=round(penalty_fee, 2),
            recommended_action=action,
            status=status,
            audit_notes=notes
        )
