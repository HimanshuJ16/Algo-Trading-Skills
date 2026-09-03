import json
import logging
import unittest

from air_gapped_signing_workflow_for_cold_storage import (
    AirGapSigningError,
    BroadcastResult,
    BroadcastStatus,
    OfflineAirGappedSigner,
    OnlineCoordinator,
    SignedPayload,
    UnsignedPayload,
)

logging.disable(logging.CRITICAL)

ADDRESS = "0x" + "a" * 40
OTHER_ADDRESS = "0x" + "b" * 40
MAINNET = 1


def approve_all(display):
    return True


def payload_kwargs(**overrides):
    values = {
        "destination_address": ADDRESS,
        "amount": "1",
        "network": "ETH",
        "chain_id": MAINNET,
        "nonce": 1,
        "version": 2,
    }
    values.update(overrides)
    return values


class RecordingAdapter:
    """Stands in for the RPC client, with a scriptable failure mode."""

    def __init__(self, reference="0xtxhash", raises=None):
        self.reference = reference
        self.raises = raises
        self.calls = []

    def __call__(self, payload):
        self.calls.append(payload)
        if self.raises is not None:
            raise self.raises
        return self.reference


class WorkflowTestBase(unittest.TestCase):
    def setUp(self):
        self.approvals = []
        self.vault = OfflineAirGappedSigner(
            "super_secret_air_gapped_key",
            approval_callback=self._record_approval,
            expected_chain_id=MAINNET,
        )
        self.adapter = RecordingAdapter()
        self.coordinator = OnlineCoordinator(
            self.vault.verification_key,
            chain_id=MAINNET,
            broadcast_adapter=self.adapter,
        )

    def _record_approval(self, display):
        self.approvals.append(display)
        return True

    def sign_new_transfer(self, address=ADDRESS, amount="5"):
        unsigned = self.coordinator.create_unsigned_transfer(address, amount)
        return unsigned, self.vault.sign_qr_payload(unsigned.to_qr_code_data())


class TestHappyPath(WorkflowTestBase):
    def test_full_workflow_accepts_once_and_rejects_replay(self):
        _, signed = self.sign_new_transfer(amount="50.000000000000000001")
        self.assertIsNotNone(signed)

        result = self.coordinator.broadcast_to_network(signed)
        self.assertIs(result.status, BroadcastStatus.ACCEPTED)
        self.assertTrue(result.is_accepted)
        self.assertEqual(result.transaction_reference, "0xtxhash")
        self.assertEqual(len(self.adapter.calls), 1)

        replay = self.coordinator.broadcast_to_network(signed)
        self.assertIs(replay.status, BroadcastStatus.REJECTED)
        self.assertEqual(replay.reason, "payload already submitted")
        # The replay must never reach the RPC layer at all.
        self.assertEqual(len(self.adapter.calls), 1)

    def test_envelope_survives_a_round_trip_over_the_return_medium(self):
        _, signed = self.sign_new_transfer()
        restored = SignedPayload.from_transport_data(signed.to_transport_data())
        self.assertEqual(restored, signed)
        self.assertIs(
            self.coordinator.broadcast_to_network(restored).status,
            BroadcastStatus.ACCEPTED,
        )


class TestClearSigningApprovalGate(WorkflowTestBase):
    """Regression cover for a vault that used to sign every well-formed payload."""

    def test_vault_without_an_approval_callback_refuses_to_sign(self):
        blind_vault = OfflineAirGappedSigner("k")
        payload = UnsignedPayload(**payload_kwargs())
        self.assertIsNone(blind_vault.sign_qr_payload(payload.to_qr_code_data()))

    def test_approver_sees_the_exact_payload_that_will_be_signed(self):
        unsigned, signed = self.sign_new_transfer(amount="2.5")
        self.assertEqual(len(self.approvals), 1)
        display = self.approvals[0]
        self.assertIn(ADDRESS, display)
        self.assertIn("2.5", display)
        self.assertIn(f"Chain ID:    {MAINNET}", display)
        self.assertIn(f"Nonce:       {unsigned.nonce}", display)
        # The hash shown is the hash actually signed and returned.
        self.assertIn(unsigned.payload_hash(), display)
        self.assertEqual(signed.original_payload_hash, unsigned.payload_hash())

    def test_denial_and_non_true_and_raising_approvers_all_fail_closed(self):
        payload = UnsignedPayload(**payload_kwargs())
        cases = {
            "explicit denial": lambda display: False,
            "truthy sentinel": lambda display: "yes",
            "truthy object": lambda display: object(),
            "none": lambda display: None,
            "raises": lambda display: (_ for _ in ()).throw(RuntimeError("ui crash")),
        }
        for label, callback in cases.items():
            with self.subTest(approver=label):
                vault = OfflineAirGappedSigner("k", approval_callback=callback)
                self.assertIsNone(vault.sign_qr_payload(payload.to_qr_code_data()))


