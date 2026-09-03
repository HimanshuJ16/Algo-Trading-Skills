import datetime
import math
import unittest

from withdrawal_velocity_limits_and_anomaly_detection import (
    WithdrawalStatus,
    RiskFlag,
    WithdrawalRequest,
    AddressWhitelistRecord,
    AccountHistoricalProfile,
    WithdrawalVelocityEngine,
    VelocityEngineError,
    max_attainable_in_sample_zscore,
)

UTC = datetime.timezone.utc

#: Distinguishes "argument not supplied" from an explicit ``None`` whitelist record.
_UNSET = object()


class VelocityTestBase(unittest.TestCase):
    def setUp(self):
        self.engine = WithdrawalVelocityEngine(
            account_hourly_limit_usd=100_000.0,
            account_daily_limit_usd=500_000.0,
            global_hot_wallet_hourly_limit_usd=1_000_000.0,
            anomaly_zscore_threshold=3.0,
            address_whitelist_cooling_hours=24.0,
            min_profile_observations=30,
        )
        # Fixed clock: every window and address age is asserted against a known
        # instant rather than wall-clock time.
        self.now = datetime.datetime(2026, 3, 2, 12, 0, 0, tzinfo=UTC)

        self.valid_whitelist = AddressWhitelistRecord(
            account_id="ACC1",
            address="0x1234567890abcdef",
            added_timestamp=self.now - datetime.timedelta(hours=48),
            is_whitelisted=True,
        )
        self.normal_profile = AccountHistoricalProfile(
            account_id="ACC1",
            mean_withdrawal_usd=20_000.0,
            std_dev_usd=10_000.0,
            historical_count=200,
        )

    def req(self, request_id="REQ1", account_id="ACC1", amount_usd=25_000.0,
            address="0x1234567890abcdef", offset_minutes=0.0, amount_crypto=10.0):
        return WithdrawalRequest(
            request_id=request_id,
            account_id=account_id,
            asset="ETH",
            amount_crypto=amount_crypto,
            amount_usd=amount_usd,
            destination_address=address,
            timestamp=self.now + datetime.timedelta(minutes=offset_minutes),
        )

    def evaluate(self, request, whitelist=_UNSET, profile=_UNSET, offset_minutes=0.0):
        return self.engine.evaluate_withdrawal_request(
            request,
            self.valid_whitelist if whitelist is _UNSET else whitelist,
            self.normal_profile if profile is _UNSET else profile,
            evaluation_timestamp=self.now + datetime.timedelta(minutes=offset_minutes),
        )


