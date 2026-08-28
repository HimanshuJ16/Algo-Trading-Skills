import unittest

from on_chain_transaction_monitoring_for_anomalies import (
    ANOMALY_SCORE_THRESHOLD,
    BLOCK_SCORE_THRESHOLD,
    NATIVE_TRANSFER_SIGNATURE,
    UNKNOWN_METHOD_SIGNATURE,
    OnChainAnomalyMonitorEngine,
    OnChainMonitoringError,
    OnChainRiskPolicy,
    OnChainTxPayload,
    normalize_evm_address,
)

SANCTIONED = "0x1111111111111111111111111111111111111111"
# Same account, EIP-55 checksummed spelling vs. all-lowercase spelling.
SANCTIONED_CHECKSUMMED = "0xdEaD00000000000000000000000000000000bEEf"
SANCTIONED_LOWERCASE = SANCTIONED_CHECKSUMMED.lower()
LIST_UPDATED_AT = 1_700_000_000.0
TX_TIME = LIST_UPDATED_AT + 100.0  # 100s after the snapshot: fresh


def make_policy(**overrides):
    kwargs = dict(
        max_transfer_usd=50000.0,
        max_gas_gwei=200.0,
        sanctioned_addresses={SANCTIONED},
        whitelisted_methods={"transfer(address,uint256)"},
        sanctions_list_updated_at=LIST_UPDATED_AT,
    )
    kwargs.update(overrides)
    return OnChainRiskPolicy(**kwargs)


def make_tx(**overrides):
    kwargs = dict(
        tx_hash="0xabc123def4567890",
        from_address="0xAAAA",
        to_address="0xBBBB",
        value_usd=1000.0,
        gas_price_gwei=30.0,
        method_signature="transfer(address,uint256)",
        block_number=18000000,
        timestamp_utc=TX_TIME,
    )
    kwargs.update(overrides)
    return OnChainTxPayload(**kwargs)


def flag_names(report):
    return [f.vector_name for f in report.risk_flags]


class TestBaselineScoring(unittest.TestCase):

    def setUp(self):
        self.engine = OnChainAnomalyMonitorEngine(make_policy())

    def test_safe_transaction_monitoring(self):
        report = self.engine.audit_transaction(make_tx())
        self.assertEqual(report.status, "TRANSACTION_SAFE")
        self.assertEqual(report.risk_score, 0)
        self.assertFalse(report.is_blocked)
        self.assertEqual(report.matched_sanctioned_addresses, [])
        self.assertTrue(report.sanctions_screening_performed)
        self.assertEqual(report.sanctions_list_updated_at, LIST_UPDATED_AT)
        self.assertAlmostEqual(report.sanctions_list_age_seconds, 100.0)

    def test_high_risk_sanctioned_and_gas_spike_block(self):
        # sanctions 80 + value 40 + gas 20 + method 30 = 170, capped at 100.
        report = self.engine.audit_transaction(make_tx(
            tx_hash="0xbad999",
            to_address=SANCTIONED,
            value_usd=100000.0,
            gas_price_gwei=500.0,
            method_signature="setApprovalForAll(address,bool)",
        ))
        self.assertEqual(report.status, "HIGH_RISK_BLOCK")
        self.assertTrue(report.is_blocked)
        self.assertEqual(report.risk_score, 100)
        self.assertEqual(len(report.risk_flags), 4)
        self.assertEqual(report.matched_sanctioned_addresses, [SANCTIONED])

    def test_score_exactly_at_block_threshold_blocks(self):
        # value 40 + unapproved method 30 = exactly 70, with no sanctions hit.
        report = self.engine.audit_transaction(make_tx(
            value_usd=50000.01,
            method_signature="setApprovalForAll(address,bool)",
        ))
        self.assertEqual(report.risk_score, BLOCK_SCORE_THRESHOLD)
        self.assertEqual(report.status, "HIGH_RISK_BLOCK")
        self.assertTrue(report.is_blocked)
        self.assertEqual(report.matched_sanctioned_addresses, [])

    def test_score_exactly_at_anomaly_threshold_is_suspected_not_blocked(self):
        report = self.engine.audit_transaction(
            make_tx(method_signature="setApprovalForAll(address,bool)")
        )
        self.assertEqual(report.risk_score, ANOMALY_SCORE_THRESHOLD)
        self.assertEqual(report.status, "ANOMALY_SUSPECTED")
        self.assertFalse(report.is_blocked)

    def test_gas_only_spike_scores_below_anomaly_threshold(self):
        report = self.engine.audit_transaction(make_tx(gas_price_gwei=201.0))
        self.assertEqual(report.risk_score, 20)
        self.assertEqual(report.status, "TRANSACTION_SAFE")
        self.assertEqual(flag_names(report), ["GAS_SPIKE_MEV"])

    def test_thresholds_are_strictly_greater_than(self):
        # Exactly at both limits must not flag.
        report = self.engine.audit_transaction(
            make_tx(value_usd=50000.0, gas_price_gwei=200.0)
        )
        self.assertEqual(report.risk_score, 0)
        self.assertEqual(report.risk_flags, [])

    def test_audit_is_deterministic_on_replay(self):
        tx = make_tx(value_usd=100000.0)
        first = self.engine.audit_transaction(tx)
        second = self.engine.audit_transaction(tx)
        self.assertEqual(first.risk_score, second.risk_score)
        self.assertEqual(first.status, second.status)
        self.assertEqual(first.audit_notes, second.audit_notes)


