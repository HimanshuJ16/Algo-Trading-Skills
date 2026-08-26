import unittest
from datetime import date, datetime, timedelta

from market_data_entitlement_and_licensing_per_venue import (
    ALL_STATUSES,
    CLASSIFICATION_NON_PROFESSIONAL,
    CLASSIFICATION_PROFESSIONAL,
    DEFAULT_MAX_ATTESTATION_AGE_DAYS,
    DataStreamRequest,
    EntitlementConfigurationError,
    MarketDataEntitlementEngine,
    ND_CATEGORY_CLIENT_FACILITATION,
    ND_CATEGORY_PRINCIPAL,
    STATUS_APPROVED,
    STATUS_EXPIRED_LICENSE,
    STATUS_MISCLASSIFIED_SUBSCRIBER,
    STATUS_MISSING_NON_DISPLAY_LICENSE,
    STATUS_STALE_CLASSIFICATION,
    STATUS_SUBSCRIBER_MISMATCH,
    STATUS_UNLICENSED_DATA_LEVEL,
    STATUS_UNLICENSED_VENUE,
    STATUS_UNRECOGNISED_USAGE_TYPE,
    UserEntitlementProfile,
    VenueEntitlement,
)

# Fixed evaluation date so expiry- and attestation-sensitive assertions never
# depend on the wall clock.
AS_OF = date(2026, 6, 15)


def prop_fund_profile(**overrides):
    """Institutional prop fund: CME non-display at L2, NASDAQ display-only at L1.

    Deliberately asymmetric, because the defect this suite exists to prevent is a
    firm-wide non-display flag leaking one venue's licence onto another.
    """
    spec = dict(
        user_id="PROP_FUND_01",
        account_holder_type="ORGANISATION",
        declared_classification=CLASSIFICATION_PROFESSIONAL,
        is_securities_professional=True,
        venue_entitlements=(
            VenueEntitlement(
                venue_id="CME",
                max_data_level="L2",
                non_display_categories=(ND_CATEGORY_PRINCIPAL,),
                license_expiry_date="2026-12-31",
            ),
            VenueEntitlement(
                venue_id="NASDAQ",
                max_data_level="L1",
                non_display_categories=(),
                license_expiry_date="2026-12-31",
            ),
        ),
    )
    spec.update(overrides)
    return UserEntitlementProfile(**spec)


class EntitlementTestBase(unittest.TestCase):

    def setUp(self):
        self.engine = MarketDataEntitlementEngine()
        self.profile = prop_fund_profile()

    def audit(self, profile=None, as_of=AS_OF, **overrides):
        spec = dict(
            user_id="PROP_FUND_01",
            venue_id="CME",
            data_level="L2",
            usage_type="NON_DISPLAY_ALGO",
            non_display_category=ND_CATEGORY_PRINCIPAL,
        )
        spec.update(overrides)
        return self.engine.audit_stream_entitlement(
            profile if profile is not None else self.profile,
            DataStreamRequest(**spec),
            as_of_date=as_of,
        )


class TestApprovalPath(EntitlementTestBase):

    def test_licensed_non_display_principal_request_approved(self):
        report = self.audit()

        self.assertTrue(report.is_authorized)
        self.assertEqual(report.status, STATUS_APPROVED)
        self.assertEqual(report.venue_id, "CME")
        self.assertEqual(report.data_level, "L2")
        self.assertEqual(report.non_display_category, ND_CATEGORY_PRINCIPAL)
        self.assertEqual(report.subscriber_classification, CLASSIFICATION_PROFESSIONAL)
        self.assertEqual(report.evaluated_on, "2026-06-15")

    def test_display_request_needs_no_non_display_category(self):
        report = self.audit(
            venue_id="NASDAQ", data_level="L1", usage_type="DISPLAY",
            non_display_category=None)

        self.assertTrue(report.is_authorized)
        self.assertIsNone(report.non_display_category)

    def test_l2_entitlement_covers_shallower_l1_request(self):
        report = self.audit(data_level="L1")

        self.assertTrue(report.is_authorized)
        self.assertEqual(report.data_level, "L1")

    def test_venue_and_usage_matching_is_case_and_whitespace_insensitive(self):
        report = self.audit(venue_id="  cme  ", usage_type="non_display_algo",
                            non_display_category="principal")

        self.assertTrue(report.is_authorized)
        self.assertEqual(report.venue_id, "CME")

    def test_entitlement_recorded_lowercase_still_matches(self):
        # Regression: the request side was normalised but the entitlement side
        # was not, so a lower-case venue record was invisible to the gate.
        profile = prop_fund_profile(venue_entitlements=(
            VenueEntitlement(
                venue_id="cme", max_data_level="L2",
                non_display_categories=("principal",),
                license_expiry_date="2026-12-31"),
        ))
        report = self.audit(profile=profile)

        self.assertTrue(report.is_authorized)

    def test_non_professional_natural_person_display_request_approved(self):
        profile = prop_fund_profile(
            user_id="RETAIL_01",
            account_holder_type="NATURAL_PERSON",
            declared_classification=CLASSIFICATION_NON_PROFESSIONAL,
            is_securities_professional=False,
            classification_attested_on="2026-05-01",
        )
        report = self.audit(profile=profile, user_id="RETAIL_01", venue_id="NASDAQ",
                            data_level="L1", usage_type="DISPLAY", non_display_category=None)

        self.assertTrue(report.is_authorized)
        self.assertEqual(report.subscriber_classification, CLASSIFICATION_NON_PROFESSIONAL)


