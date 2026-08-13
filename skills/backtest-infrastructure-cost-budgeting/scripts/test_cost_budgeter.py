"""Unit tests for backtest-infrastructure-cost-budgeting.

Coverage:
1. Cost arithmetic against independently hand-computed values.
2. Budget-guard integrity — the regression that mattered: NaN and negative
   inputs produced a meaningless total that compared False against the budget.
3. Billing-quantum floor, spot interruption overhead, parallel overhead, egress.
4. Boundary and type validation.
"""
import logging
import unittest

from cost_budgeter import (
    BacktestCostBudgeter,
    BacktestJobSpec,
    CloudPricing,
    CostBudgetError,
)

logging.getLogger("cost_budgeter").setLevel(logging.CRITICAL)


def pricing(**overrides):
    kwargs = dict(
        cpu_hourly_rate=0.04,
        ram_hourly_rate=0.005,
        storage_monthly_rate_per_gb=0.10,
    )
    kwargs.update(overrides)
    return CloudPricing(**kwargs)


def job(**overrides):
    kwargs = dict(
        instruments=100,
        parameter_combinations=50,
        cpu_hours_per_unit=0.1,
        memory_gb_required=4.0,
        storage_gb_per_unit=0.01,
    )
    kwargs.update(overrides)
    return BacktestJobSpec(**kwargs)


class TestCostArithmetic(unittest.TestCase):
    """Expected values derived by hand, not by re-running the implementation."""

    def setUp(self):
        self.budgeter = BacktestCostBudgeter(pricing(), max_budget=100.0)

    def test_on_demand_estimate(self):
        # 100 x 50 = 5000 units; 5000 * 0.1 h = 500 vCPU-h
        # CPU     : 500 * 0.04                = 20.00
        # RAM     : 500 * 4 GB * 0.005        = 10.00
        # Storage : 5000 * 0.01 GB = 50 GB; 50 * 0.10 * (30/30) = 5.00
        result = self.budgeter.estimate_costs(job())
        self.assertEqual(result["total_units"], 5000)
        self.assertAlmostEqual(result["billable_cpu_hours"], 500.0)
        self.assertAlmostEqual(result["cpu_cost"], 20.0)
        self.assertAlmostEqual(result["ram_cost"], 10.0)
        self.assertAlmostEqual(result["storage_cost"], 5.0)
        self.assertAlmostEqual(result["egress_cost"], 0.0)
        self.assertAlmostEqual(result["total_cost"], 35.0)
        self.assertFalse(result["is_over_budget"])
        self.assertAlmostEqual(result["budget_headroom"], 65.0)

    def test_over_budget_is_flagged(self):
        result = self.budgeter.estimate_costs(
            job(instruments=1000, parameter_combinations=500,
                cpu_hours_per_unit=0.5, memory_gb_required=16.0, storage_gb_per_unit=0.1)
        )
        self.assertTrue(result["is_over_budget"])
        self.assertGreater(result["total_cost"], 100.0)
        self.assertLess(result["budget_headroom"], 0.0)

    def test_spot_and_partial_retention(self):
        # Compute 30.00 on demand -> 30 * 0.3 = 9.00 on spot.
        # Storage 5.00 for 30 days -> 3 days = 0.5.
        result = self.budgeter.estimate_costs(
            job(storage_retention_days=3.0), use_spot_instances=True
        )
        self.assertAlmostEqual(result["cpu_cost"], 6.0)
        self.assertAlmostEqual(result["ram_cost"], 3.0)
        self.assertAlmostEqual(result["storage_cost"], 0.5)
        self.assertAlmostEqual(result["total_cost"], 9.5)
        self.assertTrue(result["use_spot_instances"])

    def test_storage_scales_linearly_with_retention(self):
        base = self.budgeter.estimate_costs(job(storage_retention_days=30.0))["storage_cost"]
        double = self.budgeter.estimate_costs(job(storage_retention_days=60.0))["storage_cost"]
        self.assertAlmostEqual(double, base * 2)

    def test_zero_sized_sweep_costs_nothing(self):
        result = self.budgeter.estimate_costs(
            job(instruments=0, parameter_combinations=0, cpu_hours_per_unit=0.0,
                memory_gb_required=0.0, storage_gb_per_unit=0.0)
        )
        self.assertEqual(result["total_cost"], 0.0)
        self.assertFalse(result["is_over_budget"])

    def test_bundled_instance_billing_pattern(self):
        # EC2/Compute Engine style: full instance rate in cpu_hourly_rate,
        # ram_hourly_rate zeroed so memory inside the instance is not re-charged.
        budgeter = BacktestCostBudgeter(
            pricing(cpu_hourly_rate=0.192, ram_hourly_rate=0.0), max_budget=1e9
        )
        result = budgeter.estimate_costs(job(memory_gb_required=32.0))
        self.assertAlmostEqual(result["ram_cost"], 0.0)
        self.assertAlmostEqual(result["cpu_cost"], 500.0 * 0.192)


