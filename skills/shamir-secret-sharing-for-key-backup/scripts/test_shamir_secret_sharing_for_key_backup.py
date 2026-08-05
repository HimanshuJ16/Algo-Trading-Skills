import unittest
from shamir_secret_sharing_for_key_backup import (
    Config, Engine, ShamirSecretSharingForKeyBackupEngine, SecretShare
)

class TestEngineLegacy(unittest.TestCase):
    def test_init(self):
        engine = Engine(Config())
        self.assertEqual(engine.config.name, "shamir-secret-sharing-for-key-backup")

    def test_run(self):
        engine = Engine(Config())
        self.assertTrue(engine.run())

class TestShamirSecretSharingEngine(unittest.TestCase):

    def setUp(self):
        self.sss_engine = ShamirSecretSharingForKeyBackupEngine()

    def test_split_and_reconstruct_exact_threshold(self):
        # Secret 256-bit integer
        secret = 123456789012345678901234567890
        # (3, 5) Threshold scheme: 5 total shares, 3 required to reconstruct
        result = self.sss_engine.split_secret(secret, threshold_k=3, total_shares_n=5)
        self.assertEqual(len(result.shares), 5)

        # Select any 3 shares (e.g. shares #1, #3, #5)
        subset_shares = [result.shares[0], result.shares[2], result.shares[4]]
        reconstructed = self.sss_engine.reconstruct_secret(subset_shares)

        self.assertEqual(reconstructed, secret)

    def test_insufficient_shares_produces_wrong_secret(self):
        # Secret
        secret = 987654321987654321
        result = self.sss_engine.split_secret(secret, threshold_k=3, total_shares_n=5)

        # Supply only 2 shares (k=3 required)
        insufficient_shares = [result.shares[0], result.shares[1]]
        reconstructed = self.sss_engine.reconstruct_secret(insufficient_shares)

        # Reconstructed secret must NOT match the original secret
        self.assertNotEqual(reconstructed, secret)

    def test_invalid_k_n_parameters(self):
        with self.assertRaises(ValueError):
            self.sss_engine.split_secret(100, threshold_k=5, total_shares_n=3)  # k > n invalid

if __name__ == '__main__':
    unittest.main()
