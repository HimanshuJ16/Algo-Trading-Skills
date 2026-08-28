import unittest
from decimal import Decimal

from post_incident_forensics_for_suspected_key_compromise import (
    DEFAULT_PRIVILEGED_ACTIONS,
    FINDING_CLOCK_OFFSET_RECORDED,
    FINDING_DERIVED_KEY_EXPOSURE,
    FINDING_DUPLICATE_TRANSFER_RECORDS,
    FINDING_EVIDENCE_GAP,
    FINDING_NO_SOURCE_ARTIFACT_DIGESTS,
    FINDING_PRIVILEGED_ACTION_FROM_AUTHORIZED_IP,
    FINDING_UNAUTHORIZED_ACCESS_ATTEMPT,
    FINDING_UNAUTHORIZED_OUTFLOW,
    FINDING_UNAUTHORIZED_SUCCESSFUL_ACCESS,
    STATUS_CONFIRMED,
    STATUS_INSUFFICIENT_EVIDENCE,
    STATUS_NO_EVIDENCE,
    STATUS_SUSPECTED,
    AccessControlPolicy,
    KeyAccessLogEntry,
    KeyCompromiseIncident,
    KeyForensicsAnalyzer,
    KeyForensicsError,
    OnChainTransfer,
    build_evidence_manifest,
    compute_evidence_digest,
    normalize_chain_address,
    parse_decimal_amount,
    parse_utc_timestamp,
)

WALLET = "0x1234567890abcdef1234567890abcdef12345678"
WALLET_CHECKSUMMED = "0x1234567890AbCdEf1234567890aBcDeF12345678"
DRAINER = "0x99999999999999999999999999999999deadbeef"
COLD = "0xC01D0000000000000000000000000000000000FF"
LEAK_TIME = "2026-07-31T12:00:00Z"
ANALYSIS_TIME = "2026-07-31T13:30:00Z"
DIGEST = "a" * 64


def make_policy(**overrides):
    kwargs = dict(
        authorized_networks=["192.168.1.0/24", "10.0.0.5"],
        authorized_destinations=[COLD],
    )
    kwargs.update(overrides)
    return AccessControlPolicy(**kwargs)


def make_incident(**overrides):
    kwargs = dict(
        key_id="KEY-HOT-01",
        wallet_address=WALLET,
        suspected_leak_time=LEAK_TIME,
        affected_systems=["HotWalletBot", "KMS_Prod"],
        custodian="ir-analyst-7",
        collected_at="2026-07-31T13:00:00Z",
        source_artifact_digests={"kms-access.log": DIGEST},
    )
    kwargs.update(overrides)
    return KeyCompromiseIncident(**kwargs)


def make_log(**overrides):
    kwargs = dict(
        timestamp="2026-07-31T12:05:00Z",
        ip_address="192.168.1.10",
        action="SIGN_TRANSACTION",
        status_code=200,
        principal="hot-wallet-bot",
    )
    kwargs.update(overrides)
    return KeyAccessLogEntry(**kwargs)


def make_transfer(**overrides):
    kwargs = dict(
        tx_hash="0xabc123",
        from_address=WALLET,
        to_address=DRAINER,
        amount="50",
        asset_symbol="ETH",
        timestamp="2026-07-31T12:06:00Z",
    )
    kwargs.update(overrides)
    return OnChainTransfer(**kwargs)


def covering_logs():
    """A clean log set whose window spans the suspected leak time."""
    return [
        make_log(timestamp="2026-07-31T11:00:00Z"),
        make_log(timestamp="2026-07-31T13:00:00Z"),
    ]


def analyse(logs, transfers, *, policy=None, incident=None, at=ANALYSIS_TIME):
    engine = KeyForensicsAnalyzer(policy or make_policy())
    return engine.run_forensic_analysis(incident or make_incident(), logs, transfers, at)


def categories(report):
    return {f.category for f in report.findings}


