import logging
import math
import datetime
from dataclasses import dataclass, field
from typing import Dict, Optional, List, Tuple
from enum import Enum

logger = logging.getLogger(__name__)


class WithdrawalStatus(Enum):
    APPROVED = "APPROVED"                       # Instant automated execution
    TIMELOCK_HOLD = "TIMELOCK_HOLD"             # Enforce 24-hour cooling-off / Manual Multi-Sig Approval
    REJECTED_FREEZE = "REJECTED_FREEZE"         # Global Hot Wallet Circuit Breaker Freeze / Security Incident


class RiskFlag(Enum):
    EXCEEDS_HOURLY_LIMIT = "EXCEEDS_HOURLY_LIMIT"
    EXCEEDS_DAILY_LIMIT = "EXCEEDS_DAILY_LIMIT"
    ANOMALY_SIZE_ZSCORE = "ANOMALY_SIZE_ZSCORE"
    NEW_ADDRESS_HOLD = "NEW_ADDRESS_HOLD"
    HOT_WALLET_LIMIT_EXCEEDED = "HOT_WALLET_LIMIT_EXCEEDED"


class VelocityEngineError(Exception):
    """Base exception for Withdrawal Velocity Engine errors."""
    pass


@dataclass
class WithdrawalRequest:
    request_id: str
    account_id: str
    asset: str
    amount_crypto: float
    amount_usd: float
    destination_address: str
    timestamp: datetime.datetime = field(default_factory=datetime.datetime.utcnow)


@dataclass
class AddressWhitelistRecord:
    account_id: str
    address: str
    added_timestamp: datetime.datetime
    is_whitelisted: bool = True


@dataclass
class AccountHistoricalProfile:
    account_id: str
    mean_withdrawal_usd: float
    std_dev_usd: float
    historical_count: int


@dataclass
class WithdrawalDecision:
    request_id: str
    account_id: str
    status: WithdrawalStatus
    decision_timestamp: datetime.datetime
    risk_flags: List[RiskFlag] = field(default_factory=list)
    rationale: str = ""
    required_hold_hours: float = 0.0


