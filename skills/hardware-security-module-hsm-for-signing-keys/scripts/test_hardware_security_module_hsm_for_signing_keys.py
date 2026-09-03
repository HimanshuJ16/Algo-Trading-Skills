"""
Tests for the HSM signing policy engine.

an earlier suite asserted only that a signature string was non-empty and that
`is_signature_valid` was True - a field hard-coded to True. Every test below
asserts a behaviour that can actually fail: the curve order is cross-checked
against an independently published constant, denials are asserted to appear in
the audit log, and each regression test names the the older behaviour it pins.
"""
import logging
import threading
import unittest

from hardware_security_module_hsm_for_signing_keys import (
    ALLOWED_INPUT_ENCODINGS,
    FIPS_140_2_PROGRAM_HISTORICAL_EPOCH,
    HsmAuthorizationError,
    HsmKeyAlreadyRegisteredError,
    HsmKeyNotFoundError,
    HsmPolicyViolationError,
    HsmSignatureRequest,
    HsmSignerError,
    HsmSigningManagerEngine,
    RiskLevel,
    SECP256K1_HALF_ORDER,
    SECP256K1_ORDER,
    SigningInputEncoding,
    SigningStatus,
    is_low_s,
    normalize_secp256k1_low_s,
)

# Keep the engine's denial warnings out of the test runner's output.
logging.disable(logging.CRITICAL)

# 2026-08-25T00:00:00Z and 2026-06-01T00:00:00Z - fixed clocks, so reports are
# reproducible and the FIPS 140-2 boundary can be tested from both sides.
NOW = 1_787_616_000.0
BEFORE_FIPS_SUNSET = 1_780_272_000.0

DIGEST_32 = bytes(range(32))


def stub_signer(signature: bytes):
    """A stand-in for a PKCS#11 C_Sign binding that returns a fixed signature."""
    def _sign(meta, signing_input: bytes) -> bytes:
        return signature
    return _sign


def rs_signature(r: int, s: int) -> bytes:
    """Build a raw 64-byte r||s secp256k1 signature from integers."""
    return r.to_bytes(32, "big") + s.to_bytes(32, "big")


VALID_LOW_S_SIG = rs_signature(1, 2)


class TestSecp256k1Constants(unittest.TestCase):
    """The curve constants are checked against independently published values."""

    def test_order_matches_bip146_low_s_bound(self):
        # BIP-146 publishes the inclusive upper bound of the low-S range as
        # this literal. Deriving it from SECP256K1_ORDER // 2 and comparing
        # against the published value cross-checks the order constant against a
        # source outside this module.
        bip146_low_s_upper_bound = int(
            "7FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF"
            "5D576E7357A4501DDFE92F46681B20A0", 16
        )
        self.assertEqual(SECP256K1_HALF_ORDER, bip146_low_s_upper_bound)
        self.assertEqual(SECP256K1_ORDER.bit_length(), 256)


class TestLowSNormalization(unittest.TestCase):

    def test_high_s_is_flipped_to_n_minus_s(self):
        high_s = SECP256K1_HALF_ORDER + 1
        normalized, changed = normalize_secp256k1_low_s(rs_signature(7, high_s))
        self.assertTrue(changed)
        self.assertEqual(normalized[:32], (7).to_bytes(32, "big"))
        self.assertEqual(
            int.from_bytes(normalized[32:], "big"), SECP256K1_ORDER - high_s
        )
        self.assertTrue(is_low_s(normalized))

    def test_low_s_is_returned_unchanged(self):
        signature = rs_signature(7, SECP256K1_HALF_ORDER)
        normalized, changed = normalize_secp256k1_low_s(signature)
        self.assertFalse(changed)
        self.assertEqual(normalized, signature)

    def test_boundary_is_inclusive_at_half_order(self):
        # BIP-146 makes n/2 itself a LOW s value; one above it is high.
        self.assertTrue(is_low_s(rs_signature(7, SECP256K1_HALF_ORDER)))
        self.assertFalse(is_low_s(rs_signature(7, SECP256K1_HALF_ORDER + 1)))

    def test_normalization_is_idempotent(self):
        once, _ = normalize_secp256k1_low_s(rs_signature(7, SECP256K1_HALF_ORDER + 99))
        twice, changed = normalize_secp256k1_low_s(once)
        self.assertFalse(changed)
        self.assertEqual(once, twice)

    def test_der_length_signature_is_rejected_not_silently_accepted(self):
        with self.assertRaises(ValueError):
            normalize_secp256k1_low_s(b"\x30" * 71)

    def test_zero_and_out_of_range_components_are_rejected(self):
        with self.assertRaises(ValueError):
            normalize_secp256k1_low_s(rs_signature(0, 1))
        with self.assertRaises(ValueError):
            normalize_secp256k1_low_s(rs_signature(1, 0))
        with self.assertRaises(ValueError):
            normalize_secp256k1_low_s(
                (SECP256K1_ORDER - 1).to_bytes(32, "big") + b"\xff" * 32
            )


