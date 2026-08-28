"""
Unit tests for risk-parity-allocation-across-strategies.

The ERC expected values are not re-derived from this module's own formulas. They
are the published solutions of Maillard, Roncalli and Teiletche (2009),
"On the properties of equally-weighted risk contributions portfolios", Sec. 3.2
and Sec. 4.1 -- weights, portfolio volatility, marginal contributions and risk
contribution shares all come from the paper's own tables, so a test passing here
means the implementation agrees with the reference literature rather than with
itself.
"""
import math
import unittest

from risk_parity_allocation_across_strategies import (
    AllocationMethod,
    RiskParityAllocationAcrossStrategies,
    RiskParityAllocationAcrossStrategiesConfig,
    RiskParityAllocationEngine,
    RiskParityReport,
    StrategyRiskData,
    solve_equal_risk_contribution,
)

# MRT (2009) Sec. 4.1: four assets at 10%, 20%, 30%, 40% annualized volatility.
MRT_VOLS = [0.10, 0.20, 0.30, 0.40]

# ...with rho_12 = 0.80, rho_34 = -0.50, all other pairs uncorrelated.
MRT_CORRELATION = [
    [1.00, 0.80, 0.00, 0.00],
    [0.80, 1.00, 0.00, 0.00],
    [0.00, 0.00, 1.00, -0.50],
    [0.00, 0.00, -0.50, 1.00],
]


def covariance_from(vols, correlation):
    return [
        [correlation[i][j] * vols[i] * vols[j] for j in range(len(vols))]
        for i in range(len(vols))
    ]


def constant_correlation(vols, rho):
    n = len(vols)
    return [[1.0 if i == j else rho for j in range(n)] for i in range(n)]


def mrt_strategies():
    return [StrategyRiskData(f"ASSET_{i + 1}", v) for i, v in enumerate(MRT_VOLS)]


class TestRiskParityLegacy(unittest.TestCase):

    def test_execute_true(self):
        config = RiskParityAllocationAcrossStrategiesConfig(enabled=True)
        engine = RiskParityAllocationAcrossStrategies(config)
        self.assertTrue(engine.execute())

    def test_execute_false(self):
        config = RiskParityAllocationAcrossStrategiesConfig(enabled=False)
        engine = RiskParityAllocationAcrossStrategies(config)
        self.assertFalse(engine.execute())