class TestBaselineBehaviour(VelocityTestBase):
    """Behaviour that was already correct and must stay correct."""

    def test_approved_withdrawal_within_limits(self):
        dec = self.evaluate(self.req())
        self.assertEqual(dec.status, WithdrawalStatus.APPROVED)
        self.assertEqual(dec.risk_flags, [])
        # (25_000 - 20_000) / 10_000 independently = 0.5
        self.assertAlmostEqual(dec.anomaly_zscore, 0.5)

    def test_hourly_velocity_limit_exceeded(self):
        high = AccountHistoricalProfile("ACC1", 80_000.0, 30_000.0, 200)
        first = self.evaluate(self.req("REQ1", amount_usd=80_000.0), profile=high)
        self.assertEqual(first.status, WithdrawalStatus.APPROVED)

        second = self.evaluate(self.req("REQ2", amount_usd=30_000.0, offset_minutes=15),
                               profile=high, offset_minutes=15)
        self.assertEqual(second.status, WithdrawalStatus.TIMELOCK_HOLD)
        self.assertIn(RiskFlag.EXCEEDS_HOURLY_LIMIT, second.risk_flags)

    def test_daily_limit_binds_after_hourly_window_rolls_off(self):
        high = AccountHistoricalProfile("ACC1", 90_000.0, 40_000.0, 200)
        for i in range(5):
            dec = self.evaluate(
                self.req(f"D{i}", amount_usd=95_000.0, offset_minutes=i * 90),
                profile=high, offset_minutes=i * 90)
            self.assertEqual(dec.status, WithdrawalStatus.APPROVED, f"D{i}")

        # 5 x 95k = 475k inside 24h; the hourly window holds only the last one.
        self.assertAlmostEqual(
            self.engine.get_rolling_velocity_usd("ACC1", 24.0,
                                                 self.now + datetime.timedelta(minutes=4 * 90)),
            475_000.0)
        over = self.evaluate(self.req("D_OVER", amount_usd=50_000.0, offset_minutes=5 * 90),
                             profile=high, offset_minutes=5 * 90)
        self.assertEqual(over.status, WithdrawalStatus.TIMELOCK_HOLD)
        self.assertIn(RiskFlag.EXCEEDS_DAILY_LIMIT, over.risk_flags)
        self.assertNotIn(RiskFlag.EXCEEDS_HOURLY_LIMIT, over.risk_flags)

    def test_anomaly_zscore_flagged(self):
        # mean 10k, sigma 5k, request 30k -> Z = 4.0, derived independently.
        prof = AccountHistoricalProfile("ACC1", 10_000.0, 5_000.0, 200)
        dec = self.evaluate(self.req(amount_usd=30_000.0), profile=prof)
        self.assertEqual(dec.status, WithdrawalStatus.TIMELOCK_HOLD)
        self.assertIn(RiskFlag.ANOMALY_SIZE_ZSCORE, dec.risk_flags)
        self.assertAlmostEqual(dec.anomaly_zscore, 4.0)

    def test_zscore_threshold_boundary_is_inclusive(self):
        prof = AccountHistoricalProfile("ACC1", 10_000.0, 5_000.0, 200)
        at = self.evaluate(self.req("AT", amount_usd=25_000.0), profile=prof)   # Z == 3.0
        self.assertAlmostEqual(at.anomaly_zscore, 3.0)
        self.assertIn(RiskFlag.ANOMALY_SIZE_ZSCORE, at.risk_flags)

        just_under = self.evaluate(self.req("UNDER", amount_usd=24_999.0), profile=prof)
        self.assertLess(just_under.anomaly_zscore, 3.0)
        self.assertNotIn(RiskFlag.ANOMALY_SIZE_ZSCORE, just_under.risk_flags)

    def test_large_withdrawal_below_baseline_is_not_flagged_as_anomaly(self):
        # A one-sided rule: unusually *small* is not a drain signal.
        prof = AccountHistoricalProfile("ACC1", 60_000.0, 10_000.0, 200)
        dec = self.evaluate(self.req(amount_usd=10_000.0), profile=prof)
        self.assertEqual(dec.status, WithdrawalStatus.APPROVED)
        self.assertAlmostEqual(dec.anomaly_zscore, -5.0)

    def test_newly_whitelisted_address_cooling_hold(self):
        fresh = AddressWhitelistRecord("ACC1", "0x9999999999999999",
                                       self.now - datetime.timedelta(hours=2))
        dec = self.evaluate(self.req(amount_usd=5_000.0, address="0x9999999999999999"),
                            whitelist=fresh)
        self.assertEqual(dec.status, WithdrawalStatus.TIMELOCK_HOLD)
        self.assertIn(RiskFlag.NEW_ADDRESS_HOLD, dec.risk_flags)

    def test_cooling_boundary_exactly_at_threshold_releases(self):
        exact = AddressWhitelistRecord("ACC1", "0xabc",
                                       self.now - datetime.timedelta(hours=24))
        self.assertEqual(
            self.evaluate(self.req("EXACT", address="0xabc"), whitelist=exact).status,
            WithdrawalStatus.APPROVED)

        one_second_short = AddressWhitelistRecord(
            "ACC1", "0xabc", self.now - datetime.timedelta(hours=24) + datetime.timedelta(seconds=1))
        dec = self.evaluate(self.req("SHORT", address="0xabc"), whitelist=one_second_short)
        self.assertEqual(dec.status, WithdrawalStatus.TIMELOCK_HOLD)
        self.assertIn(RiskFlag.NEW_ADDRESS_HOLD, dec.risk_flags)

    def test_unwhitelisted_and_revoked_addresses_are_held(self):
        self.assertIn(RiskFlag.NEW_ADDRESS_HOLD,
                      self.evaluate(self.req("NONE"), whitelist=None).risk_flags)
        revoked = AddressWhitelistRecord("ACC1", "0x1234567890abcdef",
                                         self.now - datetime.timedelta(days=30),
                                         is_whitelisted=False)
        self.assertIn(RiskFlag.NEW_ADDRESS_HOLD,
                      self.evaluate(self.req("REVOKED"), whitelist=revoked).risk_flags)

    def test_invalid_inputs_raise_error(self):
        with self.assertRaises(VelocityEngineError):
            self.evaluate(self.req("ERR", amount_usd=-100.0, amount_crypto=-1.0))
        with self.assertRaises(VelocityEngineError):
            self.evaluate(self.req("ZERO", amount_usd=0.0))
        with self.assertRaises(VelocityEngineError):
            self.evaluate(self.req("BLANK", address="   "))


