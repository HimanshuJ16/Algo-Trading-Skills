"""Behaviour tests for the Northbound Stock Connect order gate.

Expected values are derived from the HKEX rule text quoted in
``references/standards.md``, not from the implementation's own arithmetic.
Several tests are explicit regressions against the prior version's behaviour and
are labelled as such.
"""

import unittest
from decimal import Decimal

from shanghai_shenzhen_connect_programs import (
    BOARD_RULES,
    FOREIGN_OWNERSHIP_RESUME_PCT,
    FOREIGN_OWNERSHIP_SUSPEND_PCT,
    NORTHBOUND_DAILY_QUOTA_RMB,
    REJECT_CHANNEL_SYMBOL_MISMATCH,
    REJECT_FOREIGN_OWNERSHIP_SUSPENDED,
    REJECT_INVALID_BOARD_LOT,
    REJECT_INVALID_TICK_SIZE,
    REJECT_ORDER_SIZE_EXCEEDED,
    REJECT_ORDER_TYPE_NOT_SUPPORTED,
    REJECT_PRE_TRADE_CHECK_FAILED,
    REJECT_PRICE_LIMIT_BREACH,
    REJECT_QUOTA_EXHAUSTED,
    REJECT_SECURITY_NOT_BUY_ELIGIBLE,
    REJECT_SECURITY_NOT_REGISTERED,
    Board,
    ConnectOrder,
    ConnectRuleError,
    OrderSide,
    ShanghaiShenzhenConnectEngine,
    StockConnectChannel,
    TradingSession,
    infer_board,
)

MOUTAI = "600519.SH"        # SSE Main Board
WULIANGYE = "000858.SZ"     # SZSE Main Board
SMIC = "688981.SH"          # SSE STAR Market
CATL = "300750.SZ"          # SZSE ChiNext

SH = StockConnectChannel.SHANGHAI_CONNECT
SZ = StockConnectChannel.SHENZHEN_CONNECT
CONTINUOUS = TradingSession.CONTINUOUS_AUCTION
OPENING = TradingSession.OPENING_CALL_AUCTION
CLOSING = TradingSession.CLOSING_CALL_AUCTION


def buy(order_id, symbol, channel, quantity, price, **kwargs):
    return ConnectOrder(
        order_id=order_id, symbol=symbol, channel=channel, side=OrderSide.BUY,
        quantity=quantity, limit_price=Decimal(price), **kwargs
    )


def sell(order_id, symbol, channel, quantity, price, **kwargs):
    return ConnectOrder(
        order_id=order_id, symbol=symbol, channel=channel, side=OrderSide.SELL,
        quantity=quantity, limit_price=Decimal(price), **kwargs
    )


class BaseEngineTest(unittest.TestCase):
    """Engine with a standard four-security master and no opening positions."""

    def setUp(self):
        self.engine = ShanghaiShenzhenConnectEngine()
        self.engine.register_security(MOUTAI, "1700.00")
        self.engine.register_security(WULIANGYE, "150.00")
        self.engine.register_security(SMIC, "100.00")
        self.engine.register_security(CATL, "200.00")
        self.engine.start_trading_day({})


# ---------------------------------------------------------------------------
# Board classification and reference data
# ---------------------------------------------------------------------------

class TestBoardInference(unittest.TestCase):

    def test_boards_inferred_from_code_prefix_and_suffix(self):
        self.assertIs(infer_board(MOUTAI), Board.SSE_MAIN)
        self.assertIs(infer_board("601398.SH"), Board.SSE_MAIN)
        self.assertIs(infer_board(SMIC), Board.SSE_STAR)
        self.assertIs(infer_board(WULIANGYE), Board.SZSE_MAIN)
        self.assertIs(infer_board("002594.SZ"), Board.SZSE_MAIN)
        self.assertIs(infer_board(CATL), Board.SZSE_CHINEXT)

    def test_malformed_symbols_raise_rather_than_default_to_a_board(self):
        for bad in ["600519", "60051.SH", "600519.HK", "ABCDEF.SH", "900001.SH", 600519]:
            with self.subTest(symbol=bad):
                with self.assertRaises(ConnectRuleError):
                    infer_board(bad)

    def test_etf_registration_requires_an_explicit_price_limit(self):
        # SSE/SZSE ETFs are +/-10% normally but +/-20% for a published set, which
        # cannot be inferred from the code -- so the caller must state it.
        engine = ShanghaiShenzhenConnectEngine()
        with self.assertRaises(ConnectRuleError):
            engine.register_security("510300.SH", "4.000", board=Board.SSE_MAIN, is_etf=True)
        ref = engine.register_security(
            "510300.SH", "4.000", board=Board.SSE_MAIN, is_etf=True, price_limit_pct="10"
        )
        self.assertEqual(ref.tick_size, Decimal("0.001"))


