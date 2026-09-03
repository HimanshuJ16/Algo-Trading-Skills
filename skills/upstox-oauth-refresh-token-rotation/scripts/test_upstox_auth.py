"""
Unit tests for upstox-oauth-refresh-token-rotation.

Expected expiry instants are constructed literally (an explicit IST datetime, or an
epoch value taken from Upstox's own documented example payload) rather than by
re-running the implementation's arithmetic, so a change in that arithmetic fails the
test instead of moving the target with it.

Coverage:
1. 03:30 IST expiry derivation, including Upstox's two worked examples, the exact
   boundary instant, cross-timezone inputs, and naive inputs.
2. Regression: the old `now + 86400` model overstated validity by ~16.5h.
3. Epoch-millisecond parsing, including the seconds/milliseconds mix-up.
4. Upstox error-envelope parsing and error_code propagation.
5. Single-flight re-authentication under concurrent threads (one call, not N).
6. Read-only (Analytics/extended) token refused at order-placement call sites.
7. Atomic 0600 persistence, and propagation of persistence failure.
8. The removed refresh-token API failing loudly and informatively.
"""
import datetime
import json
import os
import stat
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock

from upstox_auth import (
    IST,
    UpstoxAuthError,
    UpstoxTokenManager,
    UpstoxTokenState,
    build_authorization_code_form,
    next_session_expiry,
    parse_upstox_epoch_millis,
    raise_for_upstox_error,
    state_for_read_only_token,
    state_from_notifier_payload,
    state_from_token_response,
)


def ist(year, month, day, hour, minute=0, second=0):
    """An explicit IST instant, used to build expected values independently."""
    return datetime.datetime(year, month, day, hour, minute, second, tzinfo=IST)


