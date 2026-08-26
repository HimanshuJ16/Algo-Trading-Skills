"""
Unit tests for leverage-limit-enforcement-across-instruments.

Tests:
1. Approval, gross / net / asset-class veto paths.
2. Per-symbol netting: closing and reducing orders lower exposure.
3. Risk-reducing orders are approved while the book is over a cap, but a
   same-size position reversal is not mistaken for remediation.
4. Marginal breaches survive to the comparison (no rounding before the gate)
   and an exactly-at-limit ratio is inclusive.
5. Input validation: side aliases, negative / non-finite notionals, bad
   equity, malformed rows, conflicting asset classes.
6. Fail-closed handling of asset classes with no configured cap.
7. Delta conversion of options to underlying-equivalent exposure.
8. Reporting: all asset-class leverages, and the gross >= net invariant.
"""
import logging
import unittest

from leverage_limit_enforcer import (
    LeverageLimitEnforcerEngine,
    LeverageEnforcementReport,
    PositionSpec,
    ProposedOrderSpec,
    STATUS_APPROVED,
    STATUS_APPROVED_RISK_REDUCING,
    STATUS_REJECTED_ASSET_CLASS,
    STATUS_REJECTED_GROSS,
    STATUS_REJECTED_NET,
    STATUS_REJECTED_UNKNOWN_ASSET_CLASS,
)


def setUpModule():
    # Silence the module's veto warnings during the run without disabling
    # logging globally for other suites.
    logging.getLogger("leverage_limit_enforcer").addHandler(logging.NullHandler())