# ---------------------------------------------------------------------------
# Board lot / order size -- §3.11
# ---------------------------------------------------------------------------

class TestOrderSizeRules(BaseEngineTest):

    def test_main_board_buy_must_be_a_100_share_multiple(self):
        res = self.engine.submit_order(buy("O1", MOUTAI, SH, 150, "1700.00"), CONTINUOUS)
        self.assertFalse(res.accepted)
        self.assertEqual(res.rejection_code, REJECT_INVALID_BOARD_LOT)

    def test_star_buy_of_250_shares_is_accepted(self):
        # REGRESSION: the prior engine rejected every non-multiple of 100. STAR
        # shares have a board lot of 1 share with a 200-share minimum, so 250 is
        # a valid STAR buy that the old rule wrongly refused.
        res = self.engine.submit_order(buy("O2", SMIC, SH, 250, "100.00"), CONTINUOUS)
        self.assertTrue(res.accepted, res.rejection_reason)

    def test_star_buy_below_the_200_share_minimum_is_rejected(self):
        res = self.engine.submit_order(buy("O3", SMIC, SH, 199, "100.00"), CONTINUOUS)
        self.assertFalse(res.accepted)
        self.assertEqual(res.rejection_code, REJECT_INVALID_BOARD_LOT)

    def test_chinext_keeps_the_100_share_board_lot(self):
        # ChiNext shares STAR's +/-20% price limit but NOT its 1-share board lot.
        self.assertEqual(BOARD_RULES[Board.SZSE_CHINEXT].board_lot, 100)
        res = self.engine.submit_order(buy("O4", CATL, SZ, 150, "200.00"), CONTINUOUS)
        self.assertFalse(res.accepted)
        self.assertEqual(res.rejection_code, REJECT_INVALID_BOARD_LOT)

    def test_odd_lot_sell_is_permitted(self):
        # "Odd lot trading is only available for sell orders." Rejecting these
        # would strand corporate-action remnants permanently.
        self.engine.start_trading_day({MOUTAI: 137})
        res = self.engine.submit_order(sell("O5", MOUTAI, SH, 137, "1700.00"), CONTINUOUS)
        self.assertTrue(res.accepted, res.rejection_reason)

    def test_maximum_order_size_is_per_board(self):
        self.assertEqual(BOARD_RULES[Board.SSE_MAIN].max_order_size, 1_000_000)
        self.assertEqual(BOARD_RULES[Board.SZSE_CHINEXT].max_order_size, 300_000)
        self.assertEqual(BOARD_RULES[Board.SSE_STAR].max_order_size, 100_000)

        ok = self.engine.submit_order(buy("O6", CATL, SZ, 300_000, "200.00"), CONTINUOUS)
        self.assertTrue(ok.accepted, ok.rejection_reason)
        over = self.engine.submit_order(buy("O7", CATL, SZ, 300_100, "200.00"), CONTINUOUS)
        self.assertFalse(over.accepted)
        self.assertEqual(over.rejection_code, REJECT_ORDER_SIZE_EXCEEDED)

    def test_max_order_size_also_binds_sells(self):
        self.engine.start_trading_day({SMIC: 200_000})
        res = self.engine.submit_order(sell("O8", SMIC, SH, 100_001, "100.00"), CONTINUOUS)
        self.assertFalse(res.accepted)
        self.assertEqual(res.rejection_code, REJECT_ORDER_SIZE_EXCEEDED)