class TestKeyRegistration(unittest.TestCase):

    def setUp(self):
        self.engine = HsmSigningManagerEngine()

    def test_duplicate_alias_raises_instead_of_overwriting(self):
        # Regression: an earlier generate_hardware_key silently replaced an
        # existing alias, destroying the metadata of a live signing key.
        self.engine.register_hardware_key("CUSTODY_HOT_01", slot_id=1)
        with self.assertRaises(HsmKeyAlreadyRegisteredError):
            self.engine.register_hardware_key("CUSTODY_HOT_01", slot_id=9)
        self.assertEqual(self.engine.get_key("CUSTODY_HOT_01").slot_id, 1)

    def test_blank_alias_is_rejected(self):
        # Regression: a naive engine accepted "" and "   " as key aliases.
        for alias in ("", "   ", "\t"):
            with self.assertRaises(ValueError):
                self.engine.register_hardware_key(alias)

    def test_unsupported_algorithm_and_negative_slot_are_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.register_hardware_key("K", algorithm="RSA_PSS")
        with self.assertRaises(ValueError):
            self.engine.register_hardware_key("K", slot_id=-1)

    def test_unrecognised_fips_label_is_rejected_not_accepted_blindly(self):
        with self.assertRaises(ValueError):
            self.engine.register_hardware_key("K", fips_certification="FIPS_140_2_LEVEL_5")

    def test_unknown_alias_lookup_raises_key_not_found(self):
        with self.assertRaises(HsmKeyNotFoundError):
            self.engine.get_key("NOPE")
        # Still a ValueError, as an earlier API raised.
        with self.assertRaises(ValueError):
            self.engine.get_key("NOPE")