class TestVaultPolicy(WorkflowTestBase):
    def test_vault_rejects_a_chain_it_does_not_sign_for(self):
        payload = UnsignedPayload(**payload_kwargs(chain_id=137))
        self.assertIsNone(self.vault.sign_qr_payload(payload.to_qr_code_data()))
        self.assertEqual(self.approvals, [])  # denied before a human is asked

    def test_destination_allowlist_and_amount_ceiling_are_enforced(self):
        vault = OfflineAirGappedSigner(
            "k",
            approval_callback=approve_all,
            allowed_destinations=[ADDRESS.upper()],
            max_amount="10",
        )
        allowed = UnsignedPayload(**payload_kwargs(amount="10"))
        self.assertIsNotNone(vault.sign_qr_payload(allowed.to_qr_code_data()))

        for label, payload in (
            ("off allowlist", UnsignedPayload(**payload_kwargs(destination_address=OTHER_ADDRESS, nonce=2))),
            ("over ceiling", UnsignedPayload(**payload_kwargs(amount="10.000000000000000001", nonce=3))),
        ):
            with self.subTest(case=label):
                self.assertIsNone(vault.sign_qr_payload(payload.to_qr_code_data()))

    def test_vault_keeps_its_own_replay_ledger(self):
        """The coordinator is the assumed adversary, so its dedupe cannot be trusted."""
        payload = UnsignedPayload(**payload_kwargs(nonce=7))
        self.assertIsNotNone(self.vault.sign_qr_payload(payload.to_qr_code_data()))
        self.assertIsNone(self.vault.sign_qr_payload(payload.to_qr_code_data()))

    def test_monotonic_nonce_blocks_an_older_intent_unless_disabled(self):
        newer = UnsignedPayload(**payload_kwargs(nonce=9))
        older = UnsignedPayload(**payload_kwargs(nonce=4, amount="2"))
        self.assertIsNotNone(self.vault.sign_qr_payload(newer.to_qr_code_data()))
        self.assertIsNone(self.vault.sign_qr_payload(older.to_qr_code_data()))

        relaxed = OfflineAirGappedSigner(
            "k", approval_callback=approve_all, enforce_monotonic_nonce=False
        )
        self.assertIsNotNone(relaxed.sign_qr_payload(newer.to_qr_code_data()))
        self.assertIsNotNone(relaxed.sign_qr_payload(older.to_qr_code_data()))


class TestChainBinding(WorkflowTestBase):
    """EIP-155: a signed intent must name the chain, not just the network label."""

    def test_chain_id_is_mandatory_and_canonicalised_into_the_hash(self):
        with self.assertRaises(TypeError):
            UnsignedPayload(ADDRESS, "1", "ETH")  # type: ignore[call-arg]
        mainnet = UnsignedPayload(**payload_kwargs(chain_id=1))
        other = UnsignedPayload(**payload_kwargs(chain_id=137))
        self.assertNotEqual(mainnet.payload_hash(), other.payload_hash())
        self.assertIn('"chain_id":1', mainnet.to_qr_code_data())

    def test_version_1_payloads_without_a_chain_id_are_rejected(self):
        legacy = json.dumps(
            {
                "destination_address": ADDRESS,
                "amount": "1",
                "network": "ETH",
                "nonce": 1,
                "version": 1,
            }
        )
        with self.assertRaises(AirGapSigningError):
            UnsignedPayload.from_qr_code_data(legacy)
        self.assertIsNone(self.vault.sign_qr_payload(legacy))

    def test_coordinator_rejects_an_envelope_for_another_chain(self):
        other_chain = OnlineCoordinator(
            self.vault.verification_key, chain_id=137, broadcast_adapter=self.adapter
        )
        unsigned = other_chain.create_unsigned_transfer(ADDRESS, "1")
        vault = OfflineAirGappedSigner(
            "super_secret_air_gapped_key", approval_callback=approve_all
        )
        signed = vault.sign_qr_payload(unsigned.to_qr_code_data())
        self.assertIsNotNone(signed)
        # Same signer, same key, valid signature — but issued for a different chain.
        result = self.coordinator.broadcast_to_network(signed)
        self.assertIs(result.status, BroadcastStatus.REJECTED)
        self.assertEqual(len(self.adapter.calls), 0)


