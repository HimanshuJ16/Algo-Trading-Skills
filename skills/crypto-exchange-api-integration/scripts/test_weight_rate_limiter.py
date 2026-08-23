"""
Unit tests for crypto-exchange-api-integration skill.

Clocks are injected throughout so the suite is deterministic and does not
sleep on the wall clock.

Covers:
1. WeightRateLimiter fixed-window accounting and boundary reset.
2. Header synchronization in both directions (regression: ratchet deadlock).
3. Unsatisfiable weight and acquire() timeout (regression: infinite spin).
4. Namespace registry refusing to invent budgets (regression: typo'd namespace).
5. Binance order payload validity per market type (regression: execInst).
6. Kraken decaying-counter limiter.
7. Rolling 24h P&L tracking (regression: falsy epoch-0 timestamp).
"""
import asyncio
import unittest

from weight_rate_limiter import (
    BINANCE_SPOT_REQUEST_WEIGHT_PER_MINUTE,
    BINANCE_USDM_FUTURES_REQUEST_WEIGHT_PER_MINUTE,
    CryptoExchangeRateLimiter,
    CryptoOrderPayload,
    KRAKEN_LEDGER_TRADE_HISTORY_COST,
    KrakenDecayCounterLimiter,
    KrakenTier,
    MarketType,
    OrderType,
    OrderValidationError,
    RateLimitTimeout,
    Rolling24hPnLTracker,
    SelfTradePreventionMode,
    TimeInForce,
    UnknownNamespaceError,
    UnsatisfiableWeightError,
    WeightRateLimiter,
)