class TestKeyAttributeAudit(unittest.TestCase):

    def setUp(self):
        self.engine = HsmSigningManagerEngine()

    def _levels(self, alias, now=NOW):
        return [f.risk_level for f in self.engine.audit_key_attributes(alias, now)]

    def test_fully_protected_current_module_is_clean(self):
        self.engine.register_hardware_key(
            "CLEAN", fips_certification="FIPS_140_3_LEVEL_3",
            fips_certificate_number="4703",
        )
        self.assertEqual(self.engine.audit_key_attributes("CLEAN", NOW), [])

    def test_extractable_key_is_critical(self):
        self.engine.register_hardware_key(
            "BAD", extractable=True, never_extractable=False,
            fips_certificate_number="4703",
        )
        self.assertIn(RiskLevel.CRITICAL, self._levels("BAD"))

    def test_non_sensitive_key_is_critical_even_when_non_extractable(self):
        # CKA_EXTRACTABLE=False does not stop C_GetAttributeValue reading the
        # value; an earlier model tracked only extractability.
        self.engine.register_hardware_key(
            "READABLE", sensitive=False, always_sensitive=False,
            fips_certificate_number="4703",
        )
        levels = self._levels("READABLE")
        self.assertEqual(levels[0], RiskLevel.CRITICAL)
        self.assertIn(RiskLevel.HIGH, levels)

    def test_never_extractable_false_is_high_even_when_currently_protected(self):
        self.engine.register_hardware_key(
            "WAS_EXPOSED", extractable=False, never_extractable=False,
            fips_certificate_number="4703",
        )
        self.assertEqual(self._levels("WAS_EXPOSED"), [RiskLevel.HIGH])

    def test_fips_140_2_after_program_sunset_is_high(self):
        self.engine.register_hardware_key(
            "LEGACY", fips_certification="FIPS_140_2_LEVEL_3",
            fips_certificate_number="4218",
        )
        findings = self.engine.audit_key_attributes(
            "LEGACY", FIPS_140_2_PROGRAM_HISTORICAL_EPOCH
        )
        self.assertEqual([f.risk_level for f in findings], [RiskLevel.HIGH])
        self.assertIn("Historical List", findings[0].issue)

    def test_fips_140_2_before_sunset_is_medium(self):
        self.engine.register_hardware_key(
            "LEGACY", fips_certification="FIPS_140_2_LEVEL_3",
            fips_certificate_number="4218",
        )
        findings = self.engine.audit_key_attributes("LEGACY", BEFORE_FIPS_SUNSET)
        self.assertEqual([f.risk_level for f in findings], [RiskLevel.MEDIUM])

    def test_per_certificate_historical_date_overrides_program_backstop(self):
        # AWS CloudHSM hsm1.medium (cert #4218) moved to the Historical List on
        # 2026-01-04, well before the program-wide 2026-09-22 date.
        cloudhsm_hsm1_historical = 1_767_484_800.0
        self.engine.register_hardware_key(
            "CLOUDHSM1", fips_certification="FIPS_140_2_LEVEL_3",
            fips_certificate_number="4218",
            fips_historical_epoch=cloudhsm_hsm1_historical,
        )
        self.assertLess(cloudhsm_hsm1_historical, FIPS_140_2_PROGRAM_HISTORICAL_EPOCH)
        findings = self.engine.audit_key_attributes("CLOUDHSM1", cloudhsm_hsm1_historical)
        self.assertEqual([f.risk_level for f in findings], [RiskLevel.HIGH])

    def test_missing_cmvp_certificate_number_is_low(self):
        self.engine.register_hardware_key("UNCITED")
        self.assertEqual(self._levels("UNCITED"), [RiskLevel.LOW])


class TestExportRejection(unittest.TestCase):

    def setUp(self):
        self.engine = HsmSigningManagerEngine()
        self.engine.register_hardware_key("CUSTODY_HOT_01", slot_id=1)

    def test_export_attempt_raises_and_is_audited(self):
        with self.assertRaises(HsmPolicyViolationError):
            self.engine.attempt_export_private_key("CUSTODY_HOT_01", NOW)
        # Still a PermissionError, as an earlier API raised.
        log = self.engine.audit_log
        self.assertEqual(len(log), 1)
        self.assertEqual(log[0].status, SigningStatus.EXPORT_ATTEMPT_REJECTED.value)
        self.assertTrue(self.engine.verify_audit_chain())

    def test_export_raises_permission_error_subclass(self):
        with self.assertRaises(PermissionError):
            self.engine.attempt_export_private_key("CUSTODY_HOT_01", NOW)

    def test_export_of_an_extractable_key_still_raises(self):
        # Regression: an earlier method returned None (a silent pass) whenever
        # is_extractable was True.
        self.engine.register_hardware_key("WEAK", extractable=True)
        with self.assertRaises(HsmPolicyViolationError):
            self.engine.attempt_export_private_key("WEAK", NOW)
        self.assertIn("CRITICAL", self.engine.audit_log[-1].detail)


