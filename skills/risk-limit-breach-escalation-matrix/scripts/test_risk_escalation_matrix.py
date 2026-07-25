import unittest
from risk_escalation_matrix import RiskEscalationMatrix, ResponseAction

class TestRiskEscalationMatrix(unittest.TestCase):
    def test_warn(self):
        matrix = RiskEscalationMatrix()
        res = matrix.evaluate(110, 100)
        self.assertEqual(res.action, ResponseAction.WARN)

    def test_flatten(self):
        matrix = RiskEscalationMatrix()
        res = matrix.evaluate(210, 100)
        self.assertEqual(res.action, ResponseAction.FLATTEN)
