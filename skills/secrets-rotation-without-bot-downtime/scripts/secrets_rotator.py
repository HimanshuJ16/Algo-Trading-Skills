"""
secrets-rotation-without-bot-downtime: Hot-swap credential rotation for live trading
bots without restart or trading gap.
"""
from dataclasses import dataclass, field
import logging
import time
from enum import Enum
from typing import Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class RotationState(Enum):
    IDLE = "IDLE"
    VALIDATING_NEW = "VALIDATING_NEW"
    SWAPPED = "SWAPPED"
    REVOKED_OLD = "REVOKED_OLD"
    FAILED_ROLLBACK = "FAILED_ROLLBACK"


@dataclass
class Credential:
    key_id: str
    secret: str
    created_at: float = 0.0
    is_valid: bool = True


@dataclass
class RotationResult:
    success: bool
    state: RotationState
    active_key_id: str
    message: str


class SecretsRotator:
    """
    Manages zero-downtime credential rotation for live trading bots.
    Validates new credentials before switchover and supports instant fallback.
    """

    def __init__(
        self,
        validate_fn: Optional[Callable[[Credential], bool]] = None,
    ):
        self.active_credential: Optional[Credential] = None
        self.previous_credential: Optional[Credential] = None
        self.validate_fn = validate_fn or (lambda cred: True)
        self.rotation_history: List[Dict] = []
        self.state = RotationState.IDLE

    def set_initial_credential(self, key_id: str, secret: str) -> None:
        """Set the initial active credential."""
        self.active_credential = Credential(
            key_id=key_id, secret=secret, created_at=time.time()
        )
        logger.info(f"Initial credential set: {key_id}")

    def rotate(self, new_key_id: str, new_secret: str) -> RotationResult:
        """
        Perform a zero-downtime credential rotation:
        1. Validate new credential.
        2. Hot-swap to new credential.
        3. Keep old credential as fallback.
        """
        if self.active_credential is None:
            return RotationResult(
                success=False,
                state=RotationState.IDLE,
                active_key_id="",
                message="No active credential to rotate from.",
            )

        new_cred = Credential(
            key_id=new_key_id, secret=new_secret, created_at=time.time()
        )

        # Step 1: Validate new credentials
        self.state = RotationState.VALIDATING_NEW
        logger.info(f"Validating new credential '{new_key_id}'...")

        if not self.validate_fn(new_cred):
            self.state = RotationState.FAILED_ROLLBACK
            msg = (
                f"ROTATION FAILED: New credential '{new_key_id}' failed validation. "
                f"Keeping active credential '{self.active_credential.key_id}'."
            )
            logger.warning(msg)
            self.rotation_history.append({
                "action": "ROTATION_FAILED",
                "old_key": self.active_credential.key_id,
                "new_key": new_key_id,
                "timestamp": time.time(),
            })
            return RotationResult(
                success=False,
                state=self.state,
                active_key_id=self.active_credential.key_id,
                message=msg,
            )

        # Step 2: Hot-swap
        self.previous_credential = self.active_credential
        self.active_credential = new_cred
        self.state = RotationState.SWAPPED

        self.rotation_history.append({
            "action": "ROTATED",
            "old_key": self.previous_credential.key_id,
            "new_key": new_key_id,
            "timestamp": time.time(),
        })

        msg = (
            f"ROTATION SUCCESS: Swapped from '{self.previous_credential.key_id}' "
            f"to '{new_key_id}'. Old credential retained as fallback."
        )
        logger.info(msg)
        return RotationResult(
            success=True,
            state=self.state,
            active_key_id=new_key_id,
            message=msg,
        )

    def revoke_previous(self) -> RotationResult:
        """Revoke the previous credential after confirming new one works."""
        if self.previous_credential is None:
            return RotationResult(
                success=False,
                state=self.state,
                active_key_id=self.active_credential.key_id if self.active_credential else "",
                message="No previous credential to revoke.",
            )

        old_key = self.previous_credential.key_id
        self.previous_credential.is_valid = False
        self.previous_credential = None
        self.state = RotationState.REVOKED_OLD

        logger.info(f"Previous credential '{old_key}' revoked.")
        return RotationResult(
            success=True,
            state=self.state,
            active_key_id=self.active_credential.key_id,
            message=f"Previous credential '{old_key}' revoked successfully.",
        )

    def fallback_to_previous(self) -> RotationResult:
        """Emergency fallback to previous credential if new one fails in production."""
        if self.previous_credential is None or not self.previous_credential.is_valid:
            return RotationResult(
                success=False,
                state=self.state,
                active_key_id=self.active_credential.key_id if self.active_credential else "",
                message="No valid previous credential available for fallback.",
            )

        failed_key = self.active_credential.key_id if self.active_credential else "unknown"
        self.active_credential = self.previous_credential
        self.previous_credential = None
        self.state = RotationState.FAILED_ROLLBACK

        msg = (
            f"FALLBACK EXECUTED: Reverted from '{failed_key}' to "
            f"'{self.active_credential.key_id}'."
        )
        logger.warning(msg)
        return RotationResult(
            success=True,
            state=self.state,
            active_key_id=self.active_credential.key_id,
            message=msg,
        )
