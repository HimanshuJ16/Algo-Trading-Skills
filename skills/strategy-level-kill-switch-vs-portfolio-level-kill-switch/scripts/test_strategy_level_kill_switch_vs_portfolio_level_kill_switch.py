import logging
import threading
import unittest

from strategy_level_kill_switch_vs_portfolio_level_kill_switch import (
    Config, Engine,
    HierarchicalKillSwitchEngine, StrategyState, PortfolioState,
    KillSwitchScope, KillSwitchAction, KillSwitchReason, KillSwitchExecutionReport
)


def setUpModule():
    # Every trip logs at CRITICAL by design; silence it so the suite output shows results
    # rather than a wall of emergency notices.
    logging.disable(logging.CRITICAL)


def tearDownModule():
    logging.disable(logging.NOTSET)


class _FakeClock:
    """Injectable clock so cooldown behaviour is testable without sleeping."""

    def __init__(self, now: float = 1_700_000_000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class TestEngineLegacy(unittest.TestCase):
    def test_init(self):
        config = Config(name="test")
        engine = Engine(config)
        self.assertEqual(engine.config.name, "test")

    def test_run(self):
        config = Config(name="test")
        engine = Engine(config)
        self.assertTrue(engine.run())


class _EngineFixture(unittest.TestCase):
    """Three $100,000 strategies inside a $300,000 fund, 10% / 15% / 2-strategy limits."""

    def setUp(self):
        self.clock = _FakeClock()
        self.s1 = StrategyState("STAT_ARB", peak_equity_usd=100_000.0, current_equity_usd=100_000.0, drawdown_limit_pct=10.0)
        self.s2 = StrategyState("MOMENTUM", peak_equity_usd=100_000.0, current_equity_usd=100_000.0, drawdown_limit_pct=10.0)
        self.s3 = StrategyState("MEAN_REVERSION", peak_equity_usd=100_000.0, current_equity_usd=100_000.0, drawdown_limit_pct=10.0)

        self.pstate = PortfolioState(total_peak_equity_usd=300_000.0, total_current_equity_usd=300_000.0, portfolio_drawdown_limit_pct=15.0, max_tripped_strategies_limit=2)

        self.engine = HierarchicalKillSwitchEngine(
            portfolio_state=self.pstate,
            strategies=[self.s1, self.s2, self.s3],
            authorized_operators=("risk.officer",),
            clock=self.clock,
        )


class TestHierarchicalKillSwitchEngineAdvanced(_EngineFixture):

    def test_strategy_level_kill_switch_isolation(self):
        # Drop STAT_ARB equity to $88,000 (12% drawdown > 10% limit)
        report = self.engine.evaluate_strategy_kill_switch("STAT_ARB", 88_000.0, action=KillSwitchAction.HARD_LIQUIDATE)

        self.assertTrue(report.is_triggered)
        self.assertEqual(report.scope, KillSwitchScope.STRATEGY_LEVEL)
        self.assertEqual(report.affected_strategies, ["STAT_ARB"])
        self.assertTrue(self.s1.is_tripped)
        # Verify other strategies remain untripped
        self.assertFalse(self.s2.is_tripped)

    def test_master_portfolio_kill_switch_drawdown_trigger(self):
        # Drop total portfolio equity from $300,000 to $240,000 (20% drawdown > 15% limit)
        report = self.engine.evaluate_portfolio_kill_switch(240_000.0, action=KillSwitchAction.HARD_LIQUIDATE)

        self.assertTrue(report.is_triggered)
        self.assertEqual(report.scope, KillSwitchScope.PORTFOLIO_LEVEL)
        self.assertEqual(len(report.affected_strategies), 3)  # All 3 strategies halted!
        self.assertTrue(self.pstate.is_portfolio_tripped)

    def test_healthy_strategy_reports_no_action_not_soft_halt(self):
        # A caller reading report.action without checking is_triggered must not halt a
        # strategy that never breached. Pre-2.0.0 this returned SOFT_HALT.
        report = self.engine.evaluate_strategy_kill_switch("MOMENTUM", 98_000.0)
        self.assertFalse(report.is_triggered)
        self.assertFalse(report.is_trading_halted)
        self.assertEqual(report.action, KillSwitchAction.NO_ACTION)
        self.assertEqual(report.reason_code, KillSwitchReason.NO_BREACH.value)
        self.assertEqual(report.drawdown_pct, 2.0)


class TestLatching(_EngineFixture):

    def test_strategy_trip_latches_through_equity_recovery(self):
        self.engine.evaluate_strategy_kill_switch("STAT_ARB", 88_000.0)
        # Equity fully recovers to the high-water mark: drawdown is now 0%.
        report = self.engine.evaluate_strategy_kill_switch("STAT_ARB", 100_000.0)

        self.assertEqual(report.drawdown_pct, 0.0)
        self.assertTrue(report.is_triggered)          # latch survives the recovery
        self.assertTrue(report.is_trading_halted)
        self.assertTrue(report.is_latched)
        self.assertFalse(report.is_newly_tripped)
        self.assertEqual(report.reason_code, KillSwitchReason.LATCHED_PRIOR_TRIP.value)
        self.assertTrue(self.s1.is_tripped)

    def test_portfolio_trip_latches_through_equity_recovery(self):
        self.engine.evaluate_portfolio_kill_switch(240_000.0)
        report = self.engine.evaluate_portfolio_kill_switch(300_000.0)

        self.assertEqual(report.drawdown_pct, 0.0)
        self.assertTrue(report.is_triggered)
        self.assertTrue(report.is_latched)
        self.assertFalse(report.is_newly_tripped)
        self.assertEqual(report.affected_strategies, [])

    def test_liquidation_dispatches_exactly_once_per_strategy_trip(self):
        reports = [
            self.engine.evaluate_strategy_kill_switch("STAT_ARB", 88_000.0)
            for _ in range(5)
        ]
        self.assertEqual(sum(1 for r in reports if r.is_newly_tripped), 1)
        self.assertEqual(sum(len(r.affected_strategies) for r in reports), 1)
        self.assertEqual(reports[0].action, KillSwitchAction.HARD_LIQUIDATE)
        self.assertTrue(all(r.action == KillSwitchAction.NO_ACTION for r in reports[1:]))

    def test_liquidation_dispatches_exactly_once_per_portfolio_trip(self):
        reports = [self.engine.evaluate_portfolio_kill_switch(240_000.0) for _ in range(4)]
        self.assertEqual(sum(1 for r in reports if r.is_newly_tripped), 1)
        self.assertEqual(sum(len(r.affected_strategies) for r in reports), 3)

    def test_concurrent_breaching_evaluations_trip_once(self):
        barrier = threading.Barrier(8)
        results = []
        lock = threading.Lock()

        def worker():
            barrier.wait()
            report = self.engine.evaluate_portfolio_kill_switch(240_000.0)
            with lock:
                results.append(report)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(sum(1 for r in results if r.is_newly_tripped), 1)
        self.assertEqual(sum(len(r.affected_strategies) for r in results), 3)


class TestFailClosed(_EngineFixture):

    def test_nan_strategy_equity_halts_instead_of_reporting_healthy(self):
        report = self.engine.evaluate_strategy_kill_switch("STAT_ARB", float("nan"))

        self.assertEqual(report.reason_code, KillSwitchReason.HALTED_INVALID_INPUT.value)
        self.assertTrue(report.is_trading_halted)
        self.assertTrue(self.s1.is_tripped)
        # A halt blocks new risk but must not market-flatten on data it could not evaluate.
        self.assertEqual(report.affected_strategies, [])
        self.assertEqual(report.action, KillSwitchAction.NO_ACTION)

    def test_inf_and_non_numeric_strategy_equity_halt(self):
        for bad in (float("inf"), float("-inf"), None, "88000"):
            with self.subTest(bad=bad):
                engine = HierarchicalKillSwitchEngine(
                    portfolio_state=PortfolioState(300_000.0, 300_000.0),
                    strategies=[StrategyState("S", 100_000.0, 100_000.0)],
                    clock=_FakeClock(),
                )
                report = engine.evaluate_strategy_kill_switch("S", bad)
                self.assertEqual(
                    report.reason_code, KillSwitchReason.HALTED_INVALID_INPUT.value
                )

    def test_non_positive_peak_equity_halts_instead_of_reporting_zero_drawdown(self):
        engine = HierarchicalKillSwitchEngine(
            portfolio_state=PortfolioState(300_000.0, 300_000.0),
            strategies=[StrategyState("ZERO_PEAK", peak_equity_usd=0.0, current_equity_usd=-500.0)],
            clock=_FakeClock(),
        )
        report = engine.evaluate_strategy_kill_switch("ZERO_PEAK", -500.0)
        self.assertEqual(report.reason_code, KillSwitchReason.HALTED_INVALID_INPUT.value)
        self.assertTrue(report.is_trading_halted)

    def test_nan_portfolio_equity_halts_without_liquidating_anything(self):
        report = self.engine.evaluate_portfolio_kill_switch(float("nan"))

        self.assertEqual(report.reason_code, KillSwitchReason.HALTED_INVALID_INPUT.value)
        self.assertTrue(report.is_trading_halted)
        self.assertTrue(self.pstate.is_portfolio_tripped)
        self.assertEqual(report.affected_strategies, [])
        # No fan-out: the engine has no evidence any strategy is actually down.
        self.assertEqual(self.engine.tripped_strategy_ids, [])

    def test_capital_flow_overflow_halts_without_poisoning_the_peak(self):
        # Both inputs are individually finite; their difference is not. The peak must not be
        # ratcheted to infinity, or every later evaluation halts forever.
        report = self.engine.evaluate_strategy_kill_switch(
            "STAT_ARB", 1e308, capital_flow_usd=-1e308
        )
        self.assertEqual(report.reason_code, KillSwitchReason.HALTED_INVALID_INPUT.value)
        self.assertEqual(self.s1.peak_equity_usd, 100_000.0)

    def test_portfolio_capital_flow_overflow_halts_without_poisoning_the_peak(self):
        report = self.engine.evaluate_portfolio_kill_switch(1e308, capital_flow_usd=-1e308)
        self.assertEqual(report.reason_code, KillSwitchReason.HALTED_INVALID_INPUT.value)
        self.assertEqual(self.pstate.total_peak_equity_usd, 300_000.0)

    def test_non_finite_drawdown_halts_rather_than_reporting_an_infinity(self):
        engine = HierarchicalKillSwitchEngine(
            portfolio_state=PortfolioState(300_000.0, 300_000.0),
            strategies=[StrategyState("TINY_PEAK", peak_equity_usd=1e-300, current_equity_usd=1e-300)],
            clock=_FakeClock(),
        )
        report = engine.evaluate_strategy_kill_switch("TINY_PEAK", -1e300)
        self.assertEqual(report.reason_code, KillSwitchReason.HALTED_INVALID_INPUT.value)
        self.assertEqual(report.drawdown_pct, 0.0)

    def test_data_outage_halts_do_not_cascade_into_a_fund_liquidation(self):
        # max_tripped_strategies_limit is 2. A shared feed outage halts every strategy at
        # once; if halts counted as cascade failures this would liquidate a fund that never
        # lost a cent.
        for sid in ("STAT_ARB", "MOMENTUM", "MEAN_REVERSION"):
            self.engine.evaluate_strategy_kill_switch(sid, float("nan"))

        self.assertEqual(len(self.engine.tripped_strategy_ids), 3)
        self.assertEqual(self.engine.cascade_trip_count, 0)

        report = self.engine.evaluate_portfolio_kill_switch(300_000.0)
        self.assertFalse(report.is_triggered)
        self.assertFalse(self.pstate.is_portfolio_tripped)


class TestHierarchyPropagation(_EngineFixture):

    def test_portfolio_halt_is_inherited_by_a_healthy_strategy(self):
        # A fail-closed portfolio halt latches the fund without fanning out to strategies,
        # so MOMENTUM is untripped yet must not be allowed to trade.
        self.engine.evaluate_portfolio_kill_switch(float("nan"))
        report = self.engine.evaluate_strategy_kill_switch("MOMENTUM", 100_000.0)

        self.assertFalse(report.is_triggered)          # the strategy itself never breached
        self.assertTrue(report.is_trading_halted)      # ...but the fund is halted
        self.assertEqual(report.reason_code, KillSwitchReason.PORTFOLIO_HALT_INHERITED.value)
        self.assertTrue(self.engine.is_strategy_trading_halted("MOMENTUM"))

    def test_strategy_trip_does_not_halt_siblings(self):
        self.engine.evaluate_strategy_kill_switch("STAT_ARB", 80_000.0)
        for sid in ("MOMENTUM", "MEAN_REVERSION"):
            report = self.engine.evaluate_strategy_kill_switch(sid, 99_000.0)
            self.assertFalse(report.is_trading_halted, sid)
        self.assertFalse(self.engine.is_portfolio_halted)

    def test_portfolio_fanout_preserves_an_existing_strategy_audit_record(self):
        self.engine.evaluate_strategy_kill_switch(
            "STAT_ARB", 88_000.0, action=KillSwitchAction.SOFT_HALT
        )
        self.clock.advance(60.0)
        report = self.engine.evaluate_portfolio_kill_switch(
            240_000.0, action=KillSwitchAction.HARD_LIQUIDATE
        )

        # STAT_ARB keeps its own action and originating scope, and is not queued for a
        # second liquidation.
        self.assertEqual(self.s1.action_taken, KillSwitchAction.SOFT_HALT)
        self.assertEqual(self.s1.tripped_by_scope, KillSwitchScope.STRATEGY_LEVEL)
        self.assertNotIn("STAT_ARB", report.affected_strategies)
        self.assertEqual(sorted(report.affected_strategies), ["MEAN_REVERSION", "MOMENTUM"])
        # The fan-out records a trip time; without one the cooldown gate cannot be evaluated.
        self.assertEqual(self.s2.tripped_time_epoch, self.clock.now)
        self.assertEqual(self.s2.tripped_by_scope, KillSwitchScope.PORTFOLIO_LEVEL)


class TestCascadeTrigger(_EngineFixture):

    def test_cascade_of_own_drawdown_trips_fires_the_master_switch(self):
        self.engine.evaluate_strategy_kill_switch("STAT_ARB", 88_000.0)
        self.engine.evaluate_strategy_kill_switch("MOMENTUM", 85_000.0)
        self.assertEqual(self.engine.cascade_trip_count, 2)

        # Fund equity is fine (2.0% drawdown vs a 15% limit) - only the cascade fires.
        report = self.engine.evaluate_portfolio_kill_switch(294_000.0)
        self.assertTrue(report.is_newly_tripped)
        self.assertEqual(report.reason_code, KillSwitchReason.CASCADE_BREACH.value)
        self.assertEqual(report.drawdown_pct, 2.0)
        self.assertEqual(report.affected_strategies, ["MEAN_REVERSION"])

    def test_portfolio_fanout_does_not_feed_the_cascade_counter(self):
        # The master switch marks every strategy tripped. If those counted, the cascade
        # trigger would permanently re-justify itself and re-trip the instant it was cleared.
        self.engine.evaluate_portfolio_kill_switch(240_000.0)
        self.assertEqual(len(self.engine.tripped_strategy_ids), 3)
        self.assertEqual(self.engine.cascade_trip_count, 0)

        self.clock.advance(86_400.0)
        self.assertTrue(self.engine.human_re_enable(
            KillSwitchScope.PORTFOLIO_LEVEL, "risk.officer", "Reviewed and re-baselined."
        ))
        self.pstate.total_peak_equity_usd = 240_000.0
        report = self.engine.evaluate_portfolio_kill_switch(240_000.0)
        self.assertFalse(report.is_triggered)


class TestThresholdAndArithmetic(_EngineFixture):

    def test_drawdown_exactly_at_the_limit_breaches(self):
        report = self.engine.evaluate_strategy_kill_switch("STAT_ARB", 90_000.0)
        self.assertEqual(report.drawdown_pct, 10.0)
        self.assertTrue(report.is_newly_tripped)

    def test_drawdown_just_below_the_limit_does_not_breach_despite_rounding_to_the_limit(self):
        # 9.9996% rounds to 10.00 for the report, but the breach decision uses the
        # unrounded value.
        report = self.engine.evaluate_strategy_kill_switch("STAT_ARB", 90_000.4)
        self.assertEqual(report.drawdown_pct, 10.0)
        self.assertFalse(report.is_triggered)
        self.assertFalse(self.s1.is_tripped)

    def test_peak_equity_ratchets_upward(self):
        self.engine.evaluate_strategy_kill_switch("STAT_ARB", 120_000.0)
        self.assertEqual(self.s1.peak_equity_usd, 120_000.0)
        # 120,000 -> 108,000 is exactly 10% off the new high-water mark.
        report = self.engine.evaluate_strategy_kill_switch("STAT_ARB", 108_000.0)
        self.assertEqual(report.drawdown_pct, 10.0)
        self.assertTrue(report.is_newly_tripped)

    def test_settled_withdrawal_is_not_read_as_drawdown(self):
        # $20,000 withdrawn from a flat $100,000 strategy: equity 80,000, but no loss.
        report = self.engine.evaluate_strategy_kill_switch(
            "STAT_ARB", 80_000.0, capital_flow_usd=-20_000.0
        )
        self.assertEqual(report.drawdown_pct, 0.0)
        self.assertFalse(report.is_triggered)
        self.assertEqual(self.s1.peak_equity_usd, 100_000.0)

    def test_settled_deposit_does_not_ratchet_the_peak_or_mask_a_loss(self):
        # $50,000 deposited, then a genuine $12,000 loss: equity 138,000.
        report = self.engine.evaluate_strategy_kill_switch(
            "STAT_ARB", 138_000.0, capital_flow_usd=50_000.0
        )
        self.assertEqual(self.s1.peak_equity_usd, 100_000.0)
        self.assertEqual(report.drawdown_pct, 12.0)
        self.assertTrue(report.is_newly_tripped)

    def test_equity_above_peak_reports_zero_drawdown_not_a_negative(self):
        report = self.engine.evaluate_strategy_kill_switch("MOMENTUM", 130_000.0)
        self.assertEqual(report.drawdown_pct, 0.0)

    def test_portfolio_withdrawal_is_not_read_as_drawdown(self):
        report = self.engine.evaluate_portfolio_kill_switch(
            240_000.0, capital_flow_usd=-60_000.0
        )
        self.assertEqual(report.drawdown_pct, 0.0)
        self.assertFalse(report.is_triggered)


class TestConstructionValidation(unittest.TestCase):

    def _strategies(self, **kwargs):
        return [StrategyState("S1", 100_000.0, 100_000.0, **kwargs)]

    def test_out_of_range_strategy_limit_raises(self):
        for bad in (0.0, -5.0, 150.0, float("nan"), float("inf"), None):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    HierarchicalKillSwitchEngine(
                        portfolio_state=PortfolioState(300_000.0, 300_000.0),
                        strategies=self._strategies(drawdown_limit_pct=bad),
                    )

    def test_out_of_range_portfolio_limit_raises(self):
        with self.assertRaises(ValueError):
            HierarchicalKillSwitchEngine(
                portfolio_state=PortfolioState(
                    300_000.0, 300_000.0, portfolio_drawdown_limit_pct=0.0
                ),
                strategies=self._strategies(),
            )

    def test_invalid_cascade_limit_raises(self):
        for bad in (0, -1, 2.5, True, "3"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    HierarchicalKillSwitchEngine(
                        portfolio_state=PortfolioState(
                            300_000.0, 300_000.0, max_tripped_strategies_limit=bad
                        ),
                        strategies=self._strategies(),
                    )

    def test_duplicate_strategy_ids_raise(self):
        with self.assertRaises(ValueError):
            HierarchicalKillSwitchEngine(
                portfolio_state=PortfolioState(300_000.0, 300_000.0),
                strategies=[
                    StrategyState("S1", 100_000.0, 100_000.0),
                    StrategyState("S1", 50_000.0, 50_000.0),
                ],
            )

    def test_negative_cooldown_raises(self):
        with self.assertRaises(ValueError):
            HierarchicalKillSwitchEngine(
                portfolio_state=PortfolioState(300_000.0, 300_000.0),
                strategies=self._strategies(),
                cooldown_seconds=-1.0,
            )

    def test_sub_one_percent_limit_warns_about_the_fraction_trap(self):
        logging.disable(logging.NOTSET)
        self.addCleanup(logging.disable, logging.CRITICAL)
        with self.assertLogs(
            "strategy_level_kill_switch_vs_portfolio_level_kill_switch", level="WARNING"
        ) as captured:
            HierarchicalKillSwitchEngine(
                portfolio_state=PortfolioState(300_000.0, 300_000.0),
                strategies=self._strategies(drawdown_limit_pct=0.10),
            )
        self.assertTrue(any("fraction" in line for line in captured.output))

    def test_unreachable_cascade_limit_warns(self):
        logging.disable(logging.NOTSET)
        self.addCleanup(logging.disable, logging.CRITICAL)
        with self.assertLogs(
            "strategy_level_kill_switch_vs_portfolio_level_kill_switch", level="WARNING"
        ) as captured:
            HierarchicalKillSwitchEngine(
                portfolio_state=PortfolioState(
                    300_000.0, 300_000.0, max_tripped_strategies_limit=5
                ),
                strategies=self._strategies(),
            )
        self.assertTrue(any("can never fire" in line for line in captured.output))


class TestCallValidation(_EngineFixture):

    def test_unknown_strategy_raises(self):
        with self.assertRaises(ValueError):
            self.engine.evaluate_strategy_kill_switch("NOT_A_STRATEGY", 90_000.0)

    def test_no_action_as_a_trip_action_raises(self):
        # A kill switch that halts nothing must never look like a successful kill.
        with self.assertRaises(ValueError):
            self.engine.evaluate_strategy_kill_switch(
                "STAT_ARB", 80_000.0, action=KillSwitchAction.NO_ACTION
            )
        with self.assertRaises(ValueError):
            self.engine.evaluate_portfolio_kill_switch(
                240_000.0, action=KillSwitchAction.NO_ACTION
            )
        self.assertFalse(self.s1.is_tripped)
        self.assertFalse(self.pstate.is_portfolio_tripped)

    def test_unknown_action_raises(self):
        for bad in ("FLATTEN", None, 7, ["HARD_LIQUIDATE"]):
            with self.subTest(bad=bad):
                # An unhashable action raises TypeError from the enum lookup; it must still
                # surface as ValueError rather than crashing the risk loop.
                with self.assertRaises(ValueError):
                    self.engine.evaluate_strategy_kill_switch("STAT_ARB", 80_000.0, action=bad)


class TestHumanReEnable(_EngineFixture):

    def _trip_strategy(self):
        self.engine.evaluate_strategy_kill_switch("STAT_ARB", 88_000.0)

    def test_blank_identity_and_blank_reason_are_refused_and_audited(self):
        self._trip_strategy()
        self.clock.advance(86_400.0)
        self.assertFalse(self.engine.human_re_enable(
            KillSwitchScope.STRATEGY_LEVEL, "   ", "Reviewed.", strategy_id="STAT_ARB"))
        self.assertFalse(self.engine.human_re_enable(
            KillSwitchScope.STRATEGY_LEVEL, "risk.officer", "", strategy_id="STAT_ARB"))
        self.assertEqual(len(self.engine.re_enable_log), 2)
        self.assertTrue(all(not e.granted for e in self.engine.re_enable_log))
        self.assertTrue(self.s1.is_tripped)

    def test_unlisted_operator_is_refused(self):
        self._trip_strategy()
        self.clock.advance(86_400.0)
        self.assertFalse(self.engine.human_re_enable(
            KillSwitchScope.STRATEGY_LEVEL, "intern", "Looks fine.", strategy_id="STAT_ARB"))
        self.assertIn("authorized_operators", self.engine.re_enable_log[-1].rejection_reason)

    def test_cooldown_gates_the_re_enable(self):
        self._trip_strategy()
        self.clock.advance(3_600.0)
        self.assertFalse(self.engine.human_re_enable(
            KillSwitchScope.STRATEGY_LEVEL, "risk.officer", "Reviewed.",
            strategy_id="STAT_ARB"))
        self.assertIn("Cooldown", self.engine.re_enable_log[-1].rejection_reason)

        self.clock.advance(86_400.0 - 3_600.0)
        self.assertTrue(self.engine.human_re_enable(
            KillSwitchScope.STRATEGY_LEVEL, "risk.officer", "Reviewed.",
            strategy_id="STAT_ARB"))
        self.assertFalse(self.s1.is_tripped)
        self.assertTrue(self.engine.re_enable_log[-1].granted)

    def test_re_enable_requires_a_strategy_id_at_strategy_scope(self):
        self._trip_strategy()
        self.clock.advance(86_400.0)
        self.assertFalse(self.engine.human_re_enable(
            KillSwitchScope.STRATEGY_LEVEL, "risk.officer", "Reviewed."))

    def test_re_enable_of_an_untripped_scope_is_refused(self):
        self.assertFalse(self.engine.human_re_enable(
            KillSwitchScope.PORTFOLIO_LEVEL, "risk.officer", "Nothing wrong."))
        self.assertFalse(self.engine.human_re_enable(
            KillSwitchScope.STRATEGY_LEVEL, "risk.officer", "Nothing wrong.",
            strategy_id="MOMENTUM"))

    def test_unknown_scope_is_refused_and_audited(self):
        # Including an unhashable scope, which raises TypeError from the enum lookup.
        for bad_scope in ("GLOBAL", None, ["PORTFOLIO_LEVEL"]):
            with self.subTest(bad_scope=bad_scope):
                self.assertFalse(self.engine.human_re_enable(
                    bad_scope, "risk.officer", "Reviewed."))
        self.assertEqual(len(self.engine.re_enable_log), 3)

    def test_strategy_latch_clears_while_the_fund_is_halted_without_resuming_trading(self):
        # Recovery runs strategies-first. Clearing one latch inside a halted fund is safe
        # because the inherited portfolio halt still gates the strategy.
        self._trip_strategy()
        self.engine.evaluate_portfolio_kill_switch(240_000.0)
        self.clock.advance(86_400.0)

        self.assertTrue(self.engine.human_re_enable(
            KillSwitchScope.STRATEGY_LEVEL, "risk.officer", "STAT_ARB reviewed.",
            strategy_id="STAT_ARB"))
        self.assertFalse(self.s1.is_tripped)
        self.assertTrue(self.engine.is_strategy_trading_halted("STAT_ARB"))
        self.s1.peak_equity_usd = 88_000.0
        report = self.engine.evaluate_strategy_kill_switch("STAT_ARB", 88_000.0)
        self.assertFalse(report.is_triggered)
        self.assertTrue(report.is_trading_halted)
        self.assertEqual(report.reason_code, KillSwitchReason.PORTFOLIO_HALT_INHERITED.value)

    def test_portfolio_re_enable_refused_while_the_cascade_condition_still_holds(self):
        # Lifting the master latch here would re-trip the fund on the next evaluation.
        self.engine.evaluate_strategy_kill_switch("STAT_ARB", 88_000.0)
        self.engine.evaluate_strategy_kill_switch("MOMENTUM", 85_000.0)
        self.engine.evaluate_portfolio_kill_switch(294_000.0)
        self.clock.advance(86_400.0)

        self.assertFalse(self.engine.human_re_enable(
            KillSwitchScope.PORTFOLIO_LEVEL, "risk.officer", "Fund reviewed."))
        self.assertIn("cascade", self.engine.re_enable_log[-1].rejection_reason)

        # Clear the two strategy latches first, then the fund re-enables and stays clear.
        for sid, peak in (("STAT_ARB", 88_000.0), ("MOMENTUM", 85_000.0)):
            self.assertTrue(self.engine.human_re_enable(
                KillSwitchScope.STRATEGY_LEVEL, "risk.officer", "Reviewed.",
                strategy_id=sid))
            getattr(self, "s1" if sid == "STAT_ARB" else "s2").peak_equity_usd = peak
        self.assertTrue(self.engine.human_re_enable(
            KillSwitchScope.PORTFOLIO_LEVEL, "risk.officer", "Fund reviewed."))

        self.pstate.total_peak_equity_usd = 294_000.0
        report = self.engine.evaluate_portfolio_kill_switch(294_000.0)
        self.assertFalse(report.is_triggered)

    def test_portfolio_re_enable_releases_only_its_own_fanout(self):
        self.engine.evaluate_strategy_kill_switch("STAT_ARB", 88_000.0)
        self.engine.evaluate_portfolio_kill_switch(240_000.0)
        self.clock.advance(86_400.0)

        self.assertTrue(self.engine.human_re_enable(
            KillSwitchScope.PORTFOLIO_LEVEL, "risk.officer", "Fund reviewed."))
        self.assertFalse(self.pstate.is_portfolio_tripped)
        self.assertFalse(self.s2.is_tripped)
        self.assertFalse(self.s3.is_tripped)
        # STAT_ARB tripped on its own drawdown and needs its own re-enable.
        self.assertTrue(self.s1.is_tripped)
        self.assertEqual(
            sorted(self.engine.re_enable_log[-1].released_strategies),
            ["MEAN_REVERSION", "MOMENTUM"],
        )

    def test_re_enable_clears_the_latch_not_the_breach(self):
        self._trip_strategy()
        self.clock.advance(86_400.0)
        self.assertTrue(self.engine.human_re_enable(
            KillSwitchScope.STRATEGY_LEVEL, "risk.officer", "Reviewed.",
            strategy_id="STAT_ARB"))
        # The high-water mark survives, so the still-breaching equity re-trips immediately.
        report = self.engine.evaluate_strategy_kill_switch("STAT_ARB", 88_000.0)
        self.assertTrue(report.is_newly_tripped)
        self.assertTrue(self.s1.is_tripped)

    def test_deliberate_peak_rebaseline_allows_the_resume_to_hold(self):
        self._trip_strategy()
        self.clock.advance(86_400.0)
        self.engine.human_re_enable(
            KillSwitchScope.STRATEGY_LEVEL, "risk.officer", "Reviewed.",
            strategy_id="STAT_ARB")
        self.s1.peak_equity_usd = 88_000.0        # explicit operator decision
        report = self.engine.evaluate_strategy_kill_switch("STAT_ARB", 88_000.0)
        self.assertFalse(report.is_triggered)


if __name__ == '__main__':
    unittest.main()