class FakeClock:
    """Manually advanced clock, so window boundaries are exact."""

    def __init__(self, start: float = 1_000_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class TestWeightRateLimiter(unittest.TestCase):

    def setUp(self):
        self.clock = FakeClock(start=1_000_000.0)  # 1000000 % 60 == 40
        self.limiter = WeightRateLimiter(
            max_weight_per_window=10, window_seconds=60.0, time_source=self.clock
        )

    def test_consume_until_budget_exhausted(self):
        self.assertTrue(self.limiter.try_consume(6))
        self.assertEqual(self.limiter.current_weight(), 6)
        self.assertFalse(self.limiter.try_consume(5))  # 6 + 5 = 11 > 10
        self.assertTrue(self.limiter.try_consume(4))   # exactly at the cap
        self.assertEqual(self.limiter.current_weight(), 10)

    def test_counter_resets_on_clock_window_boundary_not_after_elapsed_time(self):
        # Binance resets at the top of the minute, so a limiter created at
        # t=1_000_000 (40s into the window) must reset 20s later, not 60s later.
        self.limiter.try_consume(10)
        self.assertEqual(self.limiter.seconds_until_reset(), 20.0)

        self.clock.advance(19.0)
        self.assertEqual(self.limiter.current_weight(), 10)  # still same window

        self.clock.advance(1.0)  # crosses the boundary
        self.assertEqual(self.limiter.current_weight(), 0)
        self.assertTrue(self.limiter.try_consume(10))

    def test_header_sync_follows_server_downward_at_window_reset(self):
        # Regression: the previous implementation only ever ratcheted upward and
        # appended a fresh-timestamped entry, so the local counter never followed
        # the server's reset and pinned the limiter at its ceiling forever.
        for used in (2, 5, 9):
            self.limiter.update_from_header(used)
        self.assertEqual(self.limiter.current_weight(), 9)
        self.assertFalse(self.limiter.try_consume(2))

        self.clock.advance(20.0)  # server counter resets at the boundary
        self.assertEqual(self.limiter.current_weight(), 0)
        self.limiter.update_from_header(1)
        self.assertEqual(self.limiter.current_weight(), 1)
        self.assertTrue(self.limiter.try_consume(9))

    def test_header_sync_adopts_weight_consumed_by_other_processes(self):
        self.limiter.try_consume(2)
        self.limiter.update_from_header(8)  # another process shares the IP
        self.assertEqual(self.limiter.current_weight(), 8)
        self.assertFalse(self.limiter.try_consume(5))

    def test_header_sync_rejects_invalid_values(self):
        for bad in (-1, 2.5, "8", True):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    self.limiter.update_from_header(bad)

    def test_safety_margin_reserves_headroom(self):
        limiter = WeightRateLimiter(
            max_weight_per_window=100,
            window_seconds=60.0,
            safety_margin_pct=0.10,
            time_source=self.clock,
        )
        self.assertEqual(limiter.effective_max, 90)
        self.assertTrue(limiter.try_consume(90))
        self.assertFalse(limiter.try_consume(1))

    def test_unsatisfiable_weight_raises_instead_of_returning_false(self):
        # Regression: a weight larger than the whole budget used to make
        # acquire() spin forever, because try_consume just kept returning False.
        with self.assertRaises(UnsatisfiableWeightError):
            self.limiter.try_consume(11)

    def test_acquire_waits_for_the_window_boundary(self):
        async def scenario():
            clock = FakeClock(start=1_000_000.0)
            limiter = WeightRateLimiter(10, 60.0, time_source=clock)
            limiter.try_consume(10)

            slept = []

            async def fake_sleep(seconds):
                slept.append(seconds)
                clock.advance(seconds)

            original = asyncio.sleep
            asyncio.sleep = fake_sleep
            try:
                await limiter.acquire(5)
            finally:
                asyncio.sleep = original

            # One sleep, sized to the boundary (20s), not repeated 0.1s polls.
            self.assertEqual(slept, [20.0])
            self.assertEqual(limiter.current_weight(), 5)

        asyncio.run(scenario())

    def test_acquire_times_out(self):
        async def scenario():
            clock = FakeClock(start=1_000_000.0)
            limiter = WeightRateLimiter(10, 60.0, time_source=clock)
            limiter.try_consume(10)

            async def fake_sleep(seconds):
                clock.advance(seconds)

            original = asyncio.sleep
            asyncio.sleep = fake_sleep
            try:
                with self.assertRaises(RateLimitTimeout):
                    await limiter.acquire(5, timeout=5.0)
            finally:
                asyncio.sleep = original

        asyncio.run(scenario())

    def test_acquire_rejects_impossible_request_immediately(self):
        async def scenario():
            limiter = WeightRateLimiter(10, 60.0, time_source=FakeClock())
            with self.assertRaises(UnsatisfiableWeightError):
                await limiter.acquire(50)

        asyncio.run(scenario())

    def test_constructor_rejects_invalid_configuration(self):
        for kwargs in (
            {"max_weight_per_window": 0},
            {"max_weight_per_window": -5},
            {"max_weight_per_window": 10.5},
            {"window_seconds": 0},
            {"safety_margin_pct": 1.0},
            {"safety_margin_pct": -0.1},
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    WeightRateLimiter(**kwargs)


class TestNamespaceRegistry(unittest.TestCase):

    def test_binance_defaults_use_current_published_limits(self):
        mgr = CryptoExchangeRateLimiter()
        spot = mgr.get_limiter("binance_spot")
        futures = mgr.get_limiter("binance_futures")
        self.assertIsNot(spot, futures)
        # Spot moved from 1,200 to 6,000 weight/min on 2023-08-25.
        self.assertEqual(spot.max_weight, 6000)
        self.assertEqual(spot.max_weight, BINANCE_SPOT_REQUEST_WEIGHT_PER_MINUTE)
        self.assertEqual(futures.max_weight, 2400)
        self.assertEqual(futures.max_weight, BINANCE_USDM_FUTURES_REQUEST_WEIGHT_PER_MINUTE)

    def test_unregistered_namespace_raises_instead_of_inventing_a_budget(self):
        # Regression: get_limiter() used to mint a 1000 weight/min limiter for
        # any unknown string, so a typo silently ran against a fabricated limit.
        mgr = CryptoExchangeRateLimiter()
        with self.assertRaises(UnknownNamespaceError):
            mgr.get_limiter("binance_spto")

    def test_no_fabricated_coinbase_or_kraken_weight_presets(self):
        # Coinbase is requests-per-second and Kraken is a decaying counter;
        # neither is expressible as a weight-per-minute pool, so no preset.
        mgr = CryptoExchangeRateLimiter()
        self.assertEqual(sorted(mgr.limiters), ["binance_futures", "binance_spot"])

    def test_explicit_registration_is_honoured(self):
        mgr = CryptoExchangeRateLimiter(include_binance_defaults=False)
        limiter = WeightRateLimiter(300, 60.0, time_source=FakeClock())
        mgr.register("my_venue", limiter)
        self.assertIs(mgr.get_limiter("my_venue"), limiter)

    def test_build_from_exchange_info_payload(self):
        rate_limits = [
            {"rateLimitType": "RAW_REQUESTS", "interval": "MINUTE", "intervalNum": 5, "limit": 61000},
            {"rateLimitType": "REQUEST_WEIGHT", "interval": "MINUTE", "intervalNum": 1, "limit": 6000},
        ]
        limiter = CryptoExchangeRateLimiter.binance_from_exchange_info(
            rate_limits, time_source=FakeClock()
        )
        self.assertEqual(limiter.max_weight, 6000)
        self.assertEqual(limiter.window, 60.0)

    def test_build_from_exchange_info_without_weight_limit_raises(self):
        with self.assertRaises(ValueError):
            CryptoExchangeRateLimiter.binance_from_exchange_info(
                [{"rateLimitType": "ORDERS", "interval": "SECOND", "intervalNum": 10, "limit": 100}]
            )


class TestKrakenDecayCounterLimiter(unittest.TestCase):

    def test_documented_tier_limits(self):
        self.assertEqual(
            (KrakenDecayCounterLimiter(KrakenTier.STARTER).max_counter,
             KrakenDecayCounterLimiter(KrakenTier.STARTER).decay_per_sec),
            (15, 0.33),
        )
        pro = KrakenDecayCounterLimiter(KrakenTier.PRO)
        self.assertEqual((pro.max_counter, pro.decay_per_sec), (20, 1.0))

    def test_counter_rises_and_decays(self):
        clock = FakeClock()
        lim = KrakenDecayCounterLimiter(KrakenTier.PRO, time_source=clock)
        for _ in range(20):
            self.assertTrue(lim.try_consume(1))
        self.assertFalse(lim.try_consume(1))  # counter at 20, tier max 20

        clock.advance(5.0)  # Pro decays 1/sec -> counter 15
        self.assertAlmostEqual(lim.current_counter(), 15.0, places=6)
        self.assertTrue(lim.try_consume(5))
        self.assertFalse(lim.try_consume(1))

    def test_ledger_and_trade_history_calls_cost_double(self):
        clock = FakeClock()
        lim = KrakenDecayCounterLimiter(KrakenTier.STARTER, time_source=clock)
        lim.try_consume(KRAKEN_LEDGER_TRADE_HISTORY_COST)
        self.assertAlmostEqual(lim.current_counter(), 2.0, places=6)

    def test_seconds_until_available(self):
        clock = FakeClock()
        lim = KrakenDecayCounterLimiter(KrakenTier.PRO, time_source=clock)
        for _ in range(20):
            lim.try_consume(1)
        # Need 2 units of room at 1.0/sec decay -> 2 seconds.
        self.assertAlmostEqual(lim.seconds_until_available(2), 2.0, places=6)

    def test_cost_above_tier_maximum_raises(self):
        lim = KrakenDecayCounterLimiter(KrakenTier.STARTER, time_source=FakeClock())
        with self.assertRaises(UnsatisfiableWeightError):
            lim.try_consume(16)


class TestCryptoOrderPayload(unittest.TestCase):

    def test_spot_post_only_uses_limit_maker_with_no_time_in_force(self):
        # Regression: the old builder emitted execInst="PostOnly" (BitMEX
        # syntax, silently ignored by Binance) AND a timeInForce that Binance
        # rejects on LIMIT_MAKER.
        payload = CryptoOrderPayload(
            symbol="BTCUSDT",
            side="BUY",
            order_type=OrderType.LIMIT_MAKER,
            quantity=0.5,
            price=50_000.0,
            market_type=MarketType.SPOT,
            stp_mode=SelfTradePreventionMode.EXPIRE_MAKER,
        ).to_dict()

        self.assertEqual(payload["type"], "LIMIT_MAKER")
        self.assertNotIn("timeInForce", payload)
        self.assertNotIn("execInst", payload)
        self.assertEqual(payload["selfTradePreventionMode"], "EXPIRE_MAKER")
        self.assertEqual(payload["price"], "50000.0")

    def test_futures_post_only_uses_limit_with_gtx(self):
        payload = CryptoOrderPayload(
            symbol="BTCUSDT",
            side="SELL",
            order_type=OrderType.LIMIT,
            quantity=1.0,
            price=51_000.0,
            market_type=MarketType.USDM_FUTURES,
            stp_mode=SelfTradePreventionMode.EXPIRE_TAKER,
            post_only=True,
        ).to_dict()

        # USD-M futures has no LIMIT_MAKER type; post-only is LIMIT + GTX.
        self.assertEqual(payload["type"], "LIMIT")
        self.assertEqual(payload["timeInForce"], "GTX")
        self.assertNotIn("execInst", payload)

    def test_futures_limit_maker_is_rejected(self):
        with self.assertRaises(OrderValidationError):
            CryptoOrderPayload(
                symbol="BTCUSDT",
                side="BUY",
                order_type=OrderType.LIMIT_MAKER,
                quantity=1.0,
                price=50_000.0,
                market_type=MarketType.USDM_FUTURES,
                stp_mode=SelfTradePreventionMode.NONE,
                time_in_force=TimeInForce.GTC,
            ).to_dict()

    def test_plain_spot_limit_order(self):
        payload = CryptoOrderPayload(
            symbol="ETHUSDT",
            side="BUY",
            order_type=OrderType.LIMIT,
            quantity=2.0,
            price=3_000.0,
            market_type=MarketType.SPOT,
            stp_mode=SelfTradePreventionMode.NONE,
            time_in_force=TimeInForce.IOC,
        ).to_dict()
        self.assertEqual(payload["type"], "LIMIT")
        self.assertEqual(payload["timeInForce"], "IOC")

    def test_spot_rejects_futures_only_time_in_force(self):
        for tif in (TimeInForce.GTX, TimeInForce.GTD):
            with self.subTest(tif=tif):
                with self.assertRaises(OrderValidationError):
                    CryptoOrderPayload(
                        symbol="ETHUSDT",
                        side="BUY",
                        order_type=OrderType.LIMIT,
                        quantity=2.0,
                        price=3_000.0,
                        market_type=MarketType.SPOT,
                        stp_mode=SelfTradePreventionMode.NONE,
                        time_in_force=tif,
                    ).to_dict()

    def test_market_order_omits_price_and_time_in_force(self):
        payload = CryptoOrderPayload(
            symbol="BTCUSDT",
            side="SELL",
            order_type=OrderType.MARKET,
            quantity=0.1,
            market_type=MarketType.SPOT,
            stp_mode=SelfTradePreventionMode.NONE,
        ).to_dict()
        self.assertNotIn("timeInForce", payload)
        self.assertNotIn("price", payload)

    def test_market_order_with_time_in_force_is_rejected(self):
        # Binance answers "Parameter 'timeInForce' sent when not required".
        with self.assertRaises(OrderValidationError):
            CryptoOrderPayload(
                symbol="BTCUSDT",
                side="SELL",
                order_type=OrderType.MARKET,
                quantity=0.1,
                market_type=MarketType.SPOT,
                stp_mode=SelfTradePreventionMode.NONE,
                time_in_force=TimeInForce.GTC,
            ).to_dict()

    def test_market_order_cannot_be_post_only(self):
        with self.assertRaises(OrderValidationError):
            CryptoOrderPayload(
                symbol="BTCUSDT",
                side="BUY",
                order_type=OrderType.MARKET,
                quantity=0.1,
                market_type=MarketType.SPOT,
                stp_mode=SelfTradePreventionMode.NONE,
                post_only=True,
            ).to_dict()

    def test_limit_order_without_price_is_rejected(self):
        with self.assertRaises(OrderValidationError):
            CryptoOrderPayload(
                symbol="BTCUSDT",
                side="BUY",
                order_type=OrderType.LIMIT,
                quantity=0.1,
                market_type=MarketType.SPOT,
                stp_mode=SelfTradePreventionMode.NONE,
            ).to_dict()

    def test_decrement_stp_mode_is_available(self):
        payload = CryptoOrderPayload(
            symbol="BTCUSDT",
            side="BUY",
            order_type=OrderType.LIMIT,
            quantity=1.0,
            price=50_000.0,
            market_type=MarketType.SPOT,
            stp_mode=SelfTradePreventionMode.DECREMENT,
        ).to_dict()
        self.assertEqual(payload["selfTradePreventionMode"], "DECREMENT")

    def test_stp_mode_must_be_chosen_explicitly(self):
        with self.assertRaises(TypeError):
            CryptoOrderPayload(  # type: ignore[call-arg]
                symbol="BTCUSDT",
                side="BUY",
                order_type=OrderType.LIMIT,
                quantity=1.0,
                price=50_000.0,
                market_type=MarketType.SPOT,
            )

    def test_invalid_quantity_price_and_side_are_rejected(self):
        base = dict(
            symbol="BTCUSDT",
            side="BUY",
            order_type=OrderType.LIMIT,
            quantity=1.0,
            price=50_000.0,
            market_type=MarketType.SPOT,
            stp_mode=SelfTradePreventionMode.NONE,
        )
        for override in (
            {"quantity": 0.0},
            {"quantity": -1.0},
            {"quantity": float("nan")},
            {"price": 0.0},
            {"price": float("inf")},
            {"side": "buy"},
            {"symbol": "   "},
        ):
            with self.subTest(override=override):
                with self.assertRaises(OrderValidationError):
                    CryptoOrderPayload(**{**base, **override})


class TestRolling24hPnLTracker(unittest.TestCase):

    def test_window_excludes_expired_trades(self):
        clock = FakeClock(start=1_000_000.0)
        tracker = Rolling24hPnLTracker(window_hours=24.0, time_source=clock)
        tracker.record_pnl(100.0, timestamp=clock.now - 100)
        tracker.record_pnl(-20.0, timestamp=clock.now - 50)
        tracker.record_pnl(50.0, timestamp=clock.now - 25 * 3600)  # older than 24h
        self.assertEqual(tracker.get_rolling_pnl(), 80.0)
        self.assertEqual(len(tracker), 2)

    def test_trades_age_out_as_the_clock_advances(self):
        clock = FakeClock(start=1_000_000.0)
        tracker = Rolling24hPnLTracker(window_hours=1.0, time_source=clock)
        tracker.record_pnl(30.0)
        clock.advance(1800)
        tracker.record_pnl(70.0)
        self.assertEqual(tracker.get_rolling_pnl(), 100.0)

        clock.advance(1801)  # first trade now older than 1h
        self.assertEqual(tracker.get_rolling_pnl(), 70.0)

        clock.advance(3600)
        self.assertEqual(tracker.get_rolling_pnl(), 0.0)
        self.assertEqual(len(tracker), 0)

    def test_epoch_zero_timestamp_is_not_treated_as_missing(self):
        # Regression: `timestamp or time.time()` treated a literal 0.0 as
        # "not supplied" and stamped an ancient record as happening now.
        clock = FakeClock(start=1_000_000.0)
        tracker = Rolling24hPnLTracker(window_hours=24.0, time_source=clock)
        tracker.record_pnl(500.0, timestamp=0.0)
        self.assertEqual(tracker.get_rolling_pnl(), 0.0)

    def test_default_timestamp_is_now(self):
        clock = FakeClock(start=1_000_000.0)
        tracker = Rolling24hPnLTracker(window_hours=24.0, time_source=clock)
        tracker.record_pnl(42.0)
        self.assertEqual(tracker.get_rolling_pnl(), 42.0)

    def test_rejects_non_finite_values(self):
        tracker = Rolling24hPnLTracker(time_source=FakeClock())
        for bad in (float("nan"), float("inf")):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    tracker.record_pnl(bad)
        with self.assertRaises(ValueError):
            tracker.record_pnl(1.0, timestamp=float("nan"))

    def test_rejects_invalid_window(self):
        for bad in (0, -1, float("inf")):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    Rolling24hPnLTracker(window_hours=bad)


if __name__ == "__main__":
    unittest.main()