class TestAccessLogAudit(unittest.TestCase):

    def test_unauthorized_successful_access_is_confirmed_compromise(self):
        logs = covering_logs() + [
            make_log(ip_address="198.51.100.44", action="EXPORT_KEY", status_code=200),
        ]
        report = analyse(logs, [])
        self.assertEqual(report.status, STATUS_CONFIRMED)
        self.assertEqual(report.unauthorized_successful_access_count, 1)
        self.assertEqual(report.unauthorized_attempt_count, 0)
        self.assertEqual(report.unauthorized_source_ips, ("198.51.100.44",))
        self.assertIn(FINDING_UNAUTHORIZED_SUCCESSFUL_ACCESS, categories(report))

    def test_rejected_attempt_is_not_counted_as_a_successful_access(self):
        """A 403 probe and a 200 EXPORT_KEY are different findings.

        The pre-2.0 engine counted both as 'unauthorized access', which inverts
        triage priority and overstates the incident.
        """
        logs = covering_logs() + [
            make_log(ip_address="198.51.100.44", action="EXPORT_KEY", status_code=403),
        ]
        report = analyse(logs, [])
        self.assertEqual(report.unauthorized_successful_access_count, 0)
        self.assertEqual(report.unauthorized_attempt_count, 1)
        self.assertEqual(report.status, STATUS_SUSPECTED)
        self.assertIn(FINDING_UNAUTHORIZED_ACCESS_ATTEMPT, categories(report))
        self.assertNotIn(FINDING_UNAUTHORIZED_SUCCESSFUL_ACCESS, categories(report))

    def test_status_code_boundaries_of_success(self):
        for code, succeeded in ((199, False), (200, True), (299, True), (300, False)):
            with self.subTest(code=code):
                self.assertEqual(make_log(status_code=code).succeeded, succeeded)

    def test_cidr_allowlist_matches_the_whole_block(self):
        logs = covering_logs() + [make_log(ip_address="192.168.1.254")]
        report = analyse(logs, [])
        self.assertEqual(report.unauthorized_successful_access_count, 0)
        self.assertEqual(report.status, STATUS_NO_EVIDENCE)

    def test_address_just_outside_the_cidr_block_is_unauthorized(self):
        logs = covering_logs() + [make_log(ip_address="192.168.2.1")]
        report = analyse(logs, [])
        self.assertEqual(report.unauthorized_successful_access_count, 1)

    def test_ipv4_mapped_ipv6_matches_an_ipv4_allowlist_entry(self):
        report = analyse(covering_logs() + [make_log(ip_address="::ffff:10.0.0.5")], [])
        self.assertEqual(report.unauthorized_successful_access_count, 0)

    def test_mixed_family_allowlist_does_not_raise(self):
        policy = make_policy(authorized_networks=["192.168.1.0/24", "2001:db8::/32"])
        report = analyse(covering_logs() + [make_log(ip_address="2001:db8::1")], [],
                         policy=policy)
        self.assertEqual(report.unauthorized_successful_access_count, 0)

    def test_privileged_action_from_allowlisted_ip_is_still_reported(self):
        """An allowlisted IP does not clear a key-material action."""
        logs = covering_logs() + [make_log(ip_address="10.0.0.5", action="EXPORT_KEY")]
        report = analyse(logs, [])
        self.assertEqual(report.privileged_authorized_ip_access_count, 1)
        self.assertEqual(report.status, STATUS_SUSPECTED)
        self.assertIn(FINDING_PRIVILEGED_ACTION_FROM_AUTHORIZED_IP, categories(report))

    def test_routine_signing_from_an_allowlisted_ip_is_not_a_finding(self):
        """SIGN_TRANSACTION is key *use*, not key exposure.

        Flagging it would raise a HIGH finding on every bot signature and pin
        the status at COMPROMISE_SUSPECTED permanently.
        """
        self.assertNotIn("SIGN_TRANSACTION", DEFAULT_PRIVILEGED_ACTIONS)
        report = analyse(covering_logs(), [])
        self.assertEqual(report.privileged_authorized_ip_access_count, 0)
        self.assertEqual(report.status, STATUS_NO_EVIDENCE)

    def test_failed_privileged_action_from_allowlisted_ip_is_not_flagged(self):
        logs = covering_logs() + [
            make_log(ip_address="10.0.0.5", action="EXPORT_KEY", status_code=403),
        ]
        report = analyse(logs, [])
        self.assertEqual(report.privileged_authorized_ip_access_count, 0)

    def test_allowlist_is_policy_and_cannot_be_supplied_by_the_log_record(self):
        """The record under audit carries no allowlist field to forge."""
        self.assertNotIn("authorized_ips", KeyAccessLogEntry.__dataclass_fields__)


