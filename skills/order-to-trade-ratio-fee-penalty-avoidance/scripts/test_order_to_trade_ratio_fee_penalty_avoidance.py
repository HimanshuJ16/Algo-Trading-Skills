import math
import unittest

from order_to_trade_ratio_fee_penalty_avoidance import (
    ACTION_ALLOW,
    ACTION_FREEZE,
    ACTION_THROTTLE,
    NSE_ALGO_OTR_PENALTY_TIERS_2018,
    OTRConvention,
    OTRInstrumentSession,
    OTRThresholdPolicy,
    OrderToTradeRatioFeePenaltyEngine,
    PenaltyTier,
    STATUS_BREACH,
    STATUS_COMPLIANT,
    STATUS_NO_TRANSACTIONS,
    STATUS_WARNING,
    aggregate_worst_instrument,
    tiered_penalty,
    weighted_order_message_count,
)


def _session(**overrides):
    """A valid baseline session; individual tests override only what they exercise."""
    base = dict(
        venue="XEUR",
        instrument_id="DE0001102309",
        session_date="2026-08-27",
        limit_submits=0,
        limit_modifies=0,
        limit_cancels=0,
        transactions=1,
        ordered_volume=1000.0,
        traded_volume=1000.0,
    )
    base.update(overrides)
    return OTRInstrumentSession(**base)


class TestRTS9AnnexCounting(unittest.TestCase):
    """RTS 9 Annex, 'Counting methodology for orders set out for each type'."""

    def test_limit_modify_counts_as_two_orders(self):
        # Annex row 'Limit - modify': 2, because a modification entails a cancellation
        # and a new insertion. Hand count: 100*1 + 50*2 + 30*1 = 230 (a naive
        # one-message-one-order count would give 180).
        session = _session(limit_submits=100, limit_modifies=50, limit_cancels=30)
        self.assertEqual(weighted_order_message_count(session), 230)

    def test_quote_messages_carry_two_and_four_weights(self):
        # Annex: 'Quote' = 2 (one per side), 'Quote - delete' = 2, 'Quote - modify' = 4.
        # Hand count: 10*2 + 5*4 + 10*2 = 60.
        session = _session(quote_submits=10, quote_modifies=5, quote_cancels=10)
        self.assertEqual(weighted_order_message_count(session), 60)

    def test_article_1a_exempt_cancels_are_removed_from_the_count(self):
        # Art. 1(a) excludes cancellations sent subsequent to auction uncrossing, a loss
        # of venue connectivity, or use of a kill functionality.
        # Without exemption: 30*1 + 10*2 = 50. Exempting 10 pooled cancels leaves 30
        # countable cancels; the 10 quote cancels (weight 2) are retained and 20 limit
        # cancels (weight 1) remain -> 20 + 20 = 40.
        session = _session(limit_cancels=30, quote_cancels=10, exempt_cancels=10)
        self.assertEqual(weighted_order_message_count(session), 40)

    def test_exempt_cancels_never_understate_the_count(self):
        # The pooled exemption is resolved in the direction that removes the least
        # weight, so the guard can never under-throttle on the ambiguity.
        exempted = _session(limit_cancels=5, quote_cancels=5, exempt_cancels=5)
        self.assertEqual(weighted_order_message_count(exempted), 10)  # 5 quote cancels

    def test_kill_switch_mass_cancel_does_not_manufacture_a_breach(self):
        # A market maker with a modest ratio that fires its kill switch must not be
        # pushed into breach by the resulting mass cancel.
        policy = OTRThresholdPolicy(
            max_count_otr=30.0,
            max_volume_otr=1_000.0,
            convention=OTRConvention.GROSS_MESSAGES_PER_TRADE,
        )
        engine = OrderToTradeRatioFeePenaltyEngine(policy)
        counted = engine.audit_session_otr(
            _session(limit_submits=100, limit_cancels=200, transactions=10,
                     ordered_volume=1000.0, traded_volume=1000.0, exempt_cancels=200)
        )
        self.assertEqual(counted.total_order_messages, 100)   # cancels excluded
        self.assertEqual(counted.count_otr, 10.0)
        self.assertEqual(counted.status, STATUS_COMPLIANT)

        not_exempt = engine.audit_session_otr(
            _session(limit_submits=100, limit_cancels=200, transactions=10,
                     ordered_volume=1000.0, traded_volume=1000.0)
        )
        self.assertEqual(not_exempt.total_order_messages, 300)
        self.assertEqual(not_exempt.status, STATUS_BREACH)


