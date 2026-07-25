import unittest
import threading
from blue_green_deployer import BlueGreenDeployer, SlotColor, SlotState, DeployError

class TestBlueGreenDeployer(unittest.TestCase):
    def test_successful_deployment_and_cutover(self):
        deployer = BlueGreenDeployer(warmup_seconds=0.0)
        self.assertEqual(deployer.active_slot, SlotColor.BLUE)
        self.assertEqual(deployer.slots[SlotColor.BLUE].state, SlotState.LIVE)

        slot = deployer.deploy_to_inactive("v2.0")
        self.assertEqual(slot.color, SlotColor.GREEN)
        self.assertEqual(slot.state, SlotState.READY)
        self.assertTrue(slot.health_ok)

        result = deployer.cutover()
        self.assertTrue(result.success)
        self.assertEqual(result.active_slot, SlotColor.GREEN)
        self.assertEqual(deployer.slots[SlotColor.BLUE].state, SlotState.DRAINING)

    def test_health_check_failure(self):
        deployer = BlueGreenDeployer(health_check_fn=lambda _: False, warmup_seconds=0.0)
        slot = deployer.deploy_to_inactive("v3.0")
        self.assertEqual(slot.state, SlotState.FAILED)
        self.assertFalse(slot.health_ok)

        result = deployer.cutover()
        self.assertFalse(result.success)
        self.assertEqual(result.active_slot, SlotColor.BLUE)

    def test_state_sync_failure_blocks_cutover(self):
        def failing_sync(active, inactive):
            return False
        
        deployer = BlueGreenDeployer(state_sync_fn=failing_sync, warmup_seconds=0.0)
        deployer.deploy_to_inactive("v4.0")
        
        result = deployer.cutover()
        self.assertFalse(result.success)
        self.assertEqual(deployer.active_slot, SlotColor.BLUE)
        self.assertEqual(deployer.slots[SlotColor.GREEN].state, SlotState.FAILED)

    def test_rollback_restores_previous(self):
        deployer = BlueGreenDeployer(warmup_seconds=0.0)
        deployer.deploy_to_inactive("v2.0")
        deployer.cutover()
        
        self.assertEqual(deployer.active_slot, SlotColor.GREEN)
        result = deployer.rollback()
        
        self.assertTrue(result.success)
        self.assertEqual(deployer.active_slot, SlotColor.BLUE)
        self.assertEqual(deployer.slots[SlotColor.GREEN].state, SlotState.FAILED)

    def test_thread_safety(self):
        deployer = BlueGreenDeployer(warmup_seconds=0.1)
        
        def deploy_job():
            deployer.deploy_to_inactive("v_thread")
            deployer.cutover()

        t1 = threading.Thread(target=deploy_job)
        t1.start()
        
        # Test concurrent read
        active = deployer.get_active_slot()
        self.assertEqual(active.color, SlotColor.BLUE)
        
        t1.join()
        self.assertEqual(deployer.active_slot, SlotColor.GREEN)

if __name__ == "__main__":
    unittest.main()
