import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

@dataclass
class SignalProfile:
    signal_id: str
    signal_name: str
    owner_entity: str                  # e.g. 'US_Quant_Research_Lab'
    base_license_fee_annual_usd: float
    pnl_share_pct: float               # e.g. 0.05 (5% share of strategy PnL)
    max_aum_capacity_usd: float        # Maximum total AUM permitted across all consumers

@dataclass
class StrategySubscription:
    subscription_id: str
    strategy_id: str
    signal_id: str
    consumer_entity: str               # e.g. 'UK_StatArb_Desk'
    allocated_aum_usd: float
    is_active: bool

@dataclass
class FeeAttributionReport:
    subscription_id: str
    strategy_id: str
    signal_id: str
    base_fee_usd: float
    pnl_share_fee_usd: float
    total_fee_usd: float
    arm_length_compliant: bool

@dataclass
class EntitlementCheckResult:
    signal_id: str
    strategy_id: str
    is_entitled: bool
    current_total_subscribed_aum_usd: float
    max_aum_capacity_usd: float
    reason: str

class SignalReuseAndLicensingEngine:
    """
    Alpha marketplace governance engine for managing cross-strategy signal reuse,
    entitlement access control, internal transfer pricing fee attribution, and AUM capacity limits.
    """
    def __init__(self):
        self.signals: Dict[str, SignalProfile] = {}
        self.subscriptions: Dict[str, StrategySubscription] = {}

    def register_signal(self, signal: SignalProfile):
        self.signals[signal.signal_id] = signal

    def request_subscription(self, subscription: StrategySubscription) -> EntitlementCheckResult:
        """
        Audits a strategy subscription request against signal AUM capacity and active licenses.
        """
        if subscription.signal_id not in self.signals:
            return EntitlementCheckResult(
                signal_id=subscription.signal_id, strategy_id=subscription.strategy_id,
                is_entitled=False, current_total_subscribed_aum_usd=0.0, max_aum_capacity_usd=0.0,
                reason=f"Unknown signal ID {subscription.signal_id}"
            )

        signal = self.signals[subscription.signal_id]
        
        # Calculate current total subscribed AUM for this signal
        existing_aum = sum(
            s.allocated_aum_usd for s in self.subscriptions.values() 
            if s.signal_id == subscription.signal_id and s.is_active
        )
        
        projected_aum = existing_aum + subscription.allocated_aum_usd

        if projected_aum > signal.max_aum_capacity_usd:
            logger.error(
                f"ENTITLEMENT DENIED for {subscription.strategy_id} on {subscription.signal_id}: "
                f"Projected AUM ${projected_aum:,.0f} exceeds max capacity ${signal.max_aum_capacity_usd:,.0f}!"
            )
            return EntitlementCheckResult(
                signal_id=subscription.signal_id,
                strategy_id=subscription.strategy_id,
                is_entitled=False,
                current_total_subscribed_aum_usd=existing_aum,
                max_aum_capacity_usd=signal.max_aum_capacity_usd,
                reason=f"AUM capacity cap breached (${projected_aum:,.0f} > ${signal.max_aum_capacity_usd:,.0f})."
            )

        # Entitlement Granted
        self.subscriptions[subscription.subscription_id] = subscription
        logger.info(f"ENTITLEMENT GRANTED for {subscription.strategy_id} on {subscription.signal_id}.")
        
        return EntitlementCheckResult(
            signal_id=subscription.signal_id,
            strategy_id=subscription.strategy_id,
            is_entitled=True,
            current_total_subscribed_aum_usd=projected_aum,
            max_aum_capacity_usd=signal.max_aum_capacity_usd,
            reason="Subscription approved and active."
        )

    def calculate_transfer_pricing_fee(
        self,
        subscription_id: str,
        strategy_realized_pnl_usd: float
    ) -> FeeAttributionReport:
        """
        Calculates internal transfer pricing fee attribution:
        Fee = Base Fee + (PnL Share Pct * max(0, Realized PnL))
        """
        if subscription_id not in self.subscriptions:
            raise ValueError(f"Unknown subscription ID {subscription_id}")

        sub = self.subscriptions[subscription_id]
        signal = self.signals[sub.signal_id]

        base_fee = signal.base_license_fee_annual_usd
        pnl_fee = signal.pnl_share_pct * max(0.0, strategy_realized_pnl_usd)
        total_fee = base_fee + pnl_fee

        # Arm's Length compliance check: Fee must be positive and non-zero
        arm_length = (total_fee > 0)

        return FeeAttributionReport(
            subscription_id=subscription_id,
            strategy_id=sub.strategy_id,
            signal_id=sub.signal_id,
            base_fee_usd=round(base_fee, 2),
            pnl_share_fee_usd=round(pnl_fee, 2),
            total_fee_usd=round(total_fee, 2),
            arm_length_compliant=arm_length
        )
