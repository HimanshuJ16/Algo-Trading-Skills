import unittest
from multi_party_computation_mpc_custody_solutions import (
    MPCCustodyEngine, MPCCustodyConfig, MPCSigningRequest, MPCSigningReport
)

class TestMPCCustodyEngine(unittest.TestCase):

    def setUp(self):
        self.config = MPCCustodyConfig(num_shards=3, threshold_t=2, protocol="CMP")
        self.engine = MPCCustodyEngine(self.config)

    def test_valid_2_of_3_threshold_signing(self):
        # 2-of-3 threshold: Node 1 and Node 2 submit partial shares -> MPC_SIGNING_SUCCESS!
        shares = {
            "BOT_NODE_01": "PARTIAL_SHARE_HASH_01",
            "CUSTODIAN_CLOUD_02": "PARTIAL_SHARE_HASH_02"
        }
        req = MPCSigningRequest(
            tx_hash="0xabc123789def",
            amount_usd=100000.0,
            destination_address="0xDestinationAddress",
            partial_share_submissions=shares
        )

        report = self.engine.process_threshold_signing(req)

        self.assertTrue(report.is_signed)
        self.assertTrue(report.threshold_met)
        self.assertEqual(report.status, "MPC_SIGNING_SUCCESS")
        self.assertEqual(report.submitted_shares_count, 2)
        self.assertTrue(report.signature_r.startswith("0x"))
        self.assertTrue(report.signature_s.startswith("0x"))

    def test_insufficient_shares_threshold_rejection(self):
        # Only 1 share submitted for 2-of-3 threshold -> MPC_THRESHOLD_NOT_MET!
        shares = {
            "BOT_NODE_01": "PARTIAL_SHARE_HASH_01"
        }
        req = MPCSigningRequest(
            tx_hash="0xbad456",
            amount_usd=500000.0,
            destination_address="0xDestinationAddress",
            partial_share_submissions=shares
        )

        report = self.engine.process_threshold_signing(req)

        self.assertFalse(report.is_signed)
        self.assertFalse(report.threshold_met)
        self.assertEqual(report.status, "MPC_THRESHOLD_NOT_MET")
        self.assertEqual(report.submitted_shares_count, 1)

if __name__ == '__main__':
    unittest.main()