class TestSanctionsVector(unittest.TestCase):

    def setUp(self):
        self.engine = OnChainAnomalyMonitorEngine(make_policy())

    def test_sanctioned_hit_alone_blocks(self):
        report = self.engine.audit_transaction(make_tx(to_address=SANCTIONED))
        self.assertEqual(report.risk_score, 80)
        self.assertEqual(report.status, "HIGH_RISK_BLOCK")
        self.assertTrue(report.is_blocked)
        self.assertEqual(report.matched_sanctioned_addresses, [SANCTIONED])

    def test_sender_side_match_is_detected_and_named(self):
        report = self.engine.audit_transaction(make_tx(from_address=SANCTIONED))
        self.assertEqual(flag_names(report), ["BLACKLIST_INTERACTION"])
        self.assertEqual(report.matched_sanctioned_addresses, [SANCTIONED])
        self.assertIn("from=", report.risk_flags[0].details)
        self.assertNotIn("to=", report.risk_flags[0].details)

    def test_self_transfer_by_sanctioned_address_is_reported_once(self):
        report = self.engine.audit_transaction(
            make_tx(from_address=SANCTIONED, to_address=SANCTIONED)
        )
        self.assertEqual(report.matched_sanctioned_addresses, [SANCTIONED])
        self.assertEqual(report.risk_score, 80)

    def test_checksummed_and_padded_address_still_matches(self):
        # Regression: EIP-55 mixed case and stray JSON whitespace must not
        # produce a false negative on the sanctions vector. The list holds the
        # lowercase spelling; the payload arrives checksummed and padded.
        engine = OnChainAnomalyMonitorEngine(
            make_policy(sanctioned_addresses={SANCTIONED_LOWERCASE})
        )
        report = engine.audit_transaction(
            make_tx(to_address=f"  {SANCTIONED_CHECKSUMMED}  ")
        )
        self.assertTrue(report.is_blocked)
        self.assertEqual(report.matched_sanctioned_addresses, [SANCTIONED_LOWERCASE])

    def test_policy_normalizes_mixed_case_list_entries(self):
        # The mirror case: the list holds the checksummed spelling, the payload
        # arrives lowercase.
        engine = OnChainAnomalyMonitorEngine(
            make_policy(sanctioned_addresses={SANCTIONED_CHECKSUMMED})
        )
        report = engine.audit_transaction(make_tx(to_address=SANCTIONED_LOWERCASE))
        self.assertTrue(report.is_blocked)
        self.assertEqual(report.matched_sanctioned_addresses, [SANCTIONED_LOWERCASE])

    def test_screening_disabled_is_surfaced_and_does_not_flag(self):
        engine = OnChainAnomalyMonitorEngine(make_policy(
            sanctions_screening_enabled=False,
            sanctions_list_updated_at=None,
        ))
        report = engine.audit_transaction(make_tx(to_address=SANCTIONED))
        self.assertEqual(report.status, "TRANSACTION_SAFE")
        self.assertFalse(report.sanctions_screening_performed)
        self.assertEqual(report.matched_sanctioned_addresses, [])
        self.assertIn("SANCTIONS SCREENING DISABLED", report.audit_notes)

    def test_empty_sanctions_list_with_screening_enabled_is_rejected(self):
        # Regression: the old default policy screened against an empty set and
        # still reported TRANSACTION_SAFE.
        with self.assertRaises(OnChainMonitoringError):
            OnChainRiskPolicy()
        with self.assertRaises(OnChainMonitoringError):
            OnChainRiskPolicy(sanctions_list_updated_at=LIST_UPDATED_AT)

    def test_sanctions_list_is_frozen_after_construction(self):
        policy = make_policy()
        with self.assertRaises(AttributeError):
            policy.sanctioned_addresses.add("0xdeadbeef")


