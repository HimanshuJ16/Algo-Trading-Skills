import unittest
from early_exercise_assignment_risk_management import InputData, EarlyExerciseAssignmentRiskManagementEngine

class TestEarlyExerciseAssignmentRiskManagement(unittest.TestCase):
    def test_process(self):
        engine = EarlyExerciseAssignmentRiskManagementEngine()
        res = engine.process(InputData(value=10.0))
        self.assertEqual(res, 20.0)

    def test_process_zero(self):
        engine = EarlyExerciseAssignmentRiskManagementEngine()
        res = engine.process(InputData(value=0.0))
        self.assertEqual(res, 0.0)

if __name__ == '__main__':
    unittest.main()