class WithdrawalVelocityEngine:
    """
    Institutional Withdrawal Velocity Limits & Anomaly Detection Engine.
    
    Enforces multi-window rolling velocity caps (1h, 24h) per account and global hot wallet,
    evaluates user historical 90-day anomaly Z-scores, checks address whitelisting age,
    and executes automated circuit breakers (Approved, Timelock Hold, Hot Wallet Freeze).
    """

    def __init__(
        self,
        account_hourly_limit_usd: float = 100_000.0,
        account_daily_limit_usd: float = 500_000.0,
        global_hot_wallet_hourly_limit_usd: float = 2_000_000.0,
        anomaly_zscore_threshold: float = 3.0,
        address_whitelist_cooling_hours: float = 24.0,
    ):
        """
        :param account_hourly_limit_usd: Max 1-hour rolling USD withdrawal velocity per account.
        :param account_daily_limit_usd: Max 24-hour rolling USD withdrawal velocity per account.
        :param global_hot_wallet_hourly_limit_usd: Max 1-hour rolling USD velocity across hot wallet.
        :param anomaly_zscore_threshold: Z-score threshold to flag unusual withdrawal sizes.
        :param address_whitelist_cooling_hours: Required cooling period for newly whitelisted addresses.
        """
        self.account_hourly_limit_usd = account_hourly_limit_usd
        self.account_daily_limit_usd = account_daily_limit_usd
        self.global_hot_wallet_hourly_limit_usd = global_hot_wallet_hourly_limit_usd
        self.anomaly_zscore_threshold = anomaly_zscore_threshold
        self.address_whitelist_cooling_hours = address_whitelist_cooling_hours

        # Ledger of past withdrawal requests: request_id -> WithdrawalRequest
        self.withdrawal_ledger: List[WithdrawalRequest] = []
        logger.info(
            f"Initialized Withdrawal Velocity Engine (Account Limits: 1h=${account_hourly_limit_usd:,.0f}, "
            f"24h=${account_daily_limit_usd:,.0f}, HotWallet 1h=${global_hot_wallet_hourly_limit_usd:,.0f})"
        )

    def get_rolling_velocity_usd(
        self, account_id: Optional[str], window_hours: float, current_time: datetime.datetime
    ) -> float:
        """
        Calculates rolling USD withdrawal velocity over the past `window_hours` window.
        If account_id is None, calculates global hot wallet velocity.
        """
        cutoff = current_time - datetime.timedelta(hours=window_hours)
        total_usd = 0.0

        for req in self.withdrawal_ledger:
            if req.timestamp >= cutoff:
                if account_id is None or req.account_id == account_id:
                    total_usd += req.amount_usd

        return total_usd

    def evaluate_withdrawal_request(
        self,
        request: WithdrawalRequest,
        whitelist_record: Optional[AddressWhitelistRecord],
        profile: AccountHistoricalProfile,
    ) -> WithdrawalDecision:
        """
        Evaluates a withdrawal request against velocity caps, Z-score anomalies, and whitelist rules.
        """
        if request.amount_usd <= 0 or request.amount_crypto <= 0:
            raise VelocityEngineError("Withdrawal amount must be positive.")

        now = request.timestamp
        risk_flags = []

        # 1. Global Hot Wallet Hourly Velocity Check
        global_1h = self.get_rolling_velocity_usd(account_id=None, window_hours=1.0, current_time=now)
        if (global_1h + request.amount_usd) > self.global_hot_wallet_hourly_limit_usd:
            logger.error(
                f"HOT WALLET CIRCUIT BREAKER TRIGGERED: Global 1h velocity ${global_1h + request.amount_usd:,.2f} "
                f"> Limit ${self.global_hot_wallet_hourly_limit_usd:,.2f}"
            )
            return WithdrawalDecision(
                request_id=request.request_id,
                account_id=request.account_id,
                status=WithdrawalStatus.REJECTED_FREEZE,
                decision_timestamp=now,
                risk_flags=[RiskFlag.HOT_WALLET_LIMIT_EXCEEDED],
                rationale="Global hot wallet hourly withdrawal limit exceeded. Automated withdrawals frozen for SOC review.",
                required_hold_hours=0.0,
            )

        # 2. Account Rolling Hourly and Daily Velocity Checks
        acc_1h = self.get_rolling_velocity_usd(account_id=request.account_id, window_hours=1.0, current_time=now)
        acc_24h = self.get_rolling_velocity_usd(account_id=request.account_id, window_hours=24.0, current_time=now)

        if (acc_1h + request.amount_usd) > self.account_hourly_limit_usd:
            risk_flags.append(RiskFlag.EXCEEDS_HOURLY_LIMIT)

        if (acc_24h + request.amount_usd) > self.account_daily_limit_usd:
            risk_flags.append(RiskFlag.EXCEEDS_DAILY_LIMIT)

        # 3. Anomaly Z-Score Evaluation
        if profile.historical_count >= 5 and profile.std_dev_usd > 0:
            z_score = (request.amount_usd - profile.mean_withdrawal_usd) / profile.std_dev_usd
            if z_score >= self.anomaly_zscore_threshold:
                logger.warning(f"Withdrawal Anomaly [{request.account_id}]: Z={z_score:.2f} >= {self.anomaly_zscore_threshold}")
                risk_flags.append(RiskFlag.ANOMALY_SIZE_ZSCORE)

        # 4. Address Whitelist Age Check
        if whitelist_record is None or not whitelist_record.is_whitelisted:
            risk_flags.append(RiskFlag.NEW_ADDRESS_HOLD)
        else:
            age_hours = (now - whitelist_record.added_timestamp).total_seconds() / 3600.0
            if age_hours < self.address_whitelist_cooling_hours:
                logger.info(f"Newly Whitelisted Address [{request.destination_address[:8]}...]: Age={age_hours:.1f}h < {self.address_whitelist_cooling_hours}h")
                risk_flags.append(RiskFlag.NEW_ADDRESS_HOLD)

        # 5. Final Decision Determination
        if risk_flags:
            rationale = f"Withdrawal flagged for security review. Risk Flags: {[f.value for f in risk_flags]}."
            logger.warning(f"Timelock Hold Applied [{request.request_id}]: {rationale}")
            return WithdrawalDecision(
                request_id=request.request_id,
                account_id=request.account_id,
                status=WithdrawalStatus.TIMELOCK_HOLD,
                decision_timestamp=now,
                risk_flags=risk_flags,
                rationale=rationale,
                required_hold_hours=24.0,
            )

        # Record approved transaction into ledger
        self.withdrawal_ledger.append(request)

        logger.info(f"Withdrawal Approved [{request.request_id}]: ${request.amount_usd:,.2f} to {request.destination_address[:8]}...")
        return WithdrawalDecision(
            request_id=request.request_id,
            account_id=request.account_id,
            status=WithdrawalStatus.APPROVED,
            decision_timestamp=now,
            risk_flags=[],
            rationale="Within velocity limits and verified whitelist address.",
            required_hold_hours=0.0,
        )

