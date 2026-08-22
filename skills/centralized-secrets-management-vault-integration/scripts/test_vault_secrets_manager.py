"""Behavioural tests for the Vault AppRole secrets client.

The tests drive :class:`VaultSecretsManager` through ``InMemoryVaultTransport``,
which reproduces the Vault behaviours that actually break clients: 404 for
paths a policy cannot see, 404-with-metadata for soft-deleted KV v2 versions,
token TTL and max TTL, and single-use SecretIDs.
"""

import threading
import unittest

from vault_secrets_manager import (
    HttpVaultTransport,
    InMemoryVaultTransport,
    SecretBundle,
    VaultAuthenticationError,
    VaultCredentialExhausted,
    VaultPathViolation,
    VaultPermissionDenied,
    VaultResponse,
    VaultSecretNotFound,
    VaultSecretsManager,
    VaultTransportError,
)

PROD_PATH = "prod/binance/market-maker"
PROD_KEYS = {"api_key": "PROD_KEY", "api_secret": "PROD_SECRET"}


class FakeClock:
    """Monotonic clock the tests advance explicitly."""

    def __init__(self, start: float = 1_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def build(**manager_kwargs):
    """Return (manager, transport, clock) wired to a prod-scoped AppRole."""
    clock = FakeClock()
    transport = InMemoryVaultTransport(clock=clock)
    transport.register_role(
        "role-mm",
        "secret-mm",
        allowed_prefixes=["prod/binance/"],
        token_ttl=manager_kwargs.pop("token_ttl", 1200.0),
        token_max_ttl=manager_kwargs.pop("token_max_ttl", 3600.0),
        secret_id_num_uses=manager_kwargs.pop("secret_id_num_uses", 0),
    )
    transport.put_secret(PROD_PATH, PROD_KEYS)
    transport.put_secret("dev/binance/market-maker", {"api_key": "DEV_KEY"})
    transport.put_secret("prod/kraken/arb", {"api_key": "KRAKEN_KEY"})
    manager = VaultSecretsManager(
        "https://vault.internal:8200",
        "prod",
        transport=transport,
        clock=clock,
        **manager_kwargs,
    )
    return manager, transport, clock


def gets(transport) -> int:
    return sum(1 for entry in transport.request_log if entry.startswith("GET"))


def logins(transport) -> int:
    return sum(1 for entry in transport.request_log if "approle/login" in entry)


class TestConstruction(unittest.TestCase):
    def test_rejects_multi_segment_environment(self):
        with self.assertRaises(ValueError):
            VaultSecretsManager("https://v:8200", "prod/binance", transport=InMemoryVaultTransport())

    def test_rejects_empty_environment_and_bad_mount(self):
        with self.assertRaises(ValueError):
            VaultSecretsManager("https://v:8200", "", transport=InMemoryVaultTransport())
        with self.assertRaises(ValueError):
            VaultSecretsManager(
                "https://v:8200", "prod", mount="kv/data", transport=InMemoryVaultTransport()
            )

    def test_rejects_non_positive_cache_ttl(self):
        with self.assertRaises(ValueError):
            VaultSecretsManager(
                "https://v:8200", "prod", cache_ttl=0, transport=InMemoryVaultTransport()
            )


class TestAppRoleLogin(unittest.TestCase):
    def test_login_records_lease_and_accessor(self):
        manager, _, _ = build()
        manager.login_approle("role-mm", "secret-mm")
        self.assertTrue(manager.is_authenticated)
        self.assertEqual(manager.token_accessor, "acc-1")
        self.assertEqual(manager.token_ttl_remaining(), 1200.0)

    def test_empty_credentials_rejected_before_any_request(self):
        manager, transport, _ = build()
        with self.assertRaises(ValueError):
            manager.login_approle("", "secret-mm")
        with self.assertRaises(ValueError):
            manager.login_approle("role-mm", "")
        self.assertEqual(transport.request_log, [])

    def test_invalid_secret_id_is_permanent_not_retryable(self):
        manager, _, _ = build()
        with self.assertRaises(VaultCredentialExhausted) as ctx:
            manager.login_approle("role-mm", "wrong-secret")
        self.assertIn("new SecretID", str(ctx.exception))
        self.assertFalse(manager.is_authenticated)

    def test_vault_outage_during_login_is_a_transport_error(self):
        manager, transport, _ = build()
        transport.rate_limited = True
        with self.assertRaises(VaultTransportError):
            manager.login_approle("role-mm", "secret-mm")

    def test_reading_before_login_raises_authentication_error(self):
        manager, _, _ = build()
        with self.assertRaises(VaultAuthenticationError):
            manager.get_secret(PROD_PATH)


class TestPathGuard(unittest.TestCase):
    def setUp(self):
        self.manager, self.transport, _ = build()
        self.manager.login_approle("role-mm", "secret-mm")

    def test_other_environment_rejected_before_network_call(self):
        before = len(self.transport.request_log)
        with self.assertRaises(VaultPathViolation) as ctx:
            self.manager.get_secret("dev/binance/market-maker")
        self.assertIn("Environment mismatch", str(ctx.exception))
        self.assertEqual(len(self.transport.request_log), before)

    def test_traversal_out_of_environment_is_rejected(self):
        # Regression: a plain startswith("prod/") guard accepts this path.
        for path in ("prod/../dev/binance/market-maker", "prod/./../dev/x"):
            with self.subTest(path=path):
                with self.assertRaises(VaultPathViolation):
                    self.manager.get_secret(path)

    def test_environment_prefix_is_a_whole_segment(self):
        # "production/..." must not satisfy an environment of "prod".
        with self.assertRaises(VaultPathViolation):
            self.manager.get_secret("production/binance/market-maker")

    def test_malformed_paths_rejected(self):
        for path in ("", "   ", "prod//binance", "prod/bin ance", "prod/%2e%2e/dev", None, 7):
            with self.subTest(path=path):
                with self.assertRaises(VaultPathViolation):
                    self.manager.get_secret(path)

    def test_leading_and_trailing_slashes_are_normalised(self):
        bundle = self.manager.get_secret(f"/{PROD_PATH}/")
        self.assertEqual(bundle["api_key"], "PROD_KEY")
        self.assertEqual(bundle.path, PROD_PATH)


class TestSecretRetrieval(unittest.TestCase):
    def setUp(self):
        self.manager, self.transport, self.clock = build()
        self.manager.login_approle("role-mm", "secret-mm")

    def test_read_returns_values_and_kv_v2_version(self):
        bundle = self.manager.get_secret(PROD_PATH)
        self.assertEqual(bundle["api_key"], "PROD_KEY")
        self.assertEqual(bundle["api_secret"], "PROD_SECRET")
        self.assertEqual(bundle.version, 1)
        self.assertEqual(bundle.as_dict(), PROD_KEYS)

    def test_repeat_reads_are_served_from_cache(self):
        self.manager.get_secret(PROD_PATH)
        for _ in range(50):
            self.manager.get_secret(PROD_PATH)
        self.assertEqual(gets(self.transport), 1)

    def test_cache_expires_so_a_rotated_secret_is_picked_up(self):
        # Regression: an unbounded cache keeps a revoked credential forever.
        manager, transport, clock = build(cache_ttl=60.0)
        manager.login_approle("role-mm", "secret-mm")
        self.assertEqual(manager.get_secret(PROD_PATH)["api_key"], "PROD_KEY")

        transport.put_secret(PROD_PATH, {"api_key": "ROTATED", "api_secret": "ROTATED_SEC"})
        self.assertEqual(manager.get_secret(PROD_PATH)["api_key"], "PROD_KEY")  # still cached

        clock.advance(61.0)
        rotated = manager.get_secret(PROD_PATH)
        self.assertEqual(rotated["api_key"], "ROTATED")
        self.assertEqual(rotated.version, 2)

    def test_refresh_bypasses_the_cache(self):
        self.manager.get_secret(PROD_PATH)
        self.transport.put_secret(PROD_PATH, {"api_key": "ROTATED"})
        self.assertEqual(self.manager.get_secret(PROD_PATH, refresh=True)["api_key"], "ROTATED")

    def test_invalidate_forces_the_next_read_to_hit_vault(self):
        self.manager.get_secret(PROD_PATH)
        self.manager.invalidate(PROD_PATH)
        self.manager.get_secret(PROD_PATH)
        self.assertEqual(gets(self.transport), 2)

        self.manager.invalidate()
        self.manager.get_secret(PROD_PATH)
        self.assertEqual(gets(self.transport), 3)

    def test_path_denied_by_policy_reports_both_possible_causes(self):
        # Vault answers 404 — not 403 — for a path the policy cannot see.
        with self.assertRaises(VaultSecretNotFound) as ctx:
            self.manager.get_secret("prod/kraken/arb")
        message = str(ctx.exception)
        self.assertIn("policy", message)
        self.assertIn("does not exist", message)

    def test_missing_path_raises_secret_not_found(self):
        with self.assertRaises(VaultSecretNotFound):
            self.manager.get_secret("prod/binance/does-not-exist")

    def test_soft_deleted_version_is_reported_as_deleted_not_missing(self):
        self.transport.soft_delete_latest(PROD_PATH)
        with self.assertRaises(VaultSecretNotFound) as ctx:
            self.manager.get_secret(PROD_PATH)
        self.assertIn("soft-deleted", str(ctx.exception))

    def test_rate_limit_surfaces_as_retryable_transport_error(self):
        self.transport.rate_limited = True
        with self.assertRaises(VaultTransportError) as ctx:
            self.manager.get_secret(PROD_PATH)
        self.assertTrue(ctx.exception.retryable)


class TestTokenLifetime(unittest.TestCase):
    def test_token_is_renewed_before_it_expires(self):
        manager, transport, clock = build(token_ttl=1200.0, token_max_ttl=3600.0)
        manager.login_approle("role-mm", "secret-mm")
        manager.get_secret(PROD_PATH)

        clock.advance(1160.0)  # inside the 60s renew margin
        manager.get_secret(PROD_PATH, refresh=True)

        self.assertIn("POST auth/token/renew-self", transport.request_log)
        self.assertEqual(logins(transport), 1)
        self.assertGreater(manager.token_ttl_remaining(), 60.0)

    def test_reaching_max_ttl_triggers_re_login_not_endless_renewal(self):
        manager, transport, clock = build(token_ttl=600.0, token_max_ttl=600.0)
        manager.login_approle("role-mm", "secret-mm")
        manager.get_secret(PROD_PATH)

        clock.advance(560.0)
        bundle = manager.get_secret(PROD_PATH, refresh=True)

        self.assertEqual(bundle["api_key"], "PROD_KEY")
        self.assertEqual(logins(transport), 2)
        self.assertEqual(manager.token_ttl_remaining(), 600.0)

    def test_expired_token_with_single_use_secret_id_fails_permanently(self):
        manager, transport, clock = build(
            token_ttl=600.0, token_max_ttl=600.0, secret_id_num_uses=1
        )
        manager.login_approle("role-mm", "secret-mm")
        manager.get_secret(PROD_PATH)

        clock.advance(700.0)
        with self.assertRaises(VaultCredentialExhausted):
            manager.get_secret(PROD_PATH, refresh=True)
        # And it does not keep hammering Vault on the next attempt.
        before = logins(transport)
        with self.assertRaises(VaultAuthenticationError):
            manager.get_secret(PROD_PATH, refresh=True)
        self.assertEqual(logins(transport), before)

    def test_revoked_token_triggers_exactly_one_re_login(self):
        manager, transport, _ = build()
        manager.login_approle("role-mm", "secret-mm")
        manager.get_secret(PROD_PATH)

        transport._tokens.clear()  # token revoked out from under the client
        bundle = manager.get_secret(PROD_PATH, refresh=True)

        self.assertEqual(bundle["api_key"], "PROD_KEY")
        self.assertEqual(logins(transport), 2)

    def test_persistent_403_raises_permission_denied_without_looping(self):
        class AlwaysForbidden(InMemoryVaultTransport):
            def _read(self, path, token):
                return VaultResponse(403, {"errors": ["permission denied"]})

        clock = FakeClock()
        transport = AlwaysForbidden(clock=clock)
        transport.register_role("role-mm", "secret-mm")
        manager = VaultSecretsManager(
            "https://vault.internal:8200", "prod", transport=transport, clock=clock
        )
        manager.login_approle("role-mm", "secret-mm")

        with self.assertRaises(VaultPermissionDenied):
            manager.get_secret(PROD_PATH)
        self.assertEqual(logins(transport), 2)  # initial login + one retry, then stop


class TestOutageBehaviour(unittest.TestCase):
    def test_stale_cache_keeps_a_live_bot_running_during_a_vault_outage(self):
        manager, transport, clock = build(cache_ttl=60.0)
        manager.login_approle("role-mm", "secret-mm")
        manager.get_secret(PROD_PATH)

        clock.advance(61.0)
        transport.unreachable = True
        bundle = manager.get_secret(PROD_PATH)
        self.assertEqual(bundle["api_key"], "PROD_KEY")

    def test_stale_serving_can_be_disabled(self):
        manager, transport, clock = build(cache_ttl=60.0, stale_if_error=False)
        manager.login_approle("role-mm", "secret-mm")
        manager.get_secret(PROD_PATH)

        clock.advance(61.0)
        transport.unreachable = True
        with self.assertRaises(VaultTransportError):
            manager.get_secret(PROD_PATH)

    def test_outage_with_no_cached_value_raises(self):
        manager, transport, _ = build()
        manager.login_approle("role-mm", "secret-mm")
        transport.unreachable = True
        with self.assertRaises(VaultTransportError):
            manager.get_secret(PROD_PATH)


class TestLeakageGuards(unittest.TestCase):
    def test_secret_bundle_repr_shows_key_names_but_no_values(self):
        bundle = SecretBundle("prod/binance/mm", PROD_KEYS, version=3)
        for text in (repr(bundle), str(bundle), f"{bundle}", "{}".format(bundle)):
            self.assertNotIn("PROD_SECRET", text)
            self.assertNotIn("PROD_KEY", text)
            self.assertIn("api_secret", text)
            self.assertIn("version=3", text)

    def test_manager_repr_never_exposes_the_client_token(self):
        manager, transport, _ = build()
        manager.login_approle("role-mm", "secret-mm")
        manager.get_secret(PROD_PATH)
        text = repr(manager)
        self.assertNotIn("hvs.fake-1", text)
        self.assertNotIn("secret-mm", text)
        self.assertIn(PROD_PATH, text)

    def test_logout_clears_token_credentials_and_cache(self):
        manager, _, _ = build()
        manager.login_approle("role-mm", "secret-mm")
        manager.get_secret(PROD_PATH)
        manager.logout()
        self.assertFalse(manager.is_authenticated)
        with self.assertRaises(VaultAuthenticationError):
            manager.get_secret(PROD_PATH)


class TestHttpTransportGuards(unittest.TestCase):
    def test_plaintext_http_is_refused_by_default(self):
        with self.assertRaises(ValueError) as ctx:
            HttpVaultTransport("http://vault.internal:8200")
        self.assertIn("clear text", str(ctx.exception))

    def test_plaintext_http_allowed_only_when_opted_into(self):
        HttpVaultTransport("http://127.0.0.1:8200", allow_insecure_http=True)

    def test_non_http_scheme_and_bad_timeout_rejected(self):
        with self.assertRaises(ValueError):
            HttpVaultTransport("vault.internal:8200")
        with self.assertRaises(ValueError):
            HttpVaultTransport("https://vault.internal:8200", timeout=0)


class TestConcurrency(unittest.TestCase):
    def test_concurrent_readers_share_one_login_and_one_read(self):
        manager, transport, _ = build()
        results = []
        errors = []
        barrier = threading.Barrier(8)

        def worker():
            try:
                barrier.wait(timeout=5)
                results.append(manager.get_secret(PROD_PATH)["api_key"])
            except Exception as exc:  # surfaced below as a test failure
                errors.append(exc)

        manager.login_approle("role-mm", "secret-mm")
        threads = [threading.Thread(target=worker) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertEqual(errors, [])
        self.assertEqual(results, ["PROD_KEY"] * 8)
        self.assertEqual(gets(transport), 1)
        self.assertEqual(logins(transport), 1)


if __name__ == "__main__":
    unittest.main()