class TestWhitelistRecordBinding(VelocityTestBase):
    """Regression: the record handed in was never checked against the request.

    Before the fix, supplying a valid record for *any* address approved a
    withdrawal to *any* destination.
    """

    def test_record_for_a_different_address_does_not_authorise(self):
        cold_wallet_record = AddressWhitelistRecord(
            "ACC1", "0xSAFE_COLD_WALLET", self.now - datetime.timedelta(days=30))
        dec = self.evaluate(self.req(address="0xATTACKER_ADDRESS"),
                            whitelist=cold_wallet_record)
        self.assertEqual(dec.status, WithdrawalStatus.TIMELOCK_HOLD)
        self.assertIn(RiskFlag.WHITELIST_RECORD_MISMATCH, dec.risk_flags)
        self.assertIn(RiskFlag.NEW_ADDRESS_HOLD, dec.risk_flags)

    def test_record_belonging_to_another_account_does_not_authorise(self):
        other = AddressWhitelistRecord(
            "OTHER_ACCOUNT", "0x1234567890abcdef", self.now - datetime.timedelta(days=30))
        dec = self.evaluate(self.req(), whitelist=other)
        self.assertEqual(dec.status, WithdrawalStatus.TIMELOCK_HOLD)
        self.assertIn(RiskFlag.WHITELIST_RECORD_MISMATCH, dec.risk_flags)

    def test_profile_for_another_account_is_refused(self):
        wrong = AccountHistoricalProfile("SOMEONE_ELSE", 20_000.0, 10_000.0, 200)
        with self.assertRaises(VelocityEngineError):
            self.evaluate(self.req(), profile=wrong)


class TestFailClosedOnUnusableRiskData(VelocityTestBase):
    """Regression: NaN and thin profiles silently disabled the anomaly check."""

    def test_nan_profile_raises_instead_of_approving(self):
        for mean, sigma in ((float("nan"), 10_000.0),
                            (20_000.0, float("nan")),
                            (float("inf"), 10_000.0)):
            with self.subTest(mean=mean, sigma=sigma):
                prof = AccountHistoricalProfile("ACC1", mean, sigma, 200)
                with self.assertRaises(VelocityEngineError):
                    self.evaluate(self.req(f"NAN_{mean}_{sigma}", amount_usd=99_000.0),
                                  profile=prof)

    def test_nan_amount_raises_instead_of_approving(self):
        with self.assertRaises(VelocityEngineError):
            self.evaluate(self.req("NANAMT", amount_usd=float("nan")))

    def test_thin_history_is_held_not_silently_approved(self):
        thin = AccountHistoricalProfile("ACC1", 20_000.0, 10_000.0, 4)
        dec = self.evaluate(self.req(amount_usd=90_000.0), profile=thin)
        self.assertEqual(dec.status, WithdrawalStatus.TIMELOCK_HOLD)
        self.assertIn(RiskFlag.INSUFFICIENT_PROFILE_HISTORY, dec.risk_flags)
        self.assertIsNone(dec.anomaly_zscore)
        self.assertTrue(dec.warnings)

    def test_zero_variance_profile_is_held_not_divided_by_zero(self):
        flat = AccountHistoricalProfile("ACC1", 20_000.0, 0.0, 200)
        dec = self.evaluate(self.req(amount_usd=90_000.0), profile=flat)
        self.assertEqual(dec.status, WithdrawalStatus.TIMELOCK_HOLD)
        self.assertIn(RiskFlag.INSUFFICIENT_PROFILE_HISTORY, dec.risk_flags)
        self.assertIsNone(dec.anomaly_zscore)

    def test_negative_std_dev_is_refused(self):
        with self.assertRaises(VelocityEngineError):
            self.evaluate(self.req(), profile=AccountHistoricalProfile("ACC1", 20_000.0, -1.0, 200))