class TestOutflowTracing(unittest.TestCase):

    def test_unauthorized_outflow_is_confirmed_compromise(self):
        report = analyse(covering_logs(), [make_transfer()])
        self.assertEqual(report.status, STATUS_CONFIRMED)
        self.assertEqual(report.unauthorized_transfer_count, 1)
        self.assertEqual(report.exfiltrated_by_asset, {"ETH": "50"})
        self.assertIn(FINDING_UNAUTHORIZED_OUTFLOW, categories(report))

    def test_pre_incident_transfer_is_not_counted_as_exfiltration(self):
        """The pre-2.0 engine attributed every historical outflow to the breach."""
        report = analyse(covering_logs(),
                         [make_transfer(timestamp="2026-07-30T09:00:00Z", amount="3")])
        self.assertEqual(report.unauthorized_transfer_count, 0)
        self.assertEqual(report.pre_incident_transfer_count, 1)
        self.assertEqual(report.exfiltrated_by_asset, {})
        self.assertEqual(report.status, STATUS_NO_EVIDENCE)

    def test_transfer_to_an_authorized_destination_is_not_exfiltration(self):
        report = analyse(covering_logs(), [make_transfer(to_address=COLD)])
        self.assertEqual(report.authorized_transfer_count, 1)
        self.assertEqual(report.unauthorized_transfer_count, 0)

    def test_transfer_exactly_at_the_leak_time_is_attributed(self):
        """Boundary: the comparison is strictly-before, so t == leak_time counts."""
        report = analyse(covering_logs(), [make_transfer(timestamp=LEAK_TIME)])
        self.assertEqual(report.unauthorized_transfer_count, 1)

    def test_clock_offset_widens_attribution_fail_closed(self):
        """A transfer inside the recorded clock-offset margin is attributed, not excused."""
        tx = make_transfer(timestamp="2026-07-31T11:59:30Z")   # 30s before the leak time
        clean = analyse(covering_logs(), [tx])
        self.assertEqual(clean.pre_incident_transfer_count, 1)

        skewed = analyse(covering_logs(), [tx],
                         incident=make_incident(clock_offset_seconds=90.0))
        self.assertEqual(skewed.unauthorized_transfer_count, 1)
        self.assertEqual(skewed.pre_incident_transfer_count, 0)
        self.assertIn(FINDING_CLOCK_OFFSET_RECORDED, categories(skewed))

    def test_inbound_transfer_is_ignored(self):
        report = analyse(covering_logs(),
                         [make_transfer(from_address=DRAINER, to_address=WALLET)])
        self.assertEqual(report.unauthorized_transfer_count, 0)
        self.assertEqual(report.pre_incident_transfer_count, 0)

    def test_assets_are_never_summed_across_symbols(self):
        """50 ETH + 1200.5 USDC is not 1250.5 of anything."""
        report = analyse(covering_logs(), [
            make_transfer(tx_hash="0x1", amount="50", asset_symbol="ETH"),
            make_transfer(tx_hash="0x2", amount="1200.5", asset_symbol="USDC"),
            make_transfer(tx_hash="0x3", amount="0.25", asset_symbol="eth"),
        ])
        self.assertEqual(report.exfiltrated_by_asset, {"ETH": "50.25", "USDC": "1200.5"})

    def test_amounts_are_exact_and_not_rounded(self):
        """0.1 + 0.2 is exactly 0.3 in Decimal; the old float path rounded to 4dp."""
        report = analyse(covering_logs(), [
            make_transfer(tx_hash="0x1", amount="0.1"),
            make_transfer(tx_hash="0x2", amount="0.2"),
            make_transfer(tx_hash="0x3", amount="0.000000000000000001"),
        ])
        self.assertEqual(report.exfiltrated_by_asset["ETH"], "0.300000000000000001")

    def test_checksummed_wallet_matches_lowercase_transfers(self):
        report = analyse(covering_logs(), [make_transfer(from_address=WALLET_CHECKSUMMED)])
        self.assertEqual(report.unauthorized_transfer_count, 1)

    def test_blocklist_carries_only_unauthorized_destinations(self):
        report = analyse(covering_logs(), [
            make_transfer(tx_hash="0x1"),
            make_transfer(tx_hash="0x2"),
            make_transfer(tx_hash="0x3", to_address=COLD),
        ])
        self.assertEqual(report.containment.blocklist_addresses, (DRAINER.lower(),))


