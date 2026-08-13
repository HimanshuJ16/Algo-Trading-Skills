"""
blue-green-deployment-for-live-strategy-updates:
Manages the two-slot (BLUE / GREEN) routing pointer for zero-downtime updates of
a live trading strategy, with state-synchronised cutover and a guarded rollback.

The deployer owns exactly one thing: **which slot is authorised to route orders**.
It does not start processes, move positions, or cancel orders. The caller supplies
that behaviour through two callbacks:

  - ``health_check_fn(color) -> bool``      : is the instance in ``color`` fit to trade?
  - ``state_sync_fn(source, target) -> bool``: copy/reconcile positions, open orders and
                                               working alpha state from ``source`` to ``target``.

Four safeguards distinguish this from a naive pointer swap, and each one exists
because the naive version loses money in live trading:

  1. **Rollback re-synchronises state backwards.** Once GREEN has traded, BLUE's
     book is stale by however long GREEN was live. Swapping the pointer back
     without reconciling hands the market to a strategy that believes it holds a
     position it no longer has. ``rollback()`` therefore runs ``state_sync_fn``
     from the live slot to the rollback target and refuses the rollback if that
     reconciliation fails.
  2. **Rollback validates the target.** A slot that was never deployed, that
     failed its health check, or that has been decommissioned is not a rollback
     target. Routing live order flow to it is worse than not rolling back, so
     ``rollback()`` refuses and tells the operator to use the kill switch.
  3. **Health is re-checked at the instant of cutover.** ``READY`` may have been
     stamped minutes ago; ``cutover()`` re-runs ``health_check_fn`` inside the
     same critical section as the swap.
  4. **The standby slot is released explicitly.** ``deploy_to_inactive()`` will
     not overwrite a ``DRAINING`` slot, because that slot is the last-known-good
     rollback target. ``decommission_standby()`` is the deliberate act of giving
     that capability up.

Limitations the caller must handle:

  - The deployer cannot interrupt ``health_check_fn`` or ``state_sync_fn``.
    ``cutover()`` and ``rollback()`` hold the internal lock while those callbacks
    run so the swap is atomic, which means a hung callback blocks every other
    deployer operation. Bound them with your own timeout.
  - Enforcement of "only the LIVE slot may route" belongs to the execution layer:
    it must consult the deployer before every submission and reject orders from a
    slot that is not ``SlotState.LIVE``.
  - Rollback restores *routing*, not *executions*. Fills GREEN already booked at
    the venue are real; reconciliation is ``state_sync_fn``'s job.

See ``references/standards.md`` for the MiFID II RTS 6 authorisation and
change-record obligations that ``authorised_by`` and ``deployment_history``
are designed to satisfy.
"""
import dataclasses
import logging
import math
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class SlotColor(Enum):
    BLUE = "BLUE"
    GREEN = "GREEN"


class SlotState(Enum):
    """
    Lifecycle of one deployment slot.

    - ``IDLE``      : empty; no strategy instance of record. Deployable.
    - ``DEPLOYING`` : version staged, warming up, health not yet known.
    - ``READY``     : warmed and health-checked; eligible for cutover.
    - ``LIVE``      : currently authorised to route orders.
    - ``DRAINING``  : previously live, holding last-known-good state as the
                      rollback target. Not deployable until decommissioned.
    - ``FAILED``    : health check, state sync or rollback rejected this slot.
                      Deployable (a new version may replace it) but never a
                      rollback target.
    """
    IDLE = "IDLE"
    DEPLOYING = "DEPLOYING"
    READY = "READY"
    LIVE = "LIVE"
    DRAINING = "DRAINING"
    FAILED = "FAILED"


# A slot may only receive a new version from these states. DRAINING is
# deliberately excluded: it holds the rollback target and must be released with
# decommission_standby() first. READY is excluded so an un-cut-over deployment is
# not silently replaced.
DEPLOYABLE_STATES = frozenset({SlotState.IDLE, SlotState.FAILED})

# A slot may only receive live routing back from these states. IDLE (never
# deployed / decommissioned) and FAILED (rejected) are not viable targets.
ROLLBACK_ELIGIBLE_STATES = frozenset({SlotState.DRAINING, SlotState.READY})


@dataclass
class DeploymentSlot:
    color: SlotColor
    state: SlotState = SlotState.IDLE
    version: str = ""
    health_ok: bool = False
    deploy_time: float = 0.0


@dataclass
class CutoverResult:
    """
    Outcome of a routing change.

    On failure ``active_slot`` and ``previous_slot`` are both the unchanged live
    slot, and ``message`` states why the change was refused.
    """
    success: bool
    active_slot: SlotColor
    previous_slot: SlotColor
    message: str


class DeployError(Exception):
    pass


