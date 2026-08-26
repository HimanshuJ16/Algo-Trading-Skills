import unittest
from datetime import date, datetime

from multi_party_computation_mpc_custody_solutions import (
    MPCCustodyConfig,
    MPCCustodyConfigError,
    MPCCustodyError,
    MPCCustodyEngine,
    MPCShardAttestation,
    MPCShardNode,
    MPCSigningRequest,
    MPCSigningRequestError,
    PROTOCOL_CMP,
    PROTOCOL_GG18,
    PROTOCOL_GG20,
    STATUS_AUTHORIZED,
    STATUS_DUPLICATE_ATTESTATION,
    STATUS_FAILURE_DOMAIN_CONCENTRATION,
    STATUS_KEY_EPOCH_MISMATCH,
    STATUS_KEY_REFRESH_OVERDUE,
    STATUS_PROTOCOL_NOT_HARDENED,
    STATUS_THRESHOLD_NOT_MET,
    STATUS_UNAUTHORIZED_NODE,
    derive_shard_attestation_commitment,
)

EVAL_DATE = date(2026, 8, 26)
REFRESH_DATE = date(2026, 7, 1)          # 56 days before EVAL_DATE, inside a 90-day policy
TX = "0xabc123789def"

# Distinct 64-char hex commitments, one per shard.
COMMITMENT = {
    "BOT_NODE_01": "a" * 64,
    "CUSTODIAN_CLOUD_02": "b" * 64,
    "HSM_NODE_03": "c" * 64,
}


def attest(node_id, epoch=1, commitment=None):
    # `commitment=""` must survive as an empty string, so test None explicitly
    # rather than relying on truthiness.
    if commitment is None:
        commitment = COMMITMENT.get(node_id, "f" * 64)
    return MPCShardAttestation(
        node_id=node_id,
        share_commitment=commitment,
        key_epoch=epoch,
    )


def signing_request(attestations, tx_hash=TX, amount_usd=100_000.0):
    return MPCSigningRequest(
        tx_hash=tx_hash,
        amount_usd=amount_usd,
        destination_address="0xDestinationAddress",
        attestations=attestations,
    )


def hardened_config(**overrides):
    """A 2-of-3 CMP policy that is fully compliant unless an override breaks it."""
    params = dict(
        threshold_t=2,
        protocol=PROTOCOL_CMP,
        current_key_epoch=1,
        last_key_refresh_date=REFRESH_DATE,
        implementation_hardened_against_cve_2023_33241=True,
        implementation_hardened_against_tsshock=True,
    )
    params.update(overrides)
    return MPCCustodyConfig(**params)


