import time
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Key Lifecycle States: 'ACTIVE', 'NEEDS_ROTATION', 'DEPRECATED_GRACE_PERIOD', 'REVOKED_SHREDDED'

@dataclass
class HotWalletKeyMetadata:
    key_id: str                         # e.g. 'HOT_KEY_ETH_001'
    created_timestamp_epoch: float      # Creation timestamp
    last_used_timestamp_epoch: float   # Last transaction timestamp
    total_signatures_count: int         # Total txs signed by this key
    total_volume_usd_signed: float      # Total USD volume signed
    is_compromised: bool                # Emergency compromise flag
    current_state: str = "ACTIVE"       # 'ACTIVE', 'NEEDS_ROTATION', 'DEPRECATED_GRACE_PERIOD', 'REVOKED_SHREDDED'

@dataclass
class KeyRotationReport:
    key_id: str
    key_age_days: float
    total_signatures_count: int
    total_volume_usd_signed: float
    is_rotation_required: bool
    new_key_state: str
    replacement_key_id: Optional[str]
    status: str                         # 'KEY_HEALTHY_ACTIVE', 'ROTATION_INITIATED_AGE_EXPIRED', 'ROTATION_INITIATED_USAGE_EXPIRED', 'EMERGENCY_REVOKED_COMPROMISED'
    audit_notes: str

class HotWalletKeyRotationEngine:
    """
    Cryptographic key lifecycle management engine enforcing a mandatory 90-day key rotation policy,
    signature/volume triggers, zero-downtime dual-key grace periods, and secure key shredding for trading bot hot wallets.
    """
    def __init__(
        self,
        max_key_age_days: float = 90.0,
        max_signatures_limit: int = 100_000,
        max_volume_usd_limit: float = 10_000_000.0,
        grace_period_hours: float = 24.0
    ):
        self.max_key_age_days = max_key_age_days
        self.max_signatures_limit = max_signatures_limit
        self.max_volume_usd_limit = max_volume_usd_limit
        self.grace_period_hours = grace_period_hours

    def audit_and_rotate_key(
        self,
        key_meta: HotWalletKeyMetadata,
        current_time_epoch: Optional[float] = None
    ) -> KeyRotationReport:
        """
        Audits key age, signature counts, USD volume, and compromise flags, executing rotation state transitions.
        """
        now = current_time_epoch if current_time_epoch is not None else time.time()
        age_seconds = max(0.0, now - key_meta.created_timestamp_epoch)
        age_days = round(age_seconds / 86400.0, 2)

        # 1. Audit Emergency Compromise Trigger
        if key_meta.is_compromised:
            notes = f"EMERGENCY REVOCATION [{key_meta.key_id}]: Key marked as COMPROMISED! Immediate shredding initiated."
            logger.critical(notes)
            key_meta.current_state = "REVOKED_SHREDDED"
            return KeyRotationReport(
                key_id=key_meta.key_id, key_age_days=age_days,
                total_signatures_count=key_meta.total_signatures_count,
                total_volume_usd_signed=key_meta.total_volume_usd_signed,
                is_rotation_required=True, new_key_state="REVOKED_SHREDDED",
                replacement_key_id=f"{key_meta.key_id}_EMERGENCY_REPLACEMENT",
                status="EMERGENCY_REVOKED_COMPROMISED", audit_notes=notes
            )

        # 2. Audit Rotation Policy Criteria
        is_age_expired = (age_days >= self.max_key_age_days)
        is_usage_expired = (key_meta.total_signatures_count >= self.max_signatures_limit)
        is_volume_expired = (key_meta.total_volume_usd_signed >= self.max_volume_usd_limit)

        is_rotation_req = is_age_expired or is_usage_expired or is_volume_expired

        if is_rotation_req:
            # Transition to Grace Period for Zero-Downtime Swap
            key_meta.current_state = "DEPRECATED_GRACE_PERIOD"
            repl_id = f"{key_meta.key_id}_V2"

            if is_age_expired:
                reason = f"Age ({age_days:.1f} days >= {self.max_key_age_days:.1f} days limit)"
                status = "ROTATION_INITIATED_AGE_EXPIRED"
            elif is_usage_expired:
                reason = f"Signatures ({key_meta.total_signatures_count:,} >= {self.max_signatures_limit:,} limit)"
                status = "ROTATION_INITIATED_USAGE_EXPIRED"
            else:
                reason = f"Volume (${key_meta.total_volume_usd_signed:,.0f} >= ${self.max_volume_usd_limit:,.0f} limit)"
                status = "ROTATION_INITIATED_VOLUME_EXPIRED"

            notes = (
                f"KEY ROTATION INITIATED [{key_meta.key_id}]: {reason}. "
                f"Transitioned to 'DEPRECATED_GRACE_PERIOD' ({self.grace_period_hours:.0f}h). "
                f"Issued replacement key '{repl_id}'."
            )
            logger.warning(notes)

            return KeyRotationReport(
                key_id=key_meta.key_id, key_age_days=age_days,
                total_signatures_count=key_meta.total_signatures_count,
                total_volume_usd_signed=key_meta.total_volume_usd_signed,
                is_rotation_required=True, new_key_state="DEPRECATED_GRACE_PERIOD",
                replacement_key_id=repl_id, status=status, audit_notes=notes
            )

        notes = (
            f"KEY HEALTHY [{key_meta.key_id}]: Age = {age_days:.1f} days, "
            f"Txs = {key_meta.total_signatures_count:,}, Volume = ${key_meta.total_volume_usd_signed:,.0f} USD. "
            f"State = ACTIVE."
        )
        logger.info(notes)

        return KeyRotationReport(
            key_id=key_meta.key_id,
            key_age_days=age_days,
            total_signatures_count=key_meta.total_signatures_count,
            total_volume_usd_signed=key_meta.total_volume_usd_signed,
            is_rotation_required=False,
            new_key_state="ACTIVE",
            replacement_key_id=None,
            status="KEY_HEALTHY_ACTIVE",
            audit_notes=notes
        )