# ---------------------------------------------------------------------------
# Order type, tick size, price limit -- §3.8, §3.9, §3.11
# ---------------------------------------------------------------------------

class TestPriceAndOrderTypeRules(BaseEngineTest):

    def test_market_orders_are_rejected(self):
        order = buy("O9", MOUTAI, SH, 100, "1700.00", is_market_order=True)
        res = self.engine.submit_order(order, CONTINUOUS)
        self.assertFalse(res.accepted)
        self.assertEqual(res.rejection_code, REJECT_ORDER_TYPE_NOT_SUPPORTED)

    def test_sub_tick_price_is_rejected_for_an_a_share(self):
        res = self.engine.submit_order(buy("O10", MOUTAI, SH, 100, "1700.005"), CONTINUOUS)
        self.assertFalse(res.accepted)
        self.assertEqual(res.rejection_code, REJECT_INVALID_TICK_SIZE)

    def test_float_prices_are_refused_at_construction(self):
        # Decimal(0.01) is not 0.01, so a float price would make the tick check
        # depend on how the caller spelled the number.
        with self.assertRaises(ConnectRuleError):
            ConnectOrder(
                order_id="O11", symbol=MOUTAI, channel=SH, side=OrderSide.BUY,
                quantity=100, limit_price=1700.00,
            )

    def test_main_board_price_limits_are_plus_minus_10_percent(self):
        # Independently derived: 1700.00 * 0.90 = 1530.00, * 1.10 = 1870.00.
        lower, upper = self.engine.price_limits(MOUTAI)
        self.assertEqual(lower, Decimal("1530.00"))
        self.assertEqual(upper, Decimal("1870.00"))

    def test_star_and_chinext_price_limits_are_plus_minus_20_percent(self):
        self.assertEqual(self.engine.price_limits(SMIC), (Decimal("80.00"), Decimal("120.00")))
        self.assertEqual(self.engine.price_limits(CATL), (Decimal("160.00"), Decimal("240.00")))

    def test_order_outside_the_price_limit_is_rejected_on_both_sides(self):
        high = self.engine.submit_order(buy("O12", MOUTAI, SH, 100, "1870.01"), CONTINUOUS)
        self.assertEqual(high.rejection_code, REJECT_PRICE_LIMIT_BREACH)
        self.engine.start_trading_day({MOUTAI: 100})
        low = self.engine.submit_order(sell("O13", MOUTAI, SH, 100, "1529.99"), CONTINUOUS)
        self.assertEqual(low.rejection_code, REJECT_PRICE_LIMIT_BREACH)

    def test_price_exactly_on_the_limit_is_accepted(self):
        res = self.engine.submit_order(buy("O14", MOUTAI, SH, 100, "1870.00"), CONTINUOUS)
        self.assertTrue(res.accepted, res.rejection_reason)


# ---------------------------------------------------------------------------
# Routing and eligibility
# ---------------------------------------------------------------------------

class TestRoutingAndEligibility(BaseEngineTest):

    def test_shanghai_symbol_routed_over_shenzhen_connect_is_rejected(self):
        # REGRESSION: the prior engine accepted this and debited the wrong
        # channel's quota, since each channel carries a separate RMB 52bn pool.
        res = self.engine.submit_order(buy("O15", MOUTAI, SZ, 100, "1700.00"), CONTINUOUS)
        self.assertFalse(res.accepted)
        self.assertEqual(res.rejection_code, REJECT_CHANNEL_SYMBOL_MISMATCH)
        self.assertEqual(self.engine.daily_quota_balance(SZ), NORTHBOUND_DAILY_QUOTA_RMB)
        self.assertEqual(self.engine.daily_quota_balance(SH), NORTHBOUND_DAILY_QUOTA_RMB)

    def test_unregistered_security_is_rejected_not_waved_through(self):
        res = self.engine.submit_order(buy("O16", "601398.SH", SH, 100, "5.00"), CONTINUOUS)
        self.assertFalse(res.accepted)
        self.assertEqual(res.rejection_code, REJECT_SECURITY_NOT_REGISTERED)

    def test_sell_only_security_blocks_buys_but_not_sells(self):
        self.engine.register_security(WULIANGYE, "150.00", buy_eligible=False)
        self.engine.start_trading_day({WULIANGYE: 500})
        blocked = self.engine.submit_order(buy("O17", WULIANGYE, SZ, 100, "150.00"), CONTINUOUS)
        self.assertEqual(blocked.rejection_code, REJECT_SECURITY_NOT_BUY_ELIGIBLE)
        allowed = self.engine.submit_order(sell("O18", WULIANGYE, SZ, 500, "150.00"), CONTINUOUS)
        self.assertTrue(allowed.accepted, allowed.rejection_reason)


