"""
Unit tests for questrade-api-rate-limit-and-account-types.

Every rate-limit test drives an injected fake clock, so the suite is
deterministic and runs in milliseconds rather than sleeping through real
per-second and per-hour windows.
"""
import logging
import threading
import unittest

from questrade_client import (
    LIVE_LOGIN_HOST,
    PRACTICE_LOGIN_HOST,
    AccountRestrictionError,
    AccountStatus,
    AccountType,
    Eligibility,
    MultiWindowBudget,
    QuestradeAPIError,
    QuestradeAuthError,
    QuestradeClient,
    QuestradeRateLimitError,
    QuestradeRateLimiter,
    RateLimitCategory,
    RateWindow,
    SlidingWindowCounter,
    TokenBucketRateLimiter,
    categorize_endpoint,
    normalize_api_server,
    normalize_endpoint,
    parse_rate_limit_reset,
)


class FakeClock:
    """Manually advanced monotonic clock."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = float(start)

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds

    def sleep(self, seconds: float) -> None:
        self.advance(seconds)


TOKEN_RESPONSE = {
    "access_token": "access-abc",
    "refresh_token": "rotated-def",
    "api_server": "https://api01.iq.questrade.com/",
    "token_type": "Bearer",
    "expires_in": 1800,
}

ACCOUNTS_RESPONSE = {
    "accounts": [
        {
            "type": "Margin",
            "number": "26598145",
            "status": "Active",
            "isPrimary": True,
            "isBilling": True,
            "clientAccountType": "Individual",
        },
        {
            "type": "TFSA",
            "number": "26598146",
            "status": "Active",
            "isPrimary": False,
            "isBilling": False,
            "clientAccountType": "Individual",
        },
        {
            "type": "LIRA",
            "number": "26598147",
            "status": "Active",
            "isPrimary": False,
            "isBilling": False,
            "clientAccountType": "Individual",
        },
        {
            "type": "Cash",
            "number": "26598148",
            "status": "Active",
            "isPrimary": False,
            "isBilling": False,
            "clientAccountType": "Individual",
        },
    ],
    "userId": 3000124,
}


class RecordingTransport:
    """Mock transport that records every dispatched request."""

    def __init__(self, token_response=None, accounts_response=None, headers=None):
        self.token_response = dict(token_response or TOKEN_RESPONSE)
        self.accounts_response = accounts_response or ACCOUNTS_RESPONSE
        self.headers = headers
        self.calls = []

    def __call__(self, method, url, request_headers, body):
        self.calls.append((method, url, request_headers, body))
        if "/oauth2/token" in url:
            return 200, self.token_response
        if url.endswith("v1/accounts"):
            if self.headers is None:
                return 200, self.accounts_response
            return 200, self.accounts_response, self.headers
        return 404, {"code": 1002, "message": "Not found"}


def build_client(transport=None, clock=None, **kwargs):
    clock = clock or FakeClock()
    transport = transport or RecordingTransport()
    kwargs.setdefault("monotonic", clock)
    kwargs.setdefault("wall_clock", clock)
    kwargs.setdefault("sleep_fn", clock.sleep)
    kwargs.setdefault(
        "rate_limiter", QuestradeRateLimiter(monotonic=clock)
    )
    return QuestradeClient(transport, **kwargs), transport, clock


# --------------------------------------------------------------------------
# api_server normalisation  (regression: f"{api_server}v1/accounts")
# --------------------------------------------------------------------------
class TestApiServerNormalization(unittest.TestCase):
    """Questrade documents api_server in three shapes; all must join correctly."""

    def test_all_three_documented_shapes_produce_the_same_base(self):
        for raw in (
            "https://api01.iq.questrade.com",
            "https://api01.iq.questrade.com/",
            "https://api01.iq.questrade.com/v1",
        ):
            with self.subTest(raw=raw):
                self.assertEqual(
                    normalize_api_server(raw), "https://api01.iq.questrade.com/"
                )

    def test_request_url_is_well_formed_for_every_documented_shape(self):
        """Naive concatenation yielded '...comv1/accounts' and '.../v1v1/accounts'."""
        for raw in (
            "https://api01.iq.questrade.com",
            "https://api01.iq.questrade.com/",
            "https://api01.iq.questrade.com/v1",
        ):
            with self.subTest(raw=raw):
                response = dict(TOKEN_RESPONSE, api_server=raw)
                client, transport, _ = build_client(
                    RecordingTransport(token_response=response)
                )
                client.refresh_access_token("seed")
                client.fetch_accounts()
                url = transport.calls[-1][1]
                self.assertEqual(url, "https://api01.iq.questrade.com/v1/accounts")

    def test_non_https_api_server_is_rejected(self):
        with self.assertRaises(QuestradeAuthError):
            normalize_api_server("http://api01.iq.questrade.com/")

    def test_blank_api_server_is_rejected(self):
        for raw in ("", "   ", None, 42):
            with self.subTest(raw=raw):
                with self.assertRaises(QuestradeAuthError):
                    normalize_api_server(raw)


# --------------------------------------------------------------------------
# OAuth2 rotation
# --------------------------------------------------------------------------
class TestOAuthRotation(unittest.TestCase):
    def test_rotated_token_is_returned_and_stored(self):
        client, _, _ = build_client()
        token = client.refresh_access_token("seed-token")
        self.assertEqual(token.access_token, "access-abc")
        self.assertEqual(token.refresh_token, "rotated-def")
        self.assertEqual(token.api_server, "https://api01.iq.questrade.com/")
        self.assertEqual(token.expires_in, 1800.0)

    def test_refresh_token_is_percent_encoded(self):
        """Questrade tokens contain '+' and '/'; a raw '+' decodes to a space."""
        client, transport, _ = build_client()
        client.refresh_access_token("p4VTj45GhS8lY7aFoKDNZxB8yQHMOr+f/x")
        url = transport.calls[0][1]
        self.assertIn("refresh_token=p4VTj45GhS8lY7aFoKDNZxB8yQHMOr%2Bf%2Fx", url)
        self.assertNotIn("+f/x", url)

    def test_post_mode_keeps_the_secret_out_of_the_url(self):
        secret = "p4VTj45GhS8lY7aFoKDNZxB8yQHMOr+f"
        client, transport, _ = build_client(token_request_method="POST")
        client.refresh_access_token(secret)
        method, url, headers, body = transport.calls[0]
        self.assertEqual(method, "POST")
        self.assertNotIn("refresh_token", url)
        self.assertEqual(headers["Content-Type"], "application/x-www-form-urlencoded")
        # Handed over un-encoded so the transport encodes exactly once.
        self.assertEqual(body["refresh_token"], secret)
        self.assertEqual(body["grant_type"], "refresh_token")

    def test_practice_flag_selects_the_practice_login_host(self):
        client, transport, _ = build_client(practice=True)
        client.refresh_access_token("seed")
        self.assertTrue(transport.calls[0][1].startswith(PRACTICE_LOGIN_HOST))

    def test_live_is_the_default_login_host(self):
        client, transport, _ = build_client()
        client.refresh_access_token("seed")
        self.assertTrue(transport.calls[0][1].startswith(LIVE_LOGIN_HOST))

    def test_missing_expires_in_is_fatal_rather_than_defaulted(self):
        """Questrade returns 300s and 1800s in different samples; never guess."""
        response = {k: v for k, v in TOKEN_RESPONSE.items() if k != "expires_in"}
        client, _, _ = build_client(RecordingTransport(token_response=response))
        with self.assertRaises(QuestradeAuthError) as ctx:
            client.refresh_access_token("seed")
        self.assertIn("expires_in", str(ctx.exception))

    def test_missing_rotated_refresh_token_is_fatal(self):
        response = {k: v for k, v in TOKEN_RESPONSE.items() if k != "refresh_token"}
        client, _, _ = build_client(RecordingTransport(token_response=response))
        with self.assertRaises(QuestradeAuthError):
            client.refresh_access_token("seed")

    def test_blank_refresh_token_is_rejected_before_any_request(self):
        client, transport, _ = build_client()
        for bad in ("", "   ", None):
            with self.subTest(bad=bad):
                with self.assertRaises(QuestradeAuthError):
                    client.refresh_access_token(bad)
        self.assertEqual(transport.calls, [])

    def test_error_message_never_echoes_the_submitted_token(self):
        secret = "super-secret-refresh-token"

        def failing(method, url, headers, body):
            return 400, {"detail": secret}

        client, _, _ = build_client(failing)
        with self.assertRaises(QuestradeAuthError) as ctx:
            client.refresh_access_token(secret)
        self.assertNotIn(secret, str(ctx.exception))

    def test_token_repr_redacts_credentials(self):
        client, _, _ = build_client()
        token = client.refresh_access_token("seed")
        text = repr(token)
        self.assertNotIn("access-abc", text)
        self.assertNotIn("rotated-def", text)
        self.assertIn("<redacted>", text)


class TestTokenPersistence(unittest.TestCase):
    def test_rotated_token_is_persisted_before_the_call_returns(self):
        persisted = []
        client, _, _ = build_client(token_persist_fn=persisted.append)
        token = client.refresh_access_token("seed")
        self.assertEqual(len(persisted), 1)
        self.assertIs(persisted[0], token)

    def test_persistence_failure_is_fatal(self):
        """The submitted token is already spent; silently continuing strands the bot."""

        def broken(_token):
            raise OSError("disk full")

        client, _, _ = build_client(token_persist_fn=broken)
        with self.assertRaises(QuestradeAuthError) as ctx:
            client.refresh_access_token("seed")
        self.assertIn("could not be persisted", str(ctx.exception))
        self.assertIsNone(client.auth_token)


class TestTokenExpiry(unittest.TestCase):
    def test_expiry_uses_the_monotonic_deadline(self):
        client, _, clock = build_client(expiry_skew_sec=60.0)
        client.refresh_access_token("seed")
        self.assertFalse(client.is_token_expired())
        clock.advance(1700)  # 100s left, above the 60s skew
        self.assertFalse(client.is_token_expired())
        clock.advance(50)  # 50s left, inside the skew
        self.assertTrue(client.is_token_expired())

    def test_expired_token_blocks_further_calls(self):
        client, _, clock = build_client()
        client.refresh_access_token("seed")
        clock.advance(1800)
        with self.assertRaises(QuestradeAuthError):
            client.fetch_accounts()

    def test_ensure_authenticated_refreshes_only_when_near_expiry(self):
        client, transport, clock = build_client()
        client.refresh_access_token("seed")
        calls_after_login = len(transport.calls)

        client.ensure_authenticated()
        self.assertEqual(len(transport.calls), calls_after_login)

        clock.advance(1790)
        client.ensure_authenticated()
        self.assertEqual(len(transport.calls), calls_after_login + 1)

    def test_unauthenticated_client_raises(self):
        client, _, _ = build_client()
        with self.assertRaises(QuestradeAuthError):
            client.fetch_accounts()
        with self.assertRaises(QuestradeAuthError):
            client.ensure_authenticated()


# --------------------------------------------------------------------------
# Account parsing
# --------------------------------------------------------------------------
class TestAccountParsing(unittest.TestCase):
    def setUp(self):
        self.client, self.transport, self.clock = build_client()
        self.client.refresh_access_token("seed")

    def test_documented_types_are_parsed(self):
        accounts = self.client.fetch_accounts()
        self.assertEqual(
            [a.account_type for a in accounts],
            [AccountType.MARGIN, AccountType.TFSA, AccountType.LIRA, AccountType.CASH],
        )
        self.assertEqual(accounts[0].client_account_type, "Individual")
        self.assertTrue(accounts[0].is_billing)

    def test_every_documented_account_type_round_trips(self):
        documented = [
            "Cash", "Margin", "TFSA", "RRSP", "FHSA", "SRRSP", "LRRSP", "LIRA",
            "LIF", "RIF", "SRIF", "LRIF", "RRIF", "PRIF", "RESP", "FRESP",
        ]
        for value in documented:
            with self.subTest(value=value):
                self.assertEqual(AccountType.from_api(value).value, value)

    def test_unknown_type_is_not_coerced_to_margin(self):
        """The old fallback mapped every unrecognised type to MARGIN."""
        with self.assertLogs("questrade_client", level=logging.WARNING):
            parsed = AccountType.from_api("RDSP")
        self.assertIs(parsed, AccountType.UNKNOWN)
        self.assertIsNot(parsed, AccountType.MARGIN)

    def test_individual_is_a_client_account_type_not_an_account_type(self):
        with self.assertLogs("questrade_client", level=logging.WARNING):
            self.assertIs(AccountType.from_api("Individual"), AccountType.UNKNOWN)

    def test_strict_mode_rejects_unknown_types(self):
        payload = {"accounts": [{"type": "RDSP", "number": "1", "status": "Active"}]}
        client, _, _ = build_client(
            RecordingTransport(accounts_response=payload), strict_account_types=True
        )
        client.refresh_access_token("seed")
        with self.assertRaises(QuestradeAPIError):
            client.fetch_accounts()

    def test_account_record_without_number_is_rejected(self):
        payload = {"accounts": [{"type": "Margin", "status": "Active"}]}
        client, _, _ = build_client(RecordingTransport(accounts_response=payload))
        client.refresh_access_token("seed")
        with self.assertRaises(QuestradeAPIError):
            client.fetch_accounts()

    def test_registry_is_rebuilt_so_closed_accounts_do_not_linger(self):
        self.client.fetch_accounts()
        self.assertIn("26598147", self.client.accounts)
        self.transport.accounts_response = {
            "accounts": [ACCOUNTS_RESPONSE["accounts"][0]]
        }
        self.client.fetch_accounts()
        self.assertNotIn("26598147", self.client.accounts)

    def test_statuses_are_parsed_and_unknown_status_is_flagged(self):
        self.assertIs(AccountStatus.from_api("Liquidate Only"), AccountStatus.LIQUIDATE_ONLY)
        self.assertIs(
            AccountStatus.from_api("Suspended (View Only)"),
            AccountStatus.SUSPENDED_VIEW_ONLY,
        )
        with self.assertLogs("questrade_client", level=logging.WARNING):
            self.assertIs(AccountStatus.from_api("Frozen"), AccountStatus.UNKNOWN)

    def test_unknown_account_is_not_silently_accepted(self):
        self.client.fetch_accounts()
        with self.assertRaises(QuestradeAPIError):
            self.client.get_account("00000000")


# --------------------------------------------------------------------------
# Pre-trade eligibility
# --------------------------------------------------------------------------
def eligibility_client(accounts):
    client, _, _ = build_client(
        RecordingTransport(accounts_response={"accounts": accounts})
    )
    client.refresh_access_token("seed")
    client.fetch_accounts()
    return client


def account_row(acc_type, number="10000001", status="Active"):
    return {"type": acc_type, "number": number, "status": status, "isPrimary": False}


class TestOrderEligibility(unittest.TestCase):
    def test_margin_account_may_short(self):
        client = eligibility_client([account_row("Margin")])
        outcome = client.check_order_eligibility("10000001", "Short")
        self.assertIs(outcome.eligibility, Eligibility.ALLOWED)
        self.assertTrue(client.validate_order_for_account("10000001", "Short"))

    def test_every_registered_plan_denies_short_not_just_tfsa_rrsp_fhsa(self):
        """Regression: the old set covered only TFSA/RRSP/FHSA."""
        registered = [
            "TFSA", "RRSP", "FHSA", "SRRSP", "LRRSP", "LIRA", "LIF", "RIF",
            "SRIF", "LRIF", "RRIF", "PRIF", "RESP", "FRESP",
        ]
        for acc_type in registered:
            with self.subTest(acc_type=acc_type):
                client = eligibility_client([account_row(acc_type)])
                with self.assertLogs("questrade_client", level=logging.WARNING):
                    outcome = client.check_order_eligibility("10000001", "Short")
                self.assertIs(outcome.eligibility, Eligibility.DENIED)
                self.assertIn("registered plan", outcome.reason)

    def test_cash_account_cannot_short(self):
        client = eligibility_client([account_row("Cash")])
        with self.assertLogs("questrade_client", level=logging.WARNING):
            outcome = client.check_order_eligibility("10000001", "Short")
        self.assertIs(outcome.eligibility, Eligibility.DENIED)
        self.assertIn("no margin facility", outcome.reason)

    def test_unrecognised_account_type_denies_short(self):
        """The critical regression: unknown type -> MARGIN -> short approved."""
        with self.assertLogs("questrade_client", level=logging.WARNING):
            client = eligibility_client([account_row("RDSP")])
            outcome = client.check_order_eligibility("10000001", "Short")
        self.assertIs(outcome.eligibility, Eligibility.DENIED)
        self.assertIs(outcome.account_type, AccountType.UNKNOWN)

    def test_cover_also_requires_margin(self):
        client = eligibility_client([account_row("RRSP")])
        with self.assertLogs("questrade_client", level=logging.WARNING):
            self.assertFalse(client.validate_order_for_account("10000001", "Cov"))

    def test_buy_and_sell_are_permitted_in_registered_accounts(self):
        client = eligibility_client([account_row("TFSA")])
        for side in ("Buy", "Sell", "BTO", "STC", "BTC"):
            with self.subTest(side=side):
                self.assertTrue(client.validate_order_for_account("10000001", side))

    def test_option_writing_in_a_registered_plan_escalates_rather_than_guessing(self):
        client = eligibility_client([account_row("TFSA")])
        with self.assertLogs("questrade_client", level=logging.WARNING):
            outcome = client.check_order_eligibility("10000001", "STO")
        self.assertIs(outcome.eligibility, Eligibility.REVIEW_REQUIRED)
        # REVIEW_REQUIRED must fail closed through the boolean API.
        self.assertFalse(outcome.allowed)

    def test_option_writing_is_allowed_in_a_margin_account(self):
        client = eligibility_client([account_row("Margin")])
        self.assertTrue(client.validate_order_for_account("10000001", "STO"))

    def test_undocumented_order_side_raises(self):
        """'SellShort' is not a Questrade value; the documented side is 'Short'."""
        client = eligibility_client([account_row("Margin")])
        with self.assertRaises(ValueError):
            client.check_order_eligibility("10000001", "SellShort")

    def test_assert_order_allowed_raises_on_denial(self):
        client = eligibility_client([account_row("TFSA")])
        with self.assertLogs("questrade_client", level=logging.WARNING):
            with self.assertRaises(AccountRestrictionError):
                client.assert_order_allowed("10000001", "Short")
        self.assertIsNotNone(client.assert_order_allowed("10000001", "Buy"))


class TestAccountStatusGating(unittest.TestCase):
    def test_closed_and_suspended_accounts_reject_every_side(self):
        for status in ("Closed", "Suspended (Closed)", "Suspended (View Only)"):
            for side in ("Buy", "Sell"):
                with self.subTest(status=status, side=side):
                    client = eligibility_client([account_row("Margin", status=status)])
                    with self.assertLogs("questrade_client", level=logging.WARNING):
                        outcome = client.check_order_eligibility("10000001", side)
                    self.assertIs(outcome.eligibility, Eligibility.DENIED)

    def test_liquidate_only_permits_reducing_sides_only(self):
        client = eligibility_client([account_row("Margin", status="Liquidate Only")])
        self.assertTrue(client.validate_order_for_account("10000001", "Sell"))
        with self.assertLogs("questrade_client", level=logging.WARNING):
            self.assertFalse(client.validate_order_for_account("10000001", "Buy"))

    def test_unknown_status_denies(self):
        with self.assertLogs("questrade_client", level=logging.WARNING):
            client = eligibility_client([account_row("Margin", status="Frozen")])
            self.assertFalse(client.validate_order_for_account("10000001", "Buy"))


# --------------------------------------------------------------------------
# Rate limiting
# --------------------------------------------------------------------------
class TestTokenBucket(unittest.TestCase):
    def test_capacity_then_refill(self):
        clock = FakeClock()
        bucket = TokenBucketRateLimiter(capacity=5, fill_rate=5.0, monotonic=clock)
        self.assertEqual(sum(1 for _ in range(5) if bucket.acquire()), 5)
        self.assertFalse(bucket.acquire())
        clock.advance(0.2)  # exactly one token at 5/sec
        self.assertTrue(bucket.acquire())
        self.assertFalse(bucket.acquire())

    def test_bucket_never_exceeds_capacity_after_a_long_idle(self):
        clock = FakeClock()
        bucket = TokenBucketRateLimiter(capacity=3, fill_rate=3.0, monotonic=clock)
        clock.advance(10_000)
        self.assertEqual(sum(1 for _ in range(10) if bucket.acquire()), 3)

    def test_invalid_configuration_is_rejected(self):
        for capacity, fill_rate in ((0, 1.0), (-1, 1.0), (5, 0.0), (5, -2.0)):
            with self.subTest(capacity=capacity, fill_rate=fill_rate):
                with self.assertRaises(ValueError):
                    TokenBucketRateLimiter(capacity=capacity, fill_rate=fill_rate)

    def test_backwards_clock_step_does_not_grant_a_burst(self):
        clock = FakeClock()
        bucket = TokenBucketRateLimiter(capacity=4, fill_rate=4.0, monotonic=clock)
        for _ in range(4):
            bucket.acquire()
        clock.advance(-3600)  # simulate a wall-clock correction
        self.assertFalse(bucket.acquire())

    def test_resync_only_lowers_the_local_estimate(self):
        clock = FakeClock()
        bucket = TokenBucketRateLimiter(capacity=10, fill_rate=10.0, monotonic=clock)
        bucket.resync(2)
        self.assertAlmostEqual(bucket.tokens, 2.0)
        bucket.resync(9)  # a higher server figure must not restore capacity
        self.assertAlmostEqual(bucket.tokens, 2.0)

    def test_thread_safety_grants_exactly_capacity(self):
        bucket = TokenBucketRateLimiter(capacity=8, fill_rate=1e-9)
        granted = []
        lock = threading.Lock()

        def worker():
            if bucket.acquire():
                with lock:
                    granted.append(1)

        threads = [threading.Thread(target=worker) for _ in range(64)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(granted), 8)


class TestSlidingWindowCounter(unittest.TestCase):
    def test_window_count_is_exact_not_a_refill_rate(self):
        """
        Regression for the primitive choice.

        A token bucket of capacity N refilling at N/period starts full and
        refills, so it grants ~2N over the first period. Questrade's limits are
        window counts, so N must mean N.
        """
        clock = FakeClock()
        window = SlidingWindowCounter(100, 60.0, monotonic=clock)
        granted = 0
        for _ in range(600):
            if window.acquire():
                granted += 1
            clock.advance(0.1)  # 60 s of attempts at 10/s
        self.assertEqual(granted, 100)

        bucket_clock = FakeClock()
        bucket = TokenBucketRateLimiter(
            capacity=100, fill_rate=100 / 60.0, monotonic=bucket_clock
        )
        bucket_granted = 0
        for _ in range(600):
            if bucket.acquire():
                bucket_granted += 1
            bucket_clock.advance(0.1)
        self.assertGreater(bucket_granted, 150, "bucket over-grants; hence the counter")

    def test_grants_resume_only_as_the_window_slides(self):
        clock = FakeClock()
        window = SlidingWindowCounter(3, 10.0, monotonic=clock)
        for _ in range(3):
            self.assertTrue(window.acquire())
        self.assertFalse(window.acquire())
        self.assertAlmostEqual(window.time_until_available(), 10.0)
        clock.advance(9.999)
        self.assertFalse(window.acquire())
        clock.advance(0.002)
        self.assertTrue(window.acquire())

    def test_used_reflects_expiry(self):
        clock = FakeClock()
        window = SlidingWindowCounter(5, 1.0, monotonic=clock)
        for _ in range(5):
            window.acquire()
        self.assertEqual(window.used, 5)
        clock.advance(1.5)
        self.assertEqual(window.used, 0)

    def test_memory_is_bounded_by_capacity(self):
        clock = FakeClock()
        window = SlidingWindowCounter(4, 3600.0, monotonic=clock)
        for _ in range(10_000):
            window.acquire()
        self.assertEqual(window.used, 4)

    def test_resync_only_reduces_headroom(self):
        clock = FakeClock()
        window = SlidingWindowCounter(10, 60.0, monotonic=clock)
        window.resync(3)
        self.assertEqual(sum(1 for _ in range(10) if window.acquire()), 3)
        window.resync(9)  # a higher server figure must not restore headroom
        self.assertFalse(window.acquire())

    def test_invalid_configuration_is_rejected(self):
        with self.assertRaises(ValueError):
            SlidingWindowCounter(0, 1.0)
        with self.assertRaises(ValueError):
            SlidingWindowCounter(5, 0.0)

    def test_thread_safety_grants_exactly_capacity(self):
        window = SlidingWindowCounter(8, 3600.0)
        granted = []
        lock = threading.Lock()

        def worker():
            if window.acquire():
                with lock:
                    granted.append(1)

        threads = [threading.Thread(target=worker) for _ in range(64)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(granted), 8)


class TestMultiWindowBudget(unittest.TestCase):
    def test_the_slower_window_binds(self):
        """30/sec alone would pass 108,000/hour; the hourly window must bind."""
        clock = FakeClock()
        budget = MultiWindowBudget(
            [RateWindow(30, 1.0), RateWindow(50, 3600.0)], monotonic=clock
        )
        granted = 0
        for _ in range(200):
            if budget.acquire():
                granted += 1
            clock.advance(1.0)
        self.assertEqual(granted, 50)

    def test_consumption_is_all_or_nothing(self):
        """A refused attempt must not charge the faster window."""
        clock = FakeClock()
        budget = MultiWindowBudget(
            [RateWindow(100, 1.0), RateWindow(5, 60.0)], monotonic=clock
        )
        for _ in range(5):
            self.assertTrue(budget.acquire())
        for _ in range(20):
            self.assertFalse(budget.acquire())
        # Sequential consumption would have charged the per-second window 25
        # times; all-or-nothing charges it exactly 5.
        self.assertEqual(budget.usage(), (5, 5))
        clock.advance(60.0)
        self.assertTrue(budget.acquire())

    def test_binding_window_is_reported(self):
        clock = FakeClock()
        budget = MultiWindowBudget(
            [RateWindow(30, 1.0), RateWindow(4, 3600.0)], monotonic=clock
        )
        for _ in range(4):
            budget.acquire()
        window = budget.binding_window()
        self.assertIsNotNone(window)
        self.assertEqual(window.period_sec, 3600.0)

    def test_empty_window_list_is_rejected(self):
        with self.assertRaises(ValueError):
            MultiWindowBudget([])

    def test_rate_window_validates_its_arguments(self):
        with self.assertRaises(ValueError):
            RateWindow(0, 1.0)
        with self.assertRaises(ValueError):
            RateWindow(5, 0.0)


class TestEndpointCategorisation(unittest.TestCase):
    def test_documented_account_calls(self):
        for path in (
            "v1/time",
            "v1/accounts",
            "v1/accounts/26598145/positions",
            "v1/accounts/26598145/balances",
            "v1/accounts/26598145/executions",
            "v1/accounts/26598145/orders",
        ):
            with self.subTest(path=path):
                self.assertIs(categorize_endpoint(path), RateLimitCategory.ACCOUNT)

    def test_documented_market_data_calls(self):
        for path in (
            "v1/markets",
            "v1/markets/quotes/8049",
            "v1/markets/candles/8049",
            "v1/symbols/8049",
            "v1/symbols/8049/options",
        ):
            with self.subTest(path=path):
                self.assertIs(categorize_endpoint(path), RateLimitCategory.MARKET_DATA)

    def test_uncategorised_endpoints_fall_back_to_the_tighter_budget(self):
        for path in (
            "v1/accounts/26598145/activities",
            "v1/symbols/search",
            "v1/markets/quotes/options",
        ):
            with self.subTest(path=path):
                self.assertIs(categorize_endpoint(path), RateLimitCategory.MARKET_DATA)

    def test_normalisation_strips_ids_and_version(self):
        self.assertEqual(
            normalize_endpoint("/v1/accounts/26598145/orders"), "accounts/{id}/orders"
        )
        self.assertEqual(normalize_endpoint("v1/markets/quotes/8049"), "markets/quotes/{id}")


class TestClientRateLimiting(unittest.TestCase):
    def test_account_and_market_budgets_are_independent(self):
        clock = FakeClock()
        limiter = QuestradeRateLimiter(monotonic=clock)
        for _ in range(30):
            self.assertTrue(limiter.acquire(RateLimitCategory.ACCOUNT))
        self.assertFalse(limiter.acquire(RateLimitCategory.ACCOUNT))
        # Market data is a separate 20/sec budget and must be untouched.
        self.assertEqual(
            sum(1 for _ in range(25) if limiter.acquire(RateLimitCategory.MARKET_DATA)),
            20,
        )

    def test_documented_per_second_capacities(self):
        clock = FakeClock()
        limiter = QuestradeRateLimiter(monotonic=clock)
        account = sum(
            1 for _ in range(100) if limiter.acquire(RateLimitCategory.ACCOUNT)
        )
        market = sum(
            1 for _ in range(100) if limiter.acquire(RateLimitCategory.MARKET_DATA)
        )
        self.assertEqual((account, market), (30, 20))

    def test_hourly_window_binds_before_the_per_second_window(self):
        """30 req/s sustained is 108,000/hour against a 30,000/hour cap."""
        clock = FakeClock()
        limiter = QuestradeRateLimiter(monotonic=clock)
        granted = 0
        for _ in range(3600):  # one hour of 30 req/sec bursts
            for _ in range(30):
                if limiter.acquire(RateLimitCategory.ACCOUNT):
                    granted += 1
            clock.advance(1.0)
        self.assertLessEqual(granted, 30_000 + 30)
        self.assertGreater(granted, 29_000)

    def test_client_waits_within_budget_then_raises(self):
        clock = FakeClock()
        limiter = QuestradeRateLimiter(
            {
                RateLimitCategory.ACCOUNT: (RateWindow(2, 1.0),),
                RateLimitCategory.MARKET_DATA: (RateWindow(2, 1.0),),
            },
            monotonic=clock,
        )
        client, _, _ = build_client(clock=clock, rate_limiter=limiter, max_wait_sec=1.0)
        client.refresh_access_token("seed")
        client.fetch_accounts()
        client.fetch_accounts()
        # Third call must wait ~0.5s (within the 1s budget) and succeed.
        before = clock.now
        client.fetch_accounts()
        self.assertGreater(clock.now, before)

    def test_exhausted_budget_raises_without_dispatching(self):
        clock = FakeClock()
        limiter = QuestradeRateLimiter(
            {
                RateLimitCategory.ACCOUNT: (RateWindow(1, 3600.0),),
                RateLimitCategory.MARKET_DATA: (RateWindow(1, 3600.0),),
            },
            monotonic=clock,
        )
        client, transport, _ = build_client(
            clock=clock, rate_limiter=limiter, max_wait_sec=0.5
        )
        client.refresh_access_token("seed")
        client.fetch_accounts()
        dispatched = len(transport.calls)
        with self.assertRaises(QuestradeRateLimitError) as ctx:
            client.fetch_accounts()
        self.assertEqual(ctx.exception.source, "local")
        self.assertEqual(len(transport.calls), dispatched, "no request may be sent")

    def test_server_429_is_classified_structurally_with_reset(self):
        def transport(method, url, headers, body):
            if "/oauth2/token" in url:
                return 200, TOKEN_RESPONSE
            return (
                429,
                {"detail": "too many"},
                {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1300286940"},
            )

        client, _, _ = build_client(transport)
        client.refresh_access_token("seed")
        with self.assertRaises(QuestradeRateLimitError) as ctx:
            client.fetch_accounts()
        self.assertEqual(ctx.exception.source, "server")
        self.assertEqual(ctx.exception.status_code, 429)
        self.assertEqual(ctx.exception.reset_at, 1300286940.0)

    def test_order_id_containing_429_is_not_treated_as_a_throttle(self):
        """Structural classification: '429' in a message is not a rate limit."""

        def transport(method, url, headers, body):
            if "/oauth2/token" in url:
                return 200, TOKEN_RESPONSE
            return 400, {"code": 3139, "message": "Order 429123 rejected"}

        client, _, _ = build_client(transport)
        client.refresh_access_token("seed")
        with self.assertRaises(QuestradeAPIError) as ctx:
            client.fetch_accounts()
        self.assertNotIsInstance(ctx.exception, QuestradeRateLimitError)
        self.assertEqual(ctx.exception.status_code, 400)

    def test_rate_limit_headers_resync_the_local_budget(self):
        clock = FakeClock()
        limiter = QuestradeRateLimiter(monotonic=clock)
        transport = RecordingTransport(headers={"x-ratelimit-remaining": "3"})
        client, _, _ = build_client(transport, clock=clock, rate_limiter=limiter)
        client.refresh_access_token("seed")
        client.fetch_accounts()
        # Server says 3 left; the local 30/sec bucket must clamp down to it.
        granted = sum(
            1 for _ in range(30) if limiter.acquire(RateLimitCategory.ACCOUNT)
        )
        self.assertEqual(granted, 3)

    def test_resync_does_not_strand_the_hourly_window(self):
        """
        Regression: applying one 'remaining: 29' header to every window charged
        29,971 requests against the 30,000/hour budget on the first response.
        """
        clock = FakeClock()
        limiter = QuestradeRateLimiter(monotonic=clock)
        budget = limiter.budget(RateLimitCategory.ACCOUNT)
        limiter.apply_headers(
            RateLimitCategory.ACCOUNT, {"X-RateLimit-Remaining": "29"}
        )
        per_second_used, per_hour_used = budget.usage()
        self.assertEqual(per_second_used, 1)
        self.assertEqual(per_hour_used, 0, "the hourly window must be untouched")

    def test_resync_ignores_a_value_larger_than_the_short_window(self):
        clock = FakeClock()
        limiter = QuestradeRateLimiter(monotonic=clock)
        budget = limiter.budget(RateLimitCategory.ACCOUNT)
        limiter.apply_headers(
            RateLimitCategory.ACCOUNT, {"X-RateLimit-Remaining": "14999"}
        )
        self.assertEqual(budget.usage(), (0, 0))

    def test_malformed_rate_limit_headers_are_ignored(self):
        clock = FakeClock()
        limiter = QuestradeRateLimiter(monotonic=clock)
        with self.assertLogs("questrade_client", level=logging.WARNING):
            limiter.apply_headers(RateLimitCategory.ACCOUNT, {"X-RateLimit-Remaining": "n/a"})
        self.assertEqual(
            sum(1 for _ in range(30) if limiter.acquire(RateLimitCategory.ACCOUNT)), 30
        )

    def test_parse_rate_limit_reset(self):
        self.assertEqual(parse_rate_limit_reset({"X-RateLimit-Reset": "1300286940"}), 1300286940.0)
        self.assertIsNone(parse_rate_limit_reset(None))
        self.assertIsNone(parse_rate_limit_reset({}))
        with self.assertLogs("questrade_client", level=logging.WARNING):
            self.assertIsNone(parse_rate_limit_reset({"X-RateLimit-Reset": "soon"}))

    def test_limiter_requires_windows_for_every_category(self):
        with self.assertRaises(ValueError):
            QuestradeRateLimiter({RateLimitCategory.ACCOUNT: (RateWindow(1, 1.0),)})


# --------------------------------------------------------------------------
# Transport contract and error surfacing
# --------------------------------------------------------------------------
class TestTransportContract(unittest.TestCase):
    def test_two_tuple_transport_is_still_supported(self):
        client, _, _ = build_client()
        client.refresh_access_token("seed")
        self.assertEqual(len(client.fetch_accounts()), 4)

    def test_three_tuple_transport_is_supported(self):
        client, _, _ = build_client(
            RecordingTransport(headers={"X-RateLimit-Remaining": "29"})
        )
        client.refresh_access_token("seed")
        self.assertEqual(len(client.fetch_accounts()), 4)

    def test_malformed_transport_return_is_reported(self):
        client, _, _ = build_client(lambda *a: "200 OK")
        with self.assertRaises(QuestradeAPIError):
            client.refresh_access_token("seed")

    def test_non_integer_status_is_rejected(self):
        client, _, _ = build_client(lambda *a: ("200", {}))
        with self.assertRaises(QuestradeAPIError):
            client.refresh_access_token("seed")

    def test_error_body_under_http_200_is_surfaced(self):
        """Questrade documents order errors returned under HTTP 200 with a code."""

        def transport(method, url, headers, body):
            if "/oauth2/token" in url:
                return 200, TOKEN_RESPONSE
            return 200, {"code": 3054, "message": "Order was rejected", "orderId": 134353223}

        client, _, _ = build_client(transport)
        client.refresh_access_token("seed")
        with self.assertRaises(QuestradeAPIError) as ctx:
            client.fetch_accounts()
        self.assertEqual(ctx.exception.error_code, 3054)

    def test_401_is_an_auth_error(self):
        def transport(method, url, headers, body):
            if "/oauth2/token" in url:
                return 200, TOKEN_RESPONSE
            return 401, {"detail": "unauthorized"}

        client, _, _ = build_client(transport)
        client.refresh_access_token("seed")
        with self.assertRaises(QuestradeAuthError):
            client.fetch_accounts()

    def test_http_fn_is_required(self):
        with self.assertRaises(ValueError):
            QuestradeClient(None)

    def test_invalid_client_configuration_is_rejected(self):
        transport = RecordingTransport()
        with self.assertRaises(ValueError):
            QuestradeClient(transport, expiry_skew_sec=-1)
        with self.assertRaises(ValueError):
            QuestradeClient(transport, max_wait_sec=-1)
        with self.assertRaises(ValueError):
            QuestradeClient(transport, token_request_method="PUT")


if __name__ == "__main__":
    unittest.main()
