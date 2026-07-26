import unittest
from cold_storage_geographic_distribution_strategy import (
    ColdStorageGeographicDistributor, VaultShardLocation
)

class TestColdStorageGeographicDistributor(unittest.TestCase):

    def setUp(self):
        # 3-of-5 threshold scheme
        self.distributor = ColdStorageGeographicDistributor(threshold_m=3, total_shards_n=5)

    def test_compliant_distribution(self):
        # 5 shards in 5 different countries across 5 different providers
        shards = [
            VaultShardLocation(1, "Zurich Bunker", "CH", "SwissVault"),
            VaultShardLocation(2, "Singapore Fort", "SG", "SingCustody"),
            VaultShardLocation(3, "Reykjavik Cave", "IS", "NordicSecure"),
            VaultShardLocation(4, "New York Vault", "US", "AmeriGuard"),
            VaultShardLocation(5, "Tokyo Safe", "JP", "NipponTrust")
        ]
        
        report = self.distributor.audit_distribution(shards)
        self.assertTrue(report.is_compliant)
        self.assertEqual(len(report.violations), 0)
        self.assertEqual(report.max_shards_in_single_country, 1)
        self.assertGreater(report.jurisdictional_entropy, 2.0)

    def test_jurisdiction_spof_violation(self):
        # 3 out of 5 shards in Switzerland (CH) -> Exceeds M=3 limit (3 >= 3)
        shards = [
            VaultShardLocation(1, "Zurich Bunker 1", "CH", "SwissVault1"),
            VaultShardLocation(2, "Zurich Bunker 2", "CH", "SwissVault2"),
            VaultShardLocation(3, "Geneva Vault", "CH", "SwissVault3"),
            VaultShardLocation(4, "Singapore Fort", "SG", "SingCustody"),
            VaultShardLocation(5, "Reykjavik Cave", "IS", "NordicSecure")
        ]
        
        report = self.distributor.audit_distribution(shards)
        self.assertFalse(report.is_compliant)
        self.assertEqual(report.max_shards_in_single_country, 3)
        self.assertTrue(any("Jurisdiction SPOF Violation" in v for v in report.violations))

    def test_provider_spof_violation(self):
        # 3 out of 5 shards with the same provider (even if in different locations)
        shards = [
            VaultShardLocation(1, "ProviderA London", "UK", "ProviderA"),
            VaultShardLocation(2, "ProviderA Zurich", "CH", "ProviderA"),
            VaultShardLocation(3, "ProviderA Singapore", "SG", "ProviderA"),
            VaultShardLocation(4, "ProviderB New York", "US", "ProviderB"),
            VaultShardLocation(5, "ProviderC Tokyo", "JP", "ProviderC")
        ]
        
        report = self.distributor.audit_distribution(shards)
        self.assertFalse(report.is_compliant)
        self.assertEqual(report.max_shards_with_single_provider, 3)
        self.assertTrue(any("Provider SPOF Violation" in v for v in report.violations))

if __name__ == '__main__':
    unittest.main()