class TestRatioConventions(unittest.TestCase):

    def test_rts9_ratio_subtracts_one(self):
        # RTS 9 Art. 3(1)(b): (total number of orders / total number of transactions) - 1.
        # 400 messages over 20 transactions -> gross 20.0 -> RTS 9 ratio 19.0.
        # The limit of 19.5 is deliberately between the two: it separates the RTS 9
        # convention (compliant-with-warning) from the gross convention (breach).
        policy = OTRThresholdPolicy(max_count_otr=19.5, max_volume_otr=100.0)
        engine = OrderToTradeRatioFeePenaltyEngine(policy)
        report = engine.audit_session_otr(
            _session(limit_submits=400, transactions=20,
                     ordered_volume=2000.0, traded_volume=2000.0)
        )
        self.assertEqual(report.convention, OTRConvention.RTS9_UNEXECUTED)
        self.assertEqual(report.count_otr, 19.0)
        self.assertEqual(report.status, STATUS_WARNING)

    def test_gross_convention_on_identical_activity_breaches(self):
        policy = OTRThresholdPolicy(
            max_count_otr=19.5,
            max_volume_otr=100.0,
            convention=OTRConvention.GROSS_MESSAGES_PER_TRADE,
        )
        engine = OrderToTradeRatioFeePenaltyEngine(policy)
        report = engine.audit_session_otr(
            _session(limit_submits=400, transactions=20,
                     ordered_volume=2000.0, traded_volume=2000.0)
        )
        self.assertEqual(report.count_otr, 20.0)
        self.assertEqual(report.status, STATUS_BREACH)

    def test_rts9_volume_ratio_is_fully_executed_at_zero(self):
        # A member whose every ordered lot executes scores 0.0 under RTS 9, not 1.0.
        policy = OTRThresholdPolicy(max_count_otr=100.0, max_volume_otr=5.0)
        engine = OrderToTradeRatioFeePenaltyEngine(policy)
        report = engine.audit_session_otr(
            _session(limit_submits=10, transactions=10,
                     ordered_volume=500.0, traded_volume=500.0)
        )
        self.assertEqual(report.volume_otr, 0.0)


class TestBreachAndThrottleStates(unittest.TestCase):

    def setUp(self):
        self.policy = OTRThresholdPolicy(
            max_count_otr=50.0,
            max_volume_otr=1_000.0,
            convention=OTRConvention.GROSS_MESSAGES_PER_TRADE,
            warning_threshold_pct=0.80,
        )
        self.engine = OrderToTradeRatioFeePenaltyEngine(self.policy)

    def test_compliant_session_allows_order_flow(self):
        report = self.engine.audit_session_otr(
            _session(limit_submits=200, limit_cancels=100, transactions=20,
                     ordered_volume=10_000.0, traded_volume=2_000.0)
        )
        self.assertEqual(report.count_otr, 15.0)          # (200 + 100) / 20
        self.assertEqual(report.status, STATUS_COMPLIANT)
        self.assertEqual(report.recommended_action, ACTION_ALLOW)
        self.assertEqual(report.binding_ratio, "NONE")
        self.assertEqual(report.excess_messages, 0)

    def test_warning_boundary_is_inclusive(self):
        # 400 messages over 10 transactions = 40.0, exactly 80% of the 50.0 limit.
        report = self.engine.audit_session_otr(
            _session(limit_submits=400, transactions=10,
                     ordered_volume=5_000.0, traded_volume=500.0)
        )
        self.assertEqual(report.count_otr, 40.0)
        self.assertEqual(report.status, STATUS_WARNING)
        self.assertEqual(report.recommended_action, ACTION_THROTTLE)
        self.assertEqual(report.excess_messages, 0)

    def test_breach_boundary_is_inclusive_and_yields_no_excess_yet(self):
        # Exactly at the limit: 500 messages over 10 transactions = 50.0.
        report = self.engine.audit_session_otr(
            _session(limit_submits=500, transactions=10,
                     ordered_volume=5_000.0, traded_volume=500.0)
        )
        self.assertEqual(report.count_otr, 50.0)
        self.assertEqual(report.status, STATUS_BREACH)
        self.assertEqual(report.recommended_action, ACTION_FREEZE)
        self.assertEqual(report.excess_messages, 0)

    def test_volume_ratio_alone_can_force_a_breach(self):
        # RTS 9 Art. 3(2): the limit is exceeded if activity exceeds "either or both" of
        # the two ratios. The count ratio here is comfortably compliant.
        policy = OTRThresholdPolicy(
            max_count_otr=100.0,
            max_volume_otr=10.0,
            convention=OTRConvention.GROSS_MESSAGES_PER_TRADE,
        )
        engine = OrderToTradeRatioFeePenaltyEngine(policy)
        report = engine.audit_session_otr(
            _session(limit_submits=50, transactions=10,
                     ordered_volume=5_000.0, traded_volume=100.0)
        )
        self.assertEqual(report.count_otr, 5.0)
        self.assertEqual(report.volume_otr, 50.0)
        self.assertEqual(report.status, STATUS_BREACH)
        self.assertEqual(report.binding_ratio, "VOLUME")
        # A volume-only breach leaves no excess messages: excess is a count quantity.
        self.assertEqual(report.excess_messages, 0)

    def test_both_ratios_breaching_is_labelled_both(self):
        report = self.engine.audit_session_otr(
            _session(limit_submits=1_000, transactions=10,
                     ordered_volume=50_000.0, traded_volume=10.0)
        )
        self.assertEqual(report.binding_ratio, "BOTH")
        self.assertEqual(report.status, STATUS_BREACH)