class BlueGreenDeployer:
    """
    Manages blue-green routing for live quantitative trading strategy updates:
    staged deployment to the standby slot, state-synchronised atomic cutover,
    and a guarded rollback that refuses to route to an unusable slot.
    """

    def __init__(
        self,
        health_check_fn: Optional[Callable[[SlotColor], bool]] = None,
        warmup_seconds: float = 0.5,
        state_sync_fn: Optional[Callable[[SlotColor, SlotColor], bool]] = None,
        initial_version: str = "",
    ):
        """
        Args:
            health_check_fn: Returns True if the instance in the given slot is fit
                to trade. Called after warmup and again at cutover. An exception
                is treated as a failed check.
            warmup_seconds: Time allowed for JIT warmup / risk-model compilation
                before the health check runs. Must be finite and >= 0.
            state_sync_fn: Reconciles positions, open orders and working state from
                the source slot to the target slot. Called on cutover (live ->
                standby) and on rollback (live -> rollback target). An exception is
                treated as a failed sync.
            initial_version: Version already running in the initially-live BLUE
                slot, recorded in ``deployment_history`` so a rollback to it is
                identifiable in an audit trail.
        """
        if not isinstance(warmup_seconds, (int, float)) or isinstance(warmup_seconds, bool):
            raise ValueError("warmup_seconds must be a number")
        if not math.isfinite(warmup_seconds) or warmup_seconds < 0:
            raise ValueError(f"warmup_seconds must be finite and >= 0, got {warmup_seconds!r}")

        self._lock = threading.RLock()
        self.slots: Dict[SlotColor, DeploymentSlot] = {
            SlotColor.BLUE: DeploymentSlot(
                color=SlotColor.BLUE,
                state=SlotState.LIVE,
                version=initial_version,
                health_ok=True,
            ),
            SlotColor.GREEN: DeploymentSlot(color=SlotColor.GREEN, state=SlotState.IDLE),
        }
        self.active_slot: SlotColor = SlotColor.BLUE
        self.health_check_fn = health_check_fn or (lambda _: True)
        self.state_sync_fn = state_sync_fn or (lambda _, __: True)
        self.warmup_seconds = warmup_seconds
        self.deployment_history: List[Dict[str, object]] = []

    # ------------------------------------------------------------------
    # Inspection
    # ------------------------------------------------------------------

    def get_active_slot(self) -> DeploymentSlot:
        """Snapshot of the slot currently authorised to route orders."""
        with self._lock:
            return dataclasses.replace(self.slots[self.active_slot])

    def get_inactive_slot(self) -> DeploymentSlot:
        """
        Snapshot of the standby slot.

        A copy is returned deliberately: handing out the live object would let
        callers read a half-written slot, or mutate deployer state outside the
        lock, defeating the atomicity this class exists to provide.
        """
        with self._lock:
            return dataclasses.replace(self.slots[self._inactive_color()])

    def _inactive_color(self) -> SlotColor:
        with self._lock:
            return SlotColor.GREEN if self.active_slot == SlotColor.BLUE else SlotColor.BLUE

    def _record(
        self,
        action: str,
        from_color: SlotColor,
        to_color: SlotColor,
        version: str,
        success: bool,
        authorised_by: str,
        detail: str,
    ) -> None:
        """
        Append one immutable change record.

        MiFID II RTS 6 Art. 5(7) requires records of material changes to
        algorithmic trading software identifying when the change was made, who
        made it, who approved it and its nature. Refused operations are recorded
        too: an attempted rollback that was blocked is exactly the event a
        post-incident review needs to see.
        """
        with self._lock:
            self.deployment_history.append({
                "action": action,
                "from": from_color.value,
                "to": to_color.value,
                "version": version,
                "timestamp": time.time(),
                "success": success,
                "authorised_by": authorised_by,
                "detail": detail,
            })

    # ------------------------------------------------------------------
    # Deployment
    # ------------------------------------------------------------------

    def deploy_to_inactive(self, version: str, authorised_by: str = "") -> DeploymentSlot:
        """
        Stage a new strategy version on the standby slot and health-check it.

        Returns a snapshot of the slot: ``READY`` if the health check passed,
        ``FAILED`` if it did not. A bad *state* (deploying over the rollback
        target) raises ``DeployError``; a bad *health result* does not, because
        a failed health check is a normal, expected outcome of a deployment.

        Args:
            version: Non-empty version identifier of the build being staged.
            authorised_by: Person authorising the deployment. RTS 6 Art. 5(2)
                requires a person designated by senior management to authorise
                deployment or substantial update of a trading algorithm; EU
                investment firms should always populate this.
        """
        if not isinstance(version, str) or not version.strip():
            raise ValueError("version must be a non-empty string")
        if not authorised_by:
            logger.warning(
                f"Deployment of '{version}' has no recorded authoriser; "
                "MiFID II RTS 6 Art. 5(2) requires designated sign-off for EU investment firms."
            )

        with self._lock:
            color = self._inactive_color()
            slot = self.slots[color]
            if slot.state not in DEPLOYABLE_STATES:
                raise DeployError(
                    f"Inactive slot {color.value} is in state {slot.state.value} and cannot receive "
                    f"version '{version}'. A DRAINING slot is the last-known-good rollback target and "
                    f"a READY slot is an un-cut-over deployment; call decommission_standby() to "
                    f"release it deliberately."
                )

            live_color = self.active_slot
            slot.state = SlotState.DEPLOYING
            slot.version = version
            slot.health_ok = False
            slot.deploy_time = time.time()
            logger.info(f"Deploying quant version '{version}' to {color.value} slot.")

        # Warmup and the health check run OUTSIDE the lock: warmup is unbounded
        # (JIT, memory mapping, risk-rule compilation) and holding the lock across
        # it would block an emergency rollback of the currently live slot.
        if self.warmup_seconds:
            time.sleep(self.warmup_seconds)

        try:
            healthy = bool(self.health_check_fn(color))
        except Exception:
            logger.exception(f"Health check raised for {color.value} (ver '{version}'); treating as FAILED.")
            healthy = False

        with self._lock:
            slot = self.slots[color]
            # Routing may have moved while the lock was released. Writing READY or
            # FAILED onto a slot that has since gone LIVE would mislabel the
            # running strategy and could disarm the rollback guard.
            if (slot.state is not SlotState.DEPLOYING
                    or slot.version != version
                    or color == self.active_slot):
                logger.warning(
                    f"Deployment of '{version}' to {color.value} abandoned: slot changed during warmup "
                    f"(state={slot.state.value}, version='{slot.version}', active={self.active_slot.value})."
                )
                self._record("DEPLOY", live_color, color, version, False, authorised_by,
                             "abandoned: slot changed during warmup")
                return dataclasses.replace(slot)

            slot.health_ok = healthy
            slot.state = SlotState.READY if healthy else SlotState.FAILED
            if healthy:
                logger.info(f"{color.value} slot is READY with verified version '{version}'.")
            else:
                logger.error(f"Health/Risk check FAILED for {color.value} (ver '{version}').")
            self._record("DEPLOY", live_color, color, version, healthy, authorised_by,
                         "health check passed" if healthy else "health check failed")
            return dataclasses.replace(slot)

    def decommission_standby(self) -> DeploymentSlot:
        """
        Release the standby slot back to ``IDLE``, freeing it for a new version.

        This is the deliberate end of the drain window. After it returns there is
        no rollback target: ``rollback()`` will refuse until a new version has
        been deployed and cut over. Call it only once the live version has proven
        stable over a full observation window.
        """
        with self._lock:
            color = self._inactive_color()
            slot = self.slots[color]
            if slot.state is SlotState.LIVE:
                raise DeployError(f"Refusing to decommission {color.value}: slot is LIVE.")

            previous_state = slot.state
            released_version = slot.version
            slot.state = SlotState.IDLE
            slot.version = ""
            slot.health_ok = False
            slot.deploy_time = 0.0

            if previous_state is SlotState.DRAINING:
                logger.warning(
                    f"Decommissioned {color.value} (was DRAINING, ver '{released_version}'): "
                    "rollback capability released, the previous version can no longer be restored."
                )
            else:
                logger.info(
                    f"Decommissioned {color.value} (was {previous_state.value}, ver '{released_version}')."
                )
            self._record("DECOMMISSION", self.active_slot, color, released_version, True, "",
                         f"released from {previous_state.value}")
            return dataclasses.replace(slot)

    # ------------------------------------------------------------------
    # Routing changes
    # ------------------------------------------------------------------

    def cutover(self, authorised_by: str = "") -> CutoverResult:
        """
        Transfer live order routing from the active slot to the standby slot.

        The health re-check, the state synchronisation and the pointer swap all
        happen in one critical section, so no other deployer operation can
        interleave and no window exists in which both slots believe they are live.

        Args:
            authorised_by: Person authorising the cutover (RTS 6 Art. 5(2), 11(1)).
        """
        with self._lock:
            color = self._inactive_color()
            inactive = self.slots[color]
            active = self.slots[self.active_slot]

            if inactive.state is not SlotState.READY:
                msg = (f"Cutover aborted: {color.value} slot is {inactive.state.value}, not READY.")
                logger.error(msg)
                self._record("CUTOVER", self.active_slot, color, inactive.version, False, authorised_by, msg)
                return CutoverResult(False, self.active_slot, self.active_slot, msg)

            # READY was stamped at deploy time and may be arbitrarily stale — the
            # instance can have died, lost its market data feed or drifted out of
            # risk limits since. Re-verify at the moment routing actually moves.
            try:
                healthy = bool(self.health_check_fn(color))
            except Exception:
                logger.exception(f"Pre-cutover health check raised for {color.value}; treating as FAILED.")
                healthy = False
            inactive.health_ok = healthy
            if not healthy:
                inactive.state = SlotState.FAILED
                msg = (f"Cutover aborted: {color.value} failed its pre-cutover health re-check "
                       f"(ver '{inactive.version}').")
                logger.error(msg)
                self._record("CUTOVER", self.active_slot, color, inactive.version, False, authorised_by, msg)
                return CutoverResult(False, self.active_slot, self.active_slot, msg)

            logger.info("Synchronizing portfolio state and live order books...")
            try:
                sync_ok = bool(self.state_sync_fn(active.color, color))
            except Exception:
                logger.exception(f"State sync {active.color.value} -> {color.value} raised; treating as FAILED.")
                sync_ok = False
            if not sync_ok:
                # The sync may have partially applied, so the slot is quarantined
                # rather than left READY for a retry to cut over onto a torn book.
                inactive.state = SlotState.FAILED
                msg = "Cutover aborted: State synchronization failed."
                logger.error(msg)
                self._record("CUTOVER", self.active_slot, color, inactive.version, False, authorised_by, msg)
                return CutoverResult(False, self.active_slot, self.active_slot, msg)

            previous = self.active_slot
            self.active_slot = color
            inactive.state = SlotState.LIVE
            active.state = SlotState.DRAINING

            msg = f"ATOMIC CUTOVER COMPLETE: {previous.value} -> {color.value} (ver '{inactive.version}')."
            logger.info(msg)
            self._record("CUTOVER", previous, color, inactive.version, True, authorised_by, msg)
            return CutoverResult(True, color, previous, msg)

    def rollback(self, authorised_by: str = "", force: bool = False) -> CutoverResult:
        """
        Return live order routing to the standby slot after a bad deployment.

        Rollback is *not* a bare pointer swap. The live slot has been trading, so
        the rollback target's positions and open orders are stale by the length of
        the live window; ``state_sync_fn`` is run from the live slot back to the
        target before routing moves. If the target is not a viable slot, or that
        reconciliation fails, the rollback is refused — routing live flow to a
        strategy with a wrong book causes more damage than staying put.

        When rollback is refused and the position must be flattened *now*, the
        correct primitive is the kill switch (cancel all working orders and stop
        submission — MiFID II RTS 6 Art. 12), not a forced rollback.

        Args:
            authorised_by: Person authorising the rollback.
            force: Override both guards. The rollback proceeds with an
                unreconciled book and is logged at CRITICAL. Use only when a
                human has confirmed the target's state by other means.
        """
        with self._lock:
            target_color = self._inactive_color()
            target = self.slots[target_color]
            live_color = self.active_slot

            if target.state not in ROLLBACK_ELIGIBLE_STATES:
                msg = (f"Rollback refused: {target_color.value} is {target.state.value} and is not a viable "
                       f"rollback target (never deployed, already failed, or decommissioned). "
                       f"Use the kill switch to stop trading, then redeploy.")
                if force:
                    logger.critical(f"FORCED rollback onto non-viable slot. {msg}")
                else:
                    logger.critical(msg)
                    self._record("ROLLBACK", live_color, target_color, target.version, False, authorised_by, msg)
                    return CutoverResult(False, live_color, live_color, msg)

            try:
                sync_ok = bool(self.state_sync_fn(live_color, target_color))
            except Exception:
                logger.exception(
                    f"Rollback state sync {live_color.value} -> {target_color.value} raised; treating as FAILED."
                )
                sync_ok = False

            if not sync_ok:
                msg = (f"Rollback refused: could not reconcile state from {live_color.value} back to "
                       f"{target_color.value}. The rollback target's positions and open orders are stale. "
                       f"Use the kill switch, reconcile manually, then rollback with force=True.")
                if not force:
                    logger.critical(msg)
                    self._record("ROLLBACK", live_color, target_color, target.version, False, authorised_by, msg)
                    return CutoverResult(False, live_color, live_color, msg)
                logger.critical(
                    f"FORCED rollback to {target_color.value} with UNRECONCILED state — "
                    f"the restored strategy's book may not match the broker's."
                )

            self.active_slot = target_color
            target.state = SlotState.LIVE
            self.slots[live_color].state = SlotState.FAILED

            msg = (f"EMERGENCY ROLLBACK EXECUTED: Restored to {target_color.value} slot "
                   f"(ver '{target.version}'){' [FORCED]' if force and not sync_ok else ''}.")
            logger.warning(msg)
            self._record("ROLLBACK", live_color, target_color, target.version, True, authorised_by, msg)
            return CutoverResult(True, target_color, live_color, msg)