class TestConfigValidation(unittest.TestCase):
    """A quorum policy that cannot mean what it says must never construct."""

    def test_threshold_of_zero_is_rejected(self):
        # Regression: a zero threshold previously let a request with no valid
        # shares clear `valid_count < t_req` and be reported as signed.
        with self.assertRaises(MPCCustodyConfigError) as ctx:
            MPCCustodyEngine(hardened_config(threshold_t=0))
        self.assertIn("threshold_t must be >= 2", str(ctx.exception))

    def test_threshold_of_one_is_rejected(self):
        with self.assertRaises(MPCCustodyConfigError):
            MPCCustodyEngine(hardened_config(threshold_t=1))

    def test_threshold_above_shard_count_is_rejected(self):
        with self.assertRaises(MPCCustodyConfigError) as ctx:
            MPCCustodyEngine(hardened_config(threshold_t=4))
        self.assertIn("can never be met", str(ctx.exception))

    def test_fewer_than_three_shards_is_rejected(self):
        config = hardened_config(nodes=[
            MPCShardNode("A", "aws"),
            MPCShardNode("B", "gcp"),
        ])
        with self.assertRaises(MPCCustodyConfigError) as ctx:
            MPCCustodyEngine(config)
        self.assertIn("N >= 3", str(ctx.exception))

    def test_duplicate_node_id_in_roster_is_rejected(self):
        config = hardened_config(nodes=[
            MPCShardNode("A", "aws"),
            MPCShardNode("A", "gcp"),
            MPCShardNode("B", "on-prem"),
        ])
        with self.assertRaises(MPCCustodyConfigError) as ctx:
            MPCCustodyEngine(config)
        self.assertIn("Duplicate node_id", str(ctx.exception))

    def test_blank_failure_domain_is_rejected(self):
        config = hardened_config(nodes=[
            MPCShardNode("A", "aws"),
            MPCShardNode("B", "   "),
            MPCShardNode("C", "on-prem"),
        ])
        with self.assertRaises(MPCCustodyConfigError) as ctx:
            MPCCustodyEngine(config)
        self.assertIn("failure_domain", str(ctx.exception))

    def test_whitespace_padded_node_id_is_rejected(self):
        # A padded id is matched verbatim against attestations, so it would
        # silently deny every ceremony its holder takes part in.
        config = hardened_config(nodes=[
            MPCShardNode(" BOT_NODE_01 ", "aws"),
            MPCShardNode("CUSTODIAN_CLOUD_02", "gcp"),
            MPCShardNode("HSM_NODE_03", "on-prem"),
        ])
        with self.assertRaises(MPCCustodyConfigError) as ctx:
            MPCCustodyEngine(config)
        self.assertIn("whitespace", str(ctx.exception))

    def test_unknown_protocol_is_rejected(self):
        with self.assertRaises(MPCCustodyConfigError):
            MPCCustodyEngine(hardened_config(protocol="GG99"))

    def test_unsatisfiable_failure_domain_requirement_is_rejected(self):
        with self.assertRaises(MPCCustodyConfigError) as ctx:
            MPCCustodyEngine(hardened_config(threshold_t=2, min_distinct_failure_domains=3))
        self.assertIn("unsatisfiable", str(ctx.exception))

    def test_non_positive_refresh_interval_is_rejected(self):
        with self.assertRaises(MPCCustodyConfigError):
            MPCCustodyEngine(hardened_config(refresh_interval_days=0))

    def test_epoch_below_one_is_rejected(self):
        with self.assertRaises(MPCCustodyConfigError):
            MPCCustodyEngine(hardened_config(current_key_epoch=0))

    def test_default_config_denies_until_hardening_is_attested(self):
        # Deny by default: the stock config attests no library hardening.
        engine = MPCCustodyEngine()
        report = engine.evaluate_signing_authorization(
            signing_request([attest("BOT_NODE_01"), attest("CUSTODIAN_CLOUD_02")]),
            evaluation_date=EVAL_DATE,
        )
        self.assertFalse(report.is_authorized)
        self.assertEqual(report.status, STATUS_PROTOCOL_NOT_HARDENED)