class TestSigning(unittest.TestCase):

    def setUp(self):
        self.engine = HsmSigningManagerEngine()
        self.engine.register_hardware_key(
            "CUSTODY_HOT_01", algorithm="SECP256K1", slot_id=1,
            fips_certificate_number="4703",
        )

    def _request(self, **overrides):
        kwargs = dict(
            key_alias="CUSTODY_HOT_01",
            signing_input=DIGEST_32,
            input_encoding=SigningInputEncoding.KECCAK256_DIGEST.value,
            caller_identity="algo_execution_bot_01",
            caller_role="OPERATOR",
        )
        kwargs.update(overrides)
        return HsmSignatureRequest(**kwargs)

    def test_successful_signature_is_reported_and_audited(self):
        report = self.engine.sign_transaction_payload(
            self._request(), stub_signer(VALID_LOW_S_SIG), NOW
        )
        self.assertEqual(report.status, SigningStatus.SIGNATURE_SUCCESS.value)
        self.assertTrue(report.is_signature_well_formed)
        self.assertFalse(report.is_key_extractable)
        self.assertEqual(report.signature_hex, VALID_LOW_S_SIG.hex())
        self.assertEqual(self.engine.audit_log[-1].sequence, 0)
        self.assertTrue(self.engine.verify_audit_chain())

    def test_engine_signs_the_exact_bytes_given_without_rehashing(self):
        # Regression: a naive engine ran SHA-256 over whatever it was
        # handed, so a caller passing an Ethereum Keccak-256 sighash got a
        # signature over sha256(keccak256(tx)) - valid, but over the wrong value.
        seen = {}

        def capturing_signer(meta, signing_input):
            seen["input"] = signing_input
            return VALID_LOW_S_SIG

        self.engine.sign_transaction_payload(self._request(), capturing_signer, NOW)
        self.assertEqual(seen["input"], DIGEST_32)

    def test_high_s_signature_from_device_is_normalized(self):
        high_s = rs_signature(7, SECP256K1_HALF_ORDER + 1)
        report = self.engine.sign_transaction_payload(
            self._request(), stub_signer(high_s), NOW
        )
        self.assertTrue(report.was_low_s_normalization_applied)
        self.assertTrue(report.is_low_s_normalized)
        self.assertTrue(is_low_s(bytes.fromhex(report.signature_hex)))
        self.assertNotEqual(report.signature_hex, high_s.hex())

    def test_low_s_enforcement_can_be_disabled_and_is_reported_honestly(self):
        engine = HsmSigningManagerEngine(enforce_low_s=False)
        engine.register_hardware_key("K", algorithm="SECP256K1")
        high_s = rs_signature(7, SECP256K1_HALF_ORDER + 1)
        report = engine.sign_transaction_payload(
            self._request(key_alias="K"), stub_signer(high_s), NOW
        )
        self.assertEqual(report.signature_hex, high_s.hex())
        self.assertFalse(report.is_low_s_normalized)

    def test_unauthorized_role_is_denied_and_audited(self):
        with self.assertRaises(HsmAuthorizationError):
            self.engine.sign_transaction_payload(
                self._request(caller_role="AUDITOR"), stub_signer(VALID_LOW_S_SIG), NOW
            )
        record = self.engine.audit_log[-1]
        self.assertEqual(record.status, SigningStatus.AUTHORIZATION_DENIED.value)
        self.assertEqual(record.caller_role, "AUDITOR")
        self.assertIsNone(record.signature_hex)

    def test_admin_cannot_sign_by_default_but_can_be_allowed_explicitly(self):
        # Segregation of duties: a naive engine let ADMIN sign implicitly.
        with self.assertRaises(HsmAuthorizationError):
            self.engine.sign_transaction_payload(
                self._request(caller_role="ADMIN"), stub_signer(VALID_LOW_S_SIG), NOW
            )
        permissive = HsmSigningManagerEngine(allowed_signing_roles=("OPERATOR", "ADMIN"))
        permissive.register_hardware_key("K", algorithm="SECP256K1")
        report = permissive.sign_transaction_payload(
            self._request(key_alias="K", caller_role="admin"),
            stub_signer(VALID_LOW_S_SIG), NOW,
        )
        self.assertEqual(report.status, SigningStatus.SIGNATURE_SUCCESS.value)

    def test_raw_message_is_rejected_for_ecdsa(self):
        with self.assertRaises(HsmPolicyViolationError):
            self.engine.sign_transaction_payload(
                self._request(
                    signing_input=b"TRANSFER 10 BTC",
                    input_encoding=SigningInputEncoding.RAW_MESSAGE.value,
                ),
                stub_signer(VALID_LOW_S_SIG), NOW,
            )
        self.assertEqual(
            self.engine.audit_log[-1].status,
            SigningStatus.INPUT_DOMAIN_VIOLATION.value,
        )

    def test_digest_is_rejected_for_pure_ed25519(self):
        # Pure Ed25519 signs the message; handing it a digest signs the digest.
        self.engine.register_hardware_key("ED", algorithm="ED25519")
        with self.assertRaises(HsmPolicyViolationError):
            self.engine.sign_transaction_payload(
                self._request(key_alias="ED"),
                stub_signer(bytes(64)), NOW,
            )

    def test_ed25519_accepts_a_raw_message(self):
        self.engine.register_hardware_key("ED", algorithm="ED25519")
        report = self.engine.sign_transaction_payload(
            self._request(
                key_alias="ED",
                signing_input=b"TRANSFER 10 SOL",
                input_encoding=SigningInputEncoding.RAW_MESSAGE.value,
            ),
            stub_signer(bytes(range(64))), NOW,
        )
        self.assertEqual(report.status, SigningStatus.SIGNATURE_SUCCESS.value)
        self.assertIsNone(report.is_low_s_normalized)
        self.assertFalse(report.was_low_s_normalization_applied)

    def test_wrong_length_digest_is_rejected(self):
        # CKM_ECDSA truncates over-long input, silently signing a different value.
        for bad in (DIGEST_32[:31], DIGEST_32 + b"\x00"):
            with self.assertRaises(HsmPolicyViolationError):
                self.engine.sign_transaction_payload(
                    self._request(signing_input=bad), stub_signer(VALID_LOW_S_SIG), NOW
                )

    def test_unrecognised_encoding_fails_closed(self):
        with self.assertRaises(HsmPolicyViolationError):
            self.engine.sign_transaction_payload(
                self._request(input_encoding="SHA3_256"),
                stub_signer(VALID_LOW_S_SIG), NOW,
            )

    def test_der_encoded_signature_from_device_is_rejected(self):
        with self.assertRaises(HsmSignerError):
            self.engine.sign_transaction_payload(
                self._request(), stub_signer(b"\x30" * 71), NOW
            )
        self.assertEqual(
            self.engine.audit_log[-1].status,
            SigningStatus.MALFORMED_SIGNATURE.value,
        )

    def test_signer_exception_is_wrapped_audited_and_warns_about_retry(self):
        def failing_signer(meta, signing_input):
            raise TimeoutError("C_Sign timed out")

        with self.assertRaises(HsmSignerError) as ctx:
            self.engine.sign_transaction_payload(self._request(), failing_signer, NOW)
        self.assertIn("NOT proof that nothing was signed", str(ctx.exception))
        self.assertEqual(
            self.engine.audit_log[-1].status, SigningStatus.SIGNER_FAILED.value
        )

    def test_unknown_alias_is_audited_before_raising(self):
        with self.assertRaises(HsmKeyNotFoundError):
            self.engine.sign_transaction_payload(
                self._request(key_alias="GHOST"), stub_signer(VALID_LOW_S_SIG), NOW
            )
        self.assertEqual(
            self.engine.audit_log[-1].status, SigningStatus.KEY_NOT_FOUND.value
        )

    def test_disabled_key_cannot_sign(self):
        self.engine.disable_key("CUSTODY_HOT_01", "suspected compromise", NOW)
        with self.assertRaises(HsmPolicyViolationError):
            self.engine.sign_transaction_payload(
                self._request(), stub_signer(VALID_LOW_S_SIG), NOW
            )
        self.assertEqual(
            self.engine.audit_log[-1].status, SigningStatus.KEY_DISABLED.value
        )

    def test_blank_caller_identity_and_empty_input_are_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.sign_transaction_payload(
                self._request(caller_identity="   "), stub_signer(VALID_LOW_S_SIG), NOW
            )
        with self.assertRaises(ValueError):
            self.engine.sign_transaction_payload(
                self._request(signing_input=b""), stub_signer(VALID_LOW_S_SIG), NOW
            )

    def test_str_signing_input_is_rejected(self):
        with self.assertRaises(TypeError):
            self.engine.sign_transaction_payload(
                self._request(signing_input="not bytes"),
                stub_signer(VALID_LOW_S_SIG), NOW,
            )

    def test_non_callable_signer_is_rejected(self):
        with self.assertRaises(TypeError):
            self.engine.sign_transaction_payload(self._request(), None, NOW)


