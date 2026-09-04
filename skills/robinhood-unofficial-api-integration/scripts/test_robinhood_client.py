"""
Unit tests for robinhood-unofficial-api-integration skill.

Each test names the failure it defends against. The regression tests marked
"REGRESSION" fail against an earlier client and pass against this one.
"""
import time
import unittest
import uuid
from unittest import mock

import robinhood_client
from robinhood_client import (
    BASE_URL,
    AuthToken,
    OrderSide,
    OrderType,
    RobinhoodAmbiguousOrderError,
    RobinhoodAuthError,
    RobinhoodDeviceApprovalRequired,
    RobinhoodError,
    RobinhoodMFARequired,
    RobinhoodOrderError,
    RobinhoodUnofficialClient,
    TimeInForce,
    new_device_token,
)

ACCOUNT_URL = f"{BASE_URL}/accounts/5PY12345/"
AAPL_INSTRUMENT = f"{BASE_URL}/instruments/450dfc6d-5510-4d40-abfb-f633b7d2be13/"
TSLA_INSTRUMENT = f"{BASE_URL}/instruments/e39ed23a-7bd1-4587-b060-71988d9ef483/"

TOKEN_BODY = {
    "access_token": "mock_token_abc123",
    "refresh_token": "mock_refresh_xyz",
    "expires_in": 86400,
}


class RecordingTransport:
    """Transport double that records every call it is handed."""

    def __init__(self, responses=None):
        self.calls = []
        self._responses = responses or {}

    def __call__(self, method, url, headers, payload):
        self.calls.append(
            {"method": method, "url": url, "headers": headers, "payload": payload}
        )
        for key, response in self._responses.items():
            if key in url:
                return response(method, url, payload) if callable(response) else response
        return 200, {}

    @property
    def order_payloads(self):
        return [c["payload"] for c in self.calls if c["url"].endswith("/orders/")]


def _token_ok(*_args):
    return 200, dict(TOKEN_BODY)


def _order_ok(*_args):
    return 201, {"id": "order_001", "state": "queued"}


def _positions_single_page(*_args):
    return 200, {
        "results": [
            {
                "instrument": AAPL_INSTRUMENT,
                "quantity": "100.0",
                "average_buy_price": "150.00",
                "shares_held_for_sells": "10.0",
            },
            {
                "instrument": TSLA_INSTRUMENT,
                "quantity": "0.0",
                "average_buy_price": "0.00",
            },
        ],
        "next": None,
    }


def success_transport():
    return RecordingTransport({
        "oauth2/token": _token_ok,
        "/orders/": _order_ok,
        "/positions/": _positions_single_page,
    })


def make_client(transport=None, **kwargs):
    """A client wired for tests: throttling off unless a test asks for it."""
    kwargs.setdefault("account_url", ACCOUNT_URL)
    kwargs.setdefault("min_poll_interval_s", 0)
    kwargs.setdefault("client_id", "test-client-id")
    return RobinhoodUnofficialClient(
        transport or success_transport(), device_token="stored-device-token", **kwargs
    )


def authed_client(transport=None, **kwargs):
    transport = transport or success_transport()
    client = make_client(transport, **kwargs)
    client.authenticate("user@email.com", "password123")
    return client, transport


# ----------------------------------------------------------------------
# Construction
# ----------------------------------------------------------------------