# ---------------------------------------------------------------------------
# Foreign shareholding -- §3.20
# ---------------------------------------------------------------------------

class TestForeignShareholdingLatch(BaseEngineTest):

    def test_buying_suspends_at_28_percent_and_resumes_only_at_26(self):
        self.assertEqual(FOREIGN_OWNERSHIP_SUSPEND_PCT, Decimal("28"))
        self.assertEqual(FOREIGN_OWNERSHIP_RESUME_PCT, Decimal("26"))

        self.assertFalse(self.engine.set_foreign_shareholding(MOUTAI, "27.9"))
        self.assertTrue(self.engine.set_foreign_shareholding(MOUTAI, "28.0"))

        blocked = self.engine.submit_order(buy("O19", MOUTAI, SH, 100, "1700.00"), CONTINUOUS)
        self.assertEqual(blocked.rejection_code, REJECT_FOREIGN_OWNERSHIP_SUSPENDED)

        # 27% is below the suspend threshold but above the resume threshold, so
        # the hysteresis must keep buying suspended.
        self.assertTrue(self.engine.set_foreign_shareholding(MOUTAI, "27.0"))
        still = self.engine.submit_order(buy("O20", MOUTAI, SH, 100, "1700.00"), CONTINUOUS)
        self.assertEqual(still.rejection_code, REJECT_FOREIGN_OWNERSHIP_SUSPENDED)

        self.assertFalse(self.engine.set_foreign_shareholding(MOUTAI, "26.0"))
        resumed = self.engine.submit_order(buy("O21", MOUTAI, SH, 100, "1700.00"), CONTINUOUS)
        self.assertTrue(resumed.accepted, resumed.rejection_reason)

    def test_selling_continues_while_buying_is_suspended(self):
        self.engine.start_trading_day({MOUTAI: 100})
        self.engine.set_foreign_shareholding(MOUTAI, "30")
        res = self.engine.submit_order(sell("O22", MOUTAI, SH, 100, "1700.00"), CONTINUOUS)
        self.assertTrue(res.accepted, res.rejection_reason)

    def test_percentage_out_of_range_raises(self):
        for bad in ["-1", "101"]:
            with self.subTest(pct=bad):
                with self.assertRaises(ConnectRuleError):
                    self.engine.set_foreign_shareholding(MOUTAI, bad)


# ---------------------------------------------------------------------------
# Pre-trade checking / no day trading -- §3.12, §3.19
# ---------------------------------------------------------------------------