class TestSanctionsListStaleness(unittest.TestCase):

    def test_stale_list_flags_and_escalates_to_anomaly(self):
        engine = OnChainAnomalyMonitorEngine(make_policy())
        stale_tx = make_tx(timestamp_utc=LIST_UPDATED_AT + 86_400.0 + 1.0)
        report = engine.audit_transaction(stale_tx)
        self.assertEqual(flag_names(report), ["SANCTIONS_LIST_STALE"])
        self.assertEqual(report.risk_score, 30)
        self.assertEqual(report.status, "ANOMALY_SUSPECTED")
        self.assertAlmostEqual(report.sanctions_list_age_seconds, 86_401.0)

    def test_age_exactly_at_limit_is_not_stale(self):
        engine = OnChainAnomalyMonitorEngine(make_policy())
        report = engine.audit_transaction(
            make_tx(timestamp_utc=LIST_UPDATED_AT + 86_400.0)
        )
        self.assertEqual(report.risk_flags, [])

    def test_snapshot_newer_than_transaction_is_not_stale(self):
        # Historical replay: the snapshot post-dates the transaction, so the
        # age is negative and the staleness vector must stay silent.
        engine = OnChainAnomalyMonitorEngine(make_policy())
        report = engine.audit_transaction(
            make_tx(timestamp_utc=LIST_UPDATED_AT - 10_000_000.0)
        )
        self.assertEqual(report.risk_flags, [])
        self.assertLess(report.sanctions_list_age_seconds, 0.0)

    def test_stale_list_plus_high_value_transfer_blocks(self):
        engine = OnChainAnomalyMonitorEngine(make_policy())
        report = engine.audit_transaction(make_tx(
            value_usd=100000.0,
            timestamp_utc=LIST_UPDATED_AT + 200_000.0,
        ))
        self.assertEqual(report.risk_score, 70)
        self.assertTrue(report.is_blocked)

    def test_undated_list_is_rejected_when_staleness_check_is_active(self):
        with self.assertRaises(OnChainMonitoringError):
            make_policy(sanctions_list_updated_at=None)

    def test_staleness_check_can_be_disabled_explicitly(self):
        policy = make_policy(
            sanctions_list_updated_at=None,
            max_sanctions_list_age_seconds=None,
        )
        report = OnChainAnomalyMonitorEngine(policy).audit_transaction(make_tx())
        self.assertEqual(report.risk_flags, [])
        self.assertIsNone(report.sanctions_list_age_seconds)


class TestMethodVector(unittest.TestCase):

    def setUp(self):
        self.engine = OnChainAnomalyMonitorEngine(make_policy())

    def test_blank_method_signature_is_rejected(self):
        # Regression: a blank signature used to skip the vector entirely,
        # scoring an undecoded contract call as TRANSACTION_SAFE.
        for blank in ("", "   "):
            with self.assertRaises(OnChainMonitoringError):
                make_tx(method_signature=blank)

    def test_undecodable_calldata_is_flagged_not_ignored(self):
        report = self.engine.audit_transaction(
            make_tx(method_signature=UNKNOWN_METHOD_SIGNATURE)
        )
        self.assertEqual(flag_names(report), ["UNAPPROVED_METHOD_CALL"])
        self.assertEqual(report.risk_score, 30)
        self.assertIn("could not be decoded", report.risk_flags[0].details)

    def test_native_transfer_sentinel_skips_the_method_vector(self):
        report = self.engine.audit_transaction(
            make_tx(method_signature=NATIVE_TRANSFER_SIGNATURE)
        )
        self.assertEqual(report.risk_flags, [])
        self.assertEqual(report.risk_score, 0)

    def test_method_matching_is_case_sensitive(self):
        # The 4-byte selector is keccak-256 of the exact canonical signature.
        report = self.engine.audit_transaction(
            make_tx(method_signature="Transfer(address,uint256)")
        )
        self.assertEqual(flag_names(report), ["UNAPPROVED_METHOD_CALL"])

    def test_whitelist_entry_with_whitespace_is_rejected(self):
        with self.assertRaises(OnChainMonitoringError):
            make_policy(whitelisted_methods={"transfer(address, uint256)"})

    def test_unknown_sentinel_cannot_be_whitelisted(self):
        with self.assertRaises(OnChainMonitoringError):
            make_policy(whitelisted_methods={UNKNOWN_METHOD_SIGNATURE})


