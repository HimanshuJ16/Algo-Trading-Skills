import time
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class WhitelistedAddressRecord:
    address_id: str
    asset_symbol: str                   # e.g. 'BTC', 'ETH', 'USDT'
    destination_address: str
    label: str
    added_timestamp_seconds: float
    cooloff_duration_seconds: float = 86_400.0  # 24 hours default

@dataclass
class WithdrawalRequest:
    request_id: str
    asset_symbol: str
    amount: float
    destination_address: str
    request_timestamp_seconds: float
    is_withdrawal_enabled_on_key: bool = True

@dataclass
class WithdrawalWhitelistAuditReport:
    request_id: str
    asset_symbol: str
    amount: float
    destination_address: str
    is_address_whitelisted: bool
    is_cooloff_elapsed: bool
    remaining_cooloff_seconds: float
    is_withdrawal_approved: bool
    status: str                         # 'WITHDRAWAL_APPROVED', 'UNAUTHORIZED_ADDRESS_REJECTION', 'COOLOFF_PERIOD_ACTIVE_REJECTION', 'API_KEY_WITHDRAWAL_DISABLED'
    audit_notes: str

class ExchangeWithdrawalWhitelistEngine:
    """
    Quantitative crypto custody and security engine for enforcing exchange withdrawal address allowlists,
    auditing mandatory 24h/48h cool-off locks, and preventing API key drain attacks.
    """
    def __init__(self, default_cooloff_seconds: float = 86_400.0):
        self.default_cooloff_seconds = default_cooloff_seconds
        self.allowlist: Dict[str, WhitelistedAddressRecord] = {}

    def register_whitelisted_address(self, record: WhitelistedAddressRecord):
        # Keyed by (asset_symbol, destination_address)
        key = f"{record.asset_symbol.upper()}:{record.destination_address}"
        self.allowlist[key] = record
        logger.info(f"WHITELISTED ADDRESS REGISTERED [{key}]: Added at timestamp {record.added_timestamp_seconds}.")

    def audit_withdrawal_request(self, req: WithdrawalRequest) -> WithdrawalWhitelistAuditReport:
        """
        Audits withdrawal request against API key permissions, allowlist membership, and 24h cool-off lock.
        """
        # Rule 1: API Key Permission Audit
        if not req.is_withdrawal_enabled_on_key:
            msg = f"API KEY REJECTION [{req.request_id}]: API key does not have withdrawal permissions enabled."
            logger.critical(msg)
            return WithdrawalWhitelistAuditReport(
                request_id=req.request_id, asset_symbol=req.asset_symbol, amount=req.amount,
                destination_address=req.destination_address, is_address_whitelisted=False, is_cooloff_elapsed=False,
                remaining_cooloff_seconds=0.0, is_withdrawal_approved=False, status="API_KEY_WITHDRAWAL_DISABLED", audit_notes=msg
            )

        key = f"{req.asset_symbol.upper()}:{req.destination_address}"
        record = self.allowlist.get(key)

        # Rule 2: Address Membership Audit
        if not record:
            msg = f"UNAUTHORIZED ADDRESS REJECTION [{req.request_id}]: Destination '{req.destination_address}' is NOT on the whitelisted allowlist for {req.asset_symbol}!"
            logger.critical(msg)
            return WithdrawalWhitelistAuditReport(
                request_id=req.request_id, asset_symbol=req.asset_symbol, amount=req.amount,
                destination_address=req.destination_address, is_address_whitelisted=False, is_cooloff_elapsed=False,
                remaining_cooloff_seconds=0.0, is_withdrawal_approved=False, status="UNAUTHORIZED_ADDRESS_REJECTION", audit_notes=msg
            )

        # Rule 3: 24-Hour Cool-off Security Lock Audit
        elapsed = req.request_timestamp_seconds - record.added_timestamp_seconds
        remaining = max(0.0, record.cooloff_duration_seconds - elapsed)

        if elapsed < record.cooloff_duration_seconds:
            rem_hours = remaining / 3600.0
            msg = (
                f"COOLOFF LOCK ACTIVE REJECTION [{req.request_id}]: Address '{req.destination_address}' was added recently. "
                f"Security lock active for another {rem_hours:.1f} hours ({remaining:.0f}s remaining)."
            )
            logger.warning(msg)
            return WithdrawalWhitelistAuditReport(
                request_id=req.request_id, asset_symbol=req.asset_symbol, amount=req.amount,
                destination_address=req.destination_address, is_address_whitelisted=True, is_cooloff_elapsed=False,
                remaining_cooloff_seconds=remaining, is_withdrawal_approved=False, status="COOLOFF_PERIOD_ACTIVE_REJECTION", audit_notes=msg
            )

        notes = f"WITHDRAWAL APPROVED [{req.request_id}]: {req.amount} {req.asset_symbol} to whitelisted address '{req.destination_address}' ({record.label})."
        logger.info(notes)

        return WithdrawalWhitelistAuditReport(
            request_id=req.request_id,
            asset_symbol=req.asset_symbol,
            amount=req.amount,
            destination_address=req.destination_address,
            is_address_whitelisted=True,
            is_cooloff_elapsed=True,
            remaining_cooloff_seconds=0.0,
            is_withdrawal_approved=True,
            status="WITHDRAWAL_APPROVED",
            audit_notes=notes
        )
