import threading
import unittest
from datetime import date

from vendor_usage_tracking import (
    DataAccessRequest,
    VendorContractSpec,
    VendorUsageConfigurationError,
    VendorUsageRestrictionEngine,
)

# Fixed evaluation date so expiry-sensitive assertions never depend on the clock.
AS_OF = date(2026, 6, 15)


def bpipe_contract(**overrides):
    """Bloomberg B-PIPE style enterprise feed: non-display yes, redistribution no."""
    spec = dict(
        vendor_id="BLOOMBERG_BPIPE",
        vendor_name="Bloomberg B-PIPE Enterprise",
        license_tier="ENTERPRISE_FEED",
        allowed_use_cases=["INTERNAL_RESEARCH", "NON_DISPLAY_TRADING", "RISK_MANAGEMENT"],
        is_non_display_allowed=True,
        is_redistribution_allowed=False,
        max_concurrent_entitlements=10,
        current_active_entitlements=2,
        contract_expiration_date="2027-12-31",
    )
    spec.update(overrides)
    return VendorContractSpec(**spec)


class TestVendorUsageRestrictionEngine(unittest.TestCase):

    def setUp(self):
        self.engine = VendorUsageRestrictionEngine()
        self.engine.register_contract(bpipe_contract())

    def evaluate(self, **overrides):
        spec = dict(
            request_id="REQ",
            vendor_id="BLOOMBERG_BPIPE",
            requested_by_system="HFT_ENGINE_01",
            use_case_type="INTERNAL_RESEARCH",
            is_external_redistribution=False,
        )
        spec.update(overrides)
        return self.engine.evaluate_access_request(DataAccessRequest(**spec), as_of_date=AS_OF)

    # --- approval path -------------------------------------------------------

    def test_internal_hft_trading_request_approved(self):
        report = self.evaluate(request_id="REQ_01", use_case_type="NON_DISPLAY_TRADING")

        self.assertTrue(report.is_approved)
        self.assertEqual(report.status, "APPROVED")
        # Cap 10, 2 already held, 1 requested -> 7 free.
        self.assertEqual(report.active_entitlements_remaining, 7)
        self.assertEqual(report.evaluated_on, "2026-06-15")

    def test_use_case_matching_is_case_insensitive(self):
        report = self.evaluate(use_case_type="  internal_research  ")

        self.assertTrue(report.is_approved)
        self.assertEqual(report.evaluated_use_case, "INTERNAL_RESEARCH")

    def test_multi_seat_request_reserves_all_seats(self):
        report = self.evaluate(requested_seats=5)

        self.assertTrue(report.is_approved)
        self.assertEqual(report.active_entitlements_remaining, 3)
        self.assertEqual(self.engine.contracts["BLOOMBERG_BPIPE"].current_active_entitlements, 7)

    # --- redistribution ------------------------------------------------------

    def test_external_redistribution_request_denied(self):
        report = self.evaluate(
            request_id="REQ_02", requested_by_system="WEB_PORTAL",
            is_external_redistribution=True)

        self.assertFalse(report.is_approved)
        self.assertEqual(report.status, "REDISTRIBUTION_LICENSING_VIOLATION")
        self.assertTrue(report.treated_as_external_redistribution)

    def test_redistribution_use_case_denied_even_when_flag_left_false(self):
        """Regression: the flag and the use case can disagree.

        The pre-fix engine gated redistribution solely on ``is_external_redistribution``.
        A caller naming the use case EXTERNAL_REDISTRIBUTION while leaving the flag
        False therefore skipped the redistribution check entirely. It must fail
        closed on either signal.
        """
        report = self.evaluate(
            use_case_type="EXTERNAL_REDISTRIBUTION", is_external_redistribution=False)

        self.assertFalse(report.is_approved)
        self.assertEqual(report.status, "REDISTRIBUTION_LICENSING_VIOLATION")
        self.assertTrue(report.treated_as_external_redistribution)

    def test_redistribution_permitted_when_licensed(self):
        self.engine.register_contract(bpipe_contract(
            vendor_id="LSEG_RT",
            vendor_name="LSEG Real-Time",
            allowed_use_cases=["INTERNAL_RESEARCH", "EXTERNAL_REDISTRIBUTION"],
            is_non_display_allowed=False,
            is_redistribution_allowed=True,
        ))
        report = self.evaluate(
            vendor_id="LSEG_RT", use_case_type="EXTERNAL_REDISTRIBUTION",
            is_external_redistribution=True)

        self.assertTrue(report.is_approved)

    # --- non-display ---------------------------------------------------------

    def test_non_display_trading_denied_without_licence(self):
        self.engine.register_contract(bpipe_contract(
            vendor_id="DESKTOP_ONLY",
            vendor_name="Research Desktop Terminal",
            license_tier="RESEARCH_DESKTOP",
            allowed_use_cases=["INTERNAL_RESEARCH"],
            is_non_display_allowed=False,
        ))
        report = self.evaluate(vendor_id="DESKTOP_ONLY", use_case_type="NON_DISPLAY_TRADING")

        self.assertFalse(report.is_approved)
        self.assertEqual(report.status, "NON_DISPLAY_LICENSING_VIOLATION")
        # A denial must not consume headroom.
        self.assertEqual(self.engine.contracts["DESKTOP_ONLY"].current_active_entitlements, 2)

    # --- use case scope ------------------------------------------------------

    def test_unlisted_use_case_denied(self):
        report = self.evaluate(use_case_type="MARKETING_DEMO")

        self.assertFalse(report.is_approved)
        self.assertEqual(report.status, "UNAUTHORIZED_USE_CASE_VIOLATION")

    # --- concurrency ---------------------------------------------------------

    def test_concurrency_cap_denies_request_that_would_exceed_it(self):
        report = self.evaluate(requested_seats=9)  # 2 held + 9 = 11 > cap 10

        self.assertFalse(report.is_approved)
        self.assertEqual(report.status, "CONCURRENCY_CAP_EXCEEDED")
        self.assertEqual(self.engine.contracts["BLOOMBERG_BPIPE"].current_active_entitlements, 2)

    def test_request_landing_exactly_on_the_cap_is_approved(self):
        report = self.evaluate(requested_seats=8)  # 2 + 8 == cap 10

        self.assertTrue(report.is_approved)
        self.assertEqual(report.active_entitlements_remaining, 0)

    def test_released_seats_become_available_again(self):
        self.evaluate(requested_seats=8)
        self.assertFalse(self.evaluate(request_id="REQ_B").is_approved)

        remaining = self.engine.release_entitlement("BLOOMBERG_BPIPE", 3)

        self.assertEqual(remaining, 3)
        self.assertTrue(self.evaluate(request_id="REQ_C").is_approved)

    def test_releasing_more_seats_than_held_is_refused(self):
        with self.assertRaises(VendorUsageConfigurationError):
            self.engine.release_entitlement("BLOOMBERG_BPIPE", 3)  # only 2 held

    def test_concurrent_requests_never_oversubscribe_the_cap(self):
        """Twenty threads race for eight free seats; exactly eight may win."""
        engine = VendorUsageRestrictionEngine()
        engine.register_contract(bpipe_contract(vendor_id="RACE"))
        approvals = []
        barrier = threading.Barrier(20)

        def worker(i):
            barrier.wait()
            report = engine.evaluate_access_request(DataAccessRequest(
                f"REQ_{i}", "RACE", f"SYS_{i}", "INTERNAL_RESEARCH", False), as_of_date=AS_OF)
            if report.is_approved:
                approvals.append(report)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(approvals), 8)
        self.assertEqual(engine.contracts["RACE"].current_active_entitlements, 10)

    # --- contract term -------------------------------------------------------

    def test_expired_contract_denies_otherwise_compliant_request(self):
        self.engine.register_contract(bpipe_contract(
            vendor_id="LAPSED", contract_expiration_date="2026-06-14"))
        report = self.evaluate(vendor_id="LAPSED")

        self.assertFalse(report.is_approved)
        self.assertEqual(report.status, "CONTRACT_EXPIRED")

    def test_contract_is_valid_through_its_final_day(self):
        self.engine.register_contract(bpipe_contract(
            vendor_id="LAST_DAY", contract_expiration_date="2026-06-15"))
        report = self.evaluate(vendor_id="LAST_DAY")

        self.assertTrue(report.is_approved)

    def test_untracked_expiry_does_not_gate(self):
        self.engine.register_contract(bpipe_contract(
            vendor_id="NO_EXPIRY", contract_expiration_date=None))
        report = self.evaluate(vendor_id="NO_EXPIRY")

        self.assertTrue(report.is_approved)

    def test_malformed_expiry_is_rejected_at_registration(self):
        with self.assertRaises(VendorUsageConfigurationError):
            self.engine.register_contract(bpipe_contract(
                vendor_id="BAD_DATE", contract_expiration_date="31/12/2027"))

    # --- validation ----------------------------------------------------------

    def test_unregistered_vendor_raises(self):
        with self.assertRaises(ValueError):  # VendorUsageConfigurationError subclasses ValueError
            self.evaluate(vendor_id="UNKNOWN_VENDOR")

    def test_negative_seat_request_is_rejected_not_credited(self):
        """Regression: a negative seat count used to *decrement* the active count."""
        with self.assertRaises(VendorUsageConfigurationError):
            self.evaluate(requested_seats=-5)
        self.assertEqual(self.engine.contracts["BLOOMBERG_BPIPE"].current_active_entitlements, 2)

    def test_zero_seat_request_is_rejected(self):
        with self.assertRaises(VendorUsageConfigurationError):
            self.evaluate(requested_seats=0)

    def test_duplicate_registration_refused_without_replace(self):
        with self.assertRaises(VendorUsageConfigurationError):
            self.engine.register_contract(bpipe_contract())

    def test_replace_supersedes_the_existing_contract(self):
        self.engine.register_contract(
            bpipe_contract(max_concurrent_entitlements=4, current_active_entitlements=0),
            replace=True)
        report = self.evaluate()

        self.assertTrue(report.is_approved)
        self.assertEqual(report.active_entitlements_remaining, 3)

    def test_oversubscribed_contract_rejected_at_registration(self):
        with self.assertRaises(VendorUsageConfigurationError):
            self.engine.register_contract(bpipe_contract(
                vendor_id="OVERSOLD", max_concurrent_entitlements=5,
                current_active_entitlements=9))

    def test_empty_allowed_use_cases_rejected(self):
        with self.assertRaises(VendorUsageConfigurationError):
            self.engine.register_contract(bpipe_contract(vendor_id="EMPTY", allowed_use_cases=[]))

    def test_contract_contradicting_its_own_permissions_rejected(self):
        with self.assertRaises(VendorUsageConfigurationError):
            self.engine.register_contract(bpipe_contract(
                vendor_id="CONTRADICTORY",
                allowed_use_cases=["EXTERNAL_REDISTRIBUTION"],
                is_redistribution_allowed=False))

    def test_scope_edited_in_place_takes_effect_immediately(self):
        """A contract narrowed after registration must be enforced as narrowed."""
        self.assertTrue(self.evaluate(use_case_type="RISK_MANAGEMENT").is_approved)

        self.engine.contracts["BLOOMBERG_BPIPE"].allowed_use_cases = ["INTERNAL_RESEARCH"]
        report = self.evaluate(use_case_type="RISK_MANAGEMENT")

        self.assertFalse(report.is_approved)
        self.assertEqual(report.status, "UNAUTHORIZED_USE_CASE_VIOLATION")

    def test_expiry_edited_in_place_takes_effect_immediately(self):
        self.engine.contracts["BLOOMBERG_BPIPE"].contract_expiration_date = "2026-01-01"
        report = self.evaluate()

        self.assertFalse(report.is_approved)
        self.assertEqual(report.status, "CONTRACT_EXPIRED")

    # --- audit trail ---------------------------------------------------------

    def test_audit_trail_records_approvals_and_denials_in_order(self):
        self.evaluate(request_id="A")
        self.evaluate(request_id="B", is_external_redistribution=True)

        trail = self.engine.get_audit_trail()

        self.assertEqual([r.request_id for r in trail], ["A", "B"])
        self.assertEqual([r.is_approved for r in trail], [True, False])

    def test_audit_trail_is_bounded_by_capacity(self):
        engine = VendorUsageRestrictionEngine(audit_log_capacity=2)
        engine.register_contract(bpipe_contract())
        for i in range(5):
            engine.evaluate_access_request(DataAccessRequest(
                f"R{i}", "BLOOMBERG_BPIPE", "SYS", "INTERNAL_RESEARCH", False), as_of_date=AS_OF)

        self.assertEqual([r.request_id for r in engine.get_audit_trail()], ["R3", "R4"])


if __name__ == '__main__':
    unittest.main()
