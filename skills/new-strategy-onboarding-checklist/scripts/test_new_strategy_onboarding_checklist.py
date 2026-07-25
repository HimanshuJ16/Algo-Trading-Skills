import unittest
from new_strategy_onboarding_checklist import NewStrategyOnboardingChecklist, NewStrategyOnboardingChecklistConfig

class TestNewStrategyOnboardingChecklist(unittest.TestCase):
    def test_execute_true(self):
        config = NewStrategyOnboardingChecklistConfig(enabled=True)
        engine = NewStrategyOnboardingChecklist(config)
        self.assertTrue(engine.execute())

    def test_execute_false(self):
        config = NewStrategyOnboardingChecklistConfig(enabled=False)
        engine = NewStrategyOnboardingChecklist(config)
        self.assertFalse(engine.execute())

if __name__ == '__main__':
    unittest.main()