class TestGrubbsBoundConfiguration(unittest.TestCase):
    """The Z threshold must be attainable for the configured minimum history."""

    def test_bound_matches_closed_form(self):
        for n in (2, 5, 10, 11, 30):
            self.assertAlmostEqual(max_attainable_in_sample_zscore(n), (n - 1) / math.sqrt(n))
        # Independently: n=5 -> 4/2.2360679... = 1.7888543...
        self.assertAlmostEqual(max_attainable_in_sample_zscore(5), 1.7888543819998317)
        # Z=3.0 is unreachable at n=10 and reachable at n=11.
        self.assertLess(max_attainable_in_sample_zscore(10), 3.0)
        self.assertGreater(max_attainable_in_sample_zscore(11), 3.0)

    def test_unattainable_threshold_is_refused_at_construction(self):
        with self.assertRaises(VelocityEngineError):
            WithdrawalVelocityEngine(anomaly_zscore_threshold=3.0, min_profile_observations=5)

    def test_attainable_threshold_is_accepted(self):
        engine = WithdrawalVelocityEngine(anomaly_zscore_threshold=3.0,
                                          min_profile_observations=11)
        self.assertEqual(engine.min_profile_observations, 11)

    def test_incoherent_limits_are_refused(self):
        with self.assertRaises(VelocityEngineError):
            WithdrawalVelocityEngine(account_hourly_limit_usd=600_000.0,
                                     account_daily_limit_usd=500_000.0)
        with self.assertRaises(VelocityEngineError):
            WithdrawalVelocityEngine(account_hourly_limit_usd=float("nan"))
        with self.assertRaises(VelocityEngineError):
            WithdrawalVelocityEngine(global_hot_wallet_hourly_limit_usd=0.0)


class TestLatchingCircuitBreaker(VelocityTestBase):
    """Regression: the breaker tripped once, then approved the next request."""

    def _fill_hot_wallet(self, count=9, amount=100_000.0):
        prof = AccountHistoricalProfile("X", 100_000.0, 50_000.0, 200)
        for i in range(count):
            acct = f"ACC_{i}"
            req = WithdrawalRequest(f"R_{i}", acct, "USDC", amount, amount,
                                    "0xdead", self.now)
            wl = AddressWhitelistRecord(acct, "0xdead", self.now - datetime.timedelta(hours=48))
            p = AccountHistoricalProfile(acct, prof.mean_withdrawal_usd,
                                         prof.std_dev_usd, prof.historical_count)
            self.engine.evaluate_withdrawal_request(req, wl, p, evaluation_timestamp=self.now)

    def test_breaker_trips_on_global_limit(self):
        self._fill_hot_wallet()
        over = WithdrawalRequest("R_OVER", "ACC_X", "USDC", 200_000.0, 200_000.0,
                                 "0xdead", self.now)
        dec = self.engine.evaluate_withdrawal_request(
            over,
            AddressWhitelistRecord("ACC_X", "0xdead", self.now - datetime.timedelta(hours=48)),
            AccountHistoricalProfile("ACC_X", 200_000.0, 100_000.0, 200),
            evaluation_timestamp=self.now + datetime.timedelta(minutes=5))
        self.assertEqual(dec.status, WithdrawalStatus.REJECTED_FREEZE)
        self.assertIn(RiskFlag.HOT_WALLET_LIMIT_EXCEEDED, dec.risk_flags)
        self.assertTrue(self.engine.hot_wallet_frozen)

    def test_freeze_latches_against_a_later_compliant_request(self):
        self.test_breaker_trips_on_global_limit()
        # Small, whitelisted, in-baseline, and 48h later so every rolling window
        # has fully decayed. It must still be refused.
        later = self.now + datetime.timedelta(hours=48)
        dec = self.engine.evaluate_withdrawal_request(
            WithdrawalRequest("AFTER", "ACC1", "ETH", 1.0, 1_000.0, "0x1234567890abcdef", later),
            AddressWhitelistRecord("ACC1", "0x1234567890abcdef", self.now - datetime.timedelta(days=9)),
            AccountHistoricalProfile("ACC1", 1_000.0, 100.0, 200),
            evaluation_timestamp=later)
        self.assertEqual(dec.status, WithdrawalStatus.REJECTED_FREEZE)

    def test_manual_reset_re_arms_the_gate(self):
        self.test_breaker_trips_on_global_limit()
        with self.assertRaises(VelocityEngineError):
            self.engine.reset_hot_wallet_freeze("")
        self.engine.reset_hot_wallet_freeze(authorized_by="soc-oncall@example.com")
        self.assertFalse(self.engine.hot_wallet_frozen)

        later = self.now + datetime.timedelta(hours=48)
        dec = self.engine.evaluate_withdrawal_request(
            WithdrawalRequest("POST_RESET", "ACC1", "ETH", 1.0, 1_000.0,
                              "0x1234567890abcdef", later),
            AddressWhitelistRecord("ACC1", "0x1234567890abcdef",
                                   self.now - datetime.timedelta(days=9)),
            AccountHistoricalProfile("ACC1", 1_000.0, 100.0, 200),
            evaluation_timestamp=later)
        self.assertEqual(dec.status, WithdrawalStatus.APPROVED)


