"""
blue-green-deployment-for-live-strategy-updates: Zero-downtime deployment pattern
for live trading systems with atomic cutover and instant rollback.
"""
from dataclasses import dataclass, field
import logging
import time
from enum import Enum
from typing import Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class SlotColor(Enum):
    BLUE = "BLUE"
    GREEN = "GREEN"


class SlotState(Enum):
    IDLE = "IDLE"
    DEPLOYING = "DEPLOYING"
    READY = "READY"
    LIVE = "LIVE"
    DRAINING = "DRAINING"
    FAILED = "FAILED"


@dataclass
class DeploymentSlot:
    color: SlotColor
    state: SlotState = SlotState.IDLE
    version: str = ""
    health_ok: bool = False
    deploy_time: float = 0.0


@dataclass
class CutoverResult:
    success: bool
    active_slot: SlotColor
    previous_slot: SlotColor
    message: str


class BlueGreenDeployer:
    """
    Manages blue-green deployment for live trading strategy updates,
    ensuring zero-gap market coverage and instant rollback capability.
    """

    def __init__(
        self,
        health_check_fn: Optional[Callable[[SlotColor], bool]] = None,
        stabilization_seconds: float = 30.0,
    ):
        self.slots: Dict[SlotColor, DeploymentSlot] = {
            SlotColor.BLUE: DeploymentSlot(color=SlotColor.BLUE),
            SlotColor.GREEN: DeploymentSlot(color=SlotColor.GREEN),
        }
        self.active_slot: SlotColor = SlotColor.BLUE
        self.health_check_fn = health_check_fn or (lambda _: True)
        self.stabilization_seconds = stabilization_seconds
        self.deployment_history: List[Dict] = []

    def get_active_slot(self) -> DeploymentSlot:
        return self.slots[self.active_slot]

    def get_inactive_slot(self) -> DeploymentSlot:
        inactive = SlotColor.GREEN if self.active_slot == SlotColor.BLUE else SlotColor.BLUE
        return self.slots[inactive]

    def deploy_to_inactive(self, version: str) -> DeploymentSlot:
        """Deploy new version to the inactive slot."""
        slot = self.get_inactive_slot()
        slot.state = SlotState.DEPLOYING
        slot.version = version
        slot.deploy_time = time.time()

        logger.info(f"Deploying version '{version}' to {slot.color.value} slot...")

        # Simulate deployment (in production, this would start the new process)
        slot.state = SlotState.READY
        slot.health_ok = self.health_check_fn(slot.color)

        if not slot.health_ok:
            slot.state = SlotState.FAILED
            logger.error(f"Health check FAILED for {slot.color.value} slot (version '{version}').")
            return slot

        logger.info(f"{slot.color.value} slot is READY with version '{version}'.")
        return slot

    def cutover(self) -> CutoverResult:
        """Atomically switch traffic from active to inactive slot."""
        inactive = self.get_inactive_slot()

        if inactive.state != SlotState.READY or not inactive.health_ok:
            return CutoverResult(
                success=False,
                active_slot=self.active_slot,
                previous_slot=self.active_slot,
                message=f"Cannot cutover: {inactive.color.value} slot is not ready "
                        f"(state={inactive.state.value}, health={inactive.health_ok}).",
            )

        # Drain the current active slot
        previous = self.active_slot
        active_slot = self.slots[previous]
        active_slot.state = SlotState.DRAINING
        logger.info(f"Draining {previous.value} slot (setting to read-only)...")

        # Atomic switchover
        self.active_slot = inactive.color
        inactive.state = SlotState.LIVE
        active_slot.state = SlotState.IDLE

        self.deployment_history.append({
            "from": previous.value,
            "to": inactive.color.value,
            "version": inactive.version,
            "timestamp": time.time(),
            "action": "CUTOVER",
        })

        msg = (
            f"CUTOVER COMPLETE: {previous.value} -> {inactive.color.value} "
            f"(version '{inactive.version}'). Zero-gap deployment successful."
        )
        logger.info(msg)
        return CutoverResult(
            success=True,
            active_slot=inactive.color,
            previous_slot=previous,
            message=msg,
        )

    def rollback(self) -> CutoverResult:
        """Instantly rollback to the previous slot."""
        inactive = self.get_inactive_slot()

        # In rollback, we switch back regardless of state
        previous = self.active_slot
        self.active_slot = inactive.color
        inactive.state = SlotState.LIVE
        self.slots[previous].state = SlotState.FAILED

        self.deployment_history.append({
            "from": previous.value,
            "to": inactive.color.value,
            "version": inactive.version,
            "timestamp": time.time(),
            "action": "ROLLBACK",
        })

        msg = (
            f"ROLLBACK EXECUTED: {previous.value} -> {inactive.color.value} "
            f"(version '{inactive.version}'). Previous version restored."
        )
        logger.warning(msg)
        return CutoverResult(
            success=True,
            active_slot=inactive.color,
            previous_slot=previous,
            message=msg,
        )
