import unittest
from recovery_plan_for_lost_or_compromised_keys import RecoveryPlanForLostOrCompromisedKeysConfig, RecoveryPlanForLostOrCompromisedKeysEngine

class TestRecoveryPlanForLostOrCompromisedKeys(unittest.TestCase):
    def test_execute_true(self):
        engine = RecoveryPlanForLostOrCompromisedKeysEngine(RecoveryPlanForLostOrCompromisedKeysConfig(enabled=True))
        self.assertTrue(engine.execute())

    def test_execute_false(self):
        engine = RecoveryPlanForLostOrCompromisedKeysEngine(RecoveryPlanForLostOrCompromisedKeysConfig(enabled=False))
        self.assertFalse(engine.execute())

if __name__ == '__main__':
    unittest.main()