class TestEvidenceQuality(unittest.TestCase):

    def test_empty_logs_never_clear_the_key(self):
        """Absence of logs is not absence of access."""
        report = analyse([], [])
        self.assertEqual(report.status, STATUS_INSUFFICIENT_EVIDENCE)
        self.assertIn(FINDING_EVIDENCE_GAP, categories(report))
        self.assertTrue(report.containment.containment_required)

    def test_log_window_not_spanning_the_leak_time_is_an_evidence_gap(self):
        logs = [make_log(timestamp="2026-07-31T14:00:00Z"),
                make_log(timestamp="2026-07-31T15:00:00Z")]
        report = analyse(logs, [])
        self.assertEqual(report.status, STATUS_INSUFFICIENT_EVIDENCE)
        self.assertIn(FINDING_EVIDENCE_GAP, categories(report))

    def test_evidence_window_is_reported(self):
        report = analyse(covering_logs(), [])
        self.assertEqual(report.evidence_window_start, "2026-07-31T11:00:00+00:00")
        self.assertEqual(report.evidence_window_end, "2026-07-31T13:00:00+00:00")

    def test_missing_source_artifact_digests_is_reported(self):
        report = analyse(covering_logs(), [],
                         incident=make_incident(source_artifact_digests={}))
        self.assertIn(FINDING_NO_SOURCE_ARTIFACT_DIGESTS, categories(report))
        # It weakens custody but is not by itself evidence of compromise.
        self.assertEqual(report.status, STATUS_NO_EVIDENCE)

    def test_derived_keys_are_exposure_radius_not_evidence(self):
        report = analyse(covering_logs(), [],
                         incident=make_incident(derived_key_ids=["KEY-HOT-02"]))
        self.assertIn(FINDING_DERIVED_KEY_EXPOSURE, categories(report))
        self.assertEqual(report.status, STATUS_NO_EVIDENCE)

    def test_duplicate_transfer_records_are_reported_not_silently_merged(self):
        """A paginated indexer feed can return the same transfer twice."""
        report = analyse(covering_logs(), [make_transfer(), make_transfer()])
        self.assertIn(FINDING_DUPLICATE_TRANSFER_RECORDS, categories(report))
        # Not deduplicated: one transaction can legitimately emit several
        # transfer events, so collapsing them would understate a real loss.
        self.assertEqual(report.exfiltrated_by_asset, {"ETH": "100"})

    def test_distinct_transfers_do_not_trigger_the_duplicate_finding(self):
        report = analyse(covering_logs(), [
            make_transfer(tx_hash="0x1"),
            make_transfer(tx_hash="0x2"),
        ])
        self.assertNotIn(FINDING_DUPLICATE_TRANSFER_RECORDS, categories(report))

    def test_clean_covering_evidence_clears_the_key(self):
        report = analyse(covering_logs(), [])
        self.assertEqual(report.status, STATUS_NO_EVIDENCE)
        self.assertFalse(report.containment.containment_required)
        self.assertEqual(report.containment.revoke_key_ids, ())

    def test_custody_record_is_carried_into_the_report(self):
        report = analyse(covering_logs(), [])
        self.assertEqual(report.custodian, "ir-analyst-7")
        self.assertEqual(report.collected_at, "2026-07-31T13:00:00Z")
        self.assertEqual(report.source_artifact_digests, {"kms-access.log": DIGEST})


class TestEvidenceDigest(unittest.TestCase):

    def setUp(self):
        self.logs = covering_logs() + [
            make_log(ip_address="198.51.100.44", action="EXPORT_KEY", status_code=200),
        ]
        self.transfers = [make_transfer()]
        self.baseline = analyse(self.logs, self.transfers).evidence_sha256

    def test_digest_is_64_hex_characters(self):
        self.assertEqual(len(self.baseline), 64)
        self.assertTrue(all(c in "0123456789abcdef" for c in self.baseline))

    def test_digest_is_deterministic_across_runs(self):
        self.assertEqual(analyse(self.logs, self.transfers).evidence_sha256, self.baseline)

    def test_altering_a_log_ip_changes_the_digest(self):
        """The pre-2.0 digest covered record *counts*, so this mutation was invisible."""
        tampered = list(self.logs)
        tampered[-1] = make_log(ip_address="203.0.113.9", action="EXPORT_KEY", status_code=200)
        self.assertNotEqual(analyse(tampered, self.transfers).evidence_sha256, self.baseline)

    def test_altering_a_log_timestamp_changes_the_digest(self):
        tampered = list(self.logs)
        tampered[-1] = make_log(timestamp="2026-07-31T12:59:59Z",
                                ip_address="198.51.100.44", action="EXPORT_KEY")
        self.assertNotEqual(analyse(tampered, self.transfers).evidence_sha256, self.baseline)

    def test_altering_a_transfer_amount_changes_the_digest(self):
        self.assertNotEqual(
            analyse(self.logs, [make_transfer(amount="49")]).evidence_sha256, self.baseline)

    def test_reordering_records_changes_the_digest(self):
        """Log sequence is itself evidence, so order is sealed."""
        self.assertNotEqual(
            analyse(list(reversed(self.logs)), self.transfers).evidence_sha256, self.baseline)

    def test_digest_is_independently_recomputable(self):
        manifest = build_evidence_manifest(
            make_incident(), self.logs, self.transfers, make_policy(), ANALYSIS_TIME)
        self.assertEqual(compute_evidence_digest(manifest), self.baseline)

    def test_delimiter_injection_in_an_identifier_cannot_forge_a_collision(self):
        """Canonical JSON, not delimiter concatenation."""
        a = analyse(self.logs, [make_transfer(tx_hash="0xAA", asset_symbol="ETH")])
        b = analyse(self.logs, [make_transfer(tx_hash="0xAA|ETH", asset_symbol="X")])
        self.assertNotEqual(a.evidence_sha256, b.evidence_sha256)

    def test_analysis_time_is_sealed_into_the_digest(self):
        self.assertNotEqual(
            analyse(self.logs, self.transfers, at="2026-07-31T14:00:00Z").evidence_sha256,
            self.baseline)