class TestAmbiguousBroadcast(WorkflowTestBase):
    """A lost RPC response is unknown, never failed."""

    def test_adapter_exception_yields_unresolved_and_blocks_resubmission(self):
        adapter = RecordingAdapter(raises=TimeoutError("read timed out"))
        coordinator = OnlineCoordinator(
            self.vault.verification_key, chain_id=MAINNET, broadcast_adapter=adapter
        )
        unsigned = coordinator.create_unsigned_transfer(ADDRESS, "5")
        signed = self.vault.sign_qr_payload(unsigned.to_qr_code_data())

        result = coordinator.broadcast_to_network(signed)
        self.assertIs(result.status, BroadcastStatus.UNRESOLVED)
        self.assertTrue(result.needs_reconciliation)
        self.assertFalse(result.is_accepted)
        self.assertIn(unsigned.payload_hash(), coordinator.unresolved_payload_hashes)

        # The dangerous move is retrying a timeout. The payload is already
        # consumed, so a second attempt cannot reach the adapter again.
        retry = coordinator.broadcast_to_network(signed)
        self.assertIs(retry.status, BroadcastStatus.REJECTED)
        self.assertEqual(retry.reason, "payload already submitted")
        self.assertEqual(len(adapter.calls), 1)

    def test_adapter_returning_no_reference_is_also_unresolved(self):
        for reference in ("", "   ", None, 12345):
            with self.subTest(reference=reference):
                # A fresh vault per case: the vault's own replay ledger would
                # otherwise refuse to re-sign nonce 1 for each new coordinator.
                vault = OfflineAirGappedSigner("k", approval_callback=approve_all)
                adapter = RecordingAdapter(reference=reference)
                coordinator = OnlineCoordinator(
                    vault.verification_key, chain_id=MAINNET, broadcast_adapter=adapter
                )
                unsigned = coordinator.create_unsigned_transfer(ADDRESS, "5")
                signed = vault.sign_qr_payload(unsigned.to_qr_code_data())
                self.assertIsNotNone(signed)
                self.assertIs(
                    coordinator.broadcast_to_network(signed).status,
                    BroadcastStatus.UNRESOLVED,
                )

    def test_reconciliation_clears_the_unresolved_record(self):
        adapter = RecordingAdapter(raises=ConnectionError("connection reset"))
        coordinator = OnlineCoordinator(
            self.vault.verification_key, chain_id=MAINNET, broadcast_adapter=adapter
        )
        unsigned = coordinator.create_unsigned_transfer(ADDRESS, "5")
        signed = self.vault.sign_qr_payload(unsigned.to_qr_code_data())
        coordinator.broadcast_to_network(signed)

        digest = unsigned.payload_hash()
        self.assertTrue(coordinator.resolve_unresolved(digest, landed_on_chain=True))
        self.assertEqual(coordinator.unresolved_payload_hashes, frozenset())
        self.assertFalse(coordinator.resolve_unresolved(digest, landed_on_chain=True))
        # Reconciling does not re-open the payload for submission.
        self.assertIs(
            coordinator.broadcast_to_network(signed).status, BroadcastStatus.REJECTED
        )

    def test_result_has_no_truth_value(self):
        result = BroadcastResult(BroadcastStatus.REJECTED, "nope")
        with self.assertRaises(TypeError):
            bool(result)

    def test_coordinator_without_an_adapter_cannot_broadcast(self):
        coordinator = OnlineCoordinator(self.vault.verification_key, chain_id=MAINNET)
        unsigned = coordinator.create_unsigned_transfer(ADDRESS, "1")
        signed = self.vault.sign_qr_payload(unsigned.to_qr_code_data())
        result = coordinator.broadcast_to_network(signed)
        self.assertIs(result.status, BroadcastStatus.REJECTED)
        self.assertEqual(result.reason, "no broadcast adapter configured")