class TestSessionExpiryDerivation(unittest.TestCase):
    """Upstox: validity ends at 03:30 IST the following day, whenever it was issued."""

    def test_evening_issue_expires_next_morning(self):
        # Upstox's own example: generated 8 PM Tuesday -> expires 3:30 AM Wednesday.
        issued = ist(2025, 11, 11, 20, 0)
        expected = ist(2025, 11, 12, 3, 30).timestamp()
        self.assertEqual(next_session_expiry(issued), expected)

    def test_pre_dawn_issue_expires_same_day(self):
        # Upstox's second example: generated 2:30 AM Wednesday -> expires 3:30 AM the
        # SAME Wednesday, i.e. one hour of validity, not twenty-four.
        issued = ist(2025, 11, 12, 2, 30)
        expected = ist(2025, 11, 12, 3, 30).timestamp()
        self.assertEqual(next_session_expiry(issued), expected)
        self.assertEqual(expected - issued.timestamp(), 3600.0)

    def test_exact_boundary_rolls_to_next_day(self):
        issued = ist(2025, 11, 12, 3, 30, 0)
        expected = ist(2025, 11, 13, 3, 30).timestamp()
        self.assertEqual(next_session_expiry(issued), expected)

    def test_one_second_before_boundary_expires_immediately_after(self):
        issued = ist(2025, 11, 12, 3, 29, 59)
        expected = ist(2025, 11, 12, 3, 30).timestamp()
        self.assertEqual(next_session_expiry(issued), expected)

    def test_utc_input_is_converted_not_truncated(self):
        # 18:00 UTC == 23:30 IST the same day -> next boundary is 03:30 IST tomorrow.
        # A host that ignored the offset would read "18:00" as pre-03:30-of-tomorrow
        # incorrectly, or roll the day at the wrong instant.
        issued = datetime.datetime(2025, 11, 11, 18, 0, tzinfo=datetime.timezone.utc)
        expected = ist(2025, 11, 12, 3, 30).timestamp()
        self.assertEqual(next_session_expiry(issued), expected)

    def test_utc_input_just_before_ist_boundary(self):
        # 21:59 UTC == 03:29 IST next day -> boundary is 03:30 IST that same next day.
        issued = datetime.datetime(2025, 11, 11, 21, 59, tzinfo=datetime.timezone.utc)
        expected = ist(2025, 11, 12, 3, 30).timestamp()
        self.assertEqual(next_session_expiry(issued), expected)

    def test_naive_input_treated_as_utc(self):
        naive = datetime.datetime(2025, 11, 11, 18, 0)
        aware = datetime.datetime(2025, 11, 11, 18, 0, tzinfo=datetime.timezone.utc)
        self.assertEqual(next_session_expiry(naive), next_session_expiry(aware))

    def test_fixed_ist_offset_matches_real_tzdata(self):
        """The fixed UTC+05:30 shortcut must agree with the real zone, not just look right.

        India has observed no DST since 1945, so the offset is exact -- but that is an
        assumption worth holding to the tz database wherever it is installed, rather
        than asserting in a comment. Skipped on hosts without tzdata (Windows default).
        """
        try:
            from zoneinfo import ZoneInfo
            kolkata = ZoneInfo("Asia/Kolkata")
        except Exception as e:  # ZoneInfoNotFoundError, or no zoneinfo at all
            self.skipTest(f"tzdata unavailable: {e}")

        base = datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc)
        for days in range(0, 800, 7):
            moment = base + datetime.timedelta(days=days)
            local = moment.astimezone(kolkata)
            expected = local.replace(hour=3, minute=30, second=0, microsecond=0)
            if expected <= local:
                expected += datetime.timedelta(days=1)
            with self.subTest(moment=moment):
                self.assertEqual(next_session_expiry(moment), expected.timestamp())

    def test_month_and_year_rollover(self):
        self.assertEqual(
            next_session_expiry(ist(2025, 12, 31, 23, 0)),
            ist(2026, 1, 1, 3, 30).timestamp(),
        )

    def test_regression_fixed_24h_validity_model_overstates_expiry(self):
        # The previous implementation dated tokens as `now + expires_in` with an 86400
        # default. For a token minted at 20:00 IST that claims validity until 20:00 the
        # next day -- about 16.5 hours past the real 03:30 IST death, spanning the whole
        # of the next trading session. This test fails against that behaviour.
        issued = ist(2025, 11, 11, 20, 0)
        real_expiry = next_session_expiry(issued)
        naive_expiry = issued.timestamp() + 86400.0
        self.assertLess(real_expiry, naive_expiry)
        self.assertAlmostEqual((naive_expiry - real_expiry) / 3600.0, 16.5, places=6)
        # Market open (09:15 IST the next day) must fall AFTER the real expiry, which is
        # exactly why the old model let a bot start a session unauthenticated.
        market_open = ist(2025, 11, 12, 9, 15).timestamp()
        self.assertGreater(market_open, real_expiry)
        self.assertLess(market_open, naive_expiry)


class TestEpochMillisParsing(unittest.TestCase):

    def test_documented_example_is_0330_ist(self):
        # From Upstox's Access Token Request notifier example payload. Independently:
        # 1731448800000 ms -> 2024-11-13 03:30:00 IST. This cross-checks the 03:30 rule.
        seconds = parse_upstox_epoch_millis("1731448800000")
        self.assertEqual(seconds, ist(2024, 11, 13, 3, 30).timestamp())

    def test_accepts_int_and_float_and_whitespace(self):
        self.assertEqual(parse_upstox_epoch_millis(1731448800000), 1731448800.0)
        self.assertEqual(parse_upstox_epoch_millis(1731448800000.0), 1731448800.0)
        self.assertEqual(parse_upstox_epoch_millis("  1731448800000  "), 1731448800.0)

    def test_rejects_seconds_valued_timestamp(self):
        # The mix-up this guard exists for: passing seconds where ms are expected would
        # otherwise divide by 1000 and date the token to 1970.
        with self.assertRaises(ValueError):
            parse_upstox_epoch_millis(1731448800)

    def test_rejects_non_numeric_and_bool_and_none(self):
        for bad in ("not-a-number", None, True, "", [1731448800000]):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    parse_upstox_epoch_millis(bad)