class TestContainmentMandate(unittest.TestCase):

    def test_confirmed_compromise_mandates_revocation_and_rekey_of_all_affected_keys(self):
        report = analyse(covering_logs(), [make_transfer()],
                         incident=make_incident(derived_key_ids=["KEY-HOT-02", "KEY-HOT-03"]))
        mandate = report.containment
        self.assertTrue(mandate.containment_required)
        self.assertTrue(mandate.cease_cryptographic_protection)
        self.assertEqual(mandate.revoke_key_ids, ("KEY-HOT-01", "KEY-HOT-02", "KEY-HOT-03"))
        self.assertEqual(mandate.rekey_key_ids, mandate.revoke_key_ids)
        self.assertEqual(mandate.revocation_reason, "KEY_COMPROMISE")
        self.assertEqual(mandate.determined_at, "2026-07-31T13:30:00+00:00")
        self.assertTrue(mandate.actions)

    def test_suspected_compromise_also_mandates_containment(self):
        logs = covering_logs() + [make_log(ip_address="198.51.100.44", status_code=403)]
        mandate = analyse(logs, []).containment
        self.assertTrue(mandate.containment_required)
        self.assertEqual(mandate.revocation_reason, "SUSPECTED_KEY_COMPROMISE")

    def test_insufficient_evidence_mandates_containment(self):
        self.assertTrue(analyse([], []).containment.containment_required)

    def test_mandate_never_carries_key_material(self):
        with self.assertRaises(KeyForensicsError):
            make_incident(key_id="0x" + "ab" * 32)
        with self.assertRaises(KeyForensicsError):
            make_incident(derived_key_ids=["cd" * 32])

    def test_a_normal_hex_key_alias_is_not_mistaken_for_key_material(self):
        self.assertEqual(make_incident(key_id="0xdeadbeef").key_id, "0xdeadbeef")