class TestRequestValidation(unittest.TestCase):
    """Malformed input must raise, never be counted toward a quorum."""

    def setUp(self):
        self.engine = MPCCustodyEngine(hardened_config())

    def test_empty_attestation_list_raises(self):
        with self.assertRaises(MPCSigningRequestError):
            self.engine.evaluate_signing_authorization(
                signing_request([]), evaluation_date=EVAL_DATE)

    def test_empty_share_commitment_raises(self):
        # Regression: empty share strings previously counted toward the quorum.
        request = signing_request([
            attest("BOT_NODE_01", commitment=""),
            attest("CUSTODIAN_CLOUD_02", commitment=""),
        ])
        with self.assertRaises(MPCSigningRequestError) as ctx:
            self.engine.evaluate_signing_authorization(request, evaluation_date=EVAL_DATE)
        self.assertIn("empty share_commitment", str(ctx.exception))

    def test_non_hex_share_commitment_raises(self):
        request = signing_request([
            attest("BOT_NODE_01", commitment="PARTIAL_SHARE_HASH_01_NOT_HEX_ZZZZ"),
            attest("CUSTODIAN_CLOUD_02"),
        ])
        with self.assertRaises(MPCSigningRequestError) as ctx:
            self.engine.evaluate_signing_authorization(request, evaluation_date=EVAL_DATE)
        self.assertIn("non-hex", str(ctx.exception))

    def test_short_share_commitment_raises(self):
        request = signing_request([
            attest("BOT_NODE_01", commitment="abcd"),
            attest("CUSTODIAN_CLOUD_02"),
        ])
        with self.assertRaises(MPCSigningRequestError):
            self.engine.evaluate_signing_authorization(request, evaluation_date=EVAL_DATE)

    def test_nan_amount_raises(self):
        request = signing_request(
            [attest("BOT_NODE_01"), attest("CUSTODIAN_CLOUD_02")],
            amount_usd=float("nan"),
        )
        with self.assertRaises(MPCSigningRequestError):
            self.engine.evaluate_signing_authorization(request, evaluation_date=EVAL_DATE)

    def test_negative_amount_raises(self):
        request = signing_request(
            [attest("BOT_NODE_01"), attest("CUSTODIAN_CLOUD_02")], amount_usd=-1.0)
        with self.assertRaises(MPCSigningRequestError):
            self.engine.evaluate_signing_authorization(request, evaluation_date=EVAL_DATE)

    def test_blank_tx_hash_raises(self):
        request = signing_request(
            [attest("BOT_NODE_01"), attest("CUSTODIAN_CLOUD_02")], tx_hash="   ")
        with self.assertRaises(MPCSigningRequestError):
            self.engine.evaluate_signing_authorization(request, evaluation_date=EVAL_DATE)

    def test_epoch_below_one_on_attestation_raises(self):
        request = signing_request([
            attest("BOT_NODE_01", epoch=0),
            attest("CUSTODIAN_CLOUD_02"),
        ])
        with self.assertRaises(MPCSigningRequestError):
            self.engine.evaluate_signing_authorization(request, evaluation_date=EVAL_DATE)


class TestQuorumAuthorization(unittest.TestCase):

    def setUp(self):
        self.engine = MPCCustodyEngine(hardened_config())

    def test_valid_2_of_3_quorum_is_authorized(self):
        report = self.engine.evaluate_signing_authorization(
            signing_request([attest("BOT_NODE_01"), attest("CUSTODIAN_CLOUD_02")]),
            evaluation_date=EVAL_DATE,
        )
        self.assertTrue(report.is_authorized)
        self.assertEqual(report.status, STATUS_AUTHORIZED)
        self.assertEqual(report.accepted_attestation_count, 2)
        self.assertEqual(report.accepted_nodes, ["BOT_NODE_01", "CUSTODIAN_CLOUD_02"])
        self.assertEqual(report.distinct_failure_domains, 2)
        self.assertEqual(report.threshold_required, 2)
        self.assertEqual(report.num_shards, 3)
        self.assertEqual(report.findings, [])

    def test_report_carries_no_signature_fields(self):
        # The engine authorises a ceremony; it must never present a signature.
        report = self.engine.evaluate_signing_authorization(
            signing_request([attest("BOT_NODE_01"), attest("CUSTODIAN_CLOUD_02")]),
            evaluation_date=EVAL_DATE,
        )
        self.assertFalse(hasattr(report, "signature_r"))
        self.assertFalse(hasattr(report, "signature_s"))
        self.assertFalse(hasattr(report, "is_signed"))

    def test_insufficient_shares_are_rejected(self):
        report = self.engine.evaluate_signing_authorization(
            signing_request([attest("BOT_NODE_01")]), evaluation_date=EVAL_DATE)
        self.assertFalse(report.is_authorized)
        self.assertEqual(report.status, STATUS_THRESHOLD_NOT_MET)
        self.assertEqual(report.accepted_attestation_count, 1)

    def test_amount_and_destination_never_change_the_decision(self):
        small = self.engine.evaluate_signing_authorization(
            signing_request(
                [attest("BOT_NODE_01"), attest("CUSTODIAN_CLOUD_02")], amount_usd=1.0),
            evaluation_date=EVAL_DATE,
        )
        huge = self.engine.evaluate_signing_authorization(
            signing_request(
                [attest("BOT_NODE_01"), attest("CUSTODIAN_CLOUD_02")], amount_usd=1e12),
            evaluation_date=EVAL_DATE,
        )
        self.assertTrue(small.is_authorized)
        self.assertTrue(huge.is_authorized)
        self.assertTrue(any("NOT enforced" in w for w in huge.warnings))

    def test_evaluation_is_deterministic_for_a_fixed_date(self):
        request = signing_request([attest("BOT_NODE_01"), attest("CUSTODIAN_CLOUD_02")])
        first = self.engine.evaluate_signing_authorization(request, evaluation_date=EVAL_DATE)
        second = self.engine.evaluate_signing_authorization(request, evaluation_date=EVAL_DATE)
        self.assertEqual(first, second)