class TestAuditChain(unittest.TestCase):

    def setUp(self):
        self.engine = HsmSigningManagerEngine()
        self.engine.register_hardware_key("K", algorithm="SECP256K1")

    def _sign(self, role="OPERATOR"):
        request = HsmSignatureRequest(
            key_alias="K", signing_input=DIGEST_32,
            input_encoding=SigningInputEncoding.SHA256_DIGEST.value,
            caller_identity="bot", caller_role=role,
        )
        return self.engine.sign_transaction_payload(request, stub_signer(VALID_LOW_S_SIG), NOW)

    def test_chain_links_successes_and_denials_in_order(self):
        self._sign()
        with self.assertRaises(HsmAuthorizationError):
            self._sign(role="AUDITOR")
        self._sign()
        log = self.engine.audit_log
        self.assertEqual([r.sequence for r in log], [0, 1, 2])
        self.assertEqual(log[0].previous_record_hash, "0" * 64)
        self.assertEqual(log[1].previous_record_hash, log[0].record_hash)
        self.assertEqual(log[2].previous_record_hash, log[1].record_hash)
        self.assertTrue(self.engine.verify_audit_chain())

    def test_verification_fails_when_a_record_is_altered(self):
        self._sign()
        self._sign()
        original = self.engine._audit_log[0]
        self.engine._audit_log[0] = type(original)(
            **{**original.__dict__, "caller_identity": "someone_else"}
        )
        self.assertFalse(self.engine.verify_audit_chain())

    def test_verification_fails_when_a_record_is_deleted(self):
        self._sign()
        self._sign()
        del self.engine._audit_log[0]
        self.assertFalse(self.engine.verify_audit_chain())

    def test_audit_log_property_returns_an_immutable_snapshot(self):
        self._sign()
        snapshot = self.engine.audit_log
        self.assertIsInstance(snapshot, tuple)
        self._sign()
        self.assertEqual(len(snapshot), 1)
        self.assertEqual(len(self.engine.audit_log), 2)

    def test_chain_stays_gap_free_under_concurrent_signing(self):
        # The skill's own stated use is concurrent trading threads; an
        # unsynchronised list would drop or duplicate sequence numbers.
        threads = [threading.Thread(target=self._sign) for _ in range(24)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        log = self.engine.audit_log
        self.assertEqual([r.sequence for r in log], list(range(24)))
        self.assertTrue(self.engine.verify_audit_chain())


class TestConfiguration(unittest.TestCase):

    def test_empty_role_allowlist_is_rejected(self):
        with self.assertRaises(ValueError):
            HsmSigningManagerEngine(allowed_signing_roles=())

    def test_every_algorithm_declares_its_allowed_encodings(self):
        from hardware_security_module_hsm_for_signing_keys import (
            EXPECTED_SIGNATURE_LENGTHS,
        )
        self.assertEqual(
            set(ALLOWED_INPUT_ENCODINGS), set(EXPECTED_SIGNATURE_LENGTHS)
        )


if __name__ == "__main__":
    unittest.main()