class TestLeverageLimitEnforcerEngine(unittest.TestCase):

    def setUp(self):
        self.engine = LeverageLimitEnforcerEngine(
            max_gross_leverage=5.0,
            max_net_leverage=3.0,
            asset_class_limits={"EQUITY": 2.0, "CRYPTO": 3.0, "FX": 10.0},
        )

    # ------------------------------------------------------------------
    # Core approval / veto paths
    # ------------------------------------------------------------------
    def test_order_leverage_approved(self):
        # Equity = $100k
        # Active: Long $100k AAPL, Short $50k TSLA -> Gross = $150k (1.5x), Net = $50k (0.5x)
        # Proposed: Buy $50k MSFT (Equity) -> Proj Gross = $200k (2.0x <= 5.0x),
        # Proj Net = $100k (1.0x <= 3.0x), Equity class = $200k (2.0x <= 2.0x) -> APPROVED
        positions = [
            PositionSpec("AAPL", "EQUITY", "BUY", 100_000.0),
            PositionSpec("TSLA", "EQUITY", "SELL", 50_000.0),
        ]
        order = ProposedOrderSpec("MSFT", "EQUITY", "BUY", 50_000.0)
        report = self.engine.audit_proposed_order(100_000.0, positions, order)

        self.assertEqual(report.status, STATUS_APPROVED)
        self.assertTrue(report.is_approved)
        self.assertEqual(report.current_gross_leverage, 1.5)
        self.assertEqual(report.current_net_leverage, 0.5)
        self.assertEqual(report.projected_gross_leverage, 2.0)
        self.assertEqual(report.projected_net_leverage, 1.0)
        self.assertEqual(report.projected_asset_class_leverage, 2.0)
        self.assertTrue(report.is_gross_limit_passed)
        self.assertTrue(report.is_net_limit_passed)
        self.assertTrue(report.is_asset_class_limit_passed)

    def test_gross_leverage_breach_veto(self):
        # Active Gross = $200k (2.0x). Proposed Buy $350k BTC-PERP
        # -> Proj Gross = $550k (5.5x > 5.0x limit) -> gross veto.
        positions = [
            PositionSpec("AAPL", "EQUITY", "BUY", 150_000.0),
            PositionSpec("TSLA", "EQUITY", "SELL", 50_000.0),
        ]
        order = ProposedOrderSpec("BTC-PERP", "CRYPTO", "BUY", 350_000.0)
        report = self.engine.audit_proposed_order(100_000.0, positions, order)

        self.assertEqual(report.status, STATUS_REJECTED_GROSS)
        self.assertFalse(report.is_approved)
        self.assertFalse(report.is_gross_limit_passed)
        self.assertEqual(report.projected_gross_leverage, 5.5)

    def test_net_leverage_breach_veto(self):
        # Equity = $100k. Active: Long $250k AAPL (gross 2.5x, net 2.5x).
        # Proposed: Buy $100k MSFT -> Gross = $350k (3.5x <= 5.0x) but
        # Net = $350k (3.5x > 3.0x) -> net veto. Equity cap raised so the
        # asset-class gate cannot mask the net gate.
        engine = LeverageLimitEnforcerEngine(
            max_gross_leverage=5.0, max_net_leverage=3.0,
            asset_class_limits={"EQUITY": 10.0},
        )
        positions = [PositionSpec("AAPL", "EQUITY", "BUY", 250_000.0)]
        order = ProposedOrderSpec("MSFT", "EQUITY", "BUY", 100_000.0)
        report = engine.audit_proposed_order(100_000.0, positions, order)

        self.assertEqual(report.status, STATUS_REJECTED_NET)
        self.assertTrue(report.is_gross_limit_passed)
        self.assertFalse(report.is_net_limit_passed)
        self.assertEqual(report.projected_net_leverage, 3.5)

    def test_asset_class_leverage_breach_veto(self):
        # Equity = $160k. Active: Long $150k AAPL, Short $150k TSLA.
        # Proposed: Buy $100k MSFT -> Equity-class gross = $400k
        # (400/160 = 2.5x > 2.0x) while gross 2.5x <= 5.0x and net
        # |150-150+100| = $100k (0.625x) <= 3.0x -> asset-class veto.
        positions = [
            PositionSpec("AAPL", "EQUITY", "BUY", 150_000.0),
            PositionSpec("TSLA", "EQUITY", "SELL", 150_000.0),
        ]
        order = ProposedOrderSpec("MSFT", "EQUITY", "BUY", 100_000.0)
        report = self.engine.audit_proposed_order(160_000.0, positions, order)

        self.assertEqual(report.status, STATUS_REJECTED_ASSET_CLASS)
        self.assertTrue(report.is_gross_limit_passed)
        self.assertTrue(report.is_net_limit_passed)
        self.assertFalse(report.is_asset_class_limit_passed)
        self.assertEqual(report.projected_asset_class_leverage, 2.5)
        self.assertEqual(report.order_asset_class, "EQUITY")
        self.assertEqual(report.order_asset_class_limit, 2.0)

    # ------------------------------------------------------------------
    # Netting: de-risking must never be vetoed (regression for the
    # "append the order as a new leg" defect)
    # ------------------------------------------------------------------
    def test_closing_order_reduces_projected_exposure(self):
        # Equity = $100k, Long $300k AAPL (gross 3.0x).
        # Selling $100k of AAPL leaves a $200k long -> 2.0x, not 4.0x.
        engine = LeverageLimitEnforcerEngine(
            max_gross_leverage=3.0, max_net_leverage=3.0,
            asset_class_limits={"EQUITY": 3.0},
        )
        positions = [PositionSpec("AAPL", "EQUITY", "BUY", 300_000.0)]
        order = ProposedOrderSpec("AAPL", "EQUITY", "SELL", 100_000.0)
        report = engine.audit_proposed_order(100_000.0, positions, order)

        self.assertEqual(report.projected_gross_leverage, 2.0)
        self.assertEqual(report.projected_net_leverage, 2.0)
        self.assertEqual(report.status, STATUS_APPROVED)

    def test_flattening_order_takes_exposure_to_zero(self):
        engine = LeverageLimitEnforcerEngine(
            max_gross_leverage=3.0, max_net_leverage=2.0,
            asset_class_limits={"CRYPTO": 3.0},
        )
        positions = [PositionSpec("BTC-PERP", "CRYPTO", "SELL", 250_000.0)]
        order = ProposedOrderSpec("BTC-PERP", "CRYPTO", "BUY", 250_000.0)
        report = engine.audit_proposed_order(100_000.0, positions, order)

        self.assertEqual(report.current_gross_leverage, 2.5)
        self.assertEqual(report.projected_gross_leverage, 0.0)
        self.assertEqual(report.projected_net_leverage, 0.0)
        self.assertEqual(report.status, STATUS_APPROVED)

    def test_risk_reducing_order_approved_while_book_over_cap(self):
        # Book is at 4.0x gross against a 3.0x cap (e.g. after a mark move).
        # Selling $50k of the long takes it to 3.5x -- still over cap, but the
        # gate must not block the only kind of order that can cure the breach.
        engine = LeverageLimitEnforcerEngine(
            max_gross_leverage=3.0, max_net_leverage=3.0,
            asset_class_limits={"EQUITY": 5.0},
        )
        positions = [PositionSpec("AAPL", "EQUITY", "BUY", 400_000.0)]
        order = ProposedOrderSpec("AAPL", "EQUITY", "SELL", 50_000.0)
        report = engine.audit_proposed_order(100_000.0, positions, order)

        self.assertEqual(report.status, STATUS_APPROVED_RISK_REDUCING)
        self.assertTrue(report.is_approved)
        self.assertTrue(report.is_risk_reducing)
        self.assertFalse(report.is_gross_limit_passed)
        self.assertEqual(report.current_gross_leverage, 4.0)
        self.assertEqual(report.projected_gross_leverage, 3.5)

    def test_same_size_reversal_is_not_risk_reducing(self):
        # Long $400k -> Sell $800k -> Short $400k. Every ratio is unchanged,
        # so nothing was remediated: this is a large new trade and must be
        # vetoed while the book is over its cap.
        engine = LeverageLimitEnforcerEngine(
            max_gross_leverage=3.0, max_net_leverage=3.0,
            asset_class_limits={"EQUITY": 5.0},
        )
        positions = [PositionSpec("AAPL", "EQUITY", "BUY", 400_000.0)]
        order = ProposedOrderSpec("AAPL", "EQUITY", "SELL", 800_000.0)
        report = engine.audit_proposed_order(100_000.0, positions, order)

        self.assertEqual(report.projected_gross_leverage, 4.0)
        self.assertFalse(report.is_risk_reducing)
        self.assertEqual(report.status, STATUS_REJECTED_GROSS)

    def test_increasing_order_on_over_cap_book_is_vetoed(self):
        engine = LeverageLimitEnforcerEngine(
            max_gross_leverage=3.0, max_net_leverage=3.0,
            asset_class_limits={"EQUITY": 5.0},
        )
        positions = [PositionSpec("AAPL", "EQUITY", "BUY", 400_000.0)]
        order = ProposedOrderSpec("AAPL", "EQUITY", "BUY", 10_000.0)
        report = engine.audit_proposed_order(100_000.0, positions, order)

        self.assertEqual(report.status, STATUS_REJECTED_GROSS)
        self.assertFalse(report.is_risk_reducing)

    def test_multiple_rows_for_same_symbol_are_netted(self):
        # A book reported as two AAPL rows is one $60k long, not $140k gross.
        gross, net, by_class = self.engine.compute_exposures([
            PositionSpec("AAPL", "EQUITY", "BUY", 100_000.0),
            PositionSpec("AAPL", "EQUITY", "SELL", 40_000.0),
        ])
        self.assertAlmostEqual(gross, 60_000.0)
        self.assertAlmostEqual(net, 60_000.0)
        self.assertAlmostEqual(by_class["EQUITY"], 60_000.0)

    def test_symbol_matching_is_case_and_whitespace_insensitive(self):
        positions = [PositionSpec(" btc-perp ", "CRYPTO", "BUY", 100_000.0)]
        order = ProposedOrderSpec("BTC-PERP", "CRYPTO", "SELL", 100_000.0)
        report = self.engine.audit_proposed_order(100_000.0, positions, order)
        self.assertEqual(report.projected_gross_leverage, 0.0)

    def test_gross_does_not_net_across_different_symbols(self):
        # A long and an offsetting short in different instruments is a 2.0x
        # gross book with 0.0x net -- the hedge must not shrink gross.
        gross, net, _ = self.engine.compute_exposures([
            PositionSpec("AAPL", "EQUITY", "BUY", 100_000.0),
            PositionSpec("TSLA", "EQUITY", "SELL", 100_000.0),
        ])
        self.assertAlmostEqual(gross, 200_000.0)
        self.assertAlmostEqual(net, 0.0)

    # ------------------------------------------------------------------
    # Threshold precision
    # ------------------------------------------------------------------
    def test_marginal_breach_is_not_rounded_away(self):
        # Gross = 150,000 + 150,000 + 499 = 300,499 on $100k equity = 3.00499x.
        # Rounding the ratio to 2dp before comparing would report 3.00x and
        # approve a genuine breach of the 3.0x cap.
        engine = LeverageLimitEnforcerEngine(
            max_gross_leverage=3.0, max_net_leverage=1.5,
            asset_class_limits={"EQUITY": 10.0},
        )
        positions = [
            PositionSpec("AAPL", "EQUITY", "BUY", 150_000.0),
            PositionSpec("TSLA", "EQUITY", "SELL", 150_000.0),
        ]
        order = ProposedOrderSpec("MSFT", "EQUITY", "BUY", 499.0)
        report = engine.audit_proposed_order(100_000.0, positions, order)

        self.assertEqual(report.status, STATUS_REJECTED_GROSS)
        self.assertAlmostEqual(report.projected_gross_leverage, 3.00499, places=6)

    def test_exactly_at_limit_is_approved(self):
        # Gross = 150,000 + 149,000 + 1,000 = 300,000 = exactly 3.0x.
        engine = LeverageLimitEnforcerEngine(
            max_gross_leverage=3.0, max_net_leverage=1.5,
            asset_class_limits={"EQUITY": 10.0},
        )
        positions = [
            PositionSpec("AAPL", "EQUITY", "BUY", 150_000.0),
            PositionSpec("TSLA", "EQUITY", "SELL", 149_000.0),
        ]
        order = ProposedOrderSpec("MSFT", "EQUITY", "BUY", 1_000.0)
        report = engine.audit_proposed_order(100_000.0, positions, order)

        self.assertEqual(report.projected_gross_leverage, 3.0)
        self.assertEqual(report.status, STATUS_APPROVED)

    # ------------------------------------------------------------------
    # Input validation
    # ------------------------------------------------------------------
    def test_side_aliases_are_rejected_not_guessed(self):
        # 'LONG' silently counted as a short would report a 2.1x long book as
        # 0.1x net and wave through an extreme directional position.
        for alias in ("LONG", "SHORT", "B", "", "buy_to_open"):
            with self.subTest(side=alias):
                with self.assertRaises(ValueError):
                    self.engine.audit_proposed_order(
                        100_000.0,
                        [PositionSpec("AAPL", "EQUITY", alias, 100_000.0)],
                        ProposedOrderSpec("MSFT", "EQUITY", "BUY", 1_000.0),
                    )
        with self.assertRaises(ValueError):
            self.engine.audit_proposed_order(
                100_000.0, [], ProposedOrderSpec("MSFT", "EQUITY", "LONG", 1_000.0)
            )

    def test_side_is_case_and_whitespace_tolerant(self):
        report = self.engine.audit_proposed_order(
            100_000.0,
            [PositionSpec("AAPL", "EQUITY", " buy ", 100_000.0)],
            ProposedOrderSpec("MSFT", "EQUITY", "sell", 50_000.0),
        )
        self.assertEqual(report.projected_gross_leverage, 1.5)
        self.assertEqual(report.projected_net_leverage, 0.5)

    def test_negative_notional_raises(self):
        with self.assertRaises(ValueError):
            self.engine.compute_exposures(
                [PositionSpec("AAPL", "EQUITY", "BUY", -100_000.0)]
            )
        with self.assertRaises(ValueError):
            self.engine.audit_proposed_order(
                100_000.0, [], ProposedOrderSpec("AAPL", "EQUITY", "BUY", -1.0)
            )

    def test_non_finite_values_raise(self):
        nan, inf = float("nan"), float("inf")
        order = ProposedOrderSpec("AAPL", "EQUITY", "BUY", 1_000.0)
        with self.assertRaises(ValueError):
            self.engine.audit_proposed_order(nan, [], order)
        with self.assertRaises(ValueError):
            self.engine.audit_proposed_order(inf, [], order)
        with self.assertRaises(ValueError):
            self.engine.audit_proposed_order(
                100_000.0, [], ProposedOrderSpec("AAPL", "EQUITY", "BUY", nan)
            )
        with self.assertRaises(ValueError):
            self.engine.audit_proposed_order(
                100_000.0,
                [PositionSpec("AAPL", "EQUITY", "BUY", 1.0, exposure_delta=nan)],
                order,
            )

    def test_non_positive_equity_raises(self):
        order = ProposedOrderSpec("AAPL", "EQUITY", "BUY", 1_000.0)
        for equity in (0.0, -1.0):
            with self.subTest(equity=equity):
                with self.assertRaises(ValueError):
                    self.engine.audit_proposed_order(equity, [], order)

    def test_malformed_rows_raise(self):
        order = ProposedOrderSpec("AAPL", "EQUITY", "BUY", 1_000.0)
        with self.assertRaises(ValueError):
            self.engine.audit_proposed_order(100_000.0, [("AAPL", 1)], order)
        with self.assertRaises(ValueError):
            self.engine.audit_proposed_order(
                100_000.0, [PositionSpec("  ", "EQUITY", "BUY", 1.0)], order
            )
        with self.assertRaises(ValueError):
            self.engine.audit_proposed_order(
                100_000.0, [PositionSpec("AAPL", "", "BUY", 1.0)], order
            )
        with self.assertRaises(ValueError):
            self.engine.audit_proposed_order(100_000.0, [], "not-an-order")

    def test_positions_argument_must_be_a_sequence(self):
        order = ProposedOrderSpec("AAPL", "EQUITY", "BUY", 1_000.0)
        for bad in (None, "AAPL", {"AAPL": 1.0}, 7):
            with self.subTest(positions=bad):
                with self.assertRaises(ValueError):
                    self.engine.audit_proposed_order(100_000.0, bad, order)
        # A tuple is a legitimate sequence of positions.
        gross, _, _ = self.engine.compute_exposures(
            (PositionSpec("AAPL", "EQUITY", "BUY", 5_000.0),)
        )
        self.assertAlmostEqual(gross, 5_000.0)

    def test_exposure_summation_is_exact_over_a_large_book(self):
        # 20,000 rows of 0.1 sum to 1999.9999999992765 with naive float
        # accumulation -- an absolute error larger than the gate's tolerance.
        book = [PositionSpec(f"S{i}", "EQUITY", "BUY", 0.1) for i in range(20_000)]
        gross, net, by_class = self.engine.compute_exposures(book)
        self.assertEqual(gross, 2_000.0)
        self.assertEqual(net, 2_000.0)
        self.assertEqual(by_class["EQUITY"], 2_000.0)

    def test_same_symbol_under_two_asset_classes_raises(self):
        with self.assertRaises(ValueError):
            self.engine.compute_exposures([
                PositionSpec("XYZ", "EQUITY", "BUY", 1_000.0),
                PositionSpec("XYZ", "CRYPTO", "BUY", 1_000.0),
            ])
        with self.assertRaises(ValueError):
            self.engine.audit_proposed_order(
                100_000.0,
                [PositionSpec("XYZ", "EQUITY", "BUY", 1_000.0)],
                ProposedOrderSpec("XYZ", "CRYPTO", "SELL", 500.0),
            )

    def test_invalid_engine_configuration_raises(self):
        for kwargs in (
            {"max_gross_leverage": 0.0},
            {"max_gross_leverage": -1.0},
            {"max_net_leverage": float("inf")},
            {"asset_class_limits": {}},
            {"asset_class_limits": {"EQUITY": 0.0}},
            {"default_asset_class_limit": -2.0},
        ):
            with self.subTest(**kwargs):
                with self.assertRaises(ValueError):
                    LeverageLimitEnforcerEngine(**kwargs)

    # ------------------------------------------------------------------
    # Unconfigured asset classes
    # ------------------------------------------------------------------
    def test_unconfigured_asset_class_fails_closed(self):
        order = ProposedOrderSpec("XAUUSD", "METALS", "BUY", 50_000.0)
        report = self.engine.audit_proposed_order(100_000.0, [], order)

        self.assertEqual(report.status, STATUS_REJECTED_UNKNOWN_ASSET_CLASS)
        self.assertFalse(report.is_approved)
        self.assertFalse(report.is_asset_class_limit_passed)

    def test_reducing_order_in_unconfigured_asset_class_is_permitted(self):
        # An unenforceable cap must not trap exposure that is already on the
        # book: opening is rejected, but closing and partial reductions are not.
        positions = [PositionSpec("XAUUSD", "METALS", "BUY", 50_000.0)]
        for size in (50_000.0, 10_000.0):
            with self.subTest(size=size):
                report = self.engine.audit_proposed_order(
                    100_000.0,
                    positions,
                    ProposedOrderSpec("XAUUSD", "METALS", "SELL", size),
                )
                self.assertEqual(report.status, STATUS_APPROVED_RISK_REDUCING)
                self.assertTrue(report.is_approved)

        opening = self.engine.audit_proposed_order(
            100_000.0, positions, ProposedOrderSpec("XAUUSD", "METALS", "BUY", 1_000.0)
        )
        self.assertEqual(opening.status, STATUS_REJECTED_UNKNOWN_ASSET_CLASS)
        self.assertEqual(opening.order_asset_class_limit, 0.0)

    def test_explicit_default_asset_class_limit_is_applied(self):
        engine = LeverageLimitEnforcerEngine(
            max_gross_leverage=5.0, max_net_leverage=5.0,
            asset_class_limits={"EQUITY": 2.0},
            default_asset_class_limit=1.0,
        )
        approved = engine.audit_proposed_order(
            100_000.0, [], ProposedOrderSpec("XAUUSD", "METALS", "BUY", 90_000.0)
        )
        self.assertEqual(approved.status, STATUS_APPROVED)
        self.assertEqual(approved.order_asset_class_limit, 1.0)

        vetoed = engine.audit_proposed_order(
            100_000.0, [], ProposedOrderSpec("XAUUSD", "METALS", "BUY", 150_000.0)
        )
        self.assertEqual(vetoed.status, STATUS_REJECTED_ASSET_CLASS)

    # ------------------------------------------------------------------
    # Delta conversion for non-linear instruments
    # ------------------------------------------------------------------
    def test_long_put_contributes_short_underlying_exposure(self):
        # $100k SPY long plus a long put on $100k of underlying at delta -0.40
        # => underlying-equivalent exposures +100k and -40k.
        # Gross = 140k (1.4x), Net = |100k - 40k| = 60k (0.6x).
        engine = LeverageLimitEnforcerEngine(
            max_gross_leverage=3.0, max_net_leverage=1.5,
            asset_class_limits={"EQUITY": 3.0},
        )
        positions = [PositionSpec("SPY", "EQUITY", "BUY", 100_000.0)]
        order = ProposedOrderSpec(
            "SPY_20261218_P600", "EQUITY", "BUY", 100_000.0, exposure_delta=-0.40
        )
        report = engine.audit_proposed_order(100_000.0, positions, order)

        self.assertAlmostEqual(report.projected_gross_leverage, 1.4, places=6)
        self.assertAlmostEqual(report.projected_net_leverage, 0.6, places=6)
        self.assertEqual(report.status, STATUS_APPROVED)

    def test_short_call_contributes_short_underlying_exposure(self):
        # Sold call on $200k of underlying at delta 0.55 => -110k exposure.
        gross, net, _ = self.engine.compute_exposures([
            PositionSpec("AAPL_C", "EQUITY", "SELL", 200_000.0, exposure_delta=0.55)
        ])
        self.assertAlmostEqual(gross, 110_000.0)
        self.assertAlmostEqual(net, 110_000.0)

    def test_premium_sized_option_would_understate_exposure(self):
        # Regression guard for the documented pitfall: pricing the option at
        # its premium ($5k) instead of delta-adjusted underlying notional
        # ($40k) understates the leverage measure eight-fold.
        premium_view, _, _ = self.engine.compute_exposures([
            PositionSpec("AAPL_C", "EQUITY", "BUY", 5_000.0)
        ])
        delta_view, _, _ = self.engine.compute_exposures([
            PositionSpec("AAPL_C", "EQUITY", "BUY", 100_000.0, exposure_delta=0.40)
        ])
        self.assertAlmostEqual(premium_view, 5_000.0)
        self.assertAlmostEqual(delta_view, 40_000.0)

    # ------------------------------------------------------------------
    # Reporting and invariants
    # ------------------------------------------------------------------
    def test_all_asset_class_leverages_are_reported(self):
        positions = [
            PositionSpec("AAPL", "EQUITY", "BUY", 100_000.0),
            PositionSpec("EURUSD", "FX", "SELL", 80_000.0),
        ]
        order = ProposedOrderSpec("BTC-PERP", "CRYPTO", "BUY", 50_000.0)
        report = self.engine.audit_proposed_order(100_000.0, positions, order)

        self.assertEqual(
            report.projected_asset_class_leverages,
            {"EQUITY": 1.0, "FX": 0.8, "CRYPTO": 0.5},
        )
        self.assertIsInstance(report, LeverageEnforcementReport)

    def test_net_leverage_never_exceeds_gross_leverage(self):
        books = [
            [PositionSpec("A", "EQUITY", "BUY", 10_000.0)],
            [
                PositionSpec("A", "EQUITY", "BUY", 10_000.0),
                PositionSpec("B", "EQUITY", "SELL", 7_500.0),
            ],
            [
                PositionSpec("A", "EQUITY", "SELL", 90_000.0),
                PositionSpec("B", "FX", "SELL", 120_000.0),
                PositionSpec("C", "CRYPTO", "BUY", 33_333.0),
            ],
        ]
        for index, book in enumerate(books):
            with self.subTest(book=index):
                gross, net, _ = self.engine.compute_exposures(book)
                self.assertLessEqual(net, gross + 1e-9)

    def test_empty_book_opening_order(self):
        report = self.engine.audit_proposed_order(
            100_000.0, [], ProposedOrderSpec("AAPL", "EQUITY", "BUY", 150_000.0)
        )
        self.assertEqual(report.current_gross_leverage, 0.0)
        self.assertEqual(report.projected_gross_leverage, 1.5)
        self.assertEqual(report.status, STATUS_APPROVED)


if __name__ == "__main__":
    unittest.main()