class TestBlockingMethods(unittest.TestCase):
    """A drainer's setApprovalForAll moves no value, so the high-value vector
    cannot see it and the unapproved-method penalty alone (30) does not reach
    the block threshold. blocking_methods makes that categorical."""

    APPROVE_ALL = "setApprovalForAll(address,bool)"

    def test_unapproved_approval_call_alone_does_not_block_by_default(self):
        # Documents the shipped-default behaviour this control exists to fix.
        engine = OnChainAnomalyMonitorEngine(make_policy())
        report = engine.audit_transaction(
            make_tx(value_usd=0.0, method_signature=self.APPROVE_ALL)
        )
        self.assertEqual(report.risk_score, 30)
        self.assertFalse(report.is_blocked)

    def test_blocking_method_blocks_regardless_of_value(self):
        engine = OnChainAnomalyMonitorEngine(
            make_policy(blocking_methods={self.APPROVE_ALL})
        )
        report = engine.audit_transaction(
            make_tx(value_usd=0.0, method_signature=self.APPROVE_ALL)
        )
        self.assertEqual(flag_names(report), ["BLOCKING_METHOD_CALL"])
        self.assertEqual(report.status, "HIGH_RISK_BLOCK")
        self.assertTrue(report.is_blocked)
        self.assertEqual(report.risk_score, 100)

    def test_blocking_method_does_not_also_emit_unapproved_flag(self):
        engine = OnChainAnomalyMonitorEngine(
            make_policy(blocking_methods={self.APPROVE_ALL})
        )
        report = engine.audit_transaction(make_tx(method_signature=self.APPROVE_ALL))
        self.assertEqual(len(report.risk_flags), 1)

    def test_other_methods_are_unaffected_by_the_blocking_set(self):
        engine = OnChainAnomalyMonitorEngine(
            make_policy(blocking_methods={self.APPROVE_ALL})
        )
        report = engine.audit_transaction(make_tx())
        self.assertEqual(report.risk_flags, [])

    def test_undecodable_calldata_may_be_made_categorically_blocking(self):
        engine = OnChainAnomalyMonitorEngine(
            make_policy(blocking_methods={UNKNOWN_METHOD_SIGNATURE})
        )
        report = engine.audit_transaction(
            make_tx(method_signature=UNKNOWN_METHOD_SIGNATURE)
        )
        self.assertTrue(report.is_blocked)
        self.assertEqual(flag_names(report), ["BLOCKING_METHOD_CALL"])

    def test_method_cannot_be_both_whitelisted_and_blocking(self):
        with self.assertRaises(OnChainMonitoringError):
            make_policy(
                whitelisted_methods={"transfer(address,uint256)"},
                blocking_methods={"transfer(address,uint256)"},
            )

    def test_blocking_method_entry_with_whitespace_is_rejected(self):
        with self.assertRaises(OnChainMonitoringError):
            make_policy(blocking_methods={"setApprovalForAll(address, bool)"})

    def test_blocking_methods_are_frozen_after_construction(self):
        policy = make_policy(blocking_methods={self.APPROVE_ALL})
        with self.assertRaises(AttributeError):
            policy.blocking_methods.add("approve(address,uint256)")