class TestZeroTransactionHandling(unittest.TestCase):
    """ICE: 'No OTR ratios will be calculated in case the member has not traded.'"""

    def setUp(self):
        self.engine = OrderToTradeRatioFeePenaltyEngine(
            OTRThresholdPolicy(
                max_count_otr=50.0,
                max_volume_otr=100.0,
                convention=OTRConvention.GROSS_MESSAGES_PER_TRADE,
                penalty_tiers=(PenaltyTier(50.0, None, 0.05),),
            )
        )

    def test_messages_without_transactions_are_not_calculable_and_freeze(self):
        report = self.engine.audit_session_otr(
            _session(limit_submits=1_000, transactions=0,
                     ordered_volume=50_000.0, traded_volume=0.0)
        )
        self.assertEqual(report.status, STATUS_NO_TRANSACTIONS)
        self.assertEqual(report.recommended_action, ACTION_FREEZE)
        self.assertIsNone(report.count_otr)
        self.assertIsNone(report.volume_otr)
        # Regression: substituting max(1, transactions) previously granted a free
        # allowance of 50 messages and reported a fabricated 1000.0 ratio with a
        # $47.50 penalty. Not calculable must not be reported as a measurement.
        self.assertIsNone(report.penalty_fee_accrued)
        self.assertEqual(report.excess_messages, 0)

    def test_idle_instrument_is_compliant(self):
        report = self.engine.audit_session_otr(
            _session(transactions=0, ordered_volume=0.0, traded_volume=0.0)
        )
        self.assertEqual(report.status, STATUS_COMPLIANT)
        self.assertEqual(report.recommended_action, ACTION_ALLOW)
        self.assertEqual(report.total_order_messages, 0)

    def test_tiered_penalty_refuses_zero_transactions(self):
        with self.assertRaises(ValueError):
            tiered_penalty(1_000, 0, (PenaltyTier(50.0, None, 0.05),))


