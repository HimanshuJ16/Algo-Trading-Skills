import unittest

from cross_strategy_signal_reuse_and_licensing import (
    DuplicateRegistrationError,
    SignalLicensingError,
    SignalProfile,
    SignalReuseAndLicensingEngine,
    StrategySubscription,
    UnknownSignalError,
    UnknownSubscriptionError,
)


class TestSignalReuseAndLicensingEngine(unittest.TestCase):

    def setUp(self):
        self.engine = SignalReuseAndLicensingEngine()

        # Signal: Base Fee $10,000, 5% PnL Share, Max AUM $50,000,000
        self.sig = SignalProfile(
            signal_id="SIG_SENTIMENT_01",
            signal_name="Global NLP Sentiment Alpha",
            owner_entity="US_Quant_Research",
            base_license_fee_annual_usd=10_000.0,
            pnl_share_pct=0.05,
            max_aum_capacity_usd=50_000_000.0,
        )
        self.engine.register_signal(self.sig)

    def _subscribe(self, sub_id, strategy, aum, entity="US_Desk"):
        return self.engine.request_subscription(
            StrategySubscription(sub_id, strategy, "SIG_SENTIMENT_01", entity, aum, True)
        )

    # ------------------------------------------------------------------
    # Entitlement & capacity
    # ------------------------------------------------------------------
    def test_entitlement_grant_and_capacity_breach(self):
        res1 = self._subscribe("SUB_01", "Pod_Alpha", 20_000_000.0)
        self.assertTrue(res1.is_entitled)
        self.assertEqual(res1.current_total_subscribed_aum_usd, 20_000_000.0)

        res2 = self._subscribe("SUB_02", "Pod_Beta", 25_000_000.0, "UK_Desk")
        self.assertTrue(res2.is_entitled)
        self.assertEqual(res2.current_total_subscribed_aum_usd, 45_000_000.0)

        # SUB_03 tries to add $15,000,000 -> Total $60M > $50M limit.
        res3 = self._subscribe("SUB_03", "Pod_Gamma", 15_000_000.0, "HK_Desk")
        self.assertFalse(res3.is_entitled)
        self.assertIn("capacity cap breached", res3.reason.lower())

        # A denial must not consume capacity or leave a record behind.
        self.assertEqual(self.engine.get_subscribed_aum("SIG_SENTIMENT_01"), 45_000_000.0)
        self.assertNotIn("SUB_03", self.engine.subscriptions)

    def test_subscription_exactly_at_capacity_is_granted(self):
        self._subscribe("SUB_01", "Pod_Alpha", 20_000_000.0)
        self._subscribe("SUB_02", "Pod_Beta", 25_000_000.0)
        # Documented rule is sum(AUM) <= capacity, so landing exactly on $50M passes.
        res = self._subscribe("SUB_03", "Pod_Gamma", 5_000_000.0)
        self.assertTrue(res.is_entitled)
        self.assertEqual(res.current_total_subscribed_aum_usd, 50_000_000.0)

        # One dollar beyond the cap is denied.
        res_over = self._subscribe("SUB_04", "Pod_Delta", 1.0)
        self.assertFalse(res_over.is_entitled)

    def test_float_accumulation_does_not_deny_at_exact_cap(self):
        # Regression: strict `>` on float-accumulated sums can deny a projection
        # that is mathematically exactly at the cap. 0.1 * 3 == 0.30000000000000004.
        engine = SignalReuseAndLicensingEngine()
        engine.register_signal(
            SignalProfile("S", "Tiny", "Owner", 0.0, 0.0, 0.3)
        )
        for i in range(3):
            res = engine.request_subscription(
                StrategySubscription(f"SUB_{i}", f"Pod_{i}", "S", "Desk", 0.1, True)
            )
            self.assertTrue(res.is_entitled, f"leg {i} denied at exact capacity")

    def test_unknown_signal_is_denied_not_recorded(self):
        res = self.engine.request_subscription(
            StrategySubscription("SUB_X", "Pod_X", "SIG_NOPE", "US_Desk", 1_000.0, True)
        )
        self.assertFalse(res.is_entitled)
        self.assertIn("unknown signal", res.reason.lower())
        self.assertEqual(self.engine.subscriptions, {})

    def test_duplicate_subscription_id_is_rejected(self):
        # Regression: the previous implementation keyed on subscription_id and
        # blindly overwrote, erasing Pod_Alpha's entitlement record while its
        # AUM was still counted, or double-granting on re-submission.
        self._subscribe("SUB_01", "Pod_Alpha", 20_000_000.0)
        with self.assertRaises(DuplicateRegistrationError):
            self._subscribe("SUB_01", "Pod_Impostor", 25_000_000.0)
        self.assertEqual(self.engine.subscriptions["SUB_01"].strategy_id, "Pod_Alpha")
        self.assertEqual(self.engine.get_subscribed_aum("SIG_SENTIMENT_01"), 20_000_000.0)

    def test_inactive_request_is_not_recorded(self):
        res = self.engine.request_subscription(
            StrategySubscription("SUB_01", "Pod_Alpha", "SIG_SENTIMENT_01", "US_Desk", 1e6, False)
        )
        self.assertFalse(res.is_entitled)
        self.assertNotIn("SUB_01", self.engine.subscriptions)

    def test_revocation_releases_capacity_and_is_idempotent(self):
        self._subscribe("SUB_01", "Pod_Alpha", 30_000_000.0)
        denied = self._subscribe("SUB_02", "Pod_Beta", 25_000_000.0)
        self.assertFalse(denied.is_entitled)

        self.engine.revoke_subscription("SUB_01")
        self.assertEqual(self.engine.get_subscribed_aum("SIG_SENTIMENT_01"), 0.0)
        self.engine.revoke_subscription("SUB_01")  # idempotent
        self.assertEqual(self.engine.get_subscribed_aum("SIG_SENTIMENT_01"), 0.0)

        granted = self._subscribe("SUB_02", "Pod_Beta", 25_000_000.0)
        self.assertTrue(granted.is_entitled)

    def test_revoke_unknown_subscription_raises(self):
        with self.assertRaises(UnknownSubscriptionError):
            self.engine.revoke_subscription("NOPE")

    # ------------------------------------------------------------------
    # Input validation (fail-closed)
    # ------------------------------------------------------------------
    def test_nan_aum_is_rejected(self):
        # Regression: `nan > cap` is False, so an unchecked NaN would be GRANTED
        # and would then make every later capacity sum NaN, disabling the cap.
        with self.assertRaises(ValueError):
            StrategySubscription("SUB_N", "Pod_N", "SIG_SENTIMENT_01", "US_Desk", float("nan"), True)

    def test_infinite_and_negative_aum_are_rejected(self):
        for bad in (float("inf"), -1.0, -100_000_000.0):
            with self.assertRaises(ValueError):
                StrategySubscription("SUB_B", "Pod_B", "SIG_SENTIMENT_01", "US_Desk", bad, True)

    def test_invalid_signal_terms_are_rejected(self):
        with self.assertRaises(ValueError):  # pnl share as a percent, not a fraction
            SignalProfile("S", "N", "O", 1.0, 5.0, 1e6)
        with self.assertRaises(ValueError):  # negative share
            SignalProfile("S", "N", "O", 1.0, -0.01, 1e6)
        with self.assertRaises(ValueError):  # zero capacity is not a usable cap
            SignalProfile("S", "N", "O", 1.0, 0.05, 0.0)
        with self.assertRaises(ValueError):  # blank identifier
            SignalProfile("  ", "N", "O", 1.0, 0.05, 1e6)

    def test_register_signal_overwrite_requires_opt_in(self):
        cheaper = SignalProfile(
            "SIG_SENTIMENT_01", "Global NLP Sentiment Alpha", "US_Quant_Research",
            0.0, 0.0, 500_000_000.0,
        )
        with self.assertRaises(DuplicateRegistrationError):
            self.engine.register_signal(cheaper)
        self.assertEqual(
            self.engine.signals["SIG_SENTIMENT_01"].base_license_fee_annual_usd, 10_000.0
        )
        self.engine.register_signal(cheaper, replace=True)
        self.assertEqual(
            self.engine.signals["SIG_SENTIMENT_01"].max_aum_capacity_usd, 500_000_000.0
        )

    # ------------------------------------------------------------------
    # Fee attribution
    # ------------------------------------------------------------------
    def test_transfer_pricing_fee_attribution(self):
        self._subscribe("SUB_01", "Pod_Alpha", 20_000_000.0)

        # Strategy generated $1,000,000 PnL.
        # Fee = Base ($10,000) + 5% * $1,000,000 ($50,000) = $60,000
        report = self.engine.calculate_transfer_pricing_fee(
            "SUB_01",
            strategy_realized_pnl_usd=1_000_000.0,
            benchmarking_evidence_ref="TP-STUDY-2026-014",
        )

        self.assertEqual(report.base_fee_usd, 10_000.0)
        self.assertEqual(report.pnl_share_fee_usd, 50_000.0)
        self.assertEqual(report.total_fee_usd, 60_000.0)
        self.assertEqual(report.shareable_pnl_usd, 1_000_000.0)
        self.assertEqual(report.remaining_loss_carryforward_usd, 0.0)
        self.assertTrue(report.arm_length_documented)
        self.assertEqual(report.benchmarking_evidence_ref, "TP-STUDY-2026-014")
        self.assertTrue(report.is_cross_entity)  # US_Quant_Research vs US_Desk

    def test_arm_length_flag_is_documentation_presence_only(self):
        # Regression: the flag previously read `total_fee > 0`, so a fee of any
        # size was reported as arm's-length compliant. It must instead reflect
        # only whether benchmarking evidence was attached.
        self._subscribe("SUB_01", "Pod_Alpha", 20_000_000.0)
        undocumented = self.engine.calculate_transfer_pricing_fee("SUB_01", 1_000_000.0)
        self.assertGreater(undocumented.total_fee_usd, 0.0)
        self.assertFalse(undocumented.arm_length_documented)
        self.assertIn("7.47", undocumented.pricing_basis)

    def test_loss_carryforward_high_water_mark(self):
        self._subscribe("SUB_01", "Pod_Alpha", 20_000_000.0)

        # $10M of unrecouped losses, $1M of PnL this period: nothing shareable,
        # base fee only, and $9M of losses still to recoup.
        r1 = self.engine.calculate_transfer_pricing_fee(
            "SUB_01", 1_000_000.0, loss_carryforward_usd=10_000_000.0
        )
        self.assertEqual(r1.shareable_pnl_usd, 0.0)
        self.assertEqual(r1.pnl_share_fee_usd, 0.0)
        self.assertEqual(r1.total_fee_usd, 10_000.0)
        self.assertEqual(r1.loss_carryforward_applied_usd, 1_000_000.0)
        self.assertEqual(r1.remaining_loss_carryforward_usd, 9_000_000.0)

        # $12M of PnL against $9M carried forward -> 5% of $3M = $150,000.
        r2 = self.engine.calculate_transfer_pricing_fee(
            "SUB_01", 12_000_000.0, loss_carryforward_usd=9_000_000.0
        )
        self.assertEqual(r2.shareable_pnl_usd, 3_000_000.0)
        self.assertEqual(r2.pnl_share_fee_usd, 150_000.0)
        self.assertEqual(r2.total_fee_usd, 160_000.0)
        self.assertEqual(r2.remaining_loss_carryforward_usd, 0.0)

    def test_loss_period_charges_base_fee_only_and_carries_loss_forward(self):
        self._subscribe("SUB_01", "Pod_Alpha", 20_000_000.0)
        report = self.engine.calculate_transfer_pricing_fee("SUB_01", -2_000_000.0)
        self.assertEqual(report.pnl_share_fee_usd, 0.0)
        self.assertEqual(report.total_fee_usd, 10_000.0)
        self.assertEqual(report.loss_carryforward_applied_usd, 0.0)
        self.assertEqual(report.remaining_loss_carryforward_usd, 2_000_000.0)

    def test_revoked_subscription_cannot_be_billed(self):
        self._subscribe("SUB_01", "Pod_Alpha", 20_000_000.0)
        self.engine.revoke_subscription("SUB_01")
        with self.assertRaises(SignalLicensingError):
            self.engine.calculate_transfer_pricing_fee("SUB_01", 1_000_000.0)

    def test_fee_rejects_unknown_subscription_and_bad_inputs(self):
        with self.assertRaises(UnknownSubscriptionError):
            self.engine.calculate_transfer_pricing_fee("NOPE", 1.0)
        self._subscribe("SUB_01", "Pod_Alpha", 20_000_000.0)
        with self.assertRaises(ValueError):
            self.engine.calculate_transfer_pricing_fee("SUB_01", float("nan"))
        with self.assertRaises(ValueError):
            self.engine.calculate_transfer_pricing_fee("SUB_01", 1.0, loss_carryforward_usd=-1.0)
        with self.assertRaises(ValueError):
            self.engine.calculate_transfer_pricing_fee("SUB_01", 1.0, benchmarking_evidence_ref="  ")

    # ------------------------------------------------------------------
    # Audit reporting
    # ------------------------------------------------------------------
    def test_audit_report_reflects_active_and_revoked_state(self):
        self._subscribe("SUB_01", "Pod_Alpha", 20_000_000.0, "US_Desk")
        self._subscribe("SUB_02", "Pod_Beta", 5_000_000.0, "UK_Desk")
        self.engine.revoke_subscription("SUB_02")

        report = self.engine.generate_audit_report("SIG_SENTIMENT_01")
        self.assertEqual(report.total_subscribed_aum_usd, 20_000_000.0)
        self.assertEqual(report.remaining_capacity_usd, 30_000_000.0)
        self.assertEqual(report.capacity_utilisation_pct, 40.0)
        self.assertEqual(report.active_subscription_ids, ["SUB_01"])
        self.assertEqual(report.revoked_subscription_ids, ["SUB_02"])
        self.assertEqual(report.consumer_entities, ["US_Desk"])

    def test_audit_report_unknown_signal_raises(self):
        with self.assertRaises(UnknownSignalError):
            self.engine.generate_audit_report("SIG_NOPE")


if __name__ == '__main__':
    unittest.main()