class TestPublishedERCSolutions(unittest.TestCase):
    """Reproduces the worked examples of Maillard, Roncalli and Teiletche (2009)."""

    def setUp(self):
        self.engine = RiskParityAllocationEngine()

    def test_reproduces_mrt_general_correlation_erc_solution(self):
        # MRT Sec. 4.1, ERC table: weights 38.4/19.2/24.3/18.2%, sigma(x) = 10.3%,
        # marginal contributions 0.067/0.134/0.106/0.141, each c_i(x) = 25%.
        cov = covariance_from(MRT_VOLS, MRT_CORRELATION)
        report = self.engine.compute_risk_parity_allocation(
            mrt_strategies(), total_capital_usd=1_000_000.0, covariance_matrix=cov,
            method=AllocationMethod.EQUAL_RISK_CONTRIBUTION,
        )

        published_weights = [0.384, 0.192, 0.243, 0.182]
        published_mcr = [0.067, 0.134, 0.106, 0.141]
        for allocation, weight, mcr in zip(
            report.allocations, published_weights, published_mcr
        ):
            self.assertAlmostEqual(allocation.weight, weight, places=3)
            self.assertAlmostEqual(allocation.marginal_contribution_to_risk, mcr, places=3)
            self.assertAlmostEqual(allocation.risk_contribution_pct, 25.0, places=3)

        self.assertAlmostEqual(report.portfolio_annualized_volatility, 10.3, places=1)
        self.assertEqual(report.status, "RISK_PARITY_BALANCED")
        self.assertGreater(report.solver_iterations, 0)

    def test_reproduces_mrt_equal_weight_risk_decomposition(self):
        # MRT Sec. 4.1, 1/n table: sigma(x) = 11.5%, marginal contributions
        # 0.056/0.122/0.065/0.217, risk shares 12.3/26.4/14.1/47.2%. Equal weights
        # arise here because all four volatilities feed one covariance matrix and
        # the strategies are given identical volatility inputs of their own -- this
        # checks the Euler decomposition, not the weighting scheme.
        cov = covariance_from(MRT_VOLS, MRT_CORRELATION)
        weights = [0.25] * 4
        n = 4
        sigma_w = [sum(cov[i][j] * weights[j] for j in range(n)) for i in range(n)]
        port_vol = math.sqrt(sum(weights[i] * sigma_w[i] for i in range(n)))

        self.assertAlmostEqual(port_vol, 0.115, places=3)
        for i, (expected_mcr, expected_share) in enumerate(
            zip([0.056, 0.122, 0.065, 0.217], [12.3, 26.4, 14.1, 47.2])
        ):
            mcr = sigma_w[i] / port_vol
            self.assertAlmostEqual(mcr, expected_mcr, places=3)
            self.assertAlmostEqual(weights[i] * mcr / port_vol * 100.0, expected_share, places=0)

    def test_constant_correlation_erc_equals_inverse_volatility(self):
        # MRT Eq. 3 / Sec. 4.1: under a constant correlation matrix the ERC weights
        # are 48/24/16/12% for these volatilities, whatever rho is.
        strategies = mrt_strategies()
        for rho in (0.0, 0.30, 0.50, 0.90):
            with self.subTest(rho=rho):
                cov = covariance_from(MRT_VOLS, constant_correlation(MRT_VOLS, rho))
                report = self.engine.compute_risk_parity_allocation(
                    strategies, covariance_matrix=cov,
                    method=AllocationMethod.EQUAL_RISK_CONTRIBUTION,
                )
                for allocation, expected in zip(
                    report.allocations, [0.48, 0.24, 0.16, 0.12]
                ):
                    self.assertAlmostEqual(allocation.weight, expected, places=5)
                    self.assertAlmostEqual(allocation.risk_contribution_pct, 25.0, places=4)

    def test_two_strategy_inverse_vol_is_erc_regardless_of_correlation(self):
        # MRT Sec. 3.1: for n = 2 the ERC solution does not depend on rho.
        strategies = [StrategyRiskData("A", 0.15), StrategyRiskData("B", 0.10)]
        for rho in (-0.90, 0.0, 0.20, 0.95):
            with self.subTest(rho=rho):
                cov = covariance_from([0.15, 0.10], [[1.0, rho], [rho, 1.0]])
                report = self.engine.compute_risk_parity_allocation(
                    strategies, covariance_matrix=cov,
                    method=AllocationMethod.INVERSE_VOLATILITY,
                )
                self.assertAlmostEqual(report.allocations[0].weight, 0.40, places=6)
                self.assertAlmostEqual(report.allocations[1].weight, 0.60, places=6)
                for allocation in report.allocations:
                    self.assertAlmostEqual(allocation.risk_contribution_pct, 50.0, places=6)


class TestInverseVolIsNotERC(unittest.TestCase):
    """
    Regression tests for the defect this skill previously shipped: inverse-volatility
    weights were returned and described as Equal Risk Contribution. They are only
    equal under a constant correlation matrix.
    """

    def setUp(self):
        self.engine = RiskParityAllocationEngine()

    def test_inverse_vol_is_flagged_unbalanced_on_mrt_example(self):
        # Same covariance matrix as the ERC test above. Inverse volatility gives
        # 48/24/16/12% and risk shares of 39.13/39.13/10.87/10.87% against a 25%
        # target: the two correlated strategies carry 78% of portfolio risk.
        cov = covariance_from(MRT_VOLS, MRT_CORRELATION)
        report = self.engine.compute_risk_parity_allocation(
            mrt_strategies(), covariance_matrix=cov,
            method=AllocationMethod.INVERSE_VOLATILITY,
        )

        shares = [a.risk_contribution_pct for a in report.allocations]
        for actual, expected in zip(shares, [39.13, 39.13, 10.87, 10.87]):
            self.assertAlmostEqual(actual, expected, places=2)
        self.assertAlmostEqual(shares[0] + shares[1], 78.26, places=2)
        self.assertFalse(report.is_risk_balanced)
        self.assertEqual(report.status, "RISK_PARITY_UNBALANCED")

    def test_erc_repairs_what_inverse_vol_leaves_unbalanced(self):
        cov = covariance_from(MRT_VOLS, MRT_CORRELATION)
        erc = self.engine.compute_risk_parity_allocation(
            mrt_strategies(), covariance_matrix=cov,
            method=AllocationMethod.EQUAL_RISK_CONTRIBUTION,
        )
        self.assertTrue(erc.is_risk_balanced)
        self.assertLess(erc.max_relative_risk_error_pct, 1e-6)

    def test_relative_gate_catches_drift_the_absolute_gate_misses(self):
        # Ten equally volatile strategies, base correlation 0.30, with one pair at
        # 0.80. Inverse volatility puts every strategy at 10% capital but the
        # correlated pair contributes 11.05% of risk each against a 10% target:
        # 1.05 percentage points, which clears a 5pp absolute limit, yet 10.5% in
        # relative terms. The absolute gate alone loses resolution as N grows.
        n = 10
        vols = [0.20] * n
        correlation = [[1.0 if i == j else 0.30 for j in range(n)] for i in range(n)]
        correlation[0][1] = correlation[1][0] = 0.80
        cov = covariance_from(vols, correlation)
        strategies = [StrategyRiskData(f"S{i:02d}", 0.20) for i in range(n)]

        report = RiskParityAllocationEngine(
            max_allowed_risk_error_pct=5.0, max_allowed_relative_error_pct=5.0
        ).compute_risk_parity_allocation(
            strategies, covariance_matrix=cov,
            method=AllocationMethod.INVERSE_VOLATILITY,
        )

        self.assertLess(report.max_risk_parity_error_pct, 5.0)
        self.assertGreater(report.max_relative_risk_error_pct, 5.0)
        self.assertFalse(report.is_risk_balanced)

        # The absolute gate on its own would have called this balanced.
        absolute_only = RiskParityAllocationEngine(
            max_allowed_risk_error_pct=5.0, max_allowed_relative_error_pct=1000.0
        ).compute_risk_parity_allocation(
            strategies, covariance_matrix=cov,
            method=AllocationMethod.INVERSE_VOLATILITY,
        )
        self.assertTrue(absolute_only.is_risk_balanced)