class TestEnvelopeValidation(WorkflowTestBase):
    """Envelopes arrive from untrusted media and must fail closed, not raise."""

    def test_hostile_envelope_fields_are_rejected_at_construction(self):
        _, signed = self.sign_new_transfer()
        hostile = [
            ("non-string signature", {"signature": 12345}),
            ("short signature", {"signature": "abc"}),
            ("uppercase hash", {"original_payload_hash": "A" * 64}),
            ("non-ascii signature", {"signature": "é" * 64}),
            ("empty payload", {"unsigned_payload": ""}),
            ("blank signer", {"signer_key_id": "  "}),
            ("non-string signer", {"signer_key_id": None}),
        ]
        for label, override in hostile:
            with self.subTest(case=label):
                fields = {
                    "unsigned_payload": signed.unsigned_payload,
                    "original_payload_hash": signed.original_payload_hash,
                    "signature": signed.signature,
                    "signer_key_id": signed.signer_key_id,
                }
                fields.update(override)
                with self.assertRaises(AirGapSigningError):
                    SignedPayload(**fields)

    def test_malformed_transport_data_is_rejected(self):
        _, signed = self.sign_new_transfer()
        payloads = ["not-json", "[]", "null", json.dumps({"signature": "a" * 64})]
        for raw in payloads:
            with self.subTest(raw=raw[:20]):
                with self.assertRaises(AirGapSigningError):
                    SignedPayload.from_transport_data(raw)

        extra = json.loads(signed.to_transport_data())
        extra["unexpected"] = True
        with self.assertRaises(AirGapSigningError):
            SignedPayload.from_transport_data(json.dumps(extra))

    def test_non_envelope_objects_are_rejected_without_raising(self):
        for value in (None, "a string", 42, {"signature": "x"}):
            with self.subTest(value=value):
                result = self.coordinator.broadcast_to_network(value)
                self.assertIs(result.status, BroadcastStatus.REJECTED)

    def test_tampered_signature_and_rebound_payload_are_rejected(self):
        _, signed = self.sign_new_transfer()
        tampered = SignedPayload(
            signed.unsigned_payload, signed.original_payload_hash, "0" * 64, signed.signer_key_id
        )
        self.assertIs(
            self.coordinator.broadcast_to_network(tampered).status,
            BroadcastStatus.REJECTED,
        )

        changed = json.loads(signed.unsigned_payload)
        changed["amount"] = "6"
        mismatched = SignedPayload(
            json.dumps(changed, sort_keys=True, separators=(",", ":")),
            signed.original_payload_hash,
            signed.signature,
            signed.signer_key_id,
        )
        self.assertIs(
            self.coordinator.broadcast_to_network(mismatched).status,
            BroadcastStatus.REJECTED,
        )
        self.assertEqual(len(self.adapter.calls), 0)

    def test_unknown_intent_and_foreign_signer_are_rejected(self):
        foreign = OfflineAirGappedSigner("other-key", approval_callback=approve_all)
        never_issued = UnsignedPayload(**payload_kwargs(nonce=99))
        signed = foreign.sign_qr_payload(never_issued.to_qr_code_data())
        result = self.coordinator.broadcast_to_network(signed)
        self.assertIs(result.status, BroadcastStatus.REJECTED)
        self.assertEqual(result.reason, "intent was not issued here")

    def test_signature_from_a_different_key_over_an_issued_intent_is_rejected(self):
        unsigned = self.coordinator.create_unsigned_transfer(ADDRESS, "5")
        foreign = OfflineAirGappedSigner("other-key", approval_callback=approve_all)
        signed = foreign.sign_qr_payload(unsigned.to_qr_code_data())
        result = self.coordinator.broadcast_to_network(signed)
        self.assertIs(result.status, BroadcastStatus.REJECTED)
        self.assertEqual(result.reason, "signature verification failed")


class TestCoordinatorIntentLifecycle(WorkflowTestBase):
    def test_a_rejected_address_does_not_burn_a_nonce(self):
        first = self.coordinator.create_unsigned_transfer(ADDRESS, "1")
        with self.assertRaises(AirGapSigningError):
            self.coordinator.create_unsigned_transfer("MALICIOUS", "1")
        second = self.coordinator.create_unsigned_transfer(ADDRESS, "2")
        self.assertEqual((first.nonce, second.nonce), (1, 2))

    def test_invalidated_intent_cannot_be_broadcast(self):
        unsigned, signed = self.sign_new_transfer()
        digest = unsigned.payload_hash()
        self.assertTrue(self.coordinator.invalidate_intent(digest))
        result = self.coordinator.broadcast_to_network(signed)
        self.assertIs(result.status, BroadcastStatus.REJECTED)
        self.assertEqual(result.reason, "intent was invalidated")
        self.assertEqual(len(self.adapter.calls), 0)

    def test_invalidate_rejects_unknown_and_already_submitted_intents(self):
        self.assertFalse(self.coordinator.invalidate_intent("f" * 64))
        unsigned, signed = self.sign_new_transfer()
        self.coordinator.broadcast_to_network(signed)
        self.assertFalse(self.coordinator.invalidate_intent(unsigned.payload_hash()))

    def test_coordinator_construction_validates_its_configuration(self):
        for label, kwargs in (
            ("non-ascii key", {"verification_key": "kéy"}),
            ("bool nonce", {"starting_nonce": True}),
            ("negative nonce", {"starting_nonce": -1}),
            ("zero chain", {"chain_id": 0}),
            ("bool chain", {"chain_id": True}),
        ):
            with self.subTest(case=label):
                with self.assertRaises(AirGapSigningError):
                    OnlineCoordinator(**kwargs)