class TestConstruction(unittest.TestCase):

    def test_device_token_is_required(self):
        """REGRESSION: the client must never mint a throwaway device token.

        an earlier client did `uuid.uuid4()` in __init__, so every process
        restart looked like a new device -- the exact behaviour the skill's own
        pitfalls warn drives repeated challenges and security flags.
        """
        with self.assertRaises(ValueError) as ctx:
            RobinhoodUnofficialClient(success_transport(), device_token="", client_id="cid")
        self.assertIn("persisted", str(ctx.exception))

        with self.assertRaises(ValueError):
            RobinhoodUnofficialClient(success_transport(), device_token="   ", client_id="cid")

    def test_supplied_device_token_is_reused_verbatim(self):
        transport = success_transport()
        client = make_client(transport)
        client.authenticate("user@email.com", "password123")
        self.assertEqual(
            transport.calls[0]["payload"]["device_token"], "stored-device-token"
        )
        self.assertEqual(client.auth_token.device_token, "stored-device-token")

    def test_two_clients_with_same_stored_token_present_one_device(self):
        """A restart must not change device identity."""
        stored = new_device_token()
        t1, t2 = success_transport(), success_transport()
        for transport in (t1, t2):
            client = RobinhoodUnofficialClient(
                transport, device_token=stored, client_id="cid", account_url=ACCOUNT_URL,
                min_poll_interval_s=0,
            )
            client.authenticate("user@email.com", "password123")
        self.assertEqual(
            t1.calls[0]["payload"]["device_token"],
            t2.calls[0]["payload"]["device_token"],
        )

    def test_new_device_token_is_a_uuid(self):
        uuid.UUID(new_device_token())

    def test_transport_is_required_at_construction(self):
        """Fail at construction, not at the first live order."""
        with self.assertRaises(ValueError):
            RobinhoodUnofficialClient(None, device_token="tok", client_id="cid")

    def test_client_id_is_required_and_has_no_default(self):
        # Regression: the module used to ship Robinhood's web-client OAuth id as a
        # default, silently binding every caller to a harvested credential.
        with self.assertRaises(TypeError):
            RobinhoodUnofficialClient(success_transport(), device_token="tok")
        with self.assertRaises(ValueError) as ctx:
            RobinhoodUnofficialClient(success_transport(), device_token="tok", client_id="  ")
        self.assertIn("client_id", str(ctx.exception))
        client = make_client(client_id="  my-id  ")
        self.assertEqual(client.client_id, "my-id")

    def test_invalid_tuning_parameters_rejected(self):
        with self.assertRaises(ValueError):
            RobinhoodUnofficialClient(
                success_transport(), device_token="tok", client_id="cid", min_poll_interval_s=-1
            )
        with self.assertRaises(ValueError):
            RobinhoodUnofficialClient(
                success_transport(), device_token="tok", client_id="cid", max_pages=0
            )


# ----------------------------------------------------------------------
# Authentication
# ----------------------------------------------------------------------