class TestUncorrelatedPath(unittest.TestCase):
    """No covariance matrix: zero correlations, so the closed form is exact ERC."""

    def setUp(self):
        self.engine = RiskParityAllocationEngine()

    def test_inverse_volatility_weighting_diagonal(self):
        strategies = [
            StrategyRiskData("LOW_RISK", 0.10),
            StrategyRiskData("MED_RISK", 0.20),
            StrategyRiskData("HIGH_RISK", 0.30),
        ]
        report = self.engine.compute_risk_parity_allocation(
            strategies, total_capital_usd=1_000_000.0
        )

        # 1/0.10 : 1/0.20 : 1/0.30 = 10 : 5 : 3.333, summing to 18.333.
        for allocation, expected in zip(
            report.allocations, [10 / (55 / 3), 5 / (55 / 3), (10 / 3) / (55 / 3)]
        ):
            self.assertAlmostEqual(allocation.weight, expected, places=6)
            self.assertAlmostEqual(allocation.risk_contribution_pct, 100.0 / 3.0, places=3)

        self.assertEqual(report.status, "RISK_PARITY_BALANCED")
        self.assertEqual(report.solver_iterations, 0)
        self.assertFalse(report.covariance_supplied)
        # sigma_p = sqrt(3) * (w_i * sigma_i) with w_i * sigma_i equal across i.
        self.assertAlmostEqual(
            report.portfolio_annualized_volatility,
            math.sqrt(3.0) * (10 / (55 / 3)) * 0.10 * 100.0,
            places=4,
        )

    def test_both_methods_agree_when_no_covariance_supplied(self):
        strategies = [
            StrategyRiskData("A", 0.08), StrategyRiskData("B", 0.19),
            StrategyRiskData("C", 0.31), StrategyRiskData("D", 0.44),
        ]
        inverse = self.engine.compute_risk_parity_allocation(
            strategies, method=AllocationMethod.INVERSE_VOLATILITY)
        erc = self.engine.compute_risk_parity_allocation(
            strategies, method=AllocationMethod.EQUAL_RISK_CONTRIBUTION)
        for a, b in zip(inverse.allocations, erc.allocations):
            self.assertAlmostEqual(a.weight, b.weight, places=9)

    def test_allocated_capital_sums_to_total_within_cent_rounding(self):
        # Each allocation is rounded to the cent, so the residual is bounded by
        # half a cent per strategy -- not zero. A funding system must reconcile
        # that residual rather than assume the allocations tie out exactly.
        for count in (7, 40):
            with self.subTest(strategies=count):
                strategies = [
                    StrategyRiskData(f"S{i}", 0.05 + 0.02 * i) for i in range(count)
                ]
                report = self.engine.compute_risk_parity_allocation(
                    strategies, total_capital_usd=2_500_000.0
                )
                total = sum(a.allocated_capital_usd for a in report.allocations)
                self.assertLessEqual(abs(total - 2_500_000.0), 0.005 * count)

    def test_single_strategy_takes_all_capital_and_all_risk(self):
        report = self.engine.compute_risk_parity_allocation(
            [StrategyRiskData("ONLY", 0.22)], total_capital_usd=750_000.0
        )
        allocation = report.allocations[0]
        self.assertAlmostEqual(allocation.weight, 1.0, places=9)
        self.assertAlmostEqual(allocation.allocated_capital_usd, 750_000.0, places=2)
        self.assertAlmostEqual(allocation.risk_contribution_pct, 100.0, places=6)
        self.assertAlmostEqual(report.portfolio_annualized_volatility, 22.0, places=4)