class TestErrorEnvelopeParsing(unittest.TestCase):

    def test_success_envelope_does_not_raise(self):
        raise_for_upstox_error({"status": "success", "data": {}})

    def test_error_envelope_propagates_error_code(self):
        payload = {
            "status": "error",
            "errors": [{"error_code": "UDAPI100050", "message": "Invalid token used to access API"}],
        }
        with self.assertRaises(UpstoxAuthError) as ctx:
            raise_for_upstox_error(payload)
        self.assertEqual(ctx.exception.error_code, "UDAPI100050")
        self.assertIn("Invalid token used to access API", str(ctx.exception))

    def test_deprecated_camelcase_error_code_still_read(self):
        payload = {"status": "error", "errors": [{"errorCode": "UDAPI100016", "message": "bad creds"}]}
        with self.assertRaises(UpstoxAuthError) as ctx:
            raise_for_upstox_error(payload)
        self.assertEqual(ctx.exception.error_code, "UDAPI100016")

    def test_error_envelope_with_empty_errors_array_still_raises(self):
        with self.assertRaises(UpstoxAuthError):
            raise_for_upstox_error({"status": "error", "errors": []})


class TestStateConstructors(unittest.TestCase):

    def test_token_response_derives_expiry_and_ignores_extended_token(self):
        issued = ist(2025, 11, 11, 20, 0)
        payload = {
            "access_token": "ACC_1",
            "extended_token": "EXT_1",
            "user_id": "ABC123",
        }
        state = state_from_token_response(payload, now=issued)
        self.assertEqual(state.access_token, "ACC_1")
        self.assertEqual(state.expires_at, ist(2025, 11, 12, 3, 30).timestamp())
        self.assertEqual(state.source, "authorization_code")
        self.assertFalse(state.read_only)
        # The read-only extended_token must never become the tradeable access token.
        self.assertNotEqual(state.access_token, "EXT_1")

    def test_token_response_error_envelope_raises(self):
        payload = {"status": "error", "errors": [{"error_code": "UDAPI100016", "message": "bad"}]}
        with self.assertRaises(UpstoxAuthError):
            state_from_token_response(payload)

    def test_token_response_without_access_token_raises(self):
        with self.assertRaises(UpstoxAuthError):
            state_from_token_response({"user_id": "ABC123"})

    def test_notifier_payload_uses_broker_supplied_expiry(self):
        state = state_from_notifier_payload({
            "client_id": "615b1297-d443-3b39-ba19-1927fbcdddc7",
            "user_id": "ABC123",
            "access_token": "ACC_WEBHOOK",
            "token_type": "Bearer",
            "expires_at": "1731448800000",
            "issued_at": "1731412800000",
            "message_type": "access_token",
        })
        self.assertEqual(state.access_token, "ACC_WEBHOOK")
        self.assertEqual(state.expires_at, ist(2024, 11, 13, 3, 30).timestamp())
        self.assertEqual(state.issued_at, ist(2024, 11, 12, 17, 30).timestamp())
        self.assertEqual(state.source, "token_request_webhook")

    def test_notifier_payload_wrong_message_type_rejected(self):
        with self.assertRaises(UpstoxAuthError):
            state_from_notifier_payload({
                "access_token": "X", "expires_at": "1731448800000",
                "message_type": "order_update",
            })

    def test_notifier_payload_with_seconds_timestamp_rejected(self):
        with self.assertRaises(UpstoxAuthError):
            state_from_notifier_payload({
                "access_token": "X", "expires_at": 1731448800,
                "message_type": "access_token",
            })

    def test_authorization_code_form_shape(self):
        form = build_authorization_code_form("CODE", "CID", "SECRET", "https://cb.example/x")
        self.assertEqual(form["grant_type"], "authorization_code")
        self.assertEqual(form["code"], "CODE")
        self.assertEqual(form["redirect_uri"], "https://cb.example/x")
        # No refresh_token field exists anywhere in the Upstox token exchange.
        self.assertNotIn("refresh_token", form)

    def test_authorization_code_form_rejects_missing_fields(self):
        with self.assertRaises(ValueError):
            build_authorization_code_form("", "CID", "SECRET", "https://cb.example/x")