class TestAuthentication(unittest.TestCase):

    def test_successful_authentication(self):
        client, _ = authed_client()
        self.assertEqual(client.auth_token.access_token, "mock_token_abc123")
        self.assertFalse(client.auth_token.is_expired)

    def test_mfa_challenge_then_resolution(self):
        def token(_method, _url, payload):
            if "mfa_code" not in payload:
                return 400, {"mfa_required": True, "mfa_type": "sms"}
            return 200, {**TOKEN_BODY, "access_token": "mock_token_mfa"}

        client = make_client(RecordingTransport({"oauth2/token": token}))

        with self.assertRaises(RobinhoodMFARequired) as ctx:
            client.authenticate("user@email.com", "password123")
        self.assertEqual(ctx.exception.mfa_type, "sms")
        self.assertIn("MFA_REQUIRED", str(ctx.exception))
        self.assertIsNone(client.auth_token, "no session may be installed on a challenge")

        token_obj = client.authenticate("user@email.com", "password123", mfa_code="123456")
        self.assertEqual(token_obj.access_token, "mock_token_mfa")

    def test_mfa_required_is_still_an_auth_error(self):
        """Callers that catch the base class keep working."""
        self.assertTrue(issubclass(RobinhoodMFARequired, RobinhoodAuthError))
        self.assertTrue(issubclass(RobinhoodDeviceApprovalRequired, RobinhoodAuthError))

    def test_device_approval_workflow_is_distinguished_from_mfa(self):
        """REGRESSION: a verification_workflow is not an mfa_code challenge.

        Robinhood now routes most logins through in-app device approval. The
        older client would have read this 200 as a successful login.
        """
        transport = RecordingTransport({
            "oauth2/token": (200, {"verification_workflow": {"id": "wf_9"}}),
        })
        client = make_client(transport)
        with self.assertRaises(RobinhoodDeviceApprovalRequired) as ctx:
            client.authenticate("user@email.com", "password123")
        self.assertEqual(ctx.exception.workflow_id, "wf_9")
        self.assertNotIsInstance(ctx.exception, RobinhoodMFARequired)
        self.assertIsNone(client.auth_token)

    def test_device_approval_wins_over_a_200_with_access_token(self):
        transport = RecordingTransport({
            "oauth2/token": (200, {**TOKEN_BODY, "verification_workflow": {"id": "wf_1"}}),
        })
        client = make_client(transport)
        with self.assertRaises(RobinhoodDeviceApprovalRequired):
            client.authenticate("user@email.com", "password123")
        self.assertIsNone(client.auth_token)

    def test_missing_expires_in_is_fatal(self):
        """REGRESSION: never assume a token lifetime the server did not state."""
        transport = RecordingTransport({
            "oauth2/token": (200, {"access_token": "a", "refresh_token": "b"}),
        })
        client = make_client(transport)
        with self.assertRaises(RobinhoodAuthError) as ctx:
            client.authenticate("user@email.com", "password123")
        self.assertIn("expires_in", str(ctx.exception))
        self.assertIsNone(client.auth_token)

    def test_non_numeric_and_non_positive_expires_in_rejected(self):
        for bad in ("86400", None, True, 0, -5):
            transport = RecordingTransport({
                "oauth2/token": (200, {"access_token": "a", "expires_in": bad}),
            })
            client = make_client(transport)
            with self.assertRaises(RobinhoodAuthError):
                client.authenticate("user@email.com", "password123")

    def test_failed_auth_does_not_echo_the_response_body(self):
        """A failure message must not replay identifiers back into logs."""
        transport = RecordingTransport({
            "oauth2/token": (401, {"detail": "user@email.com is not valid"}),
        })
        client = make_client(transport)
        with self.assertRaises(RobinhoodAuthError) as ctx:
            client.authenticate("user@email.com", "password123")
        self.assertNotIn("user@email.com", str(ctx.exception))

    def test_blank_credentials_rejected_before_dispatch(self):
        transport = success_transport()
        client = make_client(transport)
        with self.assertRaises(ValueError):
            client.authenticate("", "password123")
        with self.assertRaises(ValueError):
            client.authenticate("user@email.com", "")
        self.assertEqual(transport.calls, [], "must not dispatch on invalid input")

    def test_expiry_uses_a_monotonic_deadline(self):
        """A wall-clock step must not resurrect or kill a token."""
        live = AuthToken("a", "b", time.monotonic() + 60, "dev")
        dead = AuthToken("a", "b", time.monotonic() - 1, "dev")
        self.assertFalse(live.is_expired)
        self.assertTrue(dead.is_expired)
        self.assertGreater(live.seconds_remaining, 0)
        self.assertEqual(dead.seconds_remaining, 0.0)

    def test_expired_token_blocks_every_authenticated_call(self):
        client, _ = authed_client()
        client.auth_token.expires_at = time.monotonic() - 1
        with self.assertRaises(RobinhoodAuthError):
            client.get_positions()
        with self.assertRaises(RobinhoodAuthError):
            client.place_order(
                "AAPL", OrderSide.BUY, 1, instrument_url=AAPL_INSTRUMENT
            )

    def test_unauthenticated_calls_are_refused(self):
        client = make_client()
        with self.assertRaises(RobinhoodAuthError):
            client.get_positions()

    def test_secrets_are_not_in_reprs(self):
        client, _ = authed_client()
        self.assertNotIn("mock_token_abc123", repr(client.auth_token))
        self.assertNotIn("mock_refresh_xyz", repr(client.auth_token))
        self.assertNotIn("mock_token_abc123", repr(client))


# ----------------------------------------------------------------------
# Order placement
# ----------------------------------------------------------------------


