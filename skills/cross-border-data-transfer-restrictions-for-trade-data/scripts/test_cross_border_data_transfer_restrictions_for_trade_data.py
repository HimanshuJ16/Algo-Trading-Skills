import unittest
from cross_border_data_transfer_restrictions_for_trade_data import (
    CrossBorderTradeDataGovernanceEngine, JurisdictionTransferPolicy, TradeDataPayload
)

class TestCrossBorderTradeDataGovernanceEngine(unittest.TestCase):

    def setUp(self):
        self.engine = CrossBorderTradeDataGovernanceEngine([
            JurisdictionTransferPolicy("CN", "US", "REQUIRES_ANONYMIZATION", "PIPL"),
            JurisdictionTransferPolicy("CH", "US", "BLOCKED", "Swiss_Art47_BankA"),
            # UK->US unrestricted presumes a DPF-certified recipient under the UK
            # Extension to the EU-US Data Privacy Framework (in force 12 Oct 2023);
            # non-certified recipients need the ICO IDTA / Addendum instead.
            JurisdictionTransferPolicy("UK", "US", "ALLOWED_UNRESTRICTED", "UK-US_Data_Bridge_DPF_UK_Extension")
        ])

        self.payload = TradeDataPayload(
            trade_id="TRD_9901", origin_country="CN",
            trader_id="QUANT_TRADER_42", client_name="John Doe Corp",
            account_number="1234567890", symbol="AAPL",
            quantity=100.0, price=150.0, tax_id="TAX-88-1234567"
        )

    def test_pii_anonymization_transfer(self):
        report = self.engine.process_data_transfer(self.payload, destination_country="US")

        self.assertTrue(report.transfer_approved)
        self.assertTrue(report.applied_anonymization)

        sanitized = report.sanitized_payload
        self.assertIsNotNone(sanitized)
        self.assertEqual(sanitized.client_name, "ANONYMOUS_CLIENT")
        self.assertTrue(sanitized.trader_id.startswith("TRD_HASH_"))
        self.assertEqual(sanitized.account_number, "XXXX-XXXX-7890")
        # Tax IDs are dropped entirely on anonymized routes, not partially masked.
        self.assertIsNone(sanitized.tax_id)
        self.assertNotIn("TAX-88", report.audit_message)
        self.assertIsNotNone(report.timestamp)

    def test_blocked_cross_border_transfer(self):
        swiss_payload = TradeDataPayload(
            trade_id="TRD_9902", origin_country="CH",
            trader_id="SWISS_TRADER", client_name="Private Bank Client",
            account_number="CH998877", symbol="NESN",
            quantity=50.0, price=100.0
        )
        report = self.engine.process_data_transfer(swiss_payload, destination_country="US")

        self.assertFalse(report.transfer_approved)
        self.assertIsNone(report.sanitized_payload)
        self.assertIn("BLOCKED", report.audit_message)

    def test_unrestricted_transfer(self):
        uk_payload = TradeDataPayload(
            trade_id="TRD_9903", origin_country="UK",
            trader_id="UK_TRADER", client_name="London Capital",
            account_number="UK112233", symbol="BP",
            quantity=500.0, price=450.0
        )
        report = self.engine.process_data_transfer(uk_payload, destination_country="US")

        self.assertTrue(report.transfer_approved)
        self.assertFalse(report.applied_anonymization)

    def test_unknown_route_defaults_to_anonymization(self):
        # No DE->BR policy registered: engine must default-deny (require
        # anonymization), never silently approve an unconfigured route.
        de_payload = TradeDataPayload(
            trade_id="TRD_9904", origin_country="DE",
            trader_id="DE_TRADER", client_name="Frankfurt Quant",
            account_number="DE9988776655", symbol="SIE",
            quantity=10.0, price=20.0
        )
        report = self.engine.process_data_transfer(de_payload, destination_country="BR")

        self.assertTrue(report.transfer_approved)
        self.assertTrue(report.applied_anonymization)
        self.assertIn("DEFAULT_STRICT_PRIVACY", report.audit_message)

    def test_domestic_transfer_approved_without_masking(self):
        report = self.engine.process_data_transfer(self.payload, destination_country="cn")

        self.assertTrue(report.transfer_approved)
        self.assertFalse(report.applied_anonymization)
        self.assertEqual(report.sanitized_payload.trader_id, "QUANT_TRADER_42")
        self.assertEqual(report.destination_country, "CN")

    def test_registered_same_country_block_is_honoured(self):
        # Regression: the domestic (origin == destination) shortcut was
        # evaluated BEFORE the policy lookup, so an explicitly registered
        # same-country BLOCKED policy was silently overridden and the raw,
        # unmasked payload was approved. An explicit policy must always win.
        engine = CrossBorderTradeDataGovernanceEngine([
            JurisdictionTransferPolicy(
                "CN", "CN", "BLOCKED", "PIPL_Art40_domestic_storage"
            )
        ])
        report = engine.process_data_transfer(self.payload, destination_country="CN")

        self.assertFalse(report.transfer_approved)
        self.assertIsNone(report.sanitized_payload)
        self.assertEqual(engine.audit_trail[-1]["decision"], "BLOCKED")

    def test_registered_same_country_anonymization_is_honoured(self):
        # Same defect, other status: a same-country REQUIRES_ANONYMIZATION
        # policy used to pass raw PII (including tax_id) straight through.
        engine = CrossBorderTradeDataGovernanceEngine([
            JurisdictionTransferPolicy("CN", "CN", "REQUIRES_ANONYMIZATION", "PIPL")
        ])
        report = engine.process_data_transfer(self.payload, destination_country="CN")

        self.assertTrue(report.applied_anonymization)
        self.assertEqual(report.sanitized_payload.client_name, "ANONYMOUS_CLIENT")
        self.assertIsNone(report.sanitized_payload.tax_id)

    def test_domestic_default_still_applies_without_a_policy(self):
        # Guard against over-correcting: with NO same-country policy
        # registered, a domestic transfer is still approved unmasked.
        report = self.engine.process_data_transfer(self.payload, destination_country="CN")
        self.assertTrue(report.transfer_approved)
        self.assertFalse(report.applied_anonymization)
        self.assertEqual(self.engine.audit_trail[-1]["decision"], "DOMESTIC_APPROVED")

    def test_audit_trail_entries_cannot_be_rewritten_by_caller(self):
        # Regression: audit_trail returned a shallow list copy, so the entry
        # dicts were still shared. A caller could rewrite a BLOCKED decision
        # into an approval after the fact, destroying the compliance record.
        swiss_payload = TradeDataPayload(
            trade_id="TRD_9908", origin_country="CH",
            trader_id="SWISS_TRADER", client_name="Private Bank Client",
            account_number="CH998877", symbol="NESN",
            quantity=50.0, price=100.0
        )
        self.engine.process_data_transfer(swiss_payload, destination_country="US")

        trail = self.engine.audit_trail
        trail[0]["decision"] = "APPROVED_UNRESTRICTED"
        trail[0]["audit_message"] = "tampered"

        self.assertEqual(self.engine.audit_trail[0]["decision"], "BLOCKED")
        self.assertIn("BLOCKED", self.engine.audit_trail[0]["audit_message"])

    def test_report_payload_is_not_aliased_to_caller_input(self):
        # Regression: on domestic and ALLOWED_UNRESTRICTED routes the report
        # held the caller's own object, so mutating the payload afterwards
        # retroactively changed what the compliance record showed.
        uk_payload = TradeDataPayload(
            trade_id="TRD_9909", origin_country="UK",
            trader_id="UK_TRADER", client_name="London Capital",
            account_number="UK112233", symbol="BP",
            quantity=500.0, price=450.0
        )
        report = self.engine.process_data_transfer(uk_payload, destination_country="US")
        self.assertIsNot(report.sanitized_payload, uk_payload)

        uk_payload.client_name = "MUTATED AFTER DECISION"
        self.assertEqual(report.sanitized_payload.client_name, "London Capital")

    def test_invalid_policy_status_rejected_at_registration(self):
        # Regression: a typo'd status used to be silently treated as
        # ALLOWED_UNRESTRICTED (fail-open). It must now fail closed.
        with self.assertRaises(ValueError):
            self.engine.register_policy(
                JurisdictionTransferPolicy("FR", "US", "REQUIERS_ANONYMIZATION", "GDPR")
            )

    def test_invalid_status_via_direct_mutation_is_not_fail_open(self):
        # Regression: even if the policy map is mutated directly, an invalid
        # status must raise rather than approve an unrestricted transfer.
        self.engine.policies[("FR", "US")] = JurisdictionTransferPolicy(
            "FR", "US", "ALWAYS_OK_TRUST_ME", "GDPR"
        )
        fr_payload = TradeDataPayload(
            trade_id="TRD_9905", origin_country="FR",
            trader_id="FR_TRADER", client_name="Paris Alpha",
            account_number="FR1122334455", symbol="MC",
            quantity=5.0, price=700.0
        )
        with self.assertRaises(ValueError):
            self.engine.process_data_transfer(fr_payload, destination_country="US")

    def test_unsalted_plain_sha256_is_not_used_for_tokenization(self):
        # Regression: tokens must not equal an unsalted SHA-256 of the trader ID
        # (dictionary-attack recoverable; EDPB Guidelines 01/2025).
        import hashlib
        plain = hashlib.sha256(b"QUANT_TRADER_42").hexdigest()
        token = self.engine.anonymize_trader_id("QUANT_TRADER_42")
        self.assertTrue(token.startswith("TRD_HASH_"))
        self.assertNotIn(plain[:12], token)
        self.assertNotIn("QUANT_TRADER_42", token)

    def test_tokenization_deterministic_within_engine(self):
        a = self.engine.anonymize_trader_id("TRADER_X")
        b = self.engine.anonymize_trader_id("TRADER_X")
        c = self.engine.anonymize_trader_id("TRADER_Y")
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)

    def test_tokenization_differs_across_keyless_engines(self):
        # Without a shared key, two engines must not produce the same token for
        # the same trader ID (per-engine random salt prevents cross-instance joins).
        other = CrossBorderTradeDataGovernanceEngine()
        self.assertNotEqual(
            self.engine.anonymize_trader_id("TRADER_X"),
            other.anonymize_trader_id("TRADER_X"),
        )

    def test_tokenization_stable_across_engines_with_same_key(self):
        # With an explicit tokenization key, pseudonyms are linkable across
        # engine instances (required for cross-border analytics joins).
        e1 = CrossBorderTradeDataGovernanceEngine(tokenization_key="corp-pseudonym-key-2026")
        e2 = CrossBorderTradeDataGovernanceEngine(tokenization_key="corp-pseudonym-key-2026")
        self.assertEqual(
            e1.anonymize_trader_id("TRADER_X"),
            e2.anonymize_trader_id("TRADER_X"),
        )

    def test_short_account_number_fully_redacted(self):
        self.assertEqual(self.engine.redact_account_number("1234"), "****")
        self.assertEqual(self.engine.redact_account_number("12"), "****")
        self.assertEqual(self.engine.redact_account_number(""), "****")
        self.assertEqual(self.engine.redact_account_number(None), "****")

    def test_invalid_inputs_raise_specific_errors(self):
        with self.assertRaises(ValueError):
            self.engine.process_data_transfer(self.payload, destination_country="")
        with self.assertRaises(ValueError):
            self.engine.process_data_transfer(self.payload, destination_country=None)
        with self.assertRaises(TypeError):
            self.engine.process_data_transfer({"trade_id": "X"}, destination_country="US")
        bad_origin = TradeDataPayload(
            trade_id="TRD_9906", origin_country="",
            trader_id="T", client_name="C", account_number="123456",
            symbol="X", quantity=1.0, price=1.0
        )
        with self.assertRaises(ValueError):
            self.engine.process_data_transfer(bad_origin, destination_country="US")
        with self.assertRaises(ValueError):
            self.engine.anonymize_trader_id("")

    def test_country_codes_are_normalized(self):
        report = self.engine.process_data_transfer(self.payload, destination_country="  us ")
        self.assertTrue(report.transfer_approved)
        self.assertEqual(report.destination_country, "US")
        self.assertEqual(report.origin_country, "CN")

    def test_audit_trail_records_every_decision(self):
        swiss_payload = TradeDataPayload(
            trade_id="TRD_9907", origin_country="CH",
            trader_id="SWISS_TRADER", client_name="Private Bank Client",
            account_number="CH998877", symbol="NESN",
            quantity=50.0, price=100.0
        )
        self.engine.process_data_transfer(self.payload, destination_country="US")       # pseudonymized
        self.engine.process_data_transfer(swiss_payload, destination_country="US")     # blocked
        self.engine.process_data_transfer(self.payload, destination_country="CN")      # domestic

        trail = self.engine.audit_trail
        self.assertEqual(len(trail), 3)
        self.assertEqual(
            [e["decision"] for e in trail],
            ["APPROVED_PSEUDONYMIZED", "BLOCKED", "DOMESTIC_APPROVED"],
        )
        for entry in trail:
            for key in ("timestamp", "trade_id", "origin_country",
                        "destination_country", "decision",
                        "regulatory_framework", "audit_message"):
                self.assertIn(key, entry)
        # Returned trail is a defensive copy: mutating it must not affect the engine.
        trail.clear()
        self.assertEqual(len(self.engine.audit_trail), 3)

if __name__ == '__main__':
    unittest.main()