class TestNodeAuthorization(unittest.TestCase):

    def setUp(self):
        self.engine = MPCCustodyEngine(hardened_config())

    def test_unauthorized_node_denies_the_whole_ceremony(self):
        # Regression: an unauthorised submitter was previously dropped with only a
        # log line, and the remaining shares could still clear the threshold.
        request = signing_request([
            attest("BOT_NODE_01"),
            attest("CUSTODIAN_CLOUD_02"),
            attest("ROGUE_NODE_99", commitment="d" * 64),
        ])
        report = self.engine.evaluate_signing_authorization(request, evaluation_date=EVAL_DATE)
        self.assertFalse(report.is_authorized)
        self.assertEqual(report.status, STATUS_UNAUTHORIZED_NODE)
        self.assertTrue(any("ROGUE_NODE_99" in f for f in report.findings))

    def test_duplicate_node_attestation_is_rejected(self):
        request = signing_request([
            attest("BOT_NODE_01"),
            attest("BOT_NODE_01", commitment="e" * 64),
        ])
        report = self.engine.evaluate_signing_authorization(request, evaluation_date=EVAL_DATE)
        self.assertFalse(report.is_authorized)
        self.assertEqual(report.status, STATUS_DUPLICATE_ATTESTATION)

    def test_cloned_shard_across_two_hosts_is_rejected(self):
        # Two distinct node ids presenting the same commitment means one shard is
        # running twice: the quorum is not two independent parties.
        shared = "1" * 64
        request = signing_request([
            attest("BOT_NODE_01", commitment=shared),
            attest("CUSTODIAN_CLOUD_02", commitment=shared),
        ])
        report = self.engine.evaluate_signing_authorization(request, evaluation_date=EVAL_DATE)
        self.assertFalse(report.is_authorized)
        self.assertEqual(report.status, STATUS_DUPLICATE_ATTESTATION)
        self.assertEqual(report.accepted_attestation_count, 0)

    def test_commitment_comparison_is_case_insensitive(self):
        request = signing_request([
            attest("BOT_NODE_01", commitment="AB" * 32),
            attest("CUSTODIAN_CLOUD_02", commitment="ab" * 32),
        ])
        report = self.engine.evaluate_signing_authorization(request, evaluation_date=EVAL_DATE)
        self.assertEqual(report.status, STATUS_DUPLICATE_ATTESTATION)


class TestKeyEpoch(unittest.TestCase):

    def test_stale_epoch_shard_cannot_participate(self):
        engine = MPCCustodyEngine(hardened_config(current_key_epoch=4))
        request = signing_request([
            attest("BOT_NODE_01", epoch=4),
            attest("CUSTODIAN_CLOUD_02", epoch=3),
        ])
        report = engine.evaluate_signing_authorization(request, evaluation_date=EVAL_DATE)
        self.assertFalse(report.is_authorized)
        self.assertEqual(report.status, STATUS_KEY_EPOCH_MISMATCH)
        self.assertEqual(report.accepted_attestation_count, 1)

    def test_matching_epoch_after_refresh_is_authorized(self):
        engine = MPCCustodyEngine(hardened_config(current_key_epoch=4))
        request = signing_request([
            attest("BOT_NODE_01", epoch=4),
            attest("CUSTODIAN_CLOUD_02", epoch=4),
        ])
        report = engine.evaluate_signing_authorization(request, evaluation_date=EVAL_DATE)
        self.assertTrue(report.is_authorized)
        self.assertEqual(report.key_epoch, 4)