class TestOrderPlacement(unittest.TestCase):

    def test_order_placement_returns_the_created_order(self):
        client, _ = authed_client()
        order = client.place_order(
            "aapl", OrderSide.BUY, 10, OrderType.MARKET,
            instrument_url=AAPL_INSTRUMENT,
        )
        self.assertEqual(order.symbol, "AAPL")
        self.assertEqual(order.side, "buy")
        self.assertEqual(order.quantity, 10)
        self.assertEqual(order.status, "queued")
        self.assertEqual(order.order_id, "order_001")
        self.assertIsNone(order.limit_price)

    def test_payload_carries_a_ref_id(self):
        """REGRESSION: an earlier payload had no ref_id at all.

        Without a client-supplied ref_id there is no handle to reconcile a
        possibly-created order against, and no de-duplication key.
        """
        client, transport = authed_client()
        order = client.place_order(
            "AAPL", OrderSide.BUY, 1, instrument_url=AAPL_INSTRUMENT
        )
        payload = transport.order_payloads[0]
        self.assertIn("ref_id", payload)
        uuid.UUID(payload["ref_id"])
        self.assertEqual(order.client_ref_id, payload["ref_id"])

    def test_caller_supplied_ref_id_is_sent_verbatim(self):
        """A resubmission of the same logical order must reuse the same key."""
        client, transport = authed_client()
        client.place_order(
            "AAPL", OrderSide.BUY, 1,
            instrument_url=AAPL_INSTRUMENT, client_ref_id="stable-ref-1",
        )
        client.place_order(
            "AAPL", OrderSide.BUY, 1,
            instrument_url=AAPL_INSTRUMENT, client_ref_id="stable-ref-1",
        )
        self.assertEqual(
            [p["ref_id"] for p in transport.order_payloads],
            ["stable-ref-1", "stable-ref-1"],
        )

    def test_distinct_orders_get_distinct_ref_ids(self):
        client, transport = authed_client()
        client.place_order("AAPL", OrderSide.BUY, 1, instrument_url=AAPL_INSTRUMENT)
        client.place_order("AAPL", OrderSide.BUY, 1, instrument_url=AAPL_INSTRUMENT)
        refs = [p["ref_id"] for p in transport.order_payloads]
        self.assertEqual(len(set(refs)), 2)

    def test_payload_carries_the_real_account_and_instrument_urls(self):
        """REGRESSION: an earlier payload hardcoded '/accounts/MOCK/'."""
        client, transport = authed_client()
        client.place_order("AAPL", OrderSide.BUY, 1, instrument_url=AAPL_INSTRUMENT)
        payload = transport.order_payloads[0]
        self.assertEqual(payload["account"], ACCOUNT_URL)
        self.assertEqual(payload["instrument"], AAPL_INSTRUMENT)
        self.assertNotIn("MOCK", payload["account"])

    def test_missing_account_url_blocks_submission(self):
        transport = success_transport()
        client = RobinhoodUnofficialClient(
            transport, device_token="dev", client_id="cid", min_poll_interval_s=0
        )
        client.authenticate("user@email.com", "password123")
        with self.assertRaises(ValueError) as ctx:
            client.place_order("AAPL", OrderSide.BUY, 1, instrument_url=AAPL_INSTRUMENT)
        self.assertIn("account_url", str(ctx.exception))
        self.assertEqual(transport.order_payloads, [])

    def test_missing_instrument_url_blocks_submission(self):
        client, transport = authed_client()
        with self.assertRaises(ValueError):
            client.place_order("AAPL", OrderSide.BUY, 1, instrument_url="")
        self.assertEqual(transport.order_payloads, [])

    def test_invalid_quantities_are_refused_before_dispatch(self):
        client, transport = authed_client()
        for bad in (0, -1, -0.5, float("nan"), float("inf"), "10", None, True):
            with self.assertRaises(ValueError, msg=f"quantity={bad!r}"):
                client.place_order(
                    "AAPL", OrderSide.BUY, bad, instrument_url=AAPL_INSTRUMENT
                )
        self.assertEqual(
            transport.order_payloads, [], "no invalid order may reach the broker"
        )

    def test_limit_order_requires_a_valid_price(self):
        client, transport = authed_client()
        with self.assertRaises(ValueError):
            client.place_order(
                "AAPL", OrderSide.BUY, 1, OrderType.LIMIT,
                instrument_url=AAPL_INSTRUMENT,
            )
        for bad in (0, -5, float("nan"), float("inf")):
            with self.assertRaises(ValueError, msg=f"price={bad!r}"):
                client.place_order(
                    "AAPL", OrderSide.BUY, 1, OrderType.LIMIT, bad,
                    instrument_url=AAPL_INSTRUMENT,
                )
        self.assertEqual(transport.order_payloads, [])

    def test_limit_order_sends_the_price(self):
        client, transport = authed_client()
        order = client.place_order(
            "AAPL", OrderSide.SELL, 3, OrderType.LIMIT, 187.25,
            instrument_url=AAPL_INSTRUMENT, time_in_force=TimeInForce.GTC,
        )
        payload = transport.order_payloads[0]
        self.assertEqual(payload["price"], 187.25)
        self.assertEqual(payload["type"], "limit")
        self.assertEqual(payload["side"], "sell")
        self.assertEqual(payload["time_in_force"], "gtc")
        self.assertEqual(payload["trigger"], "immediate")
        self.assertEqual(order.limit_price, 187.25)

    def test_market_order_omits_price(self):
        client, transport = authed_client()
        client.place_order("AAPL", OrderSide.BUY, 1, instrument_url=AAPL_INSTRUMENT)
        self.assertNotIn("price", transport.order_payloads[0])

    def test_extended_hours_requires_a_limit_order(self):
        client, transport = authed_client()
        with self.assertRaises(ValueError):
            client.place_order(
                "AAPL", OrderSide.BUY, 1, OrderType.MARKET,
                instrument_url=AAPL_INSTRUMENT, extended_hours=True,
            )
        self.assertEqual(transport.order_payloads, [])
        client.place_order(
            "AAPL", OrderSide.BUY, 1, OrderType.LIMIT, 100.0,
            instrument_url=AAPL_INSTRUMENT, extended_hours=True,
        )
        self.assertTrue(transport.order_payloads[0]["extended_hours"])

    def test_broker_rejection_raises_order_error_not_ambiguous(self):
        """A definitive rejection created nothing; it must not read as ambiguous."""
        transport = RecordingTransport({
            "oauth2/token": _token_ok,
            "/orders/": (400, {"detail": "insufficient buying power"}),
        })
        client, _ = authed_client(transport)
        with self.assertRaises(RobinhoodOrderError) as ctx:
            client.place_order("AAPL", OrderSide.BUY, 1, instrument_url=AAPL_INSTRUMENT)
        self.assertNotIsInstance(ctx.exception, RobinhoodAmbiguousOrderError)
        self.assertEqual(client.submitted_orders, [])

    def test_transport_failure_is_ambiguous_and_carries_the_ref_id(self):
        """REGRESSION: a timed-out submission may already be a live order.

        an earlier client let the transport exception propagate raw, giving a
        caller nothing to reconcile with and inviting a duplicate-order retry.
        """
        def boom(*_args):
            raise TimeoutError("read timed out")

        transport = RecordingTransport({"oauth2/token": _token_ok, "/orders/": boom})
        client, _ = authed_client(transport)
        with self.assertLogs("robinhood_client", level="ERROR") as logs:
            with self.assertRaises(RobinhoodAmbiguousOrderError) as ctx:
                client.place_order(
                    "AAPL", OrderSide.BUY, 1,
                    instrument_url=AAPL_INSTRUMENT, client_ref_id="ref-timeout-1",
                )
        self.assertTrue(any("ref-timeout-1" in m for m in logs.output))
        self.assertEqual(ctx.exception.client_ref_id, "ref-timeout-1")
        self.assertIn("Reconcile", str(ctx.exception))
        self.assertEqual(
            client.submitted_orders, [], "an unknown outcome is not a recorded order"
        )

    def test_success_without_an_order_id_is_ambiguous(self):
        transport = RecordingTransport({
            "oauth2/token": _token_ok,
            "/orders/": (201, {"state": "queued"}),
        })
        client, _ = authed_client(transport)
        with self.assertRaises(RobinhoodAmbiguousOrderError):
            client.place_order("AAPL", OrderSide.BUY, 1, instrument_url=AAPL_INSTRUMENT)

    def test_undocumented_order_state_is_surfaced_but_not_swallowed(self):
        transport = RecordingTransport({
            "oauth2/token": _token_ok,
            "/orders/": (201, {"id": "o1", "state": "pending_review"}),
        })
        client, _ = authed_client(transport)
        with self.assertLogs("robinhood_client", level="WARNING") as logs:
            order = client.place_order(
                "AAPL", OrderSide.BUY, 1, instrument_url=AAPL_INSTRUMENT
            )
        self.assertEqual(order.status, "pending_review")
        self.assertTrue(any("undocumented state" in m for m in logs.output))

    def test_submitted_orders_is_a_defensive_copy(self):
        client, _ = authed_client()
        client.place_order("AAPL", OrderSide.BUY, 1, instrument_url=AAPL_INSTRUMENT)
        snapshot = client.submitted_orders
        snapshot.clear()
        self.assertEqual(len(client.submitted_orders), 1)


