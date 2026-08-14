import itertools
import threading
import unittest

from blue_green_deployer import (
    BlueGreenDeployer,
    DeployError,
    SlotColor,
    SlotState,
)


class TestDeployment(unittest.TestCase):
    def test_successful_deployment_and_cutover(self):
        deployer = BlueGreenDeployer(warmup_seconds=0.0)
        self.assertEqual(deployer.active_slot, SlotColor.BLUE)
        self.assertEqual(deployer.slots[SlotColor.BLUE].state, SlotState.LIVE)

        slot = deployer.deploy_to_inactive("v2.0", authorised_by="head-of-trading")
        self.assertEqual(slot.color, SlotColor.GREEN)
        self.assertEqual(slot.state, SlotState.READY)
        self.assertTrue(slot.health_ok)

        result = deployer.cutover(authorised_by="head-of-trading")
        self.assertTrue(result.success)
        self.assertEqual(result.active_slot, SlotColor.GREEN)
        self.assertEqual(result.previous_slot, SlotColor.BLUE)
        self.assertEqual(deployer.slots[SlotColor.BLUE].state, SlotState.DRAINING)

    def test_health_check_failure(self):
        deployer = BlueGreenDeployer(health_check_fn=lambda _: False, warmup_seconds=0.0)
        slot = deployer.deploy_to_inactive("v3.0")
        self.assertEqual(slot.state, SlotState.FAILED)
        self.assertFalse(slot.health_ok)

        result = deployer.cutover()
        self.assertFalse(result.success)
        self.assertEqual(result.active_slot, SlotColor.BLUE)

    def test_raising_health_check_fails_slot_without_bricking_pipeline(self):
        """A callback that raises must not strand the slot in DEPLOYING forever."""
        calls = []

        def flaky_health(color):
            calls.append(color)
            if len(calls) == 1:
                raise RuntimeError("risk service unreachable")
            return True

        deployer = BlueGreenDeployer(health_check_fn=flaky_health, warmup_seconds=0.0)
        first = deployer.deploy_to_inactive("v1.1")
        self.assertEqual(first.state, SlotState.FAILED)

        # FAILED is deployable, so the operator can retry without restarting.
        second = deployer.deploy_to_inactive("v1.2")
        self.assertEqual(second.state, SlotState.READY)
        self.assertEqual(second.version, "v1.2")

    def test_deploy_rejects_empty_version(self):
        deployer = BlueGreenDeployer(warmup_seconds=0.0)
        with self.assertRaises(ValueError):
            deployer.deploy_to_inactive("   ")

    def test_invalid_warmup_seconds_rejected(self):
        for bad in (-1.0, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                BlueGreenDeployer(warmup_seconds=bad)

    def test_getters_return_snapshots_not_shared_state(self):
        deployer = BlueGreenDeployer(warmup_seconds=0.0)
        snapshot = deployer.get_inactive_slot()
        snapshot.state = SlotState.LIVE
        snapshot.version = "spoofed"
        self.assertEqual(deployer.slots[SlotColor.GREEN].state, SlotState.IDLE)
        self.assertEqual(deployer.slots[SlotColor.GREEN].version, "")


class TestRoutingAuthority(unittest.TestCase):
    """
    The execution layer gates every order submission on is_authorised_to_route.
    If it ever authorises two slots, or none, live flow is either duplicated or
    dropped.
    """

    def test_exactly_one_slot_is_authorised_before_and_after_cutover(self):
        deployer = BlueGreenDeployer(warmup_seconds=0.0, initial_version="v1.0")
        self.assertTrue(deployer.is_authorised_to_route(SlotColor.BLUE))
        self.assertFalse(deployer.is_authorised_to_route(SlotColor.GREEN))

        deployer.deploy_to_inactive("v2.0", authorised_by="ops")
        # Staging a version must not confer routing authority on its own.
        self.assertFalse(deployer.is_authorised_to_route(SlotColor.GREEN))

        deployer.cutover(authorised_by="ops")
        self.assertTrue(deployer.is_authorised_to_route(SlotColor.GREEN))
        # BLUE is DRAINING: it holds state for rollback but must not send orders.
        self.assertFalse(deployer.is_authorised_to_route(SlotColor.BLUE))

    def test_refused_cutover_leaves_routing_authority_with_the_live_slot(self):
        deployer = BlueGreenDeployer(
            state_sync_fn=lambda a, b: False, warmup_seconds=0.0, initial_version="v1.0"
        )
        deployer.deploy_to_inactive("v2.0", authorised_by="ops")
        self.assertFalse(deployer.cutover(authorised_by="ops").success)

        self.assertTrue(deployer.is_authorised_to_route(SlotColor.BLUE))
        self.assertFalse(deployer.is_authorised_to_route(SlotColor.GREEN))

    def test_rejects_non_slotcolor_argument(self):
        """A truthy string must not be mistaken for an authorised slot."""
        deployer = BlueGreenDeployer(warmup_seconds=0.0, initial_version="v1.0")
        with self.assertRaises(TypeError):
            deployer.is_authorised_to_route("BLUE")


class TestCutover(unittest.TestCase):
    def test_state_sync_failure_blocks_cutover(self):
        deployer = BlueGreenDeployer(state_sync_fn=lambda a, b: False, warmup_seconds=0.0)
        deployer.deploy_to_inactive("v4.0")

        result = deployer.cutover()
        self.assertFalse(result.success)
        self.assertEqual(deployer.active_slot, SlotColor.BLUE)
        self.assertEqual(deployer.slots[SlotColor.GREEN].state, SlotState.FAILED)

    def test_raising_state_sync_blocks_cutover(self):
        def exploding_sync(source, target):
            raise ConnectionError("shared-memory segment unavailable")

        deployer = BlueGreenDeployer(state_sync_fn=exploding_sync, warmup_seconds=0.0)
        deployer.deploy_to_inactive("v4.1")

        result = deployer.cutover()
        self.assertFalse(result.success)
        self.assertEqual(deployer.active_slot, SlotColor.BLUE)
        self.assertEqual(deployer.slots[SlotColor.GREEN].state, SlotState.FAILED)

    def test_cutover_rechecks_health_at_swap_time(self):
        """READY is stamped at deploy time; the instance can die before cutover."""
        healthy = {"value": True}
        deployer = BlueGreenDeployer(
            health_check_fn=lambda _: healthy["value"], warmup_seconds=0.0
        )
        slot = deployer.deploy_to_inactive("v5.0")
        self.assertEqual(slot.state, SlotState.READY)

        healthy["value"] = False  # GREEN loses its market data feed while staged
        result = deployer.cutover()

        self.assertFalse(result.success)
        self.assertEqual(deployer.active_slot, SlotColor.BLUE)
        self.assertEqual(deployer.slots[SlotColor.GREEN].state, SlotState.FAILED)

    def test_cutover_syncs_state_from_live_to_standby(self):
        calls = []
        deployer = BlueGreenDeployer(
            state_sync_fn=lambda a, b: calls.append((a, b)) or True, warmup_seconds=0.0
        )
        deployer.deploy_to_inactive("v6.0")
        deployer.cutover()
        self.assertEqual(calls, [(SlotColor.BLUE, SlotColor.GREEN)])


class TestRollback(unittest.TestCase):
    def test_rollback_restores_previous(self):
        deployer = BlueGreenDeployer(warmup_seconds=0.0)
        deployer.deploy_to_inactive("v2.0")
        deployer.cutover()
        self.assertEqual(deployer.active_slot, SlotColor.GREEN)

        result = deployer.rollback(authorised_by="risk-officer")

        self.assertTrue(result.success)
        self.assertEqual(deployer.active_slot, SlotColor.BLUE)
        self.assertEqual(deployer.slots[SlotColor.GREEN].state, SlotState.FAILED)

    def test_rollback_refused_when_target_never_deployed(self):
        """Regression: a bare pointer swap would route live flow to an empty slot."""
        deployer = BlueGreenDeployer(warmup_seconds=0.0)
        self.assertEqual(deployer.slots[SlotColor.GREEN].state, SlotState.IDLE)

        result = deployer.rollback()

        self.assertFalse(result.success)
        self.assertEqual(deployer.active_slot, SlotColor.BLUE)
        self.assertEqual(deployer.slots[SlotColor.GREEN].state, SlotState.IDLE)
        self.assertEqual(deployer.slots[SlotColor.BLUE].state, SlotState.LIVE)

    def test_rollback_refused_when_target_failed_health(self):
        deployer = BlueGreenDeployer(health_check_fn=lambda _: False, warmup_seconds=0.0)
        deployer.deploy_to_inactive("v7.0")
        self.assertEqual(deployer.slots[SlotColor.GREEN].state, SlotState.FAILED)

        result = deployer.rollback()

        self.assertFalse(result.success)
        self.assertEqual(deployer.active_slot, SlotColor.BLUE)

    def test_second_rollback_refused(self):
        """Rollback ping-pong would hand routing back to the version just rejected."""
        deployer = BlueGreenDeployer(warmup_seconds=0.0)
        deployer.deploy_to_inactive("v8.0")
        deployer.cutover()
        self.assertTrue(deployer.rollback().success)

        result = deployer.rollback()

        self.assertFalse(result.success)
        self.assertEqual(deployer.active_slot, SlotColor.BLUE)

    def test_rollback_resyncs_state_backwards_from_live_slot(self):
        """The rollback target's book is stale by the length of the live window."""
        calls = []
        deployer = BlueGreenDeployer(
            state_sync_fn=lambda a, b: calls.append((a, b)) or True, warmup_seconds=0.0
        )
        deployer.deploy_to_inactive("v9.0")
        deployer.cutover()
        calls.clear()

        deployer.rollback()

        self.assertEqual(calls, [(SlotColor.GREEN, SlotColor.BLUE)])

    def test_rollback_refused_when_backward_sync_fails(self):
        sync_ok = {"value": True}
        deployer = BlueGreenDeployer(
            state_sync_fn=lambda a, b: sync_ok["value"], warmup_seconds=0.0
        )
        deployer.deploy_to_inactive("v10.0")
        deployer.cutover()

        sync_ok["value"] = False
        result = deployer.rollback()

        self.assertFalse(result.success)
        self.assertEqual(deployer.active_slot, SlotColor.GREEN)
        self.assertEqual(deployer.slots[SlotColor.GREEN].state, SlotState.LIVE)
        self.assertIn("kill switch", result.message)

    def test_forced_rollback_overrides_failed_sync(self):
        sync_ok = {"value": True}
        deployer = BlueGreenDeployer(
            state_sync_fn=lambda a, b: sync_ok["value"], warmup_seconds=0.0
        )
        deployer.deploy_to_inactive("v11.0")
        deployer.cutover()

        sync_ok["value"] = False
        result = deployer.rollback(force=True)

        self.assertTrue(result.success)
        self.assertEqual(deployer.active_slot, SlotColor.BLUE)


class TestStandbyLifecycle(unittest.TestCase):
    def test_deploy_refuses_to_overwrite_draining_rollback_target(self):
        deployer = BlueGreenDeployer(warmup_seconds=0.0)
        deployer.deploy_to_inactive("v2.0")
        deployer.cutover()
        self.assertEqual(deployer.slots[SlotColor.BLUE].state, SlotState.DRAINING)

        with self.assertRaises(DeployError):
            deployer.deploy_to_inactive("v3.0")

        # The rollback target survives the refused deployment.
        self.assertEqual(deployer.slots[SlotColor.BLUE].state, SlotState.DRAINING)

    def test_decommission_frees_slot_and_removes_rollback_target(self):
        deployer = BlueGreenDeployer(warmup_seconds=0.0, initial_version="v1.0")
        deployer.deploy_to_inactive("v2.0")
        deployer.cutover()

        released = deployer.decommission_standby()
        self.assertEqual(released.state, SlotState.IDLE)
        self.assertEqual(released.version, "")

        # Rollback is no longer possible: the operator gave that capability up.
        self.assertFalse(deployer.rollback().success)

        # But the slot now accepts the next version.
        self.assertEqual(deployer.deploy_to_inactive("v3.0").state, SlotState.READY)

    def test_decommission_refuses_live_slot(self):
        deployer = BlueGreenDeployer(warmup_seconds=0.0)
        deployer.slots[SlotColor.GREEN].state = SlotState.LIVE  # corrupt state
        with self.assertRaises(DeployError):
            deployer.decommission_standby()


class TestAuditTrail(unittest.TestCase):
    def test_history_records_authoriser_for_each_action(self):
        deployer = BlueGreenDeployer(warmup_seconds=0.0)
        deployer.deploy_to_inactive("v2.0", authorised_by="alice")
        deployer.cutover(authorised_by="bob")

        actions = [(r["action"], r["authorised_by"], r["success"]) for r in deployer.deployment_history]
        self.assertIn(("DEPLOY", "alice", True), actions)
        self.assertIn(("CUTOVER", "bob", True), actions)
        for record in deployer.deployment_history:
            self.assertGreater(record["timestamp"], 0)

    def test_history_opens_with_the_version_that_was_already_live(self):
        """
        A history that begins at the first deployment cannot say what was running
        before it, so a rollback to the pre-existing version is unidentifiable.
        """
        deployer = BlueGreenDeployer(warmup_seconds=0.0, initial_version="v1.0")

        first = deployer.deployment_history[0]
        self.assertEqual(first["action"], "INITIALIZE")
        self.assertEqual(first["version"], "v1.0")
        self.assertEqual(first["to"], "BLUE")
        self.assertFalse(first["forced"])

    def test_clean_rollback_is_not_flagged_as_forced(self):
        deployer = BlueGreenDeployer(warmup_seconds=0.0, initial_version="v1.0")
        deployer.deploy_to_inactive("v2.0", authorised_by="ops")
        deployer.cutover(authorised_by="ops")

        self.assertTrue(deployer.rollback(authorised_by="risk-officer").success)

        rollbacks = [r for r in deployer.deployment_history if r["action"] == "ROLLBACK"]
        self.assertEqual(len(rollbacks), 1)
        self.assertFalse(rollbacks[0]["forced"])

    def test_forced_rollback_over_ineligible_target_is_marked_in_the_audit_trail(self):
        """
        Regression: a forced rollback whose state sync happened to succeed was
        recorded as an ordinary successful rollback, so the override of the
        target-eligibility guard left no trace for a post-incident review.
        """
        deployer = BlueGreenDeployer(warmup_seconds=0.0, initial_version="v1.0")
        self.assertEqual(deployer.slots[SlotColor.GREEN].state, SlotState.IDLE)

        result = deployer.rollback(authorised_by="head-of-trading", force=True)

        self.assertTrue(result.success)
        self.assertIn("FORCED", result.message)
        record = [r for r in deployer.deployment_history if r["action"] == "ROLLBACK"][-1]
        self.assertTrue(record["forced"])
        self.assertIn("target-eligibility", str(record["detail"]))
        self.assertEqual(record["authorised_by"], "head-of-trading")

    def test_forced_rollback_over_failed_sync_names_the_overridden_guard(self):
        sync_ok = {"value": True}
        deployer = BlueGreenDeployer(
            state_sync_fn=lambda a, b: sync_ok["value"],
            warmup_seconds=0.0,
            initial_version="v1.0",
        )
        deployer.deploy_to_inactive("v2.0", authorised_by="ops")
        deployer.cutover(authorised_by="ops")

        sync_ok["value"] = False
        self.assertTrue(deployer.rollback(authorised_by="risk-officer", force=True).success)

        record = [r for r in deployer.deployment_history if r["action"] == "ROLLBACK"][-1]
        self.assertTrue(record["forced"])
        self.assertIn("state-reconciliation", str(record["detail"]))

    def test_decommission_records_who_released_rollback_capability(self):
        deployer = BlueGreenDeployer(warmup_seconds=0.0, initial_version="v1.0")
        deployer.deploy_to_inactive("v2.0", authorised_by="ops")
        deployer.cutover(authorised_by="ops")

        deployer.decommission_standby(authorised_by="head-of-trading")

        record = [r for r in deployer.deployment_history if r["action"] == "DECOMMISSION"][-1]
        self.assertEqual(record["authorised_by"], "head-of-trading")
        self.assertEqual(record["version"], "v1.0")

    def test_history_records_refused_operations(self):
        """A blocked rollback is exactly the event a post-incident review needs."""
        deployer = BlueGreenDeployer(warmup_seconds=0.0)
        deployer.rollback(authorised_by="carol")

        refused = [r for r in deployer.deployment_history if r["action"] == "ROLLBACK"]
        self.assertEqual(len(refused), 1)
        self.assertFalse(refused[0]["success"])
        self.assertEqual(refused[0]["authorised_by"], "carol")


class TestConcurrency(unittest.TestCase):
    def test_concurrent_deploy_to_same_slot_is_rejected(self):
        """Two deployments must not race onto one standby slot."""
        entered = threading.Event()
        release = threading.Event()

        def blocking_health(_):
            entered.set()
            release.wait(timeout=5)
            return True

        deployer = BlueGreenDeployer(health_check_fn=blocking_health, warmup_seconds=0.0)
        thread = threading.Thread(target=deployer.deploy_to_inactive, args=("v2.0",))
        thread.start()
        self.assertTrue(entered.wait(timeout=5))

        with self.assertRaises(DeployError):
            deployer.deploy_to_inactive("v2.0-hotfix")

        release.set()
        thread.join(timeout=5)
        self.assertEqual(deployer.slots[SlotColor.GREEN].state, SlotState.READY)

    def test_deploy_abandons_slot_that_went_live_during_warmup(self):
        """
        The warmup/health window runs without the lock. If routing moves onto the
        slot meanwhile, stamping READY/FAILED on it would mislabel the running
        strategy and disarm the rollback guard.
        """
        entered = threading.Event()
        release = threading.Event()
        result = {}

        def blocking_health(_):
            entered.set()
            release.wait(timeout=5)
            return True

        deployer = BlueGreenDeployer(health_check_fn=blocking_health, warmup_seconds=0.0)

        def deploy():
            result["slot"] = deployer.deploy_to_inactive("v2.0")

        thread = threading.Thread(target=deploy)
        thread.start()
        self.assertTrue(entered.wait(timeout=5))

        # Operator forces routing onto GREEN while it is still DEPLOYING.
        deployer.rollback(force=True)
        self.assertEqual(deployer.active_slot, SlotColor.GREEN)

        release.set()
        thread.join(timeout=5)

        self.assertEqual(deployer.slots[SlotColor.GREEN].state, SlotState.LIVE)
        self.assertEqual(result["slot"].state, SlotState.LIVE)
        abandoned = [r for r in deployer.deployment_history
                     if r["action"] == "DEPLOY" and not r["success"]]
        self.assertEqual(len(abandoned), 1)
        self.assertIn("abandoned", str(abandoned[0]["detail"]))

    def test_stale_health_result_cannot_land_on_a_redeployed_slot(self):
        """
        Regression: the post-warmup guard identified the slot by (state, version).
        Decommissioning the standby and redeploying the *same* version while the
        first attempt was still warming up produced two indistinguishable
        deployments, letting the first attempt's stale (unhealthy) verdict be
        stamped onto the second attempt's instance.
        """
        entered = [threading.Event(), threading.Event()]
        release = [threading.Event(), threading.Event()]
        counter = itertools.count()
        counter_lock = threading.Lock()

        def health(_):
            with counter_lock:
                i = next(counter)
            if i > 1:
                return True
            entered[i].set()
            release[i].wait(timeout=5)
            return i == 1  # first attempt unhealthy, second healthy

        deployer = BlueGreenDeployer(
            health_check_fn=health, warmup_seconds=0.0, initial_version="v1.0"
        )

        first = threading.Thread(target=deployer.deploy_to_inactive, args=("v2.0",))
        first.start()
        self.assertTrue(entered[0].wait(timeout=5))

        # Operator releases the standby and redeploys the same version while the
        # first attempt is still blocked in its health check.
        deployer.decommission_standby(authorised_by="ops")
        second_result = {}

        def redeploy():
            second_result["slot"] = deployer.deploy_to_inactive("v2.0")

        second = threading.Thread(target=redeploy)
        second.start()
        self.assertTrue(entered[1].wait(timeout=5))

        release[0].set()   # stale attempt finishes first, carrying health=False
        first.join(timeout=5)
        release[1].set()
        second.join(timeout=5)

        self.assertEqual(deployer.slots[SlotColor.GREEN].state, SlotState.READY)
        self.assertTrue(deployer.slots[SlotColor.GREEN].health_ok)
        self.assertEqual(second_result["slot"].state, SlotState.READY)

        abandoned = [r for r in deployer.deployment_history
                     if r["action"] == "DEPLOY" and not r["success"]]
        self.assertEqual(len(abandoned), 1)
        self.assertIn("abandoned", str(abandoned[0]["detail"]))

    def test_deploy_abandons_slot_decommissioned_during_warmup(self):
        entered = threading.Event()
        release = threading.Event()

        def blocking_health(_):
            entered.set()
            release.wait(timeout=5)
            return True

        deployer = BlueGreenDeployer(health_check_fn=blocking_health, warmup_seconds=0.0)
        thread = threading.Thread(target=deployer.deploy_to_inactive, args=("v2.0",))
        thread.start()
        self.assertTrue(entered.wait(timeout=5))

        deployer.decommission_standby()
        release.set()
        thread.join(timeout=5)

        self.assertEqual(deployer.slots[SlotColor.GREEN].state, SlotState.IDLE)
        self.assertEqual(deployer.slots[SlotColor.GREEN].version, "")


if __name__ == "__main__":
    unittest.main()