class TestPerVenueNonDisplayIsolation(EntitlementTestBase):
    """The core defect this rewrite fixes: non-display is licensed per venue."""

    def test_cme_non_display_licence_does_not_confer_nasdaq_non_display(self):
        report = self.audit(venue_id="NASDAQ", data_level="L1")

        self.assertFalse(report.is_authorized)
        self.assertEqual(report.status, STATUS_MISSING_NON_DISPLAY_LICENSE)
        self.assertIn("display-only", report.audit_notes)

    def test_unlicensed_non_display_category_denied(self):
        # Licensed for PRINCIPAL only; client facilitation is a separate licence
        # (CME Category A2, LSE "Client Facilitation").
        report = self.audit(non_display_category=ND_CATEGORY_CLIENT_FACILITATION)

        self.assertFalse(report.is_authorized)
        self.assertEqual(report.status, STATUS_MISSING_NON_DISPLAY_LICENSE)
        self.assertIn(ND_CATEGORY_PRINCIPAL, report.audit_notes)

    def test_non_display_without_category_is_denied_not_guessed(self):
        report = self.audit(non_display_category=None)

        self.assertFalse(report.is_authorized)
        self.assertEqual(report.status, STATUS_MISSING_NON_DISPLAY_LICENSE)

    def test_sibling_dcm_is_a_separate_venue(self):
        # A CME entitlement does not cover CBOT/NYMEX/COMEX.
        report = self.audit(venue_id="NYMEX")

        self.assertFalse(report.is_authorized)
        self.assertEqual(report.status, STATUS_UNLICENSED_VENUE)


class TestDepthGate(EntitlementTestBase):

    def test_depth_beyond_entitlement_denied(self):
        report = self.audit(data_level="L3")

        self.assertFalse(report.is_authorized)
        self.assertEqual(report.status, STATUS_UNLICENSED_DATA_LEVEL)

    def test_depth_request_on_top_of_book_only_venue_denied(self):
        report = self.audit(venue_id="NASDAQ", data_level="L2", usage_type="DISPLAY",
                            non_display_category=None)

        self.assertFalse(report.is_authorized)
        self.assertEqual(report.status, STATUS_UNLICENSED_DATA_LEVEL)

    def test_request_at_exactly_the_licensed_level_is_approved(self):
        report = self.audit(data_level="L2")

        self.assertTrue(report.is_authorized)


class TestFailClosedBehaviour(EntitlementTestBase):

    def test_unrecognised_usage_type_denied_not_treated_as_display(self):
        # Regression: 'NON_DISPLAY' (missing the _ALGO suffix) previously fell
        # through the non-display gate and was approved as if it were DISPLAY.
        report = self.audit(venue_id="NASDAQ", data_level="L1", usage_type="NON_DISPLAY",
                            non_display_category=None)

        self.assertFalse(report.is_authorized)
        self.assertEqual(report.status, STATUS_UNRECOGNISED_USAGE_TYPE)

    def test_subscriber_id_mismatch_denied(self):
        # Regression: the request's user_id was never compared to the profile's,
        # so one subscriber's licences could be spent on another's request.
        report = self.audit(user_id="OTHER_DESK_07")

        self.assertFalse(report.is_authorized)
        self.assertEqual(report.status, STATUS_SUBSCRIBER_MISMATCH)
        self.assertIn("OTHER_DESK_07", report.audit_notes)

    def test_every_denial_status_is_declared(self):
        # A caller routing on `status` must be able to enumerate the vocabulary.
        self.assertEqual(len(ALL_STATUSES), len(set(ALL_STATUSES)))
        for status in ALL_STATUSES:
            self.assertTrue(status.startswith("ENTITLEMENT_"))

    def test_no_denial_status_is_authorised(self):
        denials = [
            self.audit(user_id="OTHER"),
            self.audit(usage_type="BOGUS"),
            self.audit(venue_id="LSE"),
            self.audit(data_level="L3"),
            self.audit(non_display_category=ND_CATEGORY_CLIENT_FACILITATION),
        ]
        for report in denials:
            self.assertFalse(report.is_authorized, report.status)
            self.assertNotEqual(report.status, STATUS_APPROVED)
            self.assertIn(report.status, ALL_STATUSES)