class TestPenaltySchedules(unittest.TestCase):

    def test_single_unbounded_tier_reproduces_the_flat_excess_times_fee_form(self):
        # Eurex ESU form: (transactions - transaction limit) * fee.
        # 1,000 messages, 10 transactions, limit 50 -> allowance 500, excess 500,
        # 500 * 0.05 = 25.00. Hand-computed.
        self.assertAlmostEqual(
            tiered_penalty(1_000, 10, (PenaltyTier(50.0, None, 0.05),)), 25.00, places=6
        )

    def test_rts9_convention_offsets_tier_boundaries_by_one_transaction(self):
        # Under RTS 9 a ratio of 49.0 corresponds to 50 gross messages per transaction,
        # so the bracket opens at (49 + 1) * 10 = 500 messages -- the same 25.00.
        self.assertAlmostEqual(
            tiered_penalty(
                1_000, 10, (PenaltyTier(49.0, None, 0.05),),
                convention=OTRConvention.RTS9_UNEXECUTED,
            ),
            25.00,
            places=6,
        )

    def test_nse_incremental_slabs(self):
        # NSE/SURV/38122: <50 nil; 50-250 at 2 paise; 250-500 at 10 paise; >=500 at
        # 10 paise, charged on an incremental basis.
        # 30,000 algo messages over 100 trades -> daily OTR 300. Hand-computed:
        #   slab 1: min(30000, 25000) - 5000  = 20,000 msgs * 0.02 = INR 400.00
        #   slab 2: min(30000, 50000) - 25000 =  5,000 msgs * 0.10 = INR 500.00
        #   slab 3: 30000 - 50000 < 0                              = INR   0.00
        #                                                    total = INR 900.00
        self.assertAlmostEqual(
            tiered_penalty(30_000, 100, NSE_ALGO_OTR_PENALTY_TIERS_2018), 900.00, places=6
        )

    def test_nse_slabs_charge_nothing_below_the_first_slab(self):
        # 4,000 messages over 100 trades -> OTR 40, below the 50 slab floor.
        self.assertEqual(
            tiered_penalty(4_000, 100, NSE_ALGO_OTR_PENALTY_TIERS_2018), 0.0
        )

    def test_engine_reports_penalty_in_the_configured_currency(self):
        engine = OrderToTradeRatioFeePenaltyEngine(
            OTRThresholdPolicy(
                max_count_otr=50.0,
                max_volume_otr=10_000.0,
                convention=OTRConvention.GROSS_MESSAGES_PER_TRADE,
                penalty_tiers=NSE_ALGO_OTR_PENALTY_TIERS_2018,
                penalty_currency="INR",
            )
        )
        report = engine.audit_session_otr(
            _session(venue="NSE", instrument_id="INE009A01021",
                     limit_submits=30_000, transactions=100,
                     ordered_volume=300_000.0, traded_volume=1_000.0)
        )
        self.assertEqual(report.penalty_currency, "INR")
        self.assertAlmostEqual(report.penalty_fee_accrued, 900.00, places=2)
        self.assertEqual(report.excess_messages, 30_000 - 5_000)

    def test_no_penalty_schedule_configured_accrues_nothing(self):
        engine = OrderToTradeRatioFeePenaltyEngine(
            OTRThresholdPolicy(
                max_count_otr=50.0,
                max_volume_otr=10_000.0,
                convention=OTRConvention.GROSS_MESSAGES_PER_TRADE,
            )
        )
        report = engine.audit_session_otr(
            _session(limit_submits=1_000, transactions=10,
                     ordered_volume=5_000.0, traded_volume=100.0)
        )
        self.assertEqual(report.penalty_fee_accrued, 0.0)
        # But the breach is still reported: an unpriced schedule is not a safe state.
        self.assertEqual(report.status, STATUS_BREACH)