class TestFailureDomainIndependence(unittest.TestCase):

    def test_quorum_concentrated_in_one_domain_is_rejected(self):
        # Two shards in the same AWS account: one account compromise signs, so
        # the 2-of-3 threshold buys nothing.
        config = hardened_config(nodes=[
            MPCShardNode("BOT_NODE_01", "aws-prod-account"),
            MPCShardNode("CUSTODIAN_CLOUD_02", "aws-prod-account"),
            MPCShardNode("HSM_NODE_03", "on-prem-hsm"),
        ])
        engine = MPCCustodyEngine(config)
        report = engine.evaluate_signing_authorization(
            signing_request([attest("BOT_NODE_01"), attest("CUSTODIAN_CLOUD_02")]),
            evaluation_date=EVAL_DATE,
        )
        self.assertFalse(report.is_authorized)
        self.assertEqual(report.status, STATUS_FAILURE_DOMAIN_CONCENTRATION)
        self.assertEqual(report.distinct_failure_domains, 1)

    def test_same_roster_authorizes_a_domain_spanning_quorum(self):
        config = hardened_config(nodes=[
            MPCShardNode("BOT_NODE_01", "aws-prod-account"),
            MPCShardNode("CUSTODIAN_CLOUD_02", "aws-prod-account"),
            MPCShardNode("HSM_NODE_03", "on-prem-hsm"),
        ])
        engine = MPCCustodyEngine(config)
        report = engine.evaluate_signing_authorization(
            signing_request([attest("BOT_NODE_01"), attest("HSM_NODE_03")]),
            evaluation_date=EVAL_DATE,
        )
        self.assertTrue(report.is_authorized)
        self.assertEqual(report.distinct_failure_domains, 2)

    def test_relaxed_domain_requirement_is_honoured(self):
        config = hardened_config(
            min_distinct_failure_domains=1,
            nodes=[
                MPCShardNode("BOT_NODE_01", "aws-prod-account"),
                MPCShardNode("CUSTODIAN_CLOUD_02", "aws-prod-account"),
                MPCShardNode("HSM_NODE_03", "on-prem-hsm"),
            ],
        )
        engine = MPCCustodyEngine(config)
        report = engine.evaluate_signing_authorization(
            signing_request([attest("BOT_NODE_01"), attest("CUSTODIAN_CLOUD_02")]),
            evaluation_date=EVAL_DATE,
        )
        self.assertTrue(report.is_authorized)


class TestProtocolHardening(unittest.TestCase):

    def test_gg18_without_cve_mitigation_is_denied(self):
        engine = MPCCustodyEngine(hardened_config(
            protocol=PROTOCOL_GG18,
            implementation_hardened_against_cve_2023_33241=False,
        ))
        report = engine.evaluate_signing_authorization(
            signing_request([attest("BOT_NODE_01"), attest("CUSTODIAN_CLOUD_02")]),
            evaluation_date=EVAL_DATE,
        )
        self.assertFalse(report.is_authorized)
        self.assertEqual(report.status, STATUS_PROTOCOL_NOT_HARDENED)
        self.assertTrue(any("CVE-2023-33241" in f for f in report.findings))

    def test_gg20_without_cve_mitigation_is_denied(self):
        engine = MPCCustodyEngine(hardened_config(
            protocol=PROTOCOL_GG20,
            implementation_hardened_against_cve_2023_33241=False,
        ))
        report = engine.evaluate_signing_authorization(
            signing_request([attest("BOT_NODE_01"), attest("CUSTODIAN_CLOUD_02")]),
            evaluation_date=EVAL_DATE,
        )
        self.assertEqual(report.status, STATUS_PROTOCOL_NOT_HARDENED)

    def test_cmp_is_not_exempt_from_tsshock(self):
        # TSSHOCK hit audited CGGMP21 libraries too: choosing CMP is not a fix.
        engine = MPCCustodyEngine(hardened_config(
            protocol=PROTOCOL_CMP,
            implementation_hardened_against_tsshock=False,
        ))
        report = engine.evaluate_signing_authorization(
            signing_request([attest("BOT_NODE_01"), attest("CUSTODIAN_CLOUD_02")]),
            evaluation_date=EVAL_DATE,
        )
        self.assertFalse(report.is_authorized)
        self.assertEqual(report.status, STATUS_PROTOCOL_NOT_HARDENED)
        self.assertTrue(any("TSSHOCK" in f for f in report.findings))

    def test_cmp_is_not_subject_to_cve_2023_33241(self):
        engine = MPCCustodyEngine(hardened_config(
            protocol=PROTOCOL_CMP,
            implementation_hardened_against_cve_2023_33241=False,
            implementation_hardened_against_tsshock=True,
        ))
        report = engine.evaluate_signing_authorization(
            signing_request([attest("BOT_NODE_01"), attest("CUSTODIAN_CLOUD_02")]),
            evaluation_date=EVAL_DATE,
        )
        self.assertTrue(report.is_authorized)