class TestPreTradeChecking(BaseEngineTest):

    def test_shares_bought_today_cannot_be_sold_today(self):
        self.engine.start_trading_day({})
        bought = self.engine.submit_order(buy("B1", MOUTAI, SH, 100, "1700.00"), CONTINUOUS)
        self.assertTrue(bought.accepted, bought.rejection_reason)
        self.engine.record_fill("B1", 100)

        # The fill does not raise the market-open position, which is what
        # actually prohibits day trading.
        self.assertEqual(self.engine.sellable_quantity(MOUTAI), 0)
        res = self.engine.submit_order(sell("S1", MOUTAI, SH, 100, "1700.00"), CONTINUOUS)
        self.assertFalse(res.accepted)
        self.assertEqual(res.rejection_code, REJECT_PRE_TRADE_CHECK_FAILED)

    def test_sell_with_no_recorded_position_is_rejected(self):
        # REGRESSION: the prior engine's day-trading check keyed off an optional
        # purchase date, so a sell that simply omitted it passed unchecked.
        res = self.engine.submit_order(sell("S2", MOUTAI, SH, 100, "1700.00"), CONTINUOUS)
        self.assertFalse(res.accepted)
        self.assertEqual(res.rejection_code, REJECT_PRE_TRADE_CHECK_FAILED)

    def test_cumulative_sells_are_checked_against_the_market_open_position(self):
        # REGRESSION: the prior engine tracked no cumulative quantity, so a
        # 1,000-share holding could be sold an unlimited number of times.
        self.engine.start_trading_day({MOUTAI: 1_000})
        first = self.engine.submit_order(sell("S3", MOUTAI, SH, 600, "1700.00"), CONTINUOUS)
        self.assertTrue(first.accepted, first.rejection_reason)
        self.assertEqual(self.engine.sellable_quantity(MOUTAI), 400)

        second = self.engine.submit_order(sell("S4", MOUTAI, SH, 500, "1700.00"), CONTINUOUS)
        self.assertFalse(second.accepted)
        self.assertEqual(second.rejection_code, REJECT_PRE_TRADE_CHECK_FAILED)

        exact = self.engine.submit_order(sell("S5", MOUTAI, SH, 400, "1700.00"), CONTINUOUS)
        self.assertTrue(exact.accepted, exact.rejection_reason)
        self.assertEqual(self.engine.sellable_quantity(MOUTAI), 0)

    def test_cancelling_a_sell_returns_its_headroom(self):
        self.engine.start_trading_day({MOUTAI: 1_000})
        self.engine.submit_order(sell("S6", MOUTAI, SH, 1_000, "1700.00"), CONTINUOUS)
        self.assertEqual(self.engine.sellable_quantity(MOUTAI), 0)
        self.engine.cancel_order("S6")
        self.assertEqual(self.engine.sellable_quantity(MOUTAI), 1_000)

    def test_partial_sell_fill_then_cancel_keeps_the_filled_part_consumed(self):
        self.engine.start_trading_day({MOUTAI: 1_000})
        self.engine.submit_order(sell("S7", MOUTAI, SH, 1_000, "1700.00"), CONTINUOUS)
        self.engine.record_fill("S7", 300)
        self.engine.cancel_order("S7")
        # 300 shares are gone for good; the 700 unfilled return to headroom.
        self.assertEqual(self.engine.sellable_quantity(MOUTAI), 700)

    def test_submitting_before_start_of_day_raises(self):
        engine = ShanghaiShenzhenConnectEngine()
        engine.register_security(MOUTAI, "1700.00")
        with self.assertRaises(ConnectRuleError):
            engine.submit_order(buy("S8", MOUTAI, SH, 100, "1700.00"), CONTINUOUS)

    def test_negative_opening_position_raises(self):
        with self.assertRaises(ConnectRuleError):
            self.engine.start_trading_day({MOUTAI: -100})


# ---------------------------------------------------------------------------
# Daily Quota -- §3.4
# ---------------------------------------------------------------------------

