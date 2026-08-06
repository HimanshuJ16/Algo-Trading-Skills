import unittest
import datetime
from withdrawal_velocity_limits_and_anomaly_detection import (
    WithdrawalStatus,
    RiskFlag,
    WithdrawalRequest,
    AddressWhitelistRecord,
    AccountHistoricalProfile,
    WithdrawalDecision,
    WithdrawalVelocityEngine,
    VelocityEngineError,
)


class TestWithdrawalVelocityEngine(unittest.TestCase):
    def setUp(self):
        self.engine = WithdrawalVelocityEngine(
            account_hourly_limit_usd=100_000.0,
            account_daily_limit_usd=500_000.0,
            global_hot_wallet_hourly_limit_usd=1_000_000.0,
            anomaly_zscore_threshold=3.0,
            address_whitelist_cooling_hours=24.0,
        )
        self.now = datetime.datetime.utcnow()

        self.valid_whitelist = AddressWhitelistRecord(
            account_id="ACC1",
            address="0x1234567890abcdef",
            added_timestamp=self.now - datetime.timedelta(hours=48),  # 48h old > 24h cooling
            is_whitelisted=True,
        )

        self.normal_profile = AccountHistoricalProfile(
            account_id="ACC1",
            mean_withdrawal_usd=20_000.0,
            std_dev_usd=10_000.0,
            historical_count=20,
        )

    def test_approved_withdrawal_within_limits(self):
        req = WithdrawalRequest(
            request_id="REQ1",
            account_id="ACC1",
            asset="ETH",
            amount_crypto=10.0,
            amount_usd=25_000.0,
            destination_address="0x1234567890abcdef",
            timestamp=self.now,
        )
        dec = self.engine.evaluate_withdrawal_request(req, self.valid_whitelist, self.normal_profile)

        self.assertEqual(dec.status, WithdrawalStatus.APPROVED)
        self.assertEqual(len(dec.risk_flags), 0)

    def test_hourly_velocity_limit_exceeded(self):
        # High profile so $80,000 is not an anomaly
        high_prof = AccountHistoricalProfile("ACC1", 80_000.0, 30_000.0, 20)
        # 1. First withdrawal $80,000 APPROVED
        req1 = WithdrawalRequest("REQ1", "ACC1", "ETH", 32.0, 80_000.0, "0x1234567890abcdef", self.now)
        dec1 = self.engine.evaluate_withdrawal_request(req1, self.valid_whitelist, high_prof)
        self.assertEqual(dec1.status, WithdrawalStatus.APPROVED)

        # 2. Second withdrawal $30,000 (total $110,000 > $100,000 1h limit) -> TIMELOCK_HOLD
        req2 = WithdrawalRequest("REQ2", "ACC1", "ETH", 12.0, 30_000.0, "0x1234567890abcdef", self.now + datetime.timedelta(minutes=15))
        dec2 = self.engine.evaluate_withdrawal_request(req2, self.valid_whitelist, high_prof)

        self.assertEqual(dec2.status, WithdrawalStatus.TIMELOCK_HOLD)
        self.assertIn(RiskFlag.EXCEEDS_HOURLY_LIMIT, dec2.risk_flags)

    def test_anomaly_zscore_flagged(self):
        # Profile mean = $10k, std = $5k. Request = $30k -> Z = (30k - 10k)/5k = +4.0 >= +3.0 -> TIMELOCK_HOLD
        low_prof = AccountHistoricalProfile("ACC1", 10_000.0, 5_000.0, 20)
        req = WithdrawalRequest("REQ1", "ACC1", "BTC", 0.5, 30_000.0, "0x1234567890abcdef", self.now)
        dec = self.engine.evaluate_withdrawal_request(req, self.valid_whitelist, low_prof)

        self.assertEqual(dec.status, WithdrawalStatus.TIMELOCK_HOLD)
        self.assertIn(RiskFlag.ANOMALY_SIZE_ZSCORE, dec.risk_flags)

    def test_newly_whitelisted_address_cooling_hold(self):
        new_whitelist = AddressWhitelistRecord(
            account_id="ACC1",
            address="0x9999999999999999",
            added_timestamp=self.now - datetime.timedelta(hours=2),  # Only 2 hours old!
            is_whitelisted=True,
        )
        req = WithdrawalRequest("REQ1", "ACC1", "ETH", 2.0, 5_000.0, "0x9999999999999999", self.now)
        dec = self.engine.evaluate_withdrawal_request(req, new_whitelist, self.normal_profile)

        self.assertEqual(dec.status, WithdrawalStatus.TIMELOCK_HOLD)
        self.assertIn(RiskFlag.NEW_ADDRESS_HOLD, dec.risk_flags)

    def test_global_hot_wallet_circuit_breaker_freeze(self):
        # Hot wallet 1h limit = $1,000,000
        # Account 1 withdraws $900,000 (across 10 approved transactions)
        for i in range(9):
            req_i = WithdrawalRequest(f"R_{i}", f"ACC_{i}", "USDC", 100_000.0, 100_000.0, "0x1234567890abcdef", self.now)
            wl_i = AddressWhitelistRecord(f"ACC_{i}", "0x1234567890abcdef", self.now - datetime.timedelta(hours=48))
            prof_i = AccountHistoricalProfile(f"ACC_{i}", 100_000.0, 50_000.0, 10)
            self.engine.evaluate_withdrawal_request(req_i, wl_i, prof_i)

        # 10th withdrawal $200,000 pushes global velocity to $1.1M > $1.0M -> REJECTED_FREEZE
        req_over = WithdrawalRequest("R_OVER", "ACC_X", "USDC", 200_000.0, 200_000.0, "0x1234567890abcdef", self.now + datetime.timedelta(minutes=5))
        wl_over = AddressWhitelistRecord("ACC_X", "0x1234567890abcdef", self.now - datetime.timedelta(hours=48))
        prof_over = AccountHistoricalProfile("ACC_X", 200_000.0, 100_000.0, 10)
        dec = self.engine.evaluate_withdrawal_request(req_over, wl_over, prof_over)

        self.assertEqual(dec.status, WithdrawalStatus.REJECTED_FREEZE)
        self.assertIn(RiskFlag.HOT_WALLET_LIMIT_EXCEEDED, dec.risk_flags)

    def test_invalid_inputs_raise_error(self):
        with self.assertRaises(VelocityEngineError):
            bad_req = WithdrawalRequest("ERR", "ACC1", "ETH", -1.0, -100.0, "0x1234", self.now)
            self.engine.evaluate_withdrawal_request(bad_req, self.valid_whitelist, self.normal_profile)


if __name__ == "__main__":
    unittest.main()