class TestIdempotency(VelocityTestBase):
    """Regression: replaying a request_id rescored it and double-counted velocity."""

    def test_replay_returns_the_original_decision(self):
        prof = AccountHistoricalProfile("ACC1", 60_000.0, 15_000.0, 200)
        req = self.req("DUP", amount_usd=60_000.0, address="0x1234567890abcdef")
        first = self.evaluate(req, profile=prof)
        second = self.evaluate(req, profile=prof)
        self.assertEqual(first.status, WithdrawalStatus.APPROVED)
        self.assertIs(first, second)
        self.assertEqual(len(self.engine.withdrawal_ledger), 1)
        self.assertAlmostEqual(
            self.engine.get_rolling_velocity_usd("ACC1", 1.0, self.now), 60_000.0)

    def test_replay_of_a_held_request_stays_held(self):
        fresh = AddressWhitelistRecord("ACC1", "0xnew", self.now)
        req = self.req("HELD", address="0xnew")
        first = self.evaluate(req, whitelist=fresh)
        self.assertEqual(first.status, WithdrawalStatus.TIMELOCK_HOLD)
        # Even once the address has aged out, the decision already taken stands.
        self.assertIs(self.evaluate(req, whitelist=fresh, offset_minutes=60 * 48), first)


class TestTrustedClockAndWindowBounds(VelocityTestBase):
    """Regression: windows were measured from the caller-supplied request
    timestamp and were unbounded above."""

    def test_future_dated_request_cannot_slide_the_window(self):
        high = AccountHistoricalProfile("ACC1", 90_000.0, 40_000.0, 200)
        self.evaluate(self.req("A", amount_usd=90_000.0), profile=high)

        # Claim to be 10 days in the future to escape the 1h window. The trusted
        # clock is unchanged, so the earlier 90k still counts.
        sneaky = self.req("B", amount_usd=90_000.0, offset_minutes=60 * 24 * 10)
        dec = self.evaluate(sneaky, profile=high)
        self.assertEqual(dec.status, WithdrawalStatus.TIMELOCK_HOLD)
        self.assertIn(RiskFlag.EXCEEDS_HOURLY_LIMIT, dec.risk_flags)
        self.assertTrue(any("trusted clock" in w for w in dec.warnings))

    def test_ledger_entries_are_stamped_with_the_trusted_clock(self):
        req = self.req("FUT", amount_usd=10_000.0, offset_minutes=60 * 24 * 7)
        self.evaluate(req)
        self.assertEqual(self.engine.withdrawal_ledger[0].timestamp, self.now)
        # A future-stamped entry must not leak into a window computed later.
        self.assertAlmostEqual(
            self.engine.get_rolling_velocity_usd("ACC1", 1.0, self.now), 10_000.0)

    def test_rolling_window_is_bounded_at_both_ends(self):
        self.evaluate(self.req("NOW", amount_usd=10_000.0))
        # Query a window that ended before the entry was made.
        past = self.now - datetime.timedelta(hours=3)
        self.assertAlmostEqual(self.engine.get_rolling_velocity_usd("ACC1", 1.0, past), 0.0)

    def test_window_rolls_continuously_not_at_a_calendar_boundary(self):
        high = AccountHistoricalProfile("ACC1", 90_000.0, 40_000.0, 200)
        self.evaluate(self.req("EARLY", amount_usd=90_000.0), profile=high)
        # 61 minutes later the earlier withdrawal has left the 1h window.
        dec = self.evaluate(self.req("LATER", amount_usd=90_000.0, offset_minutes=61),
                            profile=high, offset_minutes=61)
        self.assertEqual(dec.status, WithdrawalStatus.APPROVED)

    def test_naive_and_aware_datetimes_interoperate(self):
        naive = datetime.datetime(2026, 3, 2, 12, 0, 0)  # no tzinfo
        engine = WithdrawalVelocityEngine(min_profile_observations=30)
        dec = engine.evaluate_withdrawal_request(
            WithdrawalRequest("TZ", "ACC1", "ETH", 1.0, 1_000.0, "0xabc", naive),
            AddressWhitelistRecord("ACC1", "0xabc", naive - datetime.timedelta(days=3)),
            AccountHistoricalProfile("ACC1", 1_000.0, 100.0, 200),
            evaluation_timestamp=self.now)  # aware
        self.assertEqual(dec.status, WithdrawalStatus.APPROVED)

    def test_ledger_is_pruned_to_the_longest_window(self):
        prof = AccountHistoricalProfile("ACC1", 1_000.0, 200.0, 200)
        for i in range(40):
            self.evaluate(self.req(f"P{i}", amount_usd=1_000.0, offset_minutes=i * 60),
                          profile=prof, offset_minutes=i * 60)
        # 40 hourly withdrawals, but only the last 24h of them are retained.
        self.assertLessEqual(len(self.engine.withdrawal_ledger), 25)