# ----------------------------------------------------------------------
# Positions
# ----------------------------------------------------------------------


class TestPositions(unittest.TestCase):

    def test_zero_quantity_positions_are_filtered(self):
        client, _ = authed_client()
        positions = client.get_positions()
        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0].quantity, 100.0)
        self.assertEqual(positions[0].average_cost, 150.0)
        self.assertEqual(positions[0].shares_held_for_sells, 10.0)

    def test_symbol_is_none_not_a_placeholder(self):
        """REGRESSION: /positions/ carries no symbol field.

        an earlier client did `result.get("symbol", "UNKNOWN")`, so every real
        position reconciled as the ticker "UNKNOWN". An absent symbol is correct;
        a fabricated one corrupts every downstream report.
        """
        client, _ = authed_client()
        position = client.get_positions()[0]
        self.assertIsNone(position.symbol)
        self.assertEqual(position.instrument_url, AAPL_INSTRUMENT)

    def test_symbol_resolver_fills_in_the_ticker(self):
        client, _ = authed_client()
        lookup = {AAPL_INSTRUMENT: "AAPL"}
        position = client.get_positions(symbol_resolver=lookup.get)[0]
        self.assertEqual(position.symbol, "AAPL")

    def test_pagination_is_followed_to_completion(self):
        """REGRESSION: page one only silently understates the portfolio."""
        page2 = f"{BASE_URL}/positions/?cursor=abc"

        def positions(_method, url, _payload):
            if "cursor=abc" in url:
                return 200, {
                    "results": [{
                        "instrument": TSLA_INSTRUMENT,
                        "quantity": "5",
                        "average_buy_price": "200",
                    }],
                    "next": None,
                }
            return 200, {
                "results": [{
                    "instrument": AAPL_INSTRUMENT,
                    "quantity": "100",
                    "average_buy_price": "150",
                }],
                "next": page2,
            }

        transport = RecordingTransport({
            "oauth2/token": _token_ok, "/positions/": positions,
        })
        client, _ = authed_client(transport)
        positions_out = client.get_positions()
        self.assertEqual(len(positions_out), 2)
        self.assertEqual(
            {p.instrument_url for p in positions_out},
            {AAPL_INSTRUMENT, TSLA_INSTRUMENT},
        )

    def test_first_page_requests_the_nonzero_filter(self):
        client, transport = authed_client()
        client.get_positions()
        position_calls = [c for c in transport.calls if "/positions/" in c["url"]]
        self.assertIn("nonzero=true", position_calls[0]["url"])

    def test_runaway_pagination_raises_rather_than_truncating(self):
        def positions(_method, url, _payload):
            page = url.split("cursor=")[-1] if "cursor=" in url else "0"
            return 200, {
                "results": [],
                "next": f"{BASE_URL}/positions/?cursor={int(page) + 1}",
            }

        transport = RecordingTransport({
            "oauth2/token": _token_ok, "/positions/": positions,
        })
        client, _ = authed_client(transport, max_pages=3)
        with self.assertRaises(RobinhoodError) as ctx:
            client.get_positions()
        self.assertIn("truncated", str(ctx.exception))

    def test_looping_cursor_is_detected(self):
        def positions(_method, _url, _payload):
            return 200, {"results": [], "next": f"{BASE_URL}/positions/?nonzero=true"}

        transport = RecordingTransport({
            "oauth2/token": _token_ok, "/positions/": positions,
        })
        client, _ = authed_client(transport)
        with self.assertRaises(RobinhoodError) as ctx:
            client.get_positions()
        self.assertIn("looped", str(ctx.exception))

    def test_non_200_page_raises(self):
        transport = RecordingTransport({
            "oauth2/token": _token_ok, "/positions/": (503, {}),
        })
        client, _ = authed_client(transport)
        with self.assertRaises(RobinhoodError):
            client.get_positions()

    def test_unparsable_position_is_skipped_not_crashed(self):
        transport = RecordingTransport({
            "oauth2/token": _token_ok,
            "/positions/": (200, {"results": [
                {"instrument": AAPL_INSTRUMENT, "quantity": "not-a-number"},
                {"instrument": TSLA_INSTRUMENT, "quantity": "7", "average_buy_price": "1"},
            ]}),
        })
        client, _ = authed_client(transport)
        with self.assertLogs("robinhood_client", level="WARNING"):
            positions = client.get_positions()
        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0].quantity, 7.0)

    def test_negative_quantity_is_retained(self):
        """A short/negative quantity is a real exposure, not noise to drop."""
        transport = RecordingTransport({
            "oauth2/token": _token_ok,
            "/positions/": (200, {"results": [
                {"instrument": AAPL_INSTRUMENT, "quantity": "-4", "average_buy_price": "10"},
            ]}),
        })
        client, _ = authed_client(transport)
        positions = client.get_positions()
        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0].quantity, -4.0)

    def test_missing_numeric_fields_default_to_zero_not_crash(self):
        transport = RecordingTransport({
            "oauth2/token": _token_ok,
            "/positions/": (200, {"results": [
                {"instrument": AAPL_INSTRUMENT, "quantity": "3"},
            ]}),
        })
        client, _ = authed_client(transport)
        position = client.get_positions()[0]
        self.assertEqual(position.average_cost, 0.0)
        self.assertEqual(position.shares_held_for_sells, 0.0)


