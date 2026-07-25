import unittest
from multi_party_computation_mpc_custody_solutions import MultiPartyComputationMpcCustodySolutionsConfig, MultiPartyComputationMpcCustodySolutionsEngine

class TestMultiPartyComputationMpcCustodySolutions(unittest.TestCase):
    def test_execute_true(self):
        engine = MultiPartyComputationMpcCustodySolutionsEngine(MultiPartyComputationMpcCustodySolutionsConfig(enabled=True))
        self.assertTrue(engine.execute())

    def test_execute_false(self):
        engine = MultiPartyComputationMpcCustodySolutionsEngine(MultiPartyComputationMpcCustodySolutionsConfig(enabled=False))
        self.assertFalse(engine.execute())

if __name__ == '__main__':
    unittest.main()