class TestBudgetGuardIntegrity(unittest.TestCase):
    """Regression: NaN and negative inputs produced a total that silently
    compared False against the budget, disabling the guard."""

    def test_nan_inputs_are_rejected(self):
        for fieldname in ("cpu_hours_per_unit", "memory_gb_required",
                          "storage_gb_per_unit", "instruments"):
            with self.subTest(field=fieldname):
                with self.assertRaises(CostBudgetError) as ctx:
                    job(**{fieldname: float("nan")})
                self.assertIn("non-finite", str(ctx.exception))

    def test_infinite_inputs_are_rejected(self):
        with self.assertRaises(CostBudgetError):
            job(parameter_combinations=float("inf"))

    def test_negative_job_inputs_are_rejected(self):
        for fieldname in ("instruments", "parameter_combinations", "cpu_hours_per_unit",
                          "memory_gb_required", "storage_gb_per_unit",
                          "storage_retention_days", "egress_gb_per_unit"):
            with self.subTest(field=fieldname):
                with self.assertRaises(CostBudgetError):
                    job(**{fieldname: -1.0})

    def test_negative_pricing_is_rejected(self):
        for fieldname in ("cpu_hourly_rate", "ram_hourly_rate",
                          "storage_monthly_rate_per_gb", "egress_rate_per_gb",
                          "minimum_billable_seconds", "spot_interruption_overhead"):
            with self.subTest(field=fieldname):
                with self.assertRaises(CostBudgetError):
                    pricing(**{fieldname: -1.0})

    def test_nan_budget_is_rejected(self):
        with self.assertRaises(CostBudgetError):
            BacktestCostBudgeter(pricing(), max_budget=float("nan"))

    def test_negative_budget_is_rejected(self):
        with self.assertRaises(CostBudgetError):
            BacktestCostBudgeter(pricing(), max_budget=-1.0)

    def test_non_numeric_and_boolean_inputs_rejected(self):
        with self.assertRaises(CostBudgetError):
            job(instruments="100")
        with self.assertRaises(CostBudgetError):
            job(instruments=True)

    def test_wrong_types_rejected(self):
        with self.assertRaises(CostBudgetError):
            BacktestCostBudgeter(pricing(), 100.0).estimate_costs({"instruments": 1})
        with self.assertRaises(CostBudgetError):
            BacktestCostBudgeter({"cpu_hourly_rate": 0.04}, 100.0)


class TestSpotModelling(unittest.TestCase):
    def test_spot_multiplier_above_one_is_rejected(self):
        # A "discount" that raises the bill is a configuration error.
        with self.assertRaises(CostBudgetError):
            pricing(spot_discount_multiplier=5.0)

    def test_spot_multiplier_of_zero_is_rejected(self):
        with self.assertRaises(CostBudgetError):
            pricing(spot_discount_multiplier=0.0)

    def test_spot_multiplier_of_one_is_allowed(self):
        result = BacktestCostBudgeter(
            pricing(spot_discount_multiplier=1.0), 1e9
        ).estimate_costs(job(), use_spot_instances=True)
        self.assertAlmostEqual(result["cpu_cost"], 20.0)

    def test_interruption_overhead_raises_spot_cost(self):
        # AWS states interruption is always possible; redone work costs money.
        # Compute on demand = 30.00. Spot at 0.3 with 25% rework:
        # 30 * 0.3 * 1.25 = 11.25
        budgeter = BacktestCostBudgeter(
            pricing(spot_interruption_overhead=0.25), max_budget=1e9
        )
        result = budgeter.estimate_costs(job(), use_spot_instances=True)
        self.assertAlmostEqual(result["cpu_cost"] + result["ram_cost"], 11.25)

    def test_interruption_overhead_ignored_on_demand(self):
        budgeter = BacktestCostBudgeter(
            pricing(spot_interruption_overhead=0.25), max_budget=1e9
        )
        result = budgeter.estimate_costs(job(), use_spot_instances=False)
        self.assertAlmostEqual(result["cpu_cost"], 20.0)

    def test_spot_can_flip_a_job_under_budget(self):
        budgeter = BacktestCostBudgeter(pricing(), max_budget=20.0)
        self.assertTrue(budgeter.estimate_costs(job())["is_over_budget"])
        self.assertFalse(budgeter.estimate_costs(job(), use_spot_instances=True)["is_over_budget"])