class TestRiskBudgetingSolver(unittest.TestCase):

    def test_unequal_risk_budgets_are_honoured(self):
        cov = covariance_from(MRT_VOLS, MRT_CORRELATION)
        budgets = [0.40, 0.30, 0.20, 0.10]
        weights, sweeps = solve_equal_risk_contribution(cov, risk_budgets=budgets)

        self.assertGreater(sweeps, 0)
        self.assertAlmostEqual(sum(weights), 1.0, places=9)
        n = len(weights)
        sigma_w = [sum(cov[i][j] * weights[j] for j in range(n)) for i in range(n)]
        port_var = sum(weights[i] * sigma_w[i] for i in range(n))
        for i, budget in enumerate(budgets):
            self.assertAlmostEqual(weights[i] * sigma_w[i] / port_var, budget, places=8)

    def test_risk_contributions_sum_to_portfolio_volatility(self):
        # Euler's theorem: volatility is homogeneous of degree 1, so the risk
        # contributions must add back to sigma(w) exactly.
        cov = covariance_from(MRT_VOLS, MRT_CORRELATION)
        weights, _ = solve_equal_risk_contribution(cov)
        n = len(weights)
        sigma_w = [sum(cov[i][j] * weights[j] for j in range(n)) for i in range(n)]
        port_vol = math.sqrt(sum(weights[i] * sigma_w[i] for i in range(n)))
        contributions = sum(weights[i] * sigma_w[i] / port_vol for i in range(n))
        self.assertAlmostEqual(contributions, port_vol, places=12)

    def test_all_weights_strictly_positive(self):
        # CCD started from positive weights stays positive, so the result is
        # long-only without needing an explicit constraint.
        cov = covariance_from(MRT_VOLS, MRT_CORRELATION)
        weights, _ = solve_equal_risk_contribution(cov)
        for weight in weights:
            self.assertGreater(weight, 0.0)

    def test_zero_or_negative_risk_budget_rejected(self):
        cov = covariance_from(MRT_VOLS, MRT_CORRELATION)
        for bad in ([0.25, 0.25, 0.0, 0.50], [0.5, 0.6, -0.1, 0.0]):
            with self.subTest(budgets=bad):
                with self.assertRaises(ValueError):
                    solve_equal_risk_contribution(cov, risk_budgets=bad)

    def test_wrong_length_risk_budgets_rejected(self):
        cov = covariance_from(MRT_VOLS, MRT_CORRELATION)
        with self.assertRaises(ValueError):
            solve_equal_risk_contribution(cov, risk_budgets=[0.5, 0.5])


