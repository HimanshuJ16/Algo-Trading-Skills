import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class WalletBalances:
    hot_wallet_usd: float
    cold_vault_usd: float
    warm_buffer_usd: float = 0.0
    api_key_withdraw_permission_enabled: bool = False

@dataclass
class HotColdWalletAuditReport:
    total_portfolio_usd: float
    hot_wallet_usd: float
    cold_vault_usd: float
    current_hot_ratio: float
    target_hot_ratio: float
    rebalance_action: str                # 'SWEEP_TO_COLD', 'REFILL_HOT_FROM_COLD', 'HOLD_BALANCES', 'SECURITY_ALERT_WITHDRAW_ENABLED'
    proposed_transfer_usd: float
    is_api_key_secure: bool
    status: str                          # 'PORTFOLIO_BALANCED', 'REBALANCE_REQUIRED', 'CRITICAL_SECURITY_ALERT'
    audit_notes: str

class HotColdWalletManagerEngine:
    """
    Crypto treasury architecture engine for auditing Hot vs Cold wallet balance ratios (15% Hot / 85% Cold),
    managing automated sweep/refill transfers, and enforcing zero-withdrawal API keys.
    """
    def __init__(
        self,
        target_hot_ratio: float = 0.15,
        max_hot_ratio_threshold: float = 0.25, # > 25% triggers sweep
        min_hot_ratio_threshold: float = 0.05  # < 5% triggers refill
    ):
        self.target_hot_ratio = target_hot_ratio
        self.max_hot_ratio_threshold = max_hot_ratio_threshold
        self.min_hot_ratio_threshold = min_hot_ratio_threshold

    def audit_and_rebalance_treasury(self, balances: WalletBalances) -> HotColdWalletAuditReport:
        """
        Audits Hot/Cold wallet balances, checks API key permissions, and proposes rebalance transfers.
        """
        total = round(balances.hot_wallet_usd + balances.cold_vault_usd + balances.warm_buffer_usd, 2)
        if total <= 0:
            raise ValueError("Total portfolio value must be > 0.")

        current_hot_ratio = round(balances.hot_wallet_usd / float(total), 4)

        # 1. API Key Security Audit
        if balances.api_key_withdraw_permission_enabled:
            notes = (
                f"CRITICAL SECURITY ALERT: Trading bot API Key has WITHDRAWAL PERMISSION ENABLED! "
                f"Immediate security hazard. Revoke withdrawal permissions."
            )
            logger.critical(notes)
            return HotColdWalletAuditReport(
                total_portfolio_usd=total,
                hot_wallet_usd=balances.hot_wallet_usd,
                cold_vault_usd=balances.cold_vault_usd,
                current_hot_ratio=current_hot_ratio,
                target_hot_ratio=self.target_hot_ratio,
                rebalance_action="SECURITY_ALERT_WITHDRAW_ENABLED",
                proposed_transfer_usd=0.0,
                is_api_key_secure=False,
                status="CRITICAL_SECURITY_ALERT",
                audit_notes=notes
            )

        # 2. Rebalance Transfer Evaluation
        target_hot_usd = round(total * self.target_hot_ratio, 2)

        if current_hot_ratio > self.max_hot_ratio_threshold:
            # Hot balance exceeds max threshold -> Sweep excess to Cold Vault
            transfer_usd = round(balances.hot_wallet_usd - target_hot_usd, 2)
            action = "SWEEP_TO_COLD"
            status = "REBALANCE_REQUIRED"
            notes = (
                f"HOT WALLET SWEEP REQUIRED: Hot Ratio {current_hot_ratio*100:.1f}% exceeds max threshold {self.max_hot_ratio_threshold*100:.1f}%. "
                f"Proposing automated sweep of ${transfer_usd:,.2f} from Hot Wallet to Cold Vault."
            )
            logger.warning(notes)
        elif current_hot_ratio < self.min_hot_ratio_threshold:
            # Hot balance below min operational floor -> Refill Hot from Cold
            transfer_usd = round(target_hot_usd - balances.hot_wallet_usd, 2)
            action = "REFILL_HOT_FROM_COLD"
            status = "REBALANCE_REQUIRED"
            notes = (
                f"HOT WALLET REFILL REQUIRED: Hot Ratio {current_hot_ratio*100:.1f}% is below min floor {self.min_hot_ratio_threshold*100:.1f}%. "
                f"Requesting Multisig refill of ${transfer_usd:,.2f} from Cold Vault to Hot Wallet."
            )
            logger.warning(notes)
        else:
            action = "HOLD_BALANCES"
            status = "PORTFOLIO_BALANCED"
            transfer_usd = 0.0
            notes = (
                f"TREASURY BALANCED: Hot Ratio = {current_hot_ratio*100:.1f}% is within target range "
                f"[{self.min_hot_ratio_threshold*100:.0f}% - {self.max_hot_ratio_threshold*100:.0f}%]. No transfers required."
            )
            logger.info(notes)

        return HotColdWalletAuditReport(
            total_portfolio_usd=total,
            hot_wallet_usd=balances.hot_wallet_usd,
            cold_vault_usd=balances.cold_vault_usd,
            current_hot_ratio=current_hot_ratio,
            target_hot_ratio=self.target_hot_ratio,
            rebalance_action=action,
            proposed_transfer_usd=transfer_usd,
            is_api_key_secure=True,
            status=status,
            audit_notes=notes
        )
