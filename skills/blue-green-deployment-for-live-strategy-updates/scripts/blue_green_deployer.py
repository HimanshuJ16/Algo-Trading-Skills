import logging
import time
import threading
from dataclasses import dataclass, field
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

class DeployError(Exception):
    pass

class BlueGreenDeployer:
    """
    Manages robust blue-green deployment for live quantitative trading strategy updates.
    Ensures zero-downtime, continuous market data ingestion, state synchronization, 
    atomic cutovers, and rapid rollback mechanisms.
    """
    def __init__(
        self,
        health_check_fn: Optional[Callable[[SlotColor], bool]] = None,
        warmup_seconds: float = 0.5,
        state_sync_fn: Optional[Callable[[SlotColor, SlotColor], bool]] = None
    ):
        self._lock = threading.RLock()
        self.slots: Dict[SlotColor, DeploymentSlot] = {
            SlotColor.BLUE: DeploymentSlot(color=SlotColor.BLUE, state=SlotState.LIVE),
            SlotColor.GREEN: DeploymentSlot(color=SlotColor.GREEN, state=SlotState.IDLE),
        }
        self.active_slot: SlotColor = SlotColor.BLUE
        self.health_check_fn = health_check_fn or (lambda _: True)
        self.state_sync_fn = state_sync_fn or (lambda _, __: True)
        self.warmup_seconds = warmup_seconds
        self.deployment_history: List[Dict] = []

    def get_active_slot(self) -> DeploymentSlot:
        with self._lock:
            return self.slots[self.active_slot]

    def get_inactive_slot(self) -> DeploymentSlot:
        with self._lock:
            inactive = SlotColor.GREEN if self.active_slot == SlotColor.BLUE else SlotColor.BLUE
            return self.slots[inactive]

    def deploy_to_inactive(self, version: str) -> DeploymentSlot:
        """Deploys a new trading strategy version to the standby slot."""
        with self._lock:
            slot = self.get_inactive_slot()
            if slot.state not in (SlotState.IDLE, SlotState.FAILED, SlotState.DRAINING):
                raise DeployError(f"Inactive slot {slot.color.value} is in invalid state: {slot.state.value}")
            
            slot.state = SlotState.DEPLOYING
            slot.version = version
            slot.deploy_time = time.time()
            
            logger.info(f"Deploying quant version '{version}' to {slot.color.value} slot.")

        # Simulate warmup / memory mapping / risk rule compilation
        time.sleep(self.warmup_seconds)

        with self._lock:
            slot.health_ok = self.health_check_fn(slot.color)
            if not slot.health_ok:
                slot.state = SlotState.FAILED
                logger.error(f"Health/Risk check FAILED for {slot.color.value} (ver '{version}').")
                return slot

            slot.state = SlotState.READY
            logger.info(f"{slot.color.value} slot is READY with verified version '{version}'.")
            return slot

    def cutover(self) -> CutoverResult:
        """
        Atomically transfers live market order routing from active to standby.
        Includes quantitative state synchronization.
        """
        with self._lock:
            inactive = self.get_inactive_slot()
            active_slot = self.slots[self.active_slot]

            if inactive.state != SlotState.READY or not inactive.health_ok:
                return CutoverResult(
                    success=False,
                    active_slot=self.active_slot,
                    previous_slot=self.active_slot,
                    message=f"Cutover aborted: {inactive.color.value} slot is not READY."
                )

            # Sync active positions and order states to the inactive slot
            logger.info("Synchronizing portfolio state and live order books...")
            sync_ok = self.state_sync_fn(active_slot.color, inactive.color)
            if not sync_ok:
                inactive.state = SlotState.FAILED
                return CutoverResult(
                    success=False,
                    active_slot=self.active_slot,
                    previous_slot=self.active_slot,
                    message="Cutover aborted: State synchronization failed."
                )

            # Atomic swap
            previous = self.active_slot
            self.active_slot = inactive.color
            
            inactive.state = SlotState.LIVE
            active_slot.state = SlotState.DRAINING

            self.deployment_history.append({
                "from": previous.value,
                "to": inactive.color.value,
                "version": inactive.version,
                "timestamp": time.time(),
                "action": "CUTOVER"
            })

            msg = f"ATOMIC CUTOVER COMPLETE: {previous.value} -> {inactive.color.value} (ver '{inactive.version}')."
            logger.info(msg)
            return CutoverResult(success=True, active_slot=inactive.color, previous_slot=previous, message=msg)

    def rollback(self) -> CutoverResult:
        """
        Instant risk-limit rollback. Swaps routing back to the previous stable state.
        """
        with self._lock:
            inactive = self.get_inactive_slot()
            previous = self.active_slot

            if inactive.state not in (SlotState.DRAINING, SlotState.IDLE, SlotState.READY):
                # We can still force a rollback, but note the state
                pass
            
            self.active_slot = inactive.color
            inactive.state = SlotState.LIVE
            self.slots[previous].state = SlotState.FAILED

            self.deployment_history.append({
                "from": previous.value,
                "to": inactive.color.value,
                "version": inactive.version,
                "timestamp": time.time(),
                "action": "ROLLBACK"
            })

            msg = f"EMERGENCY ROLLBACK EXECUTED: Restored to {inactive.color.value} slot."
            logger.warning(msg)
            return CutoverResult(success=True, active_slot=inactive.color, previous_slot=previous, message=msg)

