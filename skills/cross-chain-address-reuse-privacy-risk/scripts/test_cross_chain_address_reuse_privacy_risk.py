import unittest
from cross_chain_address_reuse_privacy_risk import (
    CrossChainAddressPrivacyAuditor, WalletAddressRecord, PrivacyRiskReport
)


def make_record(chain_id, address, public_key, is_kyc_linked=False, wallet_label="Bot"):
    return WalletAddressRecord(chain_id, address, public_key, is_kyc_linked, wallet_label)


class TestCrossChainAddressPrivacyAuditor(unittest.TestCase):

    def setUp(self):
        self.auditor = CrossChainAddressPrivacyAuditor(high_risk_threshold=70.0, total_tracked_chains=5)

        # Shared EVM address reused across 4 chains with 1 KYC link
        addr = "0x1234567890abcdef1234567890abcdef12345678"
        self.auditor.register_wallet(make_record("Ethereum", addr, "PUBKEY_01", False, "Bot_Eth"))
        self.auditor.register_wallet(make_record("Arbitrum", addr, "PUBKEY_01", False, "Bot_Arb"))
        self.auditor.register_wallet(make_record("Optimism", addr, "PUBKEY_01", False, "Bot_Op"))
        self.auditor.register_wallet(make_record("Polygon", addr, "PUBKEY_01", True, "Binance_Deposit"))

        # Isolated unique address
        self.auditor.register_wallet(make_record("Solana", "SolAddr111111111111111111", "PUBKEY_SOL", False, "Bot_Sol"))

    def test_high_privacy_risk_on_address_reuse_and_kyc(self):
        addr = "0x1234567890abcdef1234567890abcdef12345678"
        report = self.auditor.audit_address_privacy(addr)

        self.assertEqual(report.reused_chains_count, 4)
        self.assertTrue(report.is_kyc_contaminated)
        # Reuse score: 4/5 * 50 = 40.0 + 50.0 KYC = 90.0
        self.assertEqual(report.privacy_risk_score, 90.0)
        self.assertEqual(report.risk_level, "HIGH")
        self.assertEqual(len(report.remediation_actions), 2)
        self.assertEqual(report.linked_public_keys, ["PUBKEY_01"])

    def test_low_privacy_risk_on_isolated_address(self):
        report = self.auditor.audit_address_privacy("SolAddr111111111111111111")

        self.assertEqual(report.reused_chains_count, 1)
        self.assertFalse(report.is_kyc_contaminated)
        # A single chain is NO reuse, so the reuse weight is 0.0 (it used to
        # charge 1/5 * 50 = 10.0 for a perfectly isolated address).
        self.assertEqual(report.privacy_risk_score, 0.0)
        self.assertEqual(report.risk_level, "LOW")
        self.assertEqual(
            report.remediation_actions,
            ["Wallet address exhibits strong cross-chain privacy isolation."],
        )

    def test_untracked_address_returns_not_tracked_not_low(self):
        report = self.auditor.audit_address_privacy("0x9999999999999999999999999999999999999999")

        self.assertEqual(report.reused_chains_count, 0)
        self.assertEqual(report.risk_level, "NOT_TRACKED")
        self.assertEqual(report.privacy_risk_score, 0.0)
        self.assertFalse(report.is_kyc_contaminated)
        self.assertIn("UNKNOWN", "; ".join(report.remediation_actions))

    def test_public_key_clustering_links_cross_format_addresses(self):
        # Same secp256k1 key behind an EVM address and a Bitcoin base58 address:
        # different address formats, identical public key -> one cluster.
        auditor = CrossChainAddressPrivacyAuditor(total_tracked_chains=5)
        auditor.register_wallet(make_record("Ethereum", "0xabc0000000000000000000000000000000000abc", "PUBKEY_SHARED"))
        auditor.register_wallet(make_record("Bitcoin", "1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2", "PUBKEY_SHARED"))

        report = auditor.audit_address_privacy("0xabc0000000000000000000000000000000000abc")

        self.assertEqual(report.reused_chains_count, 2)
        self.assertEqual(report.chains_list, ["Bitcoin", "Ethereum"])
        self.assertEqual(report.linked_public_keys, ["PUBKEY_SHARED"])
        self.assertTrue(any("PUBLIC KEY LINKAGE" in a for a in report.remediation_actions))
        # Reuse score: 2/5 * 50 = 20.0, no KYC -> LOW but flagged for reuse.
        self.assertEqual(report.privacy_risk_score, 20.0)
        self.assertEqual(report.risk_level, "LOW")

    def test_public_key_clustering_is_transitive(self):
        # A links B by address, B links C by public key -> all three in one cluster.
        auditor = CrossChainAddressPrivacyAuditor(total_tracked_chains=5)
        auditor.register_wallet(make_record("Ethereum", "0xaaa0000000000000000000000000000000000aaa", "PUBKEY_X"))
        auditor.register_wallet(make_record("Arbitrum", "0xaaa0000000000000000000000000000000000aaa", "PUBKEY_Y"))
        auditor.register_wallet(make_record("Solana", "SolPUBKEYY0000000000000000000000000", "PUBKEY_Y"))

        report = auditor.audit_address_privacy("0xaaa0000000000000000000000000000000000aaa")
        self.assertEqual(report.reused_chains_count, 3)
        self.assertEqual(report.chains_list, ["Arbitrum", "Ethereum", "Solana"])

    def test_base58_addresses_case_sensitive(self):
        # In base58 both letter cases are distinct characters: these are two
        # different addresses and must NOT be merged into one cluster.
        auditor = CrossChainAddressPrivacyAuditor(total_tracked_chains=5)
        auditor.register_wallet(make_record("Solana", "ABcDef1111111111111111111111111", "PUBKEY_1"))
        auditor.register_wallet(make_record("Bitcoin", "abcDEF1111111111111111111111111", "PUBKEY_2"))

        report = auditor.audit_address_privacy("ABcDef1111111111111111111111111")
        self.assertEqual(report.reused_chains_count, 1)
        self.assertEqual(report.chains_list, ["Solana"])

    def test_evm_address_matching_case_insensitive(self):
        # EIP-55 mixed-case checksum form resolves to the same EVM address.
        checksummed = "0x1234567890ABCDEF1234567890ABCDEF12345678"
        report = self.auditor.audit_address_privacy(checksummed)
        self.assertEqual(report.reused_chains_count, 4)
        self.assertIn("Ethereum", report.chains_list)

    def test_kyc_only_single_chain_is_medium(self):
        # Single chain -> 0.0 reuse weight + 50.0 KYC = 50.0 -> still MEDIUM
        auditor = CrossChainAddressPrivacyAuditor(total_tracked_chains=5)
        auditor.register_wallet(make_record("Solana", "SolKYC111111111111111111111111", "PUBKEY_K", True))
        report = auditor.audit_address_privacy("SolKYC111111111111111111111111")
        self.assertEqual(report.privacy_risk_score, 50.0)
        self.assertEqual(report.risk_level, "MEDIUM")

    def test_high_threshold_boundary_is_inclusive(self):
        # 2/5 * 50 = 20.0 + 50.0 KYC = exactly 70.0 -> HIGH (>= threshold)
        auditor = CrossChainAddressPrivacyAuditor(high_risk_threshold=70.0, total_tracked_chains=5)
        addr = "0x2220000000000000000000000000000000000222"
        auditor.register_wallet(make_record("Ethereum", addr, "PUBKEY_B", True))
        auditor.register_wallet(make_record("Arbitrum", addr, "PUBKEY_B", False))
        report = auditor.audit_address_privacy(addr)
        self.assertEqual(report.privacy_risk_score, 70.0)
        self.assertEqual(report.risk_level, "HIGH")

    def test_medium_threshold_boundary_is_inclusive(self):
        # 4/5 * 50 = exactly 40.0 without KYC -> MEDIUM (>= medium threshold)
        auditor = CrossChainAddressPrivacyAuditor(total_tracked_chains=5)
        addr = "0x3330000000000000000000000000000000000333"
        for chain in ("Ethereum", "Arbitrum", "Optimism", "Polygon"):
            auditor.register_wallet(make_record(chain, addr, "PUBKEY_C", False))
        report = auditor.audit_address_privacy(addr)
        self.assertEqual(report.privacy_risk_score, 40.0)
        self.assertEqual(report.risk_level, "MEDIUM")

    def test_reuse_score_clamped_when_registry_exceeds_configured_chains(self):
        auditor = CrossChainAddressPrivacyAuditor(total_tracked_chains=3)
        addr = "0x4440000000000000000000000000000000000444"
        for chain in ("Ethereum", "Arbitrum", "Optimism", "Polygon", "BSC"):
            auditor.register_wallet(make_record(chain, addr, "PUBKEY_D", False))
        with self.assertLogs(level="WARNING"):
            auditor.register_wallet(make_record("Avalanche", addr, "PUBKEY_D", False))
        report = auditor.audit_address_privacy(addr)
        # 6/3 * 50 = 100 raw -> clamped to 50.0 reuse weight, no KYC -> MEDIUM
        self.assertEqual(report.privacy_risk_score, 50.0)
        self.assertEqual(report.risk_level, "MEDIUM")

    def test_register_wallet_rejects_empty_and_malformed_records(self):
        with self.assertRaises(ValueError):
            self.auditor.register_wallet(make_record("Ethereum", "", "PUBKEY_X"))
        with self.assertRaises(ValueError):
            self.auditor.register_wallet(make_record("  ", "0x1", "PUBKEY_X"))
        with self.assertRaises(ValueError):
            self.auditor.register_wallet(WalletAddressRecord("Ethereum", "0x2", "PUBKEY_X", "yes", "Bot"))
        with self.assertRaises(ValueError):
            self.auditor.register_wallet("not-a-record")

    def test_register_wallet_rejects_duplicate_records(self):
        addr = "0x1234567890abcdef1234567890abcdef12345678"
        with self.assertRaises(ValueError):
            self.auditor.register_wallet(make_record("Ethereum", addr, "PUBKEY_01"))

    def test_invalid_configuration_rejected(self):
        with self.assertRaises(ValueError):
            CrossChainAddressPrivacyAuditor(high_risk_threshold=60.0, medium_risk_threshold=70.0)
        with self.assertRaises(ValueError):
            CrossChainAddressPrivacyAuditor(total_tracked_chains=0)
        with self.assertRaises(ValueError):
            CrossChainAddressPrivacyAuditor(high_risk_threshold="high")

    def test_isolated_address_scores_zero_on_single_chain_desk(self):
        # A desk tracking one chain: a perfectly isolated address must not be
        # flagged. The old K/M formula charged 1/1 * 50 = 50.0 -> MEDIUM.
        auditor = CrossChainAddressPrivacyAuditor(total_tracked_chains=1)
        auditor.register_wallet(make_record("Ethereum", "0x5550000000000000000000000000000000000555", "PUBKEY_E"))
        report = auditor.audit_address_privacy("0x5550000000000000000000000000000000000555")
        self.assertEqual(report.privacy_risk_score, 0.0)
        self.assertEqual(report.risk_level, "LOW")

    def test_low_verdict_with_findings_does_not_claim_isolation(self):
        # 2 chains, no KYC -> 20.0 (LOW), but reuse WAS detected. The old code
        # appended "strong cross-chain privacy isolation" as the last action,
        # contradicting the finding directly above it.
        auditor = CrossChainAddressPrivacyAuditor(total_tracked_chains=5)
        addr = "0x6660000000000000000000000000000000000666"
        auditor.register_wallet(make_record("Ethereum", addr, "PUBKEY_F"))
        auditor.register_wallet(make_record("Arbitrum", addr, "PUBKEY_F"))
        report = auditor.audit_address_privacy(addr)
        self.assertEqual(report.risk_level, "LOW")
        joined = " ".join(report.remediation_actions)
        self.assertIn("ADDRESS REUSE DETECTED", joined)
        self.assertNotIn("strong cross-chain privacy isolation", joined)

    # --- Chain label normalisation -------------------------------------------

    def test_chain_label_variants_count_as_one_chain(self):
        # "Ethereum" / "ethereum" / " Ethereum " are one chain, not three;
        # the old code counted 3 and reported reuse for a single-chain wallet.
        auditor = CrossChainAddressPrivacyAuditor(total_tracked_chains=5)
        addr = "0x7770000000000000000000000000000000000777"
        auditor.register_wallet(make_record("Ethereum", addr, "PUBKEY_G1"))
        auditor.register_wallet(make_record("ethereum", addr, "PUBKEY_G2"))
        auditor.register_wallet(make_record(" Ethereum ", addr, "PUBKEY_G3"))
        report = auditor.audit_address_privacy(addr)
        self.assertEqual(report.reused_chains_count, 1)
        self.assertEqual(report.chains_list, ["Ethereum"])
        self.assertEqual(report.privacy_risk_score, 0.0)

    def test_duplicate_detection_ignores_chain_label_casing(self):
        addr = "0x1234567890abcdef1234567890abcdef12345678"
        with self.assertRaises(ValueError):
            self.auditor.register_wallet(make_record("  ethereum ", addr, "PUBKEY_01"))

    # --- Unknown (unrevealed) public keys ------------------------------------

    def test_unknown_public_keys_do_not_cluster(self):
        # Two unspent Bitcoin-style addresses whose keys are not yet revealed
        # on-chain share nothing. A placeholder string would have merged them.
        auditor = CrossChainAddressPrivacyAuditor(total_tracked_chains=5)
        auditor.register_wallet(make_record("Bitcoin", "1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2", None))
        auditor.register_wallet(make_record("Litecoin", "LhyLNfBkoKshT7R8Pce6vkB9T2cP2o84hx", None, True))
        report = auditor.audit_address_privacy("1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2")
        self.assertEqual(report.reused_chains_count, 1)
        self.assertEqual(report.chains_list, ["Bitcoin"])
        # The KYC-linked Litecoin record must NOT contaminate this cluster.
        self.assertFalse(report.is_kyc_contaminated)
        self.assertEqual(report.linked_public_keys, [])

    def test_blank_public_key_still_rejected(self):
        with self.assertRaises(ValueError):
            self.auditor.register_wallet(make_record("Base", "0x8880000000000000000000000000000000000888", "   "))
        with self.assertRaises(ValueError):
            self.auditor.register_wallet(make_record("Base", "0x8880000000000000000000000000000000000888", 42))

    def test_audit_rejects_empty_target(self):
        with self.assertRaises(ValueError):
            self.auditor.audit_address_privacy("")
        with self.assertRaises(ValueError):
            self.auditor.audit_address_privacy(None)


if __name__ == '__main__':
    unittest.main()