class TestDailyQuota(BaseEngineTest):

    def test_quota_is_52_billion_per_channel_and_channels_are_independent(self):
        self.assertEqual(NORTHBOUND_DAILY_QUOTA_RMB, Decimal("52000000000"))
        self.engine.submit_order(buy("Q1", MOUTAI, SH, 100, "1700.00"), CONTINUOUS)
        # 100 * 1700 = 170,000 RMB, derived by hand.
        self.assertEqual(
            self.engine.daily_quota_balance(SH),
            Decimal("52000000000") - Decimal("170000"),
        )
        self.assertEqual(self.engine.daily_quota_balance(SZ), Decimal("52000000000"))

    def test_quota_is_consumed_by_buy_orders_not_by_buy_trades(self):
        before = self.engine.daily_quota_balance(SH)
        self.engine.submit_order(buy("Q2", MOUTAI, SH, 100, "1700.00"), CONTINUOUS)
        after_submit = self.engine.daily_quota_balance(SH)
        self.assertEqual(before - after_submit, Decimal("170000"))
        # The fill must not double-count: the value was consumed at submission.
        self.engine.record_fill("Q2", 100)
        self.assertEqual(self.engine.daily_quota_balance(SH), after_submit)

    def test_cancelling_a_buy_releases_only_the_unfilled_notional(self):
        self.engine.submit_order(buy("Q3", MOUTAI, SH, 1_000, "1700.00"), CONTINUOUS)
        self.engine.record_fill("Q3", 400)
        self.engine.cancel_order("Q3")
        # 400 shares stay bought (400 * 1700 = 680,000); 600 * 1700 is released.
        self.assertEqual(
            self.engine.daily_quota_balance(SH),
            Decimal("52000000000") - Decimal("680000"),
        )

    def test_quota_is_restored_by_sell_trades_not_by_sell_orders(self):
        # REGRESSION: the prior engine credited the quota the moment a sell order
        # was accepted, manufacturing buying power SEHK never granted.
        self.engine.start_trading_day({MOUTAI: 1_000})
        baseline = self.engine.daily_quota_balance(SH)
        self.engine.submit_order(sell("Q4", MOUTAI, SH, 1_000, "1700.00"), CONTINUOUS)
        self.assertEqual(self.engine.daily_quota_balance(SH), baseline)

        self.engine.record_fill("Q4", 1_000)
        self.assertEqual(
            self.engine.daily_quota_balance(SH), baseline + Decimal("1700000")
        )

    def test_net_sell_day_lifts_the_balance_above_the_daily_quota(self):
        # REGRESSION: the prior engine clamped the balance at the Daily Quota,
        # silently discarding headroom the "net buy" basis creates.
        self.engine.start_trading_day({MOUTAI: 1_000})
        self.engine.submit_order(sell("Q5", MOUTAI, SH, 1_000, "1700.00"), CONTINUOUS)
        self.engine.record_fill("Q5", 1_000)
        self.assertGreater(self.engine.daily_quota_balance(SH), NORTHBOUND_DAILY_QUOTA_RMB)

    def test_the_order_that_exhausts_the_quota_is_accepted(self):
        # REGRESSION: the prior engine rejected any buy whose notional exceeded
        # the balance, which both refuses an order SEHK accepts and makes the
        # negative balance HKEX describes ("or the Daily Quota is exceeded")
        # unreachable.
        self.engine.apply_quota_adjustment(SH, "-51999000000", "test: near-exhaust")
        self.assertEqual(self.engine.daily_quota_balance(SH), Decimal("1000000"))

        res = self.engine.submit_order(buy("Q6", MOUTAI, SH, 1_000, "1700.00"), CONTINUOUS)
        self.assertTrue(res.accepted, res.rejection_reason)
        # 1,000 * 1,700 = 1,700,000 against 1,000,000 available -> -700,000.
        self.assertEqual(self.engine.daily_quota_balance(SH), Decimal("-700000"))
        self.assertTrue(res.northbound_buying_suspended)

    def test_exhaustion_in_continuous_auction_latches_for_the_day(self):
        self.engine.apply_quota_adjustment(SH, "-52000000000", "test: exhaust")
        self.engine.submit_order(buy("Q7", MOUTAI, SH, 100, "1700.00"), CONTINUOUS)
        self.assertTrue(self.engine.is_buying_suspended(SH))

        blocked = self.engine.submit_order(buy("Q8", MOUTAI, SH, 100, "1700.00"), CONTINUOUS)
        self.assertEqual(blocked.rejection_code, REJECT_QUOTA_EXHAUSTED)

        # A sell trade restoring the balance must NOT lift the day's suspension.
        self.engine.start_trading_day({MOUTAI: 1_000})
        self.engine.apply_quota_adjustment(SH, "-52000000000", "test: exhaust")
        self.engine.submit_order(buy("Q9", MOUTAI, SH, 100, "1700.00"), CONTINUOUS)
        self.assertTrue(self.engine.is_buying_suspended(SH))
        self.engine.submit_order(sell("Q10", MOUTAI, SH, 1_000, "1700.00"), CONTINUOUS)
        self.engine.record_fill("Q10", 1_000)
        self.assertGreater(self.engine.daily_quota_balance(SH), 0)
        still = self.engine.submit_order(buy("Q11", MOUTAI, SH, 100, "1700.00"), CONTINUOUS)
        self.assertEqual(still.rejection_code, REJECT_QUOTA_EXHAUSTED)

    def test_continuous_session_latch_survives_cancelling_the_exhausting_order(self):
        # The rule conditions resumption on the opening call auction only. In a
        # continuous session, restoring the balance -- by cancellation here, by a
        # sell trade above -- does not reopen buying.
        self.engine.apply_quota_adjustment(SH, "-51999830000", "test: near-exhaust")
        self.engine.submit_order(buy("Q19", MOUTAI, SH, 100, "1700.00"), CONTINUOUS)
        self.assertTrue(self.engine.is_buying_suspended(SH))

        self.engine.cancel_order("Q19")
        self.assertEqual(self.engine.daily_quota_balance(SH), Decimal("170000"))
        self.assertTrue(self.engine.is_buying_suspended(SH))
        blocked = self.engine.submit_order(buy("Q20", MOUTAI, SH, 100, "1700.00"), CONTINUOUS)
        self.assertEqual(blocked.rejection_code, REJECT_QUOTA_EXHAUSTED)

    def test_closing_call_auction_latches_like_the_continuous_session(self):
        self.engine.apply_quota_adjustment(SH, "-52000000000", "test: exhaust")
        self.engine.submit_order(buy("Q12", MOUTAI, SH, 100, "1700.00"), CLOSING)
        self.assertTrue(self.engine.is_buying_suspended(SH))

    def test_opening_call_auction_exhaustion_does_not_latch(self):
        # "as order cancellation is common during opening call auction, the
        # Northbound Daily Quota Balance may resume to a positive level [...]
        # SEHK will again accept Northbound buy orders."
        self.engine.apply_quota_adjustment(SH, "-51999830000", "test: near-exhaust")
        self.assertEqual(self.engine.daily_quota_balance(SH), Decimal("170000"))

        first = self.engine.submit_order(buy("Q13", MOUTAI, SH, 100, "1700.00"), OPENING)
        self.assertTrue(first.accepted, first.rejection_reason)
        self.assertEqual(self.engine.daily_quota_balance(SH), Decimal("0"))
        self.assertFalse(self.engine.is_buying_suspended(SH))

        blocked = self.engine.submit_order(buy("Q14", MOUTAI, SH, 100, "1700.00"), OPENING)
        self.assertEqual(blocked.rejection_code, REJECT_QUOTA_EXHAUSTED)

        self.engine.cancel_order("Q13")
        resumed = self.engine.submit_order(buy("Q15", MOUTAI, SH, 100, "1700.00"), OPENING)
        self.assertTrue(resumed.accepted, resumed.rejection_reason)

    def test_sells_are_accepted_regardless_of_quota_balance(self):
        # "investors are always allowed to sell their cross-boundary securities
        # regardless of the quota balance."
        self.engine.start_trading_day({MOUTAI: 1_000})
        self.engine.apply_quota_adjustment(SH, "-52000000000", "test: exhaust")
        self.engine.submit_order(buy("Q16", MOUTAI, SH, 100, "1700.00"), CONTINUOUS)
        self.assertTrue(self.engine.is_buying_suspended(SH))
        res = self.engine.submit_order(sell("Q17", MOUTAI, SH, 1_000, "1700.00"), CONTINUOUS)
        self.assertTrue(res.accepted, res.rejection_reason)

    def test_start_trading_day_resets_quota_and_the_suspension_latch(self):
        self.engine.apply_quota_adjustment(SH, "-52000000000", "test: exhaust")
        self.engine.submit_order(buy("Q18", MOUTAI, SH, 100, "1700.00"), CONTINUOUS)
        self.assertTrue(self.engine.is_buying_suspended(SH))

        self.engine.start_trading_day({})
        self.assertEqual(self.engine.daily_quota_balance(SH), NORTHBOUND_DAILY_QUOTA_RMB)
        self.assertFalse(self.engine.is_buying_suspended(SH))

    def test_quota_arithmetic_is_exact_at_full_scale(self):
        # 52bn is beyond float's exact-integer range once fractional RMB appear;
        # 520,000 fills of RMB 0.01 must land exactly on the Daily Quota.
        self.engine.register_security("600000.SH", "0.10", price_limit_pct="20")
        for i in range(1_000):
            self.engine.submit_order(
                buy(f"P{i}", "600000.SH", SH, 100, "0.11"), CONTINUOUS
            )
        self.assertEqual(
            self.engine.daily_quota_balance(SH),
            NORTHBOUND_DAILY_QUOTA_RMB - Decimal("11000"),
        )