class TestTokenStateSerialisation(unittest.TestCase):

    def test_round_trip(self):
        state = UpstoxTokenState("ACC", 1731448800.0, 1731412800.0, "authorization_code", False)
        self.assertEqual(UpstoxTokenState.from_dict(state.to_dict()), state)

    def test_from_dict_refuses_missing_expires_at(self):
        # Defaulting here would reintroduce the "assume it's fine for a day" bug.
        with self.assertRaises(ValueError):
            UpstoxTokenState.from_dict({"access_token": "ACC"})

    def test_from_dict_refuses_empty_access_token(self):
        with self.assertRaises(ValueError):
            UpstoxTokenState.from_dict({"access_token": "", "expires_at": 1731448800.0})


class TestTokenManager(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.token_file = os.path.join(self.temp_dir.name, "upstox_tokens.json")
        self.mgr = UpstoxTokenManager(token_file_path=self.token_file, buffer_seconds=900.0)

    def tearDown(self):
        self.temp_dir.cleanup()

    @staticmethod
    def _live_state(token="ACC_LIVE", ttl=10_000.0, read_only=False):
        return UpstoxTokenState(token, time.time() + ttl, time.time(), "authorization_code", read_only)

    def test_expiry_detection_respects_buffer(self):
        self.mgr.state = self._live_state(ttl=100.0)     # inside the 900s buffer
        self.assertTrue(self.mgr.is_token_expiring())
        self.mgr.state = self._live_state(ttl=10_000.0)  # well clear of it
        self.assertFalse(self.mgr.is_token_expiring())

    def test_no_state_is_expiring(self):
        self.assertIsNone(self.mgr.state)
        self.assertTrue(self.mgr.is_token_expiring())

    def test_valid_token_returned_without_reauth(self):
        self.mgr.state = self._live_state()
        reauth = Mock()
        self.assertEqual(self.mgr.get_valid_access_token(reauth), "ACC_LIVE")
        reauth.assert_not_called()

    def test_expired_token_triggers_reauth_and_persists(self):
        self.mgr.state = UpstoxTokenState("ACC_DEAD", time.time() - 100)
        new = self._live_state("ACC_FRESH")
        token = self.mgr.get_valid_access_token(lambda: new)

        self.assertEqual(token, "ACC_FRESH")
        self.assertEqual(self.mgr.state.access_token, "ACC_FRESH")
        with open(self.token_file, encoding="utf-8") as f:
            on_disk = json.load(f)
        self.assertEqual(on_disk["access_token"], "ACC_FRESH")
        # The persisted record must carry no refresh_token: Upstox issues none, and a
        # field named that would invite a caller to try an exchange that does not exist.
        self.assertNotIn("refresh_token", on_disk)

    def test_persisted_token_is_reloaded_by_a_new_manager(self):
        self.mgr.state = UpstoxTokenState("ACC_DEAD", time.time() - 100)
        self.mgr.get_valid_access_token(lambda: self._live_state("ACC_FRESH"))
        reloaded = UpstoxTokenManager(token_file_path=self.token_file)
        self.assertIsNotNone(reloaded.state)
        self.assertEqual(reloaded.state.access_token, "ACC_FRESH")
        self.assertFalse(reloaded.is_token_expiring())

    def test_corrupt_token_file_is_treated_as_no_token(self):
        with open(self.token_file, "w", encoding="utf-8") as f:
            f.write("{ this is not json")
        mgr = UpstoxTokenManager(token_file_path=self.token_file)
        self.assertIsNone(mgr.state)
        self.assertTrue(mgr.is_token_expiring())

    def test_single_flight_reauth_across_concurrent_threads(self):
        """Ten workers noticing expiry must produce ONE approval prompt, not ten.

        This is the concurrency test the previous revision's docstring advertised but
        never actually contained.
        """
        self.mgr.state = UpstoxTokenState("ACC_DEAD", time.time() - 100)
        calls = []
        calls_lock = threading.Lock()
        start = threading.Barrier(10)

        def slow_reauth():
            with calls_lock:
                calls.append(1)
            time.sleep(0.05)  # widen the window a second caller could slip through
            return self._live_state("ACC_SHARED")

        results = []
        results_lock = threading.Lock()

        def worker():
            start.wait(timeout=5)
            token = self.mgr.get_valid_access_token(slow_reauth)
            with results_lock:
                results.append(token)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        self.assertEqual(len(calls), 1, "re-authentication must be single-flighted")
        self.assertEqual(results, ["ACC_SHARED"] * 10)

    def test_read_only_token_refused_for_write_operations(self):
        self.mgr.state = self._live_state("ACC_ANALYTICS", read_only=True)
        # Read paths are fine.
        self.assertEqual(self.mgr.get_valid_access_token(Mock()), "ACC_ANALYTICS")
        # Order placement is not.
        with self.assertRaises(UpstoxAuthError) as ctx:
            self.mgr.get_valid_access_token(Mock(), require_write=True)
        self.assertEqual(ctx.exception.error_code, "UDAPI100067")

    def test_read_only_flag_survives_persistence(self):
        self.mgr.state = UpstoxTokenState("ACC_DEAD", time.time() - 100)
        analytics = state_for_read_only_token("ACC_ANALYTICS", time.time() + 365 * 86400)
        self.mgr.get_valid_access_token(lambda: analytics)
        reloaded = UpstoxTokenManager(token_file_path=self.token_file)
        self.assertTrue(reloaded.state.read_only)
        with self.assertRaises(UpstoxAuthError):
            reloaded.get_valid_access_token(Mock(), require_write=True)

    def test_reauth_returning_expired_token_raises_instead_of_looping(self):
        self.mgr.state = None
        dead = UpstoxTokenState("ACC_DEAD", time.time() - 1)
        with self.assertRaises(UpstoxAuthError) as ctx:
            self.mgr.get_valid_access_token(lambda: dead)
        self.assertIn("already-expired", str(ctx.exception))
        self.assertFalse(os.path.exists(self.token_file))

    def test_reauth_returning_wrong_type_raises(self):
        with self.assertRaises(UpstoxAuthError):
            self.mgr.get_valid_access_token(lambda: {"access_token": "ACC"})

    def test_persistence_failure_propagates_and_state_not_published(self):
        # A token held only in RAM survives until restart, then leaves an unattended bot
        # unable to start. The failure must surface, not be logged and swallowed.
        mgr = UpstoxTokenManager(
            token_file_path=os.path.join(self.temp_dir.name, "no_such_dir", "t.json")
        )
        with self.assertRaises(OSError):
            mgr.get_valid_access_token(lambda: self._live_state("ACC_FRESH"))
        self.assertIsNone(mgr.state)

    @unittest.skipIf(os.name == "nt", "POSIX permission bits are not meaningful on Windows")
    def test_token_file_is_not_world_readable(self):
        self.mgr.state = UpstoxTokenState("ACC_DEAD", time.time() - 100)
        self.mgr.get_valid_access_token(lambda: self._live_state("ACC_FRESH"))
        mode = stat.S_IMODE(os.stat(self.token_file).st_mode)
        self.assertEqual(mode, 0o600, f"token file mode is {oct(mode)}, expected 0o600")

    def test_no_temp_file_left_behind(self):
        self.mgr.state = UpstoxTokenState("ACC_DEAD", time.time() - 100)
        self.mgr.get_valid_access_token(lambda: self._live_state("ACC_FRESH"))
        self.assertFalse(os.path.exists(f"{self.token_file}.tmp"))

    def test_out_of_range_buffer_rejected(self):
        # A buffer >= one full session marks every token as expiring forever, which for
        # the approval flow means an unbounded stream of prompts to the user's phone.
        for bad in (-1.0, 86400.0, 172800.0):
            with self.subTest(buffer_seconds=bad):
                with self.assertRaises(ValueError):
                    UpstoxTokenManager(token_file_path=self.token_file, buffer_seconds=bad)

    def test_largest_valid_buffer_accepted(self):
        mgr = UpstoxTokenManager(token_file_path=self.token_file, buffer_seconds=86399.0)
        self.assertEqual(mgr.buffer_seconds, 86399.0)

    def test_removed_refresh_token_api_fails_loudly(self):
        with self.assertRaises(UpstoxAuthError) as ctx:
            self.mgr.rotate_refresh_token("CLIENT_ID", "SECRET", lambda url, body: {})
        message = str(ctx.exception)
        self.assertIn("does not support refresh tokens", message)
        self.assertIn("get_valid_access_token", message)


if __name__ == "__main__":
    unittest.main()
