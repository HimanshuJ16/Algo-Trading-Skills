import unittest

from cold_storage_geographic_distribution_strategy import (
    ColdStorageGeographicDistributor, VaultShardLocation
)


def _compliant_five():
    """5 shards, 5 countries, 5 providers - the reference safe 3-of-5 placement."""
    return [
        VaultShardLocation(1, "Zurich Bunker", "CH", "SwissVault"),
        VaultShardLocation(2, "Singapore Fort", "SG", "SingCustody"),
        VaultShardLocation(3, "Reykjavik Cave", "IS", "NordicSecure"),
        VaultShardLocation(4, "New York Vault", "US", "AmeriGuard"),
        VaultShardLocation(5, "Tokyo Safe", "JP", "NipponTrust"),
    ]


class TestColdStorageGeographicDistributor(unittest.TestCase):

    def setUp(self):
        # 3-of-5 threshold scheme
        self.distributor = ColdStorageGeographicDistributor(threshold_m=3, total_shards_n=5)

    def test_compliant_distribution(self):
        report = self.distributor.audit_distribution(_compliant_five())
        self.assertTrue(report.is_compliant)
        self.assertEqual(len(report.violations), 0)
        self.assertEqual(report.max_shards_in_single_country, 1)
        self.assertEqual(report.max_shards_in_single_jurisdiction, 1)
        self.assertEqual(report.redundancy_gap, 2)
        # H = -5 * (0.2 * log2(0.2)) = log2(5)
        self.assertAlmostEqual(report.jurisdictional_entropy, 2.322, places=3)

    def test_jurisdiction_spof_violation(self):
        # 3 of 5 shards in Switzerland -> a single country reaches M=3
        shards = [
            VaultShardLocation(1, "Zurich Bunker 1", "CH", "SwissVault1"),
            VaultShardLocation(2, "Zurich Bunker 2", "CH", "SwissVault2"),
            VaultShardLocation(3, "Geneva Vault", "CH", "SwissVault3"),
            VaultShardLocation(4, "Singapore Fort", "SG", "SingCustody"),
            VaultShardLocation(5, "Reykjavik Cave", "IS", "NordicSecure"),
        ]
        report = self.distributor.audit_distribution(shards)
        self.assertFalse(report.is_compliant)
        self.assertEqual(report.max_shards_in_single_country, 3)
        self.assertTrue(any("Country Confidentiality SPOF Violation" in v for v in report.violations))

    def test_provider_spof_violation(self):
        # 3 of 5 shards with the same provider, in three different countries
        shards = [
            VaultShardLocation(1, "ProviderA London", "GB", "ProviderA"),
            VaultShardLocation(2, "ProviderA Zurich", "CH", "ProviderA"),
            VaultShardLocation(3, "ProviderA Singapore", "SG", "ProviderA"),
            VaultShardLocation(4, "ProviderB New York", "US", "ProviderB"),
            VaultShardLocation(5, "ProviderC Tokyo", "JP", "ProviderC"),
        ]
        report = self.distributor.audit_distribution(shards)
        self.assertFalse(report.is_compliant)
        self.assertEqual(report.max_shards_with_single_provider, 3)
        self.assertTrue(any("Provider Confidentiality SPOF Violation" in v for v in report.violations))

    def test_shared_legal_jurisdiction_across_countries_is_flagged(self):
        # Three distinct countries, one shared legal regime: country counts are all
        # 1, so only the jurisdiction grouping can catch this concentration.
        shards = [
            VaultShardLocation(1, "Dublin Vault", "IE", "IrishCustody", jurisdiction="EU"),
            VaultShardLocation(2, "Frankfurt Vault", "DE", "GermanCustody", jurisdiction="EU"),
            VaultShardLocation(3, "Paris Vault", "FR", "FrenchCustody", jurisdiction="EU"),
            VaultShardLocation(4, "Singapore Fort", "SG", "SingCustody"),
            VaultShardLocation(5, "Tokyo Safe", "JP", "NipponTrust"),
        ]
        report = self.distributor.audit_distribution(shards)
        self.assertFalse(report.is_compliant)
        self.assertEqual(report.max_shards_in_single_country, 1)
        self.assertEqual(report.max_shards_in_single_jurisdiction, 3)
        self.assertTrue(
            any("Jurisdiction Confidentiality SPOF Violation" in v for v in report.violations)
        )

    def test_availability_spof_without_confidentiality_breach(self):
        # Regression: 4-of-6 with 3 shards in one country is confidentiality-safe
        # (3 < M=4) but losing that country leaves 3 < 4 shards -> assets locked
        # forever. A confidentiality-only audit passes this placement.
        distributor = ColdStorageGeographicDistributor(threshold_m=4, total_shards_n=6)
        shards = [
            VaultShardLocation(1, "NY Vault", "US", "AmeriGuard"),
            VaultShardLocation(2, "Chicago Vault", "US", "MidwestVault"),
            VaultShardLocation(3, "Dallas Vault", "US", "TexVault"),
            VaultShardLocation(4, "Zurich Bunker", "CH", "SwissVault"),
            VaultShardLocation(5, "Singapore Fort", "SG", "SingCustody"),
            VaultShardLocation(6, "Tokyo Safe", "JP", "NipponTrust"),
        ]
        report = distributor.audit_distribution(shards)
        self.assertFalse(report.is_compliant)
        self.assertLess(report.max_shards_in_single_country, distributor.threshold_m)
        self.assertTrue(any("Country Availability SPOF Violation" in v for v in report.violations))
        self.assertFalse(any("Confidentiality" in v for v in report.violations))

    def test_case_and_whitespace_variants_group_together(self):
        # Regression: 'ch', 'CH' and ' Ch ' are one country, not three.
        shards = [
            VaultShardLocation(1, "Zurich Bunker", "ch", "SwissVault1"),
            VaultShardLocation(2, "Geneva Vault", "CH", "SwissVault2"),
            VaultShardLocation(3, "Lugano Vault", " Ch ", "SwissVault3"),
            VaultShardLocation(4, "Singapore Fort", "SG", "SingCustody"),
            VaultShardLocation(5, "Tokyo Safe", "JP", "NipponTrust"),
        ]
        report = self.distributor.audit_distribution(shards)
        self.assertEqual(report.max_shards_in_single_country, 3)
        self.assertFalse(report.is_compliant)

    def test_same_provider_under_different_spellings_group_together(self):
        shards = [
            VaultShardLocation(1, "Vault A", "CH", "provider a"),
            VaultShardLocation(2, "Vault B", "SG", "PROVIDER A"),
            VaultShardLocation(3, "Vault C", "JP", " Provider A "),
            VaultShardLocation(4, "Vault D", "US", "ProviderB"),
            VaultShardLocation(5, "Vault E", "IS", "ProviderC"),
        ]
        report = self.distributor.audit_distribution(shards)
        self.assertEqual(report.max_shards_with_single_provider, 3)
        self.assertFalse(report.is_compliant)

    def test_duplicate_shard_id_is_rejected(self):
        # Two copies of shard 1 are not two shards: the scheme really has 4.
        shards = _compliant_five()
        shards[1] = VaultShardLocation(1, "Singapore Fort", "SG", "SingCustody")
        with self.assertRaises(ValueError) as ctx:
            self.distributor.audit_distribution(shards)
        self.assertIn("Duplicate shard_id", str(ctx.exception))

    def test_shard_id_above_n_is_rejected(self):
        shards = _compliant_five()
        shards[4] = VaultShardLocation(6, "Tokyo Safe", "JP", "NipponTrust")
        with self.assertRaises(ValueError):
            self.distributor.audit_distribution(shards)

    def test_wrong_shard_count_is_rejected(self):
        with self.assertRaises(ValueError):
            self.distributor.audit_distribution(_compliant_five()[:4])

    def test_missing_iso_27001_is_a_violation(self):
        shards = _compliant_five()
        shards[0] = VaultShardLocation(1, "Zurich Bunker", "CH", "SwissVault", is_iso_27001=False)
        report = self.distributor.audit_distribution(shards)
        self.assertFalse(report.is_compliant)
        self.assertTrue(any("ISO 27001" in v for v in report.violations))

    def test_redundancy_reserve_violation_for_thin_scheme(self):
        # 2-of-3: losing 2 shards destroys access, so N-M=1 is below the default
        # minimum reserve of 2 even though no group reaches the threshold.
        distributor = ColdStorageGeographicDistributor(threshold_m=2, total_shards_n=3)
        shards = [
            VaultShardLocation(1, "Zurich Bunker", "CH", "SwissVault"),
            VaultShardLocation(2, "Singapore Fort", "SG", "SingCustody"),
            VaultShardLocation(3, "Tokyo Safe", "JP", "NipponTrust"),
        ]
        report = distributor.audit_distribution(shards)
        self.assertFalse(report.is_compliant)
        self.assertEqual(report.redundancy_gap, 1)
        self.assertTrue(any("Redundancy Reserve Violation" in v for v in report.violations))

    def test_redundancy_reserve_is_configurable(self):
        distributor = ColdStorageGeographicDistributor(
            threshold_m=2, total_shards_n=3, min_redundancy_gap=1
        )
        shards = [
            VaultShardLocation(1, "Zurich Bunker", "CH", "SwissVault"),
            VaultShardLocation(2, "Singapore Fort", "SG", "SingCustody"),
            VaultShardLocation(3, "Tokyo Safe", "JP", "NipponTrust"),
        ]
        self.assertTrue(distributor.audit_distribution(shards).is_compliant)

    def test_invalid_threshold_parameters(self):
        with self.assertRaises(ValueError):
            ColdStorageGeographicDistributor(threshold_m=1, total_shards_n=5)
        with self.assertRaises(ValueError):
            ColdStorageGeographicDistributor(threshold_m=6, total_shards_n=5)
        with self.assertRaises(ValueError):
            ColdStorageGeographicDistributor(threshold_m=3, total_shards_n=5, min_redundancy_gap=-1)

    def test_invalid_vault_attributes(self):
        with self.assertRaises(ValueError):
            VaultShardLocation(1, "Zurich Bunker", "Switzerland", "SwissVault")
        with self.assertRaises(ValueError):
            VaultShardLocation(0, "Zurich Bunker", "CH", "SwissVault")
        with self.assertRaises(ValueError):
            VaultShardLocation(1, "Zurich Bunker", "CH", "   ")
        with self.assertRaises(ValueError):
            VaultShardLocation(1, "  ", "CH", "SwissVault")

    def test_entropy_values(self):
        # 2/2/1 over CH/SG/IS: H = -2*(0.4*log2(0.4)) - 0.2*log2(0.2) = 1.521928
        self.assertAlmostEqual(
            self.distributor.calculate_entropy({"CH": 2, "SG": 2, "IS": 1}), 1.522, places=3
        )
        # Fully concentrated distribution carries no geographic information.
        self.assertEqual(self.distributor.calculate_entropy({"CH": 5}), 0.0)
        self.assertEqual(self.distributor.calculate_entropy({}), 0.0)


if __name__ == '__main__':
    unittest.main()