class TestInputValidation(unittest.TestCase):

    def setUp(self):
        self.engine = RiskParityAllocationEngine()

    def test_empty_strategies_raises_error(self):
        with self.assertRaises(ValueError):
            self.engine.compute_risk_parity_allocation([])

    def test_nan_volatility_raises_instead_of_reporting_balanced(self):
        # Regression: a NaN volatility used to produce NaN weights whose error
        # comparison evaluated False, so the report came back RISK_PARITY_BALANCED
        # with NaN capital allocations.
        strategies = [
            StrategyRiskData("CORRUPT", float("nan")),
            StrategyRiskData("GOOD", 0.10),
        ]
        with self.assertRaises(ValueError):
            self.engine.compute_risk_parity_allocation(strategies)

    def test_infinite_volatility_raises(self):
        with self.assertRaises(ValueError):
            self.engine.compute_risk_parity_allocation(
                [StrategyRiskData("INF", float("inf")), StrategyRiskData("OK", 0.1)]
            )

    def test_negative_volatility_raises_instead_of_concentrating_capital(self):
        # Regression: a sign-flipped volatility used to hand 99.9% of the book to
        # a single strategy with no error raised.
        with self.assertRaises(ValueError):
            self.engine.compute_risk_parity_allocation(
                [StrategyRiskData("FLIPPED", -0.20), StrategyRiskData("OK", 0.10)]
            )

    def test_zero_volatility_raises_instead_of_being_clamped(self):
        # Regression: zero volatility was clamped to 1e-4, giving that strategy an
        # inverse-volatility weight ~1000x every other strategy's.
        with self.assertRaises(ValueError):
            self.engine.compute_risk_parity_allocation(
                [StrategyRiskData("ZERO", 0.0), StrategyRiskData("OK", 0.10)]
            )

    def test_duplicate_strategy_id_raises(self):
        with self.assertRaises(ValueError):
            self.engine.compute_risk_parity_allocation(
                [StrategyRiskData("DUP", 0.10), StrategyRiskData("DUP", 0.20)]
            )

    def test_empty_strategy_id_raises(self):
        with self.assertRaises(ValueError):
            self.engine.compute_risk_parity_allocation([StrategyRiskData("", 0.10)])

    def test_invalid_total_capital_raises(self):
        strategies = [StrategyRiskData("A", 0.10), StrategyRiskData("B", 0.20)]
        for bad in (0.0, -1000.0, float("nan"), float("inf")):
            with self.subTest(capital=bad):
                with self.assertRaises(ValueError):
                    self.engine.compute_risk_parity_allocation(strategies, bad)

    def test_invalid_error_tolerances_rejected(self):
        with self.assertRaises(ValueError):
            RiskParityAllocationEngine(max_allowed_risk_error_pct=0.0)
        with self.assertRaises(ValueError):
            RiskParityAllocationEngine(max_allowed_relative_error_pct=-1.0)


class TestCovarianceValidation(unittest.TestCase):

    def setUp(self):
        self.engine = RiskParityAllocationEngine()
        self.strategies = [StrategyRiskData("A", 0.10), StrategyRiskData("B", 0.10)]

    def test_wrong_shape_raises_value_error_not_index_error(self):
        with self.assertRaises(ValueError):
            self.engine.compute_risk_parity_allocation(
                self.strategies, covariance_matrix=[[0.01]]
            )

    def test_ragged_row_raises(self):
        with self.assertRaises(ValueError):
            self.engine.compute_risk_parity_allocation(
                self.strategies, covariance_matrix=[[0.01, 0.001], [0.001]]
            )

    def test_asymmetric_matrix_raises(self):
        with self.assertRaises(ValueError):
            self.engine.compute_risk_parity_allocation(
                self.strategies, covariance_matrix=[[0.01, 0.002], [0.005, 0.01]]
            )

    def test_non_finite_entry_raises(self):
        with self.assertRaises(ValueError):
            self.engine.compute_risk_parity_allocation(
                self.strategies,
                covariance_matrix=[[0.01, float("nan")], [float("nan"), 0.01]],
            )

    def test_non_positive_definite_matrix_raises(self):
        # Implies a correlation of 5.0. Previously absorbed by a variance floor,
        # which returned a plausible portfolio volatility and a balanced verdict.
        with self.assertRaises(ValueError):
            self.engine.compute_risk_parity_allocation(
                self.strategies, covariance_matrix=[[0.01, 0.05], [0.05, 0.01]]
            )

    def test_perfectly_correlated_pair_is_singular_and_raises(self):
        with self.assertRaises(ValueError):
            self.engine.compute_risk_parity_allocation(
                self.strategies, covariance_matrix=[[0.01, 0.01], [0.01, 0.01]]
            )

    def test_diagonal_inconsistent_with_declared_volatility_raises(self):
        # Diagonal says 30% vol, the strategy declares 10%.
        with self.assertRaises(ValueError):
            self.engine.compute_risk_parity_allocation(
                self.strategies, covariance_matrix=[[0.09, 0.0], [0.0, 0.01]]
            )

    def test_consistent_diagonal_accepted(self):
        report = self.engine.compute_risk_parity_allocation(
            self.strategies, covariance_matrix=[[0.01, 0.003], [0.003, 0.01]]
        )
        self.assertIsInstance(report, RiskParityReport)
        self.assertTrue(report.covariance_supplied)


if __name__ == '__main__':
    unittest.main()