class TestLicenceTerm(EntitlementTestBase):

    def _profile_expiring(self, expiry):
        return prop_fund_profile(venue_entitlements=(
            VenueEntitlement(
                venue_id="CME", max_data_level="L2",
                non_display_categories=(ND_CATEGORY_PRINCIPAL,),
                license_expiry_date=expiry),
        ))

    def test_expired_licence_denied(self):
        report = self.audit(profile=self._profile_expiring("2026-06-14"))

        self.assertFalse(report.is_authorized)
        self.assertEqual(report.status, STATUS_EXPIRED_LICENSE)
        self.assertIn("2026-06-14", report.audit_notes)

    def test_licence_valid_through_its_expiry_date(self):
        # Boundary: the expiry date itself is the last licensed day.
        report = self.audit(profile=self._profile_expiring("2026-06-15"))

        self.assertTrue(report.is_authorized)

    def test_untracked_expiry_is_not_gated_but_is_warned_once(self):
        profile = self._profile_expiring(None)
        with self.assertLogs(
                "market_data_entitlement_and_licensing_per_venue", level="WARNING") as captured:
            first = self.audit(profile=profile)
            second = self.audit(profile=profile)

        self.assertTrue(first.is_authorized)
        self.assertTrue(second.is_authorized)
        untracked = [m for m in captured.output if "no license_expiry_date" in m]
        self.assertEqual(len(untracked), 1)

    def test_expiry_gate_survives_venue_key_whitespace(self):
        # Regression: expiry was looked up on an un-normalised key, so a venue
        # recorded as 'CME ' matched the venue check but missed its own expiry
        # and was approved after the licence had lapsed.
        profile = prop_fund_profile(venue_entitlements=(
            VenueEntitlement(
                venue_id=" CME ", max_data_level="L2",
                non_display_categories=(ND_CATEGORY_PRINCIPAL,),
                license_expiry_date="2020-01-01"),
        ))
        report = self.audit(profile=profile)

        self.assertFalse(report.is_authorized)
        self.assertEqual(report.status, STATUS_EXPIRED_LICENSE)