class TestHeldWithdrawalAccounting(VelocityTestBase):
    """A released hold moves funds, so it must consume velocity capacity."""

    def test_released_hold_counts_toward_velocity(self):
        high = AccountHistoricalProfile("ACC1", 90_000.0, 40_000.0, 200)
        fresh = AddressWhitelistRecord("ACC1", "0xnew", self.now)
        held = self.evaluate(self.req("H1", amount_usd=95_000.0, address="0xnew"),
                             whitelist=fresh, profile=high)
        self.assertEqual(held.status, WithdrawalStatus.TIMELOCK_HOLD)
        # Held funds have not moved, so nothing is consumed yet.
        self.assertAlmostEqual(self.engine.get_rolling_velocity_usd("ACC1", 1.0, self.now), 0.0)

        self.engine.release_held_withdrawal("H1", authorized_by="ops@example.com",
                                            release_timestamp=self.now)
        self.assertAlmostEqual(self.engine.get_rolling_velocity_usd("ACC1", 1.0, self.now),
                               95_000.0)
        # The released amount now constrains the next request.
        follow_up = self.evaluate(self.req("H2", amount_usd=20_000.0), profile=high)
        self.assertIn(RiskFlag.EXCEEDS_HOURLY_LIMIT, follow_up.risk_flags)

    def test_cancelled_hold_can_never_be_released(self):
        fresh = AddressWhitelistRecord("ACC1", "0xnew", self.now)
        self.evaluate(self.req("H3", amount_usd=5_000.0, address="0xnew"), whitelist=fresh)
        self.engine.cancel_held_withdrawal("H3")
        with self.assertRaises(VelocityEngineError):
            self.engine.release_held_withdrawal("H3", authorized_by="ops@example.com")

    def test_release_requires_a_known_hold_and_a_named_authoriser(self):
        with self.assertRaises(VelocityEngineError):
            self.engine.release_held_withdrawal("NOPE", authorized_by="ops@example.com")
        fresh = AddressWhitelistRecord("ACC1", "0xnew", self.now)
        self.evaluate(self.req("H4", amount_usd=5_000.0, address="0xnew"), whitelist=fresh)
        with self.assertRaises(VelocityEngineError):
            self.engine.release_held_withdrawal("H4", authorized_by="  ")


class TestDrainScenario(VelocityTestBase):
    """End-to-end: a compromised API key trying to drain an account."""

    def test_many_small_withdrawals_still_hit_the_rolling_cap(self):
        prof = AccountHistoricalProfile("ACC1", 25_000.0, 5_000.0, 200)
        approved = 0
        for i in range(12):
            dec = self.evaluate(
                self.req(f"S{i}", amount_usd=25_000.0, offset_minutes=i),
                profile=prof, offset_minutes=i)
            if dec.status is WithdrawalStatus.APPROVED:
                approved += 1
        # $100k hourly cap / $25k slices = 4 approvals, then held.
        self.assertEqual(approved, 4)


if __name__ == "__main__":
    unittest.main()
