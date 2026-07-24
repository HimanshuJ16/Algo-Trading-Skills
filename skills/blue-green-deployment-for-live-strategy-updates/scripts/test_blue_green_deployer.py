"""
Unit tests for blue-green-deployment-for-live-strategy-updates skill.
"""
import unittest
from blue_green_deployer import BlueGreenDeployer, SlotColor, SlotState


class TestBlueGreenDeployer(unittest.TestCase):

    def test_successful_deployment_and_cutover(self):
        """Deploy to green, cutover, verify green is now active."""
        deployer = BlueGreenDeployer()
        # Blue starts as active
        self.assertEqual(deployer.active_slot, SlotColor.BLUE)

        # Deploy v2.0 to green (inactive)
        slot = deployer.deploy_to_inactive("v2.0")
        self.assertEqual(slot.color, SlotColor.GREEN)
        self.assertEqual(slot.state, SlotState.READY)
        self.assertTrue(slot.health_ok)

        # Cutover
        result = deployer.cutover()
        self.assertTrue(result.success)
        self.assertEqual(result.active_slot, SlotColor.GREEN)
        self.assertEqual(result.previous_slot, SlotColor.BLUE)

    def test_failed_health_check_blocks_cutover(self):
        """If health check fails, deployment should fail and cutover should be blocked."""
        deployer = BlueGreenDeployer(health_check_fn=lambda _: False)

        slot = deployer.deploy_to_inactive("v3.0")
        self.assertEqual(slot.state, SlotState.FAILED)
        self.assertFalse(slot.health_ok)

        result = deployer.cutover()
        self.assertFalse(result.success)
        self.assertEqual(result.active_slot, SlotColor.BLUE)  # Still on blue

    def test_rollback_restores_previous_version(self):
        """After cutover to green, rollback should restore blue."""
        deployer = BlueGreenDeployer()
        deployer.slots[SlotColor.BLUE].version = "v1.0"
        deployer.slots[SlotColor.BLUE].state = SlotState.LIVE

        deployer.deploy_to_inactive("v2.0")
        deployer.cutover()
        self.assertEqual(deployer.active_slot, SlotColor.GREEN)

        # Rollback
        result = deployer.rollback()
        self.assertTrue(result.success)
        self.assertEqual(result.active_slot, SlotColor.BLUE)
        self.assertEqual(len(deployer.deployment_history), 2)  # cutover + rollback


if __name__ == "__main__":
    unittest.main()