class TestSubscriberClassification(EntitlementTestBase):

    def _non_pro(self, **overrides):
        spec = dict(
            user_id="PROP_FUND_01",
            account_holder_type="NATURAL_PERSON",
            declared_classification=CLASSIFICATION_NON_PROFESSIONAL,
            is_securities_professional=False,
            classification_attested_on="2026-05-01",
        )
        spec.update(overrides)
        return prop_fund_profile(**spec)

    def test_organisation_declared_non_professional_denied(self):
        # CTA: only natural persons can qualify as Nonprofessionals.
        report = self.audit(
            profile=self._non_pro(account_holder_type="ORGANISATION"),
            venue_id="NASDAQ", data_level="L1", usage_type="DISPLAY",
            non_display_category=None)

        self.assertFalse(report.is_authorized)
        self.assertEqual(report.status, STATUS_MISCLASSIFIED_SUBSCRIBER)
        self.assertIn("ORGANISATION", report.audit_notes)

    def test_securities_professional_declared_non_professional_denied(self):
        report = self.audit(
            profile=self._non_pro(is_securities_professional=True),
            venue_id="NASDAQ", data_level="L1", usage_type="DISPLAY",
            non_display_category=None)

        self.assertFalse(report.is_authorized)
        self.assertEqual(report.status, STATUS_MISCLASSIFIED_SUBSCRIBER)
        self.assertIn("Securities Professional", report.audit_notes)

    def test_non_professional_cannot_run_non_display(self):
        report = self.audit(profile=self._non_pro())

        self.assertFalse(report.is_authorized)
        self.assertEqual(report.status, STATUS_MISCLASSIFIED_SUBSCRIBER)

    def test_professional_never_needs_an_attestation(self):
        profile = prop_fund_profile(classification_attested_on=None)
        report = self.audit(profile=profile)

        self.assertTrue(report.is_authorized)

    def test_non_professional_without_attestation_denied(self):
        report = self.audit(
            profile=self._non_pro(classification_attested_on=None),
            venue_id="NASDAQ", data_level="L1", usage_type="DISPLAY",
            non_display_category=None)

        self.assertFalse(report.is_authorized)
        self.assertEqual(report.status, STATUS_STALE_CLASSIFICATION)

    def test_attestation_stale_beyond_window_denied(self):
        stale = AS_OF - timedelta(days=DEFAULT_MAX_ATTESTATION_AGE_DAYS + 1)
        report = self.audit(
            profile=self._non_pro(classification_attested_on=stale.isoformat()),
            venue_id="NASDAQ", data_level="L1", usage_type="DISPLAY",
            non_display_category=None)

        self.assertFalse(report.is_authorized)
        self.assertEqual(report.status, STATUS_STALE_CLASSIFICATION)

    def test_attestation_exactly_at_window_edge_still_valid(self):
        edge = AS_OF - timedelta(days=DEFAULT_MAX_ATTESTATION_AGE_DAYS)
        report = self.audit(
            profile=self._non_pro(classification_attested_on=edge.isoformat()),
            venue_id="NASDAQ", data_level="L1", usage_type="DISPLAY",
            non_display_category=None)

        self.assertTrue(report.is_authorized)

    def test_future_dated_attestation_denied(self):
        future = (AS_OF + timedelta(days=1)).isoformat()
        report = self.audit(
            profile=self._non_pro(classification_attested_on=future),
            venue_id="NASDAQ", data_level="L1", usage_type="DISPLAY",
            non_display_category=None)

        self.assertFalse(report.is_authorized)
        self.assertEqual(report.status, STATUS_STALE_CLASSIFICATION)

    def test_tighter_attestation_window_is_honoured(self):
        self.engine = MarketDataEntitlementEngine(max_attestation_age_days=30)
        report = self.audit(
            profile=self._non_pro(classification_attested_on="2026-05-01"),
            venue_id="NASDAQ", data_level="L1", usage_type="DISPLAY",
            non_display_category=None)

        self.assertFalse(report.is_authorized)
        self.assertEqual(report.status, STATUS_STALE_CLASSIFICATION)


class TestDecisionPrecedence(EntitlementTestBase):
    """A request breaching several rules must report the documented one."""

    def test_identity_mismatch_outranks_everything_else(self):
        report = self.audit(user_id="SOMEONE_ELSE", venue_id="LSE", data_level="L3",
                            usage_type="BOGUS")

        self.assertEqual(report.status, STATUS_SUBSCRIBER_MISMATCH)

    def test_unrecognised_usage_outranks_venue_and_depth(self):
        report = self.audit(venue_id="LSE", data_level="L3", usage_type="BOGUS")

        self.assertEqual(report.status, STATUS_UNRECOGNISED_USAGE_TYPE)

    def test_misclassification_outranks_unlicensed_venue(self):
        profile = prop_fund_profile(
            account_holder_type="ORGANISATION",
            declared_classification=CLASSIFICATION_NON_PROFESSIONAL,
            classification_attested_on="2026-05-01",
            is_securities_professional=False,
        )
        report = self.audit(profile=profile, venue_id="LSE", usage_type="DISPLAY",
                            non_display_category=None)

        self.assertEqual(report.status, STATUS_MISCLASSIFIED_SUBSCRIBER)

    def test_expired_licence_outranks_depth_and_non_display(self):
        profile = prop_fund_profile(venue_entitlements=(
            VenueEntitlement(
                venue_id="CME", max_data_level="L1", non_display_categories=(),
                license_expiry_date="2020-01-01"),
        ))
        report = self.audit(profile=profile, data_level="L3")

        self.assertEqual(report.status, STATUS_EXPIRED_LICENSE)

    def test_depth_outranks_non_display_category(self):
        report = self.audit(data_level="L3",
                            non_display_category=ND_CATEGORY_CLIENT_FACILITATION)

        self.assertEqual(report.status, STATUS_UNLICENSED_DATA_LEVEL)