class TestPayloadValidation(unittest.TestCase):
    def test_canonical_serialisation_is_stable_and_round_trips(self):
        payload = UnsignedPayload(**payload_kwargs(amount="1.20"))
        self.assertEqual(
            payload.to_qr_code_data(),
            '{"amount":"1.20","chain_id":1,"destination_address":"'
            + ADDRESS
            + '","network":"ETH","nonce":1,"version":2}',
        )
        restored = UnsignedPayload.from_qr_code_data(payload.to_qr_code_data())
        self.assertEqual(restored, payload)
        self.assertEqual(restored.payload_hash(), payload.payload_hash())

    def test_payload_hash_matches_an_independently_computed_digest(self):
        import hashlib

        payload = UnsignedPayload(**payload_kwargs())
        expected = hashlib.sha256(
            (
                '{"amount":"1","chain_id":1,"destination_address":"'
                + ADDRESS
                + '","network":"ETH","nonce":1,"version":2}'
            ).encode("utf-8")
        ).hexdigest()
        self.assertEqual(payload.payload_hash(), expected)

    def test_amounts_are_canonicalised_and_bounded(self):
        self.assertEqual(UnsignedPayload(**payload_kwargs(amount="1E+3")).amount, "1000")
        # uint256 max in wei, expressed in whole units, is the largest amount.
        max_units = str((2**256 - 1) // 10**18)
        self.assertEqual(
            UnsignedPayload(**payload_kwargs(amount=max_units)).amount, max_units
        )
        for label, amount in (
            ("zero", "0"),
            ("negative", "-1"),
            ("nan", "NaN"),
            ("inf", "Infinity"),
            ("nineteen decimals", "1.0000000000000000001"),
            ("non-string", 5),
            ("garbage", "1.2.3"),
            ("over uint256", str(2**256)),
            ("huge exponent", "1E+1000"),
        ):
            with self.subTest(case=label):
                with self.assertRaises(AirGapSigningError):
                    UnsignedPayload(**payload_kwargs(amount=amount))

    def test_address_network_nonce_and_version_validation(self):
        for label, override in (
            ("bad address", {"destination_address": "MALICIOUS_ADDRESS"}),
            ("short address", {"destination_address": "0x123"}),
            ("non-string address", {"destination_address": None}),
            ("wrong network", {"network": "BTC"}),
            ("zero nonce", {"nonce": 0}),
            ("bool nonce", {"nonce": True}),
            ("float nonce", {"nonce": 1.0}),
            ("zero chain", {"chain_id": 0}),
            ("bool chain", {"chain_id": True}),
            ("old version", {"version": 1}),
            ("future version", {"version": 3}),
        ):
            with self.subTest(case=label):
                with self.assertRaises(AirGapSigningError):
                    UnsignedPayload(**payload_kwargs(**override))

    def test_qr_decoding_rejects_wrong_shapes_and_field_sets(self):
        for raw in ("not-json", "[]", '"text"', "null", json.dumps({"amount": "1"})):
            with self.subTest(raw=raw[:20]):
                with self.assertRaises(AirGapSigningError):
                    UnsignedPayload.from_qr_code_data(raw)

        extra = payload_kwargs()
        extra["unexpected"] = True
        with self.assertRaises(AirGapSigningError):
            UnsignedPayload.from_qr_code_data(json.dumps(extra))

    def test_vault_construction_validates_its_configuration(self):
        for label, kwargs in (
            ("empty key", {"vault_private_key": ""}),
            ("non-string key", {"vault_private_key": None}),
            ("blank key id", {"vault_private_key": "k", "key_id": " "}),
            ("zero chain", {"vault_private_key": "k", "expected_chain_id": 0}),
            ("bad ceiling", {"vault_private_key": "k", "max_amount": "-1"}),
        ):
            with self.subTest(case=label):
                with self.assertRaises(AirGapSigningError):
                    OfflineAirGappedSigner(**kwargs)


if __name__ == "__main__":
    unittest.main()
