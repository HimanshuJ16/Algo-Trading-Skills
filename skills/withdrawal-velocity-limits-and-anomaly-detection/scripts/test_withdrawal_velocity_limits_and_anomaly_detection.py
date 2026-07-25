import unittest
from withdrawal_velocity_limits_and_anomaly_detection import WithdrawalVelocityLimitsAndAnomalyDetectionConfig, WithdrawalVelocityLimitsAndAnomalyDetectionEngine

class TestWithdrawalVelocityLimitsAndAnomalyDetection(unittest.TestCase):
    def test_execute_true(self):
        engine = WithdrawalVelocityLimitsAndAnomalyDetectionEngine(WithdrawalVelocityLimitsAndAnomalyDetectionConfig(enabled=True))
        self.assertTrue(engine.execute())

    def test_execute_false(self):
        engine = WithdrawalVelocityLimitsAndAnomalyDetectionEngine(WithdrawalVelocityLimitsAndAnomalyDetectionConfig(enabled=False))
        self.assertFalse(engine.execute())

if __name__ == '__main__':
    unittest.main()