class TestProactiveRefresh(unittest.TestCase):

    def test_overdue_refresh_warns_by_default(self):
        engine = MPCCustodyEngine(hardened_config(
            last_key_refresh_date=date(2026, 1, 1), refresh_interval_days=90.0))
        report = engine.evaluate_signing_authorization(
            signing_request([attest("BOT_NODE_01"), attest("CUSTODIAN_CLOUD_02")]),
            evaluation_date=EVAL_DATE,
        )
        self.assertTrue(report.is_authorized)
        self.assertTrue(any("overdue" in w for w in report.warnings))

    def test_overdue_refresh_denies_when_configured(self):
        engine = MPCCustodyEngine(hardened_config(
            last_key_refresh_date=date(2026, 1, 1),
            refresh_interval_days=90.0,
            deny_on_overdue_refresh=True,
        ))
        report = engine.evaluate_signing_authorization(
            signing_request([attest("BOT_NODE_01"), attest("CUSTODIAN_CLOUD_02")]),
            evaluation_date=EVAL_DATE,
        )
        self.assertFalse(report.is_authorized)
        self.assertEqual(report.status, STATUS_KEY_REFRESH_OVERDUE)

    def test_exactly_at_the_interval_is_not_overdue(self):
        engine = MPCCustodyEngine(hardened_config(
            last_key_refresh_date=date(2026, 5, 28),   # exactly 90 days before EVAL_DATE
            refresh_interval_days=90.0,
            deny_on_overdue_refresh=True,
        ))
        self.assertEqual((EVAL_DATE - date(2026, 5, 28)).days, 90)
        report = engine.evaluate_signing_authorization(
            signing_request([attest("BOT_NODE_01"), attest("CUSTODIAN_CLOUD_02")]),
            evaluation_date=EVAL_DATE,
        )
        self.assertTrue(report.is_authorized)

    def test_one_day_past_the_interval_is_overdue(self):
        engine = MPCCustodyEngine(hardened_config(
            last_key_refresh_date=date(2026, 5, 27),   # 91 days before EVAL_DATE
            refresh_interval_days=90.0,
            deny_on_overdue_refresh=True,
        ))
        report = engine.evaluate_signing_authorization(
            signing_request([attest("BOT_NODE_01"), attest("CUSTODIAN_CLOUD_02")]),
            evaluation_date=EVAL_DATE,
        )
        self.assertFalse(report.is_authorized)
        self.assertEqual(report.status, STATUS_KEY_REFRESH_OVERDUE)

    def test_datetime_evaluation_date_is_normalised(self):
        # datetime subclasses date but `datetime - date` raises TypeError, which
        # would surface as an opaque crash inside the refresh check.
        engine = MPCCustodyEngine(hardened_config(
            last_key_refresh_date=date(2026, 1, 1),
            refresh_interval_days=90.0,
            deny_on_overdue_refresh=True,
        ))
        report = engine.evaluate_signing_authorization(
            signing_request([attest("BOT_NODE_01"), attest("CUSTODIAN_CLOUD_02")]),
            evaluation_date=datetime(2026, 8, 26, 13, 45),
        )
        self.assertEqual(report.evaluation_date, EVAL_DATE)
        self.assertEqual(report.status, STATUS_KEY_REFRESH_OVERDUE)

    def test_non_date_evaluation_date_raises(self):
        engine = MPCCustodyEngine(hardened_config())
        with self.assertRaises(MPCCustodyError):
            engine.evaluate_signing_authorization(
                signing_request([attest("BOT_NODE_01"), attest("CUSTODIAN_CLOUD_02")]),
                evaluation_date="2026-08-26",
            )

    def test_missing_refresh_date_is_reported(self):
        engine = MPCCustodyEngine(hardened_config(last_key_refresh_date=None))
        report = engine.evaluate_signing_authorization(
            signing_request([attest("BOT_NODE_01"), attest("CUSTODIAN_CLOUD_02")]),
            evaluation_date=EVAL_DATE,
        )
        self.assertTrue(any("unverifiable" in w for w in report.warnings))

    def test_future_refresh_date_is_reported_as_inconsistent(self):
        engine = MPCCustodyEngine(hardened_config(last_key_refresh_date=date(2027, 1, 1)))
        report = engine.evaluate_signing_authorization(
            signing_request([attest("BOT_NODE_01"), attest("CUSTODIAN_CLOUD_02")]),
            evaluation_date=EVAL_DATE,
        )
        self.assertTrue(any("inconsistent" in w for w in report.warnings))