class TestBillingQuantum(unittest.TestCase):
    """Fargate bills per second with a 1-minute minimum; short units pay it."""

    def test_minimum_applies_per_unit_not_per_sweep(self):
        # 5000 units of 3.6 s each. Raw = 5000 * 0.001 h = 5 vCPU-h.
        # With a 60 s floor each unit bills 60/3600 h, so 5000 * (1/60) = 83.33 h.
        short = job(instruments=1000, parameter_combinations=5,
                    cpu_hours_per_unit=0.001, memory_gb_required=2.0,
                    storage_gb_per_unit=0.0)
        no_floor = BacktestCostBudgeter(pricing(), 1e9).estimate_costs(short)
        floored = BacktestCostBudgeter(
            pricing(minimum_billable_seconds=60.0), 1e9
        ).estimate_costs(short)
        self.assertAlmostEqual(no_floor["billable_cpu_hours"], 5.0)
        self.assertAlmostEqual(floored["billable_cpu_hours"], 5000.0 / 60.0, places=6)
        self.assertGreater(floored["total_cost"], no_floor["total_cost"] * 16)

    def test_minimum_has_no_effect_when_units_run_longer(self):
        long_units = job(cpu_hours_per_unit=1.0)
        with_floor = BacktestCostBudgeter(
            pricing(minimum_billable_seconds=60.0), 1e9
        ).estimate_costs(long_units)
        without = BacktestCostBudgeter(pricing(), 1e9).estimate_costs(long_units)
        self.assertAlmostEqual(with_floor["total_cost"], without["total_cost"])

    def test_zero_minimum_is_the_default(self):
        self.assertEqual(pricing().minimum_billable_seconds, 0.0)

    def test_zero_duration_units_still_pay_the_minimum(self):
        # Launching a task costs the minimum even if it does no work. Biasing
        # high is the safe direction for a guard; pin the behaviour so it is a
        # decision rather than an accident.
        result = BacktestCostBudgeter(
            pricing(minimum_billable_seconds=60.0), 1e9
        ).estimate_costs(job(instruments=10, parameter_combinations=10,
                             cpu_hours_per_unit=0.0, storage_gb_per_unit=0.0))
        self.assertAlmostEqual(result["billable_cpu_hours"], 100.0 / 60.0, places=6)

    def test_storage_only_estimate_uses_zero_minimum(self):
        result = BacktestCostBudgeter(pricing(), 1e9).estimate_costs(
            job(cpu_hours_per_unit=0.0, memory_gb_required=0.0)
        )
        self.assertAlmostEqual(result["cpu_cost"], 0.0)
        self.assertAlmostEqual(result["ram_cost"], 0.0)
        self.assertAlmostEqual(result["storage_cost"], 5.0)


class TestOverheadAndEgress(unittest.TestCase):
    def test_parallel_overhead_scales_compute_only(self):
        # SKILL.md warns that scaling is not linear; the model must be able to
        # express that rather than silently assuming perfection.
        base = BacktestCostBudgeter(pricing(), 1e9).estimate_costs(job())
        overhead = BacktestCostBudgeter(pricing(), 1e9).estimate_costs(
            job(parallel_overhead_factor=1.2)
        )
        self.assertAlmostEqual(overhead["cpu_cost"], base["cpu_cost"] * 1.2)
        self.assertAlmostEqual(overhead["ram_cost"], base["ram_cost"] * 1.2)
        self.assertAlmostEqual(overhead["storage_cost"], base["storage_cost"])

    def test_overhead_below_one_is_rejected(self):
        # Better-than-linear scaling is not a budgeting assumption worth allowing.
        with self.assertRaises(CostBudgetError):
            job(parallel_overhead_factor=0.9)

    def test_egress_cost_is_counted(self):
        # 5000 units * 0.002 GB = 10 GB; 10 * 0.09 = 0.90
        budgeter = BacktestCostBudgeter(pricing(egress_rate_per_gb=0.09), 1e9)
        result = budgeter.estimate_costs(job(egress_gb_per_unit=0.002))
        self.assertAlmostEqual(result["egress_cost"], 0.90)
        self.assertAlmostEqual(result["total_cost"], 35.0 + 0.90)

    def test_egress_defaults_to_zero(self):
        self.assertAlmostEqual(
            BacktestCostBudgeter(pricing(), 1e9).estimate_costs(job())["egress_cost"], 0.0
        )


class TestBudgetBoundary(unittest.TestCase):
    def test_cost_exactly_equal_to_budget_is_not_over(self):
        budgeter = BacktestCostBudgeter(pricing(), max_budget=35.0)
        result = budgeter.estimate_costs(job())
        self.assertAlmostEqual(result["total_cost"], 35.0)
        self.assertFalse(result["is_over_budget"])
        self.assertAlmostEqual(result["budget_headroom"], 0.0)

    def test_one_cent_over_is_over(self):
        budgeter = BacktestCostBudgeter(pricing(), max_budget=34.99)
        self.assertTrue(budgeter.estimate_costs(job())["is_over_budget"])

    def test_zero_budget_flags_any_nonzero_cost(self):
        budgeter = BacktestCostBudgeter(pricing(), max_budget=0.0)
        self.assertTrue(budgeter.estimate_costs(job())["is_over_budget"])


if __name__ == "__main__":
    unittest.main()