class TestThrottling(unittest.TestCase):
    """Throttling is asserted on the sleep the client *requests*.

    Measuring elapsed wall-clock instead would make these tests hostage to the
    host timer granularity (~15.6 ms on Windows), which is not the behaviour
    under test.
    """

    def setUp(self):
        self.slept = []
        patcher = mock.patch.object(
            robinhood_client.time, "sleep", side_effect=self.slept.append
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_consecutive_polls_are_spaced(self):
        client, _ = authed_client(min_poll_interval_s=0.05)
        client.get_positions()
        client.get_positions()
        self.assertEqual(len(self.slept), 1, "only the second poll should wait")
        self.assertGreater(self.slept[0], 0.0)
        self.assertLessEqual(self.slept[0], 0.05)

    def test_each_page_of_a_paginated_poll_is_throttled(self):
        page2 = f"{BASE_URL}/positions/?cursor=abc"

        def positions(_method, url, _payload):
            if "cursor=abc" in url:
                return 200, {"results": [], "next": None}
            return 200, {"results": [], "next": page2}

        transport = RecordingTransport({
            "oauth2/token": _token_ok, "/positions/": positions,
        })
        client, _ = authed_client(transport, min_poll_interval_s=0.05)
        client.get_positions()
        self.assertEqual(len(self.slept), 1, "page two must not bypass the throttle")

    def test_zero_interval_disables_throttling(self):
        client, _ = authed_client(min_poll_interval_s=0)
        for _ in range(3):
            client.get_positions()
        self.assertEqual(self.slept, [])


if __name__ == "__main__":
    unittest.main()