class TestDenialPriorityAndReporting(unittest.TestCase):

    def test_all_failures_are_reported_not_just_the_first(self):
        engine = MPCCustodyEngine(hardened_config(
            protocol=PROTOCOL_GG18,
            implementation_hardened_against_cve_2023_33241=False,
        ))
        request = signing_request([attest("ROGUE_NODE_99", commitment="d" * 64)])
        report = engine.evaluate_signing_authorization(request, evaluation_date=EVAL_DATE)
        joined = " ".join(report.findings)
        self.assertEqual(report.status, STATUS_UNAUTHORIZED_NODE)
        self.assertIn("ROGUE_NODE_99", joined)
        self.assertIn("Quorum not met", joined)
        self.assertIn("CVE-2023-33241", joined)

    def test_threshold_equal_to_n_warns_about_lost_shard_risk(self):
        engine = MPCCustodyEngine(hardened_config(threshold_t=3))
        report = engine.evaluate_signing_authorization(
            signing_request([
                attest("BOT_NODE_01"),
                attest("CUSTODIAN_CLOUD_02"),
                attest("HSM_NODE_03"),
            ]),
            evaluation_date=EVAL_DATE,
        )
        self.assertTrue(report.is_authorized)
        self.assertTrue(any("unspendable" in w for w in report.warnings))


class TestCommitmentDerivation(unittest.TestCase):

    def test_same_shard_on_two_hosts_yields_the_same_commitment(self):
        a = derive_shard_attestation_commitment("pubshare-x1", 1, TX)
        b = derive_shard_attestation_commitment("pubshare-x1", 1, TX)
        self.assertEqual(a, b)
        self.assertEqual(len(a), 64)

    def test_distinct_shards_yield_distinct_commitments(self):
        self.assertNotEqual(
            derive_shard_attestation_commitment("pubshare-x1", 1, TX),
            derive_shard_attestation_commitment("pubshare-x2", 1, TX),
        )

    def test_commitment_is_bound_to_epoch_and_transaction(self):
        base = derive_shard_attestation_commitment("pubshare-x1", 1, TX)
        self.assertNotEqual(base, derive_shard_attestation_commitment("pubshare-x1", 2, TX))
        self.assertNotEqual(base, derive_shard_attestation_commitment("pubshare-x1", 1, "0xother"))

    def test_derived_commitments_are_accepted_end_to_end(self):
        engine = MPCCustodyEngine(hardened_config())
        request = signing_request([
            attest("BOT_NODE_01",
                   commitment=derive_shard_attestation_commitment("pub-bot", 1, TX)),
            attest("CUSTODIAN_CLOUD_02",
                   commitment=derive_shard_attestation_commitment("pub-custodian", 1, TX)),
        ])
        report = engine.evaluate_signing_authorization(request, evaluation_date=EVAL_DATE)
        self.assertTrue(report.is_authorized)

    def test_empty_public_share_raises(self):
        with self.assertRaises(MPCSigningRequestError):
            derive_shard_attestation_commitment("", 1, TX)


if __name__ == "__main__":
    unittest.main()