class TestGasVector(unittest.TestCase):

    def test_baseline_multiple_catches_spike_below_fixed_ceiling(self):
        # 20 Gwei baseline x 5 = 100 Gwei trigger; 150 Gwei is well under the
        # 200 Gwei fixed ceiling but is a 7.5x regime spike.
        engine = OnChainAnomalyMonitorEngine(make_policy(gas_baseline_gwei=20.0))
        report = engine.audit_transaction(make_tx(gas_price_gwei=150.0))
        self.assertEqual(flag_names(report), ["GAS_SPIKE_MEV"])
        self.assertEqual(report.risk_score, 20)
        self.assertIn("baseline", report.risk_flags[0].details)

    def test_baseline_and_ceiling_together_are_not_double_counted(self):
        engine = OnChainAnomalyMonitorEngine(make_policy(gas_baseline_gwei=20.0))
        report = engine.audit_transaction(make_tx(gas_price_gwei=500.0))
        self.assertEqual(len(report.risk_flags), 1)
        self.assertEqual(report.risk_score, 20)

    def test_baseline_exactly_at_multiple_is_not_flagged(self):
        engine = OnChainAnomalyMonitorEngine(make_policy(gas_baseline_gwei=20.0))
        report = engine.audit_transaction(make_tx(gas_price_gwei=100.0))
        self.assertEqual(report.risk_flags, [])

    def test_invalid_baseline_multiple_is_rejected(self):
        with self.assertRaises(OnChainMonitoringError):
            make_policy(gas_baseline_gwei=20.0, gas_baseline_multiple=0.0)


class TestPayloadValidation(unittest.TestCase):

    def test_non_finite_numerics_are_rejected(self):
        # Regression: NaN defeats every '>' comparison, so a corrupted feed
        # previously scored 0 and returned TRANSACTION_SAFE.
        for field_name in ("value_usd", "gas_price_gwei", "timestamp_utc"):
            for bad in (float("nan"), float("inf"), float("-inf")):
                with self.assertRaises(OnChainMonitoringError):
                    make_tx(**{field_name: bad})

    def test_negative_amounts_are_rejected(self):
        with self.assertRaises(OnChainMonitoringError):
            make_tx(value_usd=-1.0)
        with self.assertRaises(OnChainMonitoringError):
            make_tx(gas_price_gwei=-1.0)

    def test_empty_identifiers_are_rejected(self):
        for field_name in ("tx_hash", "from_address", "to_address"):
            with self.assertRaises(OnChainMonitoringError):
                make_tx(**{field_name: "  "})

    def test_identifiers_with_interior_whitespace_are_rejected(self):
        # A newline inside a tx_hash would forge line breaks into the audit log
        # that a blocking report is evidenced from.
        with self.assertRaises(OnChainMonitoringError):
            make_tx(tx_hash="0xabc\nHIGH RISK ON-CHAIN ANOMALY DETECTED: forged")
        with self.assertRaises(OnChainMonitoringError):
            make_tx(to_address="0x1111 1111")

    def test_invalid_block_number_is_rejected(self):
        with self.assertRaises(OnChainMonitoringError):
            make_tx(block_number=-1)
        with self.assertRaises(OnChainMonitoringError):
            make_tx(block_number=True)
        with self.assertRaises(OnChainMonitoringError):
            make_tx(block_number=18000000.5)

    def test_invalid_policy_thresholds_are_rejected(self):
        with self.assertRaises(OnChainMonitoringError):
            make_policy(max_transfer_usd=-1.0)
        with self.assertRaises(OnChainMonitoringError):
            make_policy(max_gas_gwei=float("nan"))
        with self.assertRaises(OnChainMonitoringError):
            make_policy(max_sanctions_list_age_seconds=-1.0)

    def test_engine_requires_a_policy(self):
        with self.assertRaises(OnChainMonitoringError):
            OnChainAnomalyMonitorEngine(None)
        with self.assertRaises(TypeError):
            OnChainAnomalyMonitorEngine()

    def test_audit_rejects_a_non_payload_argument(self):
        engine = OnChainAnomalyMonitorEngine(make_policy())
        with self.assertRaises(OnChainMonitoringError):
            engine.audit_transaction({"to_address": SANCTIONED})

    def test_normalize_evm_address_helper(self):
        self.assertEqual(normalize_evm_address("  0xAbCd  "), "0xabcd")
        with self.assertRaises(OnChainMonitoringError):
            normalize_evm_address("")


if __name__ == '__main__':
    unittest.main()