# ---------------------------------------------------------------------------
# Order construction and lifecycle guards
# ---------------------------------------------------------------------------

class TestOrderValidation(BaseEngineTest):

    def test_side_must_be_the_enum(self):
        # REGRESSION: the prior engine took `side.upper() == "BUY"` and fell
        # through to the sell branch otherwise, so side="LONG" silently
        # *credited* the Daily Quota.
        with self.assertRaises(ConnectRuleError):
            ConnectOrder(
                order_id="X1", symbol=MOUTAI, channel=SH, side="LONG",
                quantity=100, limit_price=Decimal("1700.00"),
            )

    def test_invalid_quantities_and_prices_raise(self):
        for quantity in [0, -100, 100.0, True]:
            with self.subTest(quantity=quantity):
                with self.assertRaises(ConnectRuleError):
                    buy("X2", MOUTAI, SH, quantity, "1700.00")
        for price in ["0", "-1700.00", "not-a-price"]:
            with self.subTest(price=price):
                with self.assertRaises(ConnectRuleError):
                    ConnectOrder(
                        order_id="X3", symbol=MOUTAI, channel=SH, side=OrderSide.BUY,
                        quantity=100, limit_price=price,
                    )

    def test_nan_and_infinite_prices_are_refused(self):
        for price in [Decimal("NaN"), Decimal("Infinity")]:
            with self.subTest(price=price):
                with self.assertRaises(ConnectRuleError):
                    ConnectOrder(
                        order_id="X4", symbol=MOUTAI, channel=SH, side=OrderSide.BUY,
                        quantity=100, limit_price=price,
                    )

    def test_duplicate_live_order_id_raises(self):
        self.engine.submit_order(buy("D1", MOUTAI, SH, 100, "1700.00"), CONTINUOUS)
        with self.assertRaises(ConnectRuleError):
            self.engine.submit_order(buy("D1", MOUTAI, SH, 100, "1700.00"), CONTINUOUS)

    def test_a_rejected_order_reserves_nothing_and_can_be_resubmitted(self):
        bad = self.engine.submit_order(buy("R1", MOUTAI, SH, 150, "1700.00"), CONTINUOUS)
        self.assertFalse(bad.accepted)
        self.assertEqual(self.engine.daily_quota_balance(SH), NORTHBOUND_DAILY_QUOTA_RMB)
        good = self.engine.submit_order(buy("R1", MOUTAI, SH, 100, "1700.00"), CONTINUOUS)
        self.assertTrue(good.accepted, good.rejection_reason)

    def test_over_fill_and_unknown_order_fills_raise(self):
        self.engine.submit_order(buy("F1", MOUTAI, SH, 100, "1700.00"), CONTINUOUS)
        with self.assertRaises(ConnectRuleError):
            self.engine.record_fill("F1", 101)
        with self.assertRaises(ConnectRuleError):
            self.engine.record_fill("NOPE", 100)
        self.engine.record_fill("F1", 100)
        with self.assertRaises(ConnectRuleError):
            self.engine.cancel_order("F1")

    def test_quota_adjustment_requires_a_reason(self):
        with self.assertRaises(ConnectRuleError):
            self.engine.apply_quota_adjustment(SH, "-1000", "  ")


if __name__ == "__main__":
    unittest.main()