class TestInputValidation(unittest.TestCase):

    def test_naive_timestamp_is_rejected(self):
        with self.assertRaises(KeyForensicsError):
            make_log(timestamp="2026-07-31T12:05:00")
        with self.assertRaises(KeyForensicsError):
            make_incident(suspected_leak_time="2026-07-31T12:00:00")

    def test_non_iso_timestamp_is_rejected(self):
        with self.assertRaises(KeyForensicsError):
            make_log(timestamp="31/07/2026 12:05")

    def test_offset_timestamps_are_normalized_to_utc(self):
        self.assertEqual(parse_utc_timestamp("2026-07-31T14:00:00+02:00", "t").isoformat(),
                         "2026-07-31T12:00:00+00:00")

    def test_float_amount_is_rejected(self):
        with self.assertRaises(KeyForensicsError):
            make_transfer(amount=50.0)

    def test_negative_and_non_finite_amounts_are_rejected(self):
        for bad in ("-1", "NaN", "Infinity", "-Infinity", "not-a-number", None, True):
            with self.subTest(amount=bad), self.assertRaises(KeyForensicsError):
                make_transfer(amount=bad)

    def test_integer_and_decimal_amounts_are_accepted(self):
        self.assertEqual(make_transfer(amount=7).amount, Decimal(7))
        self.assertEqual(make_transfer(amount=Decimal("1.5")).amount, Decimal("1.5"))

    def test_invalid_ip_is_rejected(self):
        for bad in ("192.168.1.256", "not-an-ip", "192.168.1.0/24"):
            with self.subTest(ip=bad), self.assertRaises(KeyForensicsError):
                make_log(ip_address=bad)

    def test_out_of_range_and_non_integer_status_codes_are_rejected(self):
        for bad in (0, 99, 600, "200", 200.0, True):
            with self.subTest(code=bad), self.assertRaises(KeyForensicsError):
                make_log(status_code=bad)

    def test_blank_and_whitespace_bearing_identifiers_are_rejected(self):
        with self.assertRaises(KeyForensicsError):
            make_transfer(tx_hash="")
        with self.assertRaises(KeyForensicsError):
            make_transfer(tx_hash="0xabc\ndef")
        with self.assertRaises(KeyForensicsError):
            make_incident(custodian="   ")

    def test_empty_allowlist_is_rejected(self):
        with self.assertRaises(KeyForensicsError):
            make_policy(authorized_networks=[])

    def test_malformed_allowlist_entry_is_rejected(self):
        with self.assertRaises(KeyForensicsError):
            make_policy(authorized_networks=["192.168.1.0/33"])

    def test_all_addresses_allowlist_is_rejected(self):
        """A /0 block authorizes the attacker and makes the audit vacuous."""
        for entry in ("0.0.0.0/0", "::/0"):
            with self.subTest(entry=entry), self.assertRaises(KeyForensicsError):
                make_policy(authorized_networks=[entry])

    def test_incident_requires_at_least_one_affected_system(self):
        with self.assertRaises(KeyForensicsError):
            make_incident(affected_systems=[])

    def test_derived_key_ids_must_not_repeat_the_primary_key(self):
        with self.assertRaises(KeyForensicsError):
            make_incident(derived_key_ids=["KEY-HOT-01"])

    def test_source_artifact_digest_must_be_sha256_hex(self):
        for bad in ("abc", "z" * 64, DIGEST[:-1]):
            with self.subTest(digest=bad), self.assertRaises(KeyForensicsError):
                make_incident(source_artifact_digests={"f.log": bad})

    def test_clock_offset_must_be_finite(self):
        with self.assertRaises(KeyForensicsError):
            make_incident(clock_offset_seconds=float("nan"))
        with self.assertRaises(KeyForensicsError):
            make_incident(clock_offset_seconds="90")

    def test_engine_requires_a_policy(self):
        with self.assertRaises(KeyForensicsError):
            KeyForensicsAnalyzer(None)
        with self.assertRaises(KeyForensicsError):
            KeyForensicsAnalyzer({"authorized_networks": ["10.0.0.1"]})

    def test_engine_rejects_wrongly_typed_evidence(self):
        engine = KeyForensicsAnalyzer(make_policy())
        with self.assertRaises(KeyForensicsError):
            engine.run_forensic_analysis({"key_id": "K"}, [], [], ANALYSIS_TIME)
        with self.assertRaises(KeyForensicsError):
            engine.run_forensic_analysis(make_incident(), [{"ip": "1.2.3.4"}], [], ANALYSIS_TIME)
        with self.assertRaises(KeyForensicsError):
            engine.run_forensic_analysis(make_incident(), [], [make_log()], ANALYSIS_TIME)
        with self.assertRaises(KeyForensicsError):
            engine.run_forensic_analysis(make_incident(), covering_logs(), [], "not-a-time")


class TestNormalizationHelpers(unittest.TestCase):

    def test_hex_addresses_are_lowercased(self):
        self.assertEqual(normalize_chain_address("  0xAbCdEf  "), "0xabcdef")

    def test_non_hex_addresses_keep_their_case(self):
        """Base58 and Bech32 encodings are case-sensitive."""
        btc = "bc1QW508D6QEJXTDG4Y5R3ZARVARY0C5XW7KV8F3T4"
        self.assertEqual(normalize_chain_address(btc), btc)
        tron = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
        self.assertEqual(normalize_chain_address(tron), tron)

    def test_parse_decimal_amount_preserves_precision(self):
        self.assertEqual(parse_decimal_amount("0.000000000000000001", "a"),
                         Decimal("0.000000000000000001"))


if __name__ == '__main__':
    unittest.main()
