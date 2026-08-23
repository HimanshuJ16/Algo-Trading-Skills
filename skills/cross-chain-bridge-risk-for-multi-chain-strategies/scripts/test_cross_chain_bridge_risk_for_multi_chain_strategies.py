import unittest
from cross_chain_bridge_risk_for_multi_chain_strategies import (
    CrossChainBridgeRiskManager, BridgeProfile
)

class TestCrossChainBridgeRiskManager(unittest.TestCase):

    def setUp(self):
        # Portfolio NAV = $1,000,000
        self.manager = CrossChainBridgeRiskManager(
            portfolio_nav_usd=1_000_000.0,
            max_depeg_threshold_pct=1.0,
            max_allowed_finality_minutes=120.0
        )

        # Bridge 1 (Stargate-style pool): 15% NAV limit ($150k), 15 mins, in-flight $100k
        self.b1 = BridgeProfile(
            bridge_id="STARGATE_POOL", name="Stargate Finance", bridge_type="LIQUIDITY_POOL",
            finality_delay_minutes=15.0, max_nav_pct_cap=0.15, current_inflight_usd=100_000.0, audit_score_pct=95.0
        )
        # Bridge 2 (Synapse-style pool): 15% NAV limit, 20 mins, in-flight $20k
        self.b2 = BridgeProfile(
            bridge_id="SYNAPSE_POOL", name="Synapse Protocol", bridge_type="LIQUIDITY_POOL",
            finality_delay_minutes=20.0, max_nav_pct_cap=0.15, current_inflight_usd=20_000.0, audit_score_pct=90.0
        )
        # Bridge 3 (Arbitrum canonical): 20% NAV limit, 10,080 mins (7-day L1 withdrawal)
        self.b3 = BridgeProfile(
            bridge_id="ARBITRUM_CANONICAL", name="Arbitrum Rollup Bridge", bridge_type="CANONICAL_ROLLUP",
            finality_delay_minutes=10080.0, max_nav_pct_cap=0.20, current_inflight_usd=0.0, audit_score_pct=99.0
        )

        self.manager.register_bridge(self.b1)
        self.manager.register_bridge(self.b2)
        self.manager.register_bridge(self.b3)

    def test_depeg_blocks_transfer(self):
        # Native ETH = $3,000, wrapped wETH = $2,910 -> exactly 3.0% de-peg >= 1.0%
        decision = self.manager.evaluate_bridge_transfer(
            target_bridge_id="STARGATE_POOL", transfer_amount_usd=20_000.0,
            native_price=3000.0, wrapped_price=2910.0
        )
        self.assertFalse(decision.is_approved)
        # De-peg is systemic: never rerouted around an impaired wrapped asset
        self.assertFalse(decision.is_rerouted)
        self.assertEqual(decision.depeg_pct, 3.0)
        self.assertIn("de-peg", decision.reason.lower())

    def test_depeg_boundary_is_inclusive(self):
        # wrapped = 2970 -> |2970-3000|/3000 = exactly 1.00% -> blocked
        at_threshold = self.manager.evaluate_bridge_transfer(
            "STARGATE_POOL", 20_000.0, native_price=3000.0, wrapped_price=2970.0
        )
        self.assertFalse(at_threshold.is_approved)
        self.assertEqual(at_threshold.depeg_pct, 1.0)
        # wrapped = 2970.3 -> 0.99% -> passes the de-peg gate
        below_threshold = self.manager.evaluate_bridge_transfer(
            "STARGATE_POOL", 20_000.0, native_price=3000.0, wrapped_price=2970.3
        )
        self.assertEqual(below_threshold.depeg_pct, 0.99)
        self.assertTrue(below_threshold.is_approved)

    def test_broken_price_feed_raises_instead_of_all_clear(self):
        # Regression: NaN prices and zero/dead feeds previously returned
        # depeg 0.0 and APPROVED the transfer through the capital gate
        with self.assertRaises(ValueError):
            self.manager.evaluate_bridge_transfer(
                "STARGATE_POOL", 20_000.0, native_price=float("nan"), wrapped_price=float("nan")
            )
        with self.assertRaises(ValueError):
            self.manager.evaluate_bridge_transfer(
                "STARGATE_POOL", 20_000.0, native_price=0.0, wrapped_price=0.0
            )
        with self.assertRaises(ValueError):
            self.manager.calculate_depeg_pct(native_price=-3000.0, wrapped_price=2990.0)
        with self.assertRaises(ValueError):
            self.manager.calculate_depeg_pct(native_price=3000.0, wrapped_price=float("inf"))

    def test_non_positive_transfer_amount_rejected(self):
        # Regression: negative amounts previously reduced projected in-flight
        # and dodged the NAV cap
        with self.assertRaises(ValueError):
            self.manager.evaluate_bridge_transfer(
                "STARGATE_POOL", -50_000.0, native_price=3000.0, wrapped_price=2999.0
            )
        with self.assertRaises(ValueError):
            self.manager.evaluate_bridge_transfer(
                "STARGATE_POOL", 0.0, native_price=3000.0, wrapped_price=2999.0
            )

    def test_cap_breach_reroutes_to_failover(self):
        # Transfer $60k to STARGATE ($100k + $60k = $160k = 16% > 15% cap)
        # -> re-route to SYNAPSE ($20k + $60k = $80k = 8% <= 15%)
        decision = self.manager.evaluate_bridge_transfer(
            target_bridge_id="STARGATE_POOL", transfer_amount_usd=60_000.0,
            native_price=3000.0, wrapped_price=2997.0
        )
        self.assertTrue(decision.is_approved)
        self.assertTrue(decision.is_rerouted)
        self.assertEqual(decision.selected_bridge_id, "SYNAPSE_POOL")

    def test_cap_boundary_equality_is_allowed(self):
        # $100k in-flight + $50k transfer = $150k = exactly 15% of NAV: allowed
        decision = self.manager.evaluate_bridge_transfer(
            "STARGATE_POOL", 50_000.0, native_price=3000.0, wrapped_price=3000.0
        )
        self.assertTrue(decision.is_approved)
        self.assertFalse(decision.is_rerouted)
        self.assertEqual(decision.projected_nav_pct, 15.0)

    def test_finality_delay_breach_reroutes_to_highest_audit_score(self):
        # ARBITRUM_CANONICAL (10,080 mins > 120 SLA) breaches; compliant
        # candidates: SYNAPSE (audit 90, registered 2nd) and STARGATE (audit 95)
        # Lowest-risk routing must pick STARGATE by audit score
        decision = self.manager.evaluate_bridge_transfer(
            target_bridge_id="ARBITRUM_CANONICAL", transfer_amount_usd=10_000.0,
            native_price=3000.0, wrapped_price=3000.0
        )
        self.assertTrue(decision.is_approved)
        self.assertTrue(decision.is_rerouted)
        self.assertEqual(decision.selected_bridge_id, "STARGATE_POOL")

    def test_failover_ranks_by_audit_score_not_registration_order(self):
        # Register the lower-scored SYNAPSE FIRST; primary ARBITRUM breaches
        # finality. First-fit routing would pick SYNAPSE; audit-ranked
        # lowest-risk routing must pick STARGATE (95 > 90).
        manager = CrossChainBridgeRiskManager(portfolio_nav_usd=1_000_000.0)
        manager.register_bridge(self.b2)   # SYNAPSE, audit 90
        manager.register_bridge(self.b1)   # STARGATE, audit 95
        manager.register_bridge(self.b3)   # ARBITRUM, breaches finality SLA
        decision = manager.evaluate_bridge_transfer(
            "ARBITRUM_CANONICAL", 10_000.0, native_price=3000.0, wrapped_price=3000.0
        )
        self.assertTrue(decision.is_rerouted)
        self.assertEqual(decision.selected_bridge_id, "STARGATE_POOL")

    def test_min_audit_gate_excludes_weak_bridges(self):
        # Audit floor 92 excludes SYNAPSE (90); primary STARGATE breaches cap
        # with $60k -> only secondary would be SYNAPSE, now ineligible -> block
        manager = CrossChainBridgeRiskManager(
            portfolio_nav_usd=1_000_000.0, min_audit_score_pct=92.0
        )
        manager.register_bridge(self.b1)
        manager.register_bridge(self.b2)
        decision = manager.evaluate_bridge_transfer(
            "STARGATE_POOL", 60_000.0, native_price=3000.0, wrapped_price=2997.0
        )
        self.assertFalse(decision.is_approved)
        self.assertFalse(decision.is_rerouted)
        self.assertIn("no compliant secondary", decision.reason)

    def test_finality_boundary_equality_allowed(self):
        # Finality exactly equal to the 120-minute SLA is compliant
        manager = CrossChainBridgeRiskManager(portfolio_nav_usd=1_000_000.0)
        manager.register_bridge(BridgeProfile(
            "SLOW_OK", "Boundary Bridge", "LIQUIDITY_POOL",
            finality_delay_minutes=120.0, max_nav_pct_cap=0.15,
            current_inflight_usd=0.0, audit_score_pct=90.0
        ))
        decision = manager.evaluate_bridge_transfer(
            "SLOW_OK", 10_000.0, native_price=3000.0, wrapped_price=3000.0
        )
        self.assertTrue(decision.is_approved)

    def test_bridge_profile_validation(self):
        base = dict(bridge_id="X", name="Y", bridge_type="LIQUIDITY_POOL",
                    finality_delay_minutes=10.0, max_nav_pct_cap=0.15,
                    current_inflight_usd=0.0, audit_score_pct=90.0)
        for bad in (
            dict(bridge_id=""), dict(name="  "), dict(bridge_type=""),
            dict(finality_delay_minutes=-1.0), dict(max_nav_pct_cap=0.0),
            dict(max_nav_pct_cap=1.5), dict(current_inflight_usd=-1.0),
            dict(audit_score_pct=-5.0), dict(audit_score_pct=105.0),
            dict(finality_delay_minutes=float("nan")),
        ):
            with self.assertRaises(ValueError):
                BridgeProfile(**{**base, **bad})

    def test_manager_validation(self):
        with self.assertRaises(ValueError):
            CrossChainBridgeRiskManager(portfolio_nav_usd=0.0)
        with self.assertRaises(ValueError):
            CrossChainBridgeRiskManager(portfolio_nav_usd=-5.0)
        with self.assertRaises(ValueError):
            CrossChainBridgeRiskManager(portfolio_nav_usd=float("nan"))
        with self.assertRaises(ValueError):
            CrossChainBridgeRiskManager(max_depeg_threshold_pct=-1.0)
        with self.assertRaises(ValueError):
            CrossChainBridgeRiskManager(max_allowed_finality_minutes=0.0)
        with self.assertRaises(ValueError):
            CrossChainBridgeRiskManager(min_audit_score_pct=105.0)
        with self.assertRaises(ValueError):
            self.manager.evaluate_bridge_transfer(
                "UNKNOWN", 10_000.0, native_price=3000.0, wrapped_price=3000.0
            )

    def test_duplicate_registration_warns(self):
        with self.assertLogs(level="WARNING"):
            self.manager.register_bridge(self.b1)

    # --- Threshold must be gated on the exact value, not the display value ---

    def test_subrounding_depeg_still_blocks(self):
        # Regression: the halt compared the 2dp-ROUNDED de-peg against the
        # threshold, so a real 0.004% de-peg reported 0.0% and was APPROVED
        # under a 0.001% threshold. Independently: |1000.04-1000|/1000*100
        # = 0.004, and 0.004 >= 0.001, so this must block.
        manager = CrossChainBridgeRiskManager(
            portfolio_nav_usd=1_000_000.0, max_depeg_threshold_pct=0.001
        )
        manager.register_bridge(self.b1)
        decision = manager.evaluate_bridge_transfer(
            "STARGATE_POOL", 10_000.0, native_price=1000.0, wrapped_price=1000.04
        )
        self.assertFalse(decision.is_approved)
        self.assertFalse(decision.is_rerouted)
        self.assertIn("de-peg", decision.reason.lower())

    def test_exact_gate_and_rounded_report_may_disagree_at_boundary(self):
        # Consequence of gating on the exact value: |2970.0001-3000|/3000*100
        # = 0.99999966...%, which is genuinely BELOW the 1.0% threshold and so
        # must be approved - even though the reported field rounds to 1.0.
        # Pinned deliberately: it reads like a contradiction in an audit trail.
        decision = self.manager.evaluate_bridge_transfer(
            "STARGATE_POOL", 20_000.0, native_price=3000.0, wrapped_price=2970.0001
        )
        self.assertTrue(decision.is_approved)
        self.assertEqual(decision.depeg_pct, 1.0)

    def test_reported_depeg_stays_rounded_for_display(self):
        # The reported field remains 2dp even though the gate uses the exact
        # value: |2997-3000|/3000*100 = 0.10 exactly.
        decision = self.manager.evaluate_bridge_transfer(
            "STARGATE_POOL", 10_000.0, native_price=3000.0, wrapped_price=2997.0
        )
        self.assertEqual(decision.depeg_pct, 0.1)
        self.assertTrue(decision.is_approved)

    def test_depeg_block_records_true_projected_exposure(self):
        # Regression: a de-peg block reported projected_nav_pct=0.0, which
        # reads as "no capital at risk" in the audit trail. STARGATE holds
        # $100k in-flight; +$50k on $1M NAV = 15.0%.
        decision = self.manager.evaluate_bridge_transfer(
            "STARGATE_POOL", 50_000.0, native_price=3000.0, wrapped_price=2900.0
        )
        self.assertFalse(decision.is_approved)
        self.assertEqual(decision.projected_nav_pct, 15.0)

    # --- Stateless/advisory contract -----------------------------------------

    def test_evaluation_is_stateless_and_caller_must_update_inflight(self):
        # The engine does NOT book the transfer. Documented contract: three
        # identical evaluations each pass on the SAME stale in-flight balance,
        # which is why the caller must re-register after executing. Pinning
        # this stops a future change from silently making it stateful.
        for _ in range(3):
            decision = self.manager.evaluate_bridge_transfer(
                "STARGATE_POOL", 50_000.0, native_price=3000.0, wrapped_price=3000.0
            )
            self.assertTrue(decision.is_approved)
            self.assertEqual(decision.projected_nav_pct, 15.0)
        self.assertEqual(self.manager.bridges["STARGATE_POOL"].current_inflight_usd,
                         100_000.0)

        # Re-registering with the executed balance is the update mechanism,
        # and the cap then bites.
        self.manager.register_bridge(BridgeProfile(
            bridge_id="STARGATE_POOL", name="Stargate Finance",
            bridge_type="LIQUIDITY_POOL", finality_delay_minutes=15.0,
            max_nav_pct_cap=0.15, current_inflight_usd=150_000.0,
            audit_score_pct=95.0,
        ))
        after = self.manager.evaluate_bridge_transfer(
            "STARGATE_POOL", 50_000.0, native_price=3000.0, wrapped_price=3000.0
        )
        self.assertTrue(after.is_rerouted)
        self.assertNotEqual(after.selected_bridge_id, "STARGATE_POOL")

    # --- External input arrives as the wrong type ----------------------------

    def test_non_numeric_input_raises_value_error(self):
        # Regression: a JSON string price raised TypeError and a non-string
        # bridge_id raised AttributeError, though every doc and test in this
        # skill promises ValueError.
        with self.assertRaises(ValueError):
            self.manager.evaluate_bridge_transfer(
                "STARGATE_POOL", 10_000.0, native_price="3000", wrapped_price=3000.0
            )
        with self.assertRaises(ValueError):
            self.manager.evaluate_bridge_transfer(
                "STARGATE_POOL", 10_000.0, native_price=None, wrapped_price=3000.0
            )
        with self.assertRaises(ValueError):
            BridgeProfile(
                bridge_id=5, name="Y", bridge_type="LIQUIDITY_POOL",
                finality_delay_minutes=10.0, max_nav_pct_cap=0.15,
                current_inflight_usd=0.0, audit_score_pct=90.0,
            )

    def test_bool_is_not_a_valid_transfer_amount(self):
        # Regression: bool is an int subclass, so True passed validation and
        # was evaluated as a $1 transfer.
        with self.assertRaises(ValueError):
            self.manager.evaluate_bridge_transfer(
                "STARGATE_POOL", True, native_price=3000.0, wrapped_price=3000.0
            )

if __name__ == '__main__':
    unittest.main()
