import unittest
from multi_day_execution_schedules_for_very_large_orders import (
    MultiDayExecutionSchedulerEngine, MultiDayOrderConfig, MultiDayScheduleReport
)

class TestMultiDayExecutionSchedulerEngine(unittest.TestCase):

    def setUp(self):
        self.engine = MultiDayExecutionSchedulerEngine()

    def test_multi_day_horizon_and_slicing_equal_daily(self):
        # Parent order: 500,000 shares AAPL @ $150. ADV = 1,000,000 shares (50% ADV).
        # Max participation = 10% ADV (100,000 shares/day) -> Horizon N_days = 5 days.
        cfg = MultiDayOrderConfig(
            symbol="AAPL",
            total_parent_quantity=500000.0,
            current_price=150.0,
            adv_shares=1000000.0,
            max_daily_participation_pct=0.10,
            schedule_profile="EQUAL_DAILY"
        )

        report = self.engine.generate_multi_day_schedule(cfg)

        self.assertEqual(report.status, "SCHEDULE_GENERATED_SUCCESS")
        self.assertEqual(report.execution_horizon_days, 5)
        self.assertEqual(report.daily_participation_cap_shares, 100000.0)
        self.assertEqual(len(report.daily_schedules), 5)

        # Equal daily slices = 100,000 shares per day
        for s in report.daily_schedules:
            self.assertEqual(s.target_quantity, 100000.0)
            self.assertEqual(s.participation_pct_adv, 10.0)

        self.assertGreater(report.expected_temp_impact_bps, 0.0)
        self.assertGreater(report.overnight_volatility_risk_usd, 0.0)

    def test_front_loaded_profile_participation_cap_enforcement(self):
        # Front loaded schedule profile with strict participation cap enforcement
        cfg = MultiDayOrderConfig(
            symbol="MSFT",
            total_parent_quantity=300000.0,
            current_price=300.0,
            adv_shares=1000000.0,
            max_daily_participation_pct=0.10,   # 100k cap
            schedule_profile="FRONT_LOADED"
        )

        report = self.engine.generate_multi_day_schedule(cfg)

        self.assertEqual(report.execution_horizon_days, 3)
        for s in report.daily_schedules:
            self.assertLessEqual(s.target_quantity, 100000.0)

if __name__ == '__main__':
    unittest.main()