class TestInputValidation(unittest.TestCase):

    def test_negative_counter_rejected(self):
        with self.assertRaises(ValueError):
            _session(limit_submits=-1)

    def test_non_integer_counter_rejected(self):
        with self.assertRaises(TypeError):
            _session(limit_submits=10.5)

    def test_boolean_counter_rejected(self):
        with self.assertRaises(TypeError):
            _session(transactions=True)

    def test_non_finite_volume_rejected(self):
        with self.assertRaises(ValueError):
            _session(ordered_volume=float("nan"))
        with self.assertRaises(ValueError):
            _session(ordered_volume=math.inf)

    def test_traded_volume_above_ordered_volume_rejected(self):
        # A transaction is an executed order, so this can only be a unit mismatch --
        # and it would otherwise produce a spuriously compliant negative RTS 9 ratio.
        with self.assertRaises(ValueError):
            _session(ordered_volume=100.0, traded_volume=500.0)

    def test_transactions_without_traded_volume_rejected(self):
        with self.assertRaises(ValueError):
            _session(transactions=5, ordered_volume=100.0, traded_volume=0.0)

    def test_exempt_cancels_cannot_exceed_cancels_sent(self):
        with self.assertRaises(ValueError):
            _session(limit_cancels=5, exempt_cancels=6)

    def test_blank_instrument_id_rejected(self):
        with self.assertRaises(ValueError):
            _session(instrument_id="   ")

    def test_policy_requires_positive_limits(self):
        with self.assertRaises(ValueError):
            OTRThresholdPolicy(max_count_otr=0.0, max_volume_otr=10.0)
        with self.assertRaises(ValueError):
            OTRThresholdPolicy(max_count_otr=10.0, max_volume_otr=-1.0)

    def test_policy_rejects_out_of_range_warning_threshold(self):
        with self.assertRaises(ValueError):
            OTRThresholdPolicy(max_count_otr=10.0, max_volume_otr=10.0,
                               warning_threshold_pct=1.5)

    def test_policy_rejects_overlapping_penalty_tiers(self):
        with self.assertRaises(ValueError):
            OTRThresholdPolicy(
                max_count_otr=10.0,
                max_volume_otr=10.0,
                penalty_tiers=(PenaltyTier(50.0, 250.0, 0.02),
                               PenaltyTier(100.0, 500.0, 0.10)),
            )

    def test_penalty_tier_rejects_inverted_bounds(self):
        with self.assertRaises(ValueError):
            PenaltyTier(ratio_from=250.0, ratio_to=50.0, fee_per_message=0.02)

    def test_engine_requires_an_explicit_policy(self):
        # There is no venue-agnostic default limit; published limits span from NSE's
        # daily 50 to ICE Futures Europe's 2,500,000 Red Threshold in number terms.
        with self.assertRaises(TypeError):
            OrderToTradeRatioFeePenaltyEngine(None)

    def test_engine_rejects_a_non_session_argument(self):
        engine = OrderToTradeRatioFeePenaltyEngine(
            OTRThresholdPolicy(max_count_otr=50.0, max_volume_otr=100.0)
        )
        with self.assertRaises(TypeError):
            engine.audit_session_otr({"limit_submits": 10})


class TestPerInstrumentAggregation(unittest.TestCase):
    """RTS 9 Art. 3(2) determines breach 'in one specific instrument'."""

    def setUp(self):
        self.engine = OrderToTradeRatioFeePenaltyEngine(
            OTRThresholdPolicy(
                max_count_otr=50.0,
                max_volume_otr=1_000.0,
                convention=OTRConvention.GROSS_MESSAGES_PER_TRADE,
            )
        )

    def test_one_breaching_instrument_is_not_hidden_by_quiet_ones(self):
        # 20 heavily-executing instruments (10 messages, 100 transactions each) plus one
        # instrument quoting 5,000 messages against 10 transactions. A venue-aggregated
        # roll-up would read (20 * 10 + 5,000) / (20 * 100 + 10) = 5,200 / 2,010 = 2.6
        # messages per trade -- comfortably inside the 50.0 limit -- while the venue
        # charges on the NOISY instrument's ratio of 500.0.
        quiet = [
            self.engine.audit_session_otr(
                _session(instrument_id=f"QUIET{i}", limit_submits=10, transactions=100,
                         ordered_volume=1_000.0, traded_volume=1_000.0)
            )
            for i in range(20)
        ]
        noisy = self.engine.audit_session_otr(
            _session(instrument_id="NOISY", limit_submits=5_000, transactions=10,
                     ordered_volume=50_000.0, traded_volume=500.0)
        )
        aggregate_messages = sum(r.total_order_messages for r in quiet + [noisy])
        aggregate_transactions = sum(r.transactions for r in quiet + [noisy])
        self.assertLess(aggregate_messages / aggregate_transactions, 50.0)

        worst = aggregate_worst_instrument(quiet + [noisy])
        self.assertEqual(worst.instrument_id, "NOISY")
        self.assertEqual(worst.count_otr, 500.0)
        self.assertEqual(worst.status, STATUS_BREACH)

    def test_not_calculable_outranks_a_finite_warning(self):
        warned = self.engine.audit_session_otr(
            _session(instrument_id="WARN", limit_submits=400, transactions=10,
                     ordered_volume=1_000.0, traded_volume=100.0)
        )
        dark = self.engine.audit_session_otr(
            _session(instrument_id="DARK", limit_submits=100, transactions=0,
                     ordered_volume=1_000.0, traded_volume=0.0)
        )
        self.assertEqual(warned.status, STATUS_WARNING)
        self.assertEqual(aggregate_worst_instrument([warned, dark]).instrument_id, "DARK")

    def test_empty_input_returns_none(self):
        self.assertIsNone(aggregate_worst_instrument([]))


if __name__ == "__main__":
    unittest.main()