class TestConfigurationValidation(EntitlementTestBase):

    def test_unknown_data_level_is_a_configuration_error_not_a_denial(self):
        with self.assertRaises(EntitlementConfigurationError):
            self.audit(data_level="L4")

    def test_unknown_classification_rejected(self):
        with self.assertRaises(EntitlementConfigurationError):
            self.audit(profile=prop_fund_profile(declared_classification="RETAIL"))

    def test_unknown_account_holder_type_rejected(self):
        with self.assertRaises(EntitlementConfigurationError):
            self.audit(profile=prop_fund_profile(account_holder_type="TRUST"))

    def test_empty_venue_id_rejected(self):
        with self.assertRaises(EntitlementConfigurationError):
            self.audit(venue_id="   ")

    def test_malformed_expiry_date_rejected(self):
        profile = prop_fund_profile(venue_entitlements=(
            VenueEntitlement(venue_id="CME", max_data_level="L2",
                             non_display_categories=(ND_CATEGORY_PRINCIPAL,),
                             license_expiry_date="31/12/2026"),
        ))
        with self.assertRaises(EntitlementConfigurationError):
            self.audit(profile=profile)

    def test_duplicate_venue_entitlement_rejected(self):
        profile = prop_fund_profile(venue_entitlements=(
            VenueEntitlement(venue_id="CME", max_data_level="L1",
                             non_display_categories=()),
            VenueEntitlement(venue_id="cme", max_data_level="L3",
                             non_display_categories=(ND_CATEGORY_PRINCIPAL,)),
        ))
        with self.assertRaises(EntitlementConfigurationError):
            self.audit(profile=profile)

    def test_non_display_categories_given_as_bare_string_rejected(self):
        # "PRINCIPAL" would otherwise iterate into single characters and license
        # nothing while looking populated.
        profile = prop_fund_profile(venue_entitlements=(
            VenueEntitlement(venue_id="CME", max_data_level="L2",
                             non_display_categories="PRINCIPAL",
                             license_expiry_date="2026-12-31"),
        ))
        with self.assertRaises(EntitlementConfigurationError):
            self.audit(profile=profile)

    def test_non_bool_securities_professional_rejected(self):
        with self.assertRaises(EntitlementConfigurationError):
            self.audit(profile=prop_fund_profile(is_securities_professional="yes"))

    def test_invalid_attestation_window_rejected(self):
        for bad in (0, -1, True, 3.5):
            with self.assertRaises(EntitlementConfigurationError):
                MarketDataEntitlementEngine(max_attestation_age_days=bad)

    def test_non_date_as_of_rejected(self):
        with self.assertRaises(EntitlementConfigurationError):
            self.audit(as_of="2026-06-15")

    def test_datetime_as_of_is_narrowed_to_its_date(self):
        # datetime subclasses date, so an unguarded isinstance check would let it
        # through and then raise TypeError inside the expiry comparison. Agents
        # reach for datetime.now() by reflex.
        report = self.audit(as_of=datetime(2026, 6, 15, 14, 30, 0))

        self.assertTrue(report.is_authorized)
        self.assertEqual(report.evaluated_on, "2026-06-15")

    def test_datetime_as_of_still_enforces_expiry(self):
        profile = prop_fund_profile(venue_entitlements=(
            VenueEntitlement(venue_id="CME", max_data_level="L2",
                             non_display_categories=(ND_CATEGORY_PRINCIPAL,),
                             license_expiry_date="2026-06-14"),
        ))
        report = self.audit(profile=profile, as_of=datetime(2026, 6, 15, 9, 0, 0))

        self.assertFalse(report.is_authorized)
        self.assertEqual(report.status, STATUS_EXPIRED_LICENSE)


class TestAuditRecordFidelity(EntitlementTestBase):

    def test_display_approval_note_does_not_claim_a_non_display_category(self):
        # A stray category on a DISPLAY request must not render as "granted
        # DISPLAY ... under non-display category X" in the audit evidence.
        report = self.audit(venue_id="NASDAQ", data_level="L1", usage_type="DISPLAY",
                            non_display_category=ND_CATEGORY_PRINCIPAL)

        self.assertTrue(report.is_authorized)
        self.assertNotIn("non-display category", report.audit_notes)

    def test_non_display_approval_note_names_the_category(self):
        report = self.audit()

        self.assertIn("non-display category 'PRINCIPAL'", report.audit_notes)

    def test_denial_report_echoes_normalised_decision_inputs(self):
        report = self.audit(venue_id=" nasdaq ", data_level="L1")

        self.assertFalse(report.is_authorized)
        self.assertEqual(report.venue_id, "NASDAQ")
        self.assertEqual(report.usage_type, "NON_DISPLAY_ALGO")
        self.assertEqual(report.data_level, "L1")
        self.assertEqual(report.evaluated_on, "2026-06-15")
        self.assertEqual(report.subscriber_classification, CLASSIFICATION_PROFESSIONAL)


if __name__ == "__main__":
    unittest.main()
