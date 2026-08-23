import unittest

from deribit_crypto_options_api import (
    ACTION_BUY,
    ACTION_SELL,
    DeribitApiError,
    DeribitCryptoOptionsApiEngine,
    DeribitMarginQuote,
    DeribitOptionPosition,
    DeribitOptionTicker,
    DeribitOrderRequest,
    MAX_LABEL_LENGTH,
    OPTION_CALL,
    OPTION_PUT,
    WS_ENDPOINT_MAINNET,
    WS_ENDPOINT_TESTNET,
)

INSTRUMENT = "BTC-28MAR26-60000-C"


def make_ticker(**overrides) -> DeribitOptionTicker:
    kwargs = dict(
        instrument_name=INSTRUMENT,
        index_price_usd=60_000.0,
        mark_price_coin=0.05,
        mark_iv=65.0,
        delta=0.60,
        gamma=0.0001,
        vega=12.5,
        theta=-1.5,
        best_bid_price_coin=0.049,
        best_ask_price_coin=0.051,
    )
    kwargs.update(overrides)
    return DeribitOptionTicker(**kwargs)


def make_order(**overrides) -> DeribitOrderRequest:
    kwargs = dict(
        order_id="ORD_DERIBIT_01",
        instrument_name=INSTRUMENT,
        action=ACTION_BUY,
        amount_coin=10.0,
        price_coin=0.05,
    )
    kwargs.update(overrides)
    return DeribitOrderRequest(**kwargs)


class TestInstrumentParsing(unittest.TestCase):

    def setUp(self):
        self.engine = DeribitCryptoOptionsApiEngine()

    def test_parses_call_and_put(self):
        call = self.engine.parse_instrument_name(INSTRUMENT)
        self.assertEqual(call.base_currency, "BTC")
        self.assertEqual(call.expiry_date, "28MAR26")
        self.assertEqual(call.strike, 60_000.0)
        self.assertEqual(call.option_type, OPTION_CALL)
        self.assertEqual(call.quote_currency, "USD")

        put = self.engine.parse_instrument_name("ETH-26JUN26-4000-P")
        self.assertEqual(put.base_currency, "ETH")
        self.assertEqual(put.option_type, OPTION_PUT)
        self.assertEqual(put.strike, 4_000.0)

    def test_unknown_option_type_suffix_raises_instead_of_defaulting_to_put(self):
        # Regression: the old parser used `"call" if parts[3] == "C" else "put"`,
        # so any typo silently produced a PUT on a live order path.
        with self.assertRaises(DeribitApiError):
            self.engine.parse_instrument_name("BTC-28MAR26-60000-X")

    def test_linear_usdc_option_is_rejected_not_parsed(self):
        # Linear options are quoted in the settlement currency; running them
        # through the inverse coin->USD conversion inflates the premium by the
        # index price.
        with self.assertRaises(DeribitApiError) as ctx:
            self.engine.parse_instrument_name("BTC_USDC-28MAR26-60000-C")
        self.assertIn("linear", str(ctx.exception).lower())

    def test_malformed_symbols_raise(self):
        for symbol in ("", "   ", "BTC-PERPETUAL", "BTC-28MAR26-60000",
                       "BTC-28MAR26-60000-C-EXTRA", "BTC-2026MAR28-60000-C"):
            with self.subTest(symbol=symbol):
                with self.assertRaises(DeribitApiError):
                    self.engine.parse_instrument_name(symbol)

    def test_symbol_is_normalised_to_upper_case(self):
        spec = self.engine.parse_instrument_name("btc-28mar26-60000-c")
        self.assertEqual(spec.instrument_name, INSTRUMENT)


class TestPremiumConversion(unittest.TestCase):

    def setUp(self):
        self.engine = DeribitCryptoOptionsApiEngine()

    def test_inverse_premium_to_usd(self):
        # 0.05 BTC per BTC of underlying at a $60,000 index = $3,000.
        self.assertEqual(self.engine.convert_inverse_premium_to_usd(0.05, 60_000.0), 3_000.0)

    def test_zero_premium_is_allowed(self):
        self.assertEqual(self.engine.convert_inverse_premium_to_usd(0.0, 60_000.0), 0.0)

    def test_invalid_conversion_inputs_raise(self):
        for price, index in ((-0.01, 60_000.0), (float("nan"), 60_000.0),
                             (0.05, 0.0), (0.05, -1.0), (0.05, float("inf"))):
            with self.subTest(price=price, index=index):
                with self.assertRaises(DeribitApiError):
                    self.engine.convert_inverse_premium_to_usd(price, index)


class TestJsonRpcPayload(unittest.TestCase):

    def setUp(self):
        self.engine = DeribitCryptoOptionsApiEngine()

    def test_order_payload_shape(self):
        payload = self.engine.format_json_rpc_order(make_order())
        self.assertEqual(payload["jsonrpc"], "2.0")
        self.assertEqual(payload["method"], "private/buy")
        params = payload["params"]
        self.assertEqual(params["instrument_name"], INSTRUMENT)
        self.assertEqual(params["amount"], 10.0)
        self.assertEqual(params["price"], 0.05)
        self.assertEqual(params["type"], "limit")
        self.assertEqual(params["time_in_force"], "good_til_cancelled")

    def test_order_id_is_sent_as_label_for_post_reconnect_lookup(self):
        # Regression: the old payload omitted `label` entirely, so an order
        # placed before a session-terminating 10028 could not be located and a
        # blind resend would duplicate it.
        payload = self.engine.format_json_rpc_order(make_order(order_id="HEDGE-42"))
        self.assertEqual(payload["params"]["label"], "HEDGE-42")

    def test_post_only_is_always_explicit(self):
        # Regression: Deribit defaults post_only to True. The old payload never
        # sent the field, so a "limit" order meant to cross rested instead.
        taker = self.engine.format_json_rpc_order(make_order(post_only=False))
        self.assertIs(taker["params"]["post_only"], False)
        maker = self.engine.format_json_rpc_order(make_order(post_only=True))
        self.assertIs(maker["params"]["post_only"], True)

    def test_market_orders_carry_no_price(self):
        payload = self.engine.format_json_rpc_order(
            make_order(order_type="market", price_coin=None))
        self.assertNotIn("price", payload["params"])

    def test_market_order_with_a_price_is_rejected(self):
        with self.assertRaises(DeribitApiError):
            self.engine.format_json_rpc_order(
                make_order(order_type="market", price_coin=0.05))

    def test_limit_order_without_a_price_is_rejected(self):
        with self.assertRaises(DeribitApiError):
            self.engine.format_json_rpc_order(make_order(price_coin=None))

    def test_request_ids_are_unique_per_engine(self):
        # Regression: the old signature defaulted request_id=1 for every call,
        # making responses on a multiplexed socket impossible to correlate.
        ids = [self.engine.format_json_rpc_order(make_order())["id"] for _ in range(5)]
        self.assertEqual(len(set(ids)), 5)

    def test_explicit_request_id_is_honoured(self):
        payload = self.engine.format_json_rpc_order(make_order(), request_id=99)
        self.assertEqual(payload["id"], 99)

    def test_sell_uses_private_sell(self):
        payload = self.engine.format_json_rpc_order(make_order(action=ACTION_SELL))
        self.assertEqual(payload["method"], "private/sell")

    def test_ticker_request_validates_the_instrument(self):
        payload = self.engine.format_ticker_request(INSTRUMENT)
        self.assertEqual(payload["method"], "public/ticker")
        self.assertEqual(payload["params"], {"instrument_name": INSTRUMENT})
        with self.assertRaises(DeribitApiError):
            self.engine.format_ticker_request("BTC-PERPETUAL")

    def test_optional_flags_are_emitted_only_when_set(self):
        plain = self.engine.format_json_rpc_order(make_order())["params"]
        self.assertNotIn("reject_post_only", plain)
        self.assertNotIn("valid_until", plain)

        rich = self.engine.format_json_rpc_order(
            make_order(reject_post_only=True, valid_until_ms=1_700_000_000_000))["params"]
        self.assertIs(rich["reject_post_only"], True)
        self.assertEqual(rich["valid_until"], 1_700_000_000_000)


class TestOrderValidation(unittest.TestCase):

    def setUp(self):
        self.engine = DeribitCryptoOptionsApiEngine()

    def test_label_longer_than_the_deribit_limit_is_rejected(self):
        with self.assertRaises(DeribitApiError):
            self.engine.format_json_rpc_order(
                make_order(order_id="X" * (MAX_LABEL_LENGTH + 1)))

    def test_label_at_the_limit_is_accepted(self):
        payload = self.engine.format_json_rpc_order(
            make_order(order_id="X" * MAX_LABEL_LENGTH))
        self.assertEqual(len(payload["params"]["label"]), MAX_LABEL_LENGTH)

    def test_invalid_enum_values_raise(self):
        for override in ({"action": "short"}, {"order_type": "iceberg"},
                         {"time_in_force": "gtc"}, {"order_id": ""}):
            with self.subTest(override=override):
                with self.assertRaises(DeribitApiError):
                    self.engine.format_json_rpc_order(make_order(**override))

    def test_non_positive_and_non_finite_sizes_and_prices_raise(self):
        for override in ({"amount_coin": 0.0}, {"amount_coin": -1.0},
                         {"amount_coin": float("nan")}, {"amount_coin": float("inf")},
                         {"price_coin": 0.0}, {"price_coin": -0.01},
                         {"price_coin": float("nan")}):
            with self.subTest(override=override):
                with self.assertRaises(DeribitApiError):
                    self.engine.format_json_rpc_order(make_order(**override))

    def test_invalid_utilisation_policy_raises(self):
        for value in (0.0, -0.1, 1.5, float("nan")):
            with self.subTest(value=value):
                with self.assertRaises(DeribitApiError):
                    DeribitCryptoOptionsApiEngine(max_equity_utilisation=value)


class TestBuySideGate(unittest.TestCase):

    def setUp(self):
        self.engine = DeribitCryptoOptionsApiEngine()
        self.ticker = make_ticker()

    def test_premium_and_delta_conversions(self):
        # Premium coin = 10 * 0.05 = 0.50 BTC; USD = 0.50 * 60,000 = $30,000.
        # Delta coin = 10 * 0.60 = 6.0 BTC; USD = 6.0 * 60,000 = $360,000.
        report = self.engine.process_option_order(
            make_order(), self.ticker, available_balance_coin=2.0)
        self.assertTrue(report.is_approved_for_dispatch)
        self.assertEqual(report.price_usd_equivalent, 3_000.0)
        self.assertEqual(report.total_premium_coin, 0.50)
        self.assertEqual(report.total_premium_usd, 30_000.0)
        self.assertEqual(report.position_delta_coin, 6.0)
        self.assertEqual(report.position_delta_usd, 360_000.0)

    def test_net_coin_delta_subtracts_the_premium_leg_for_a_buyer(self):
        # The buyer pays 0.50 BTC away, so coin exposure is 6.0 - 0.50 = 5.5 BTC,
        # derived independently of the engine's (delta - price) form.
        report = self.engine.process_option_order(
            make_order(), self.ticker, available_balance_coin=2.0)
        self.assertAlmostEqual(report.net_coin_delta_after_premium, 5.5, places=8)

    def test_net_coin_delta_adds_the_premium_leg_for_a_seller(self):
        # The seller receives 0.50 BTC, so coin exposure is -6.0 + 0.50 = -5.5.
        report = self.engine.process_option_order(
            make_order(action=ACTION_SELL), self.ticker,
            available_balance_coin=10.0,
            margin_quote=DeribitMarginQuote(initial_margin_coin=1.0))
        self.assertAlmostEqual(report.net_coin_delta_after_premium, -5.5, places=8)

    def test_buy_requirement_includes_the_commission(self):
        # Regression: the old check compared premium alone against the balance.
        # Premium 0.50 + fee 0.003 = 0.503 BTC, which exceeds a 0.502 balance.
        report = self.engine.process_option_order(
            make_order(), self.ticker, available_balance_coin=0.502,
            margin_quote=DeribitMarginQuote(fee_coin=0.003))
        self.assertEqual(report.required_coin, 0.503)
        self.assertFalse(report.is_approved_for_dispatch)

    def test_insufficient_balance_is_rejected_with_a_reason(self):
        report = self.engine.process_option_order(
            make_order(), self.ticker, available_balance_coin=0.10)
        self.assertFalse(report.is_approved_for_dispatch)
        self.assertTrue(any("Insufficient balance" in r for r in report.rejection_reasons))

    def test_missing_margin_quote_warns_that_fees_are_unmodelled(self):
        report = self.engine.process_option_order(
            make_order(), self.ticker, available_balance_coin=2.0)
        self.assertTrue(any("commission is treated as zero" in w for w in report.warnings))


class TestSellSideMarginGate(unittest.TestCase):
    """Short options must not be approved without an exchange margin figure."""

    def setUp(self):
        self.engine = DeribitCryptoOptionsApiEngine()
        self.ticker = make_ticker()

    def test_sell_without_margin_quote_is_rejected(self):
        # Regression: the old engine checked balance for buys only, so this
        # naked short was approved with is_dispatched=True and no margin at all.
        report = self.engine.process_option_order(
            make_order(action=ACTION_SELL), self.ticker,
            available_balance_coin=100.0)
        self.assertFalse(report.is_approved_for_dispatch)
        self.assertTrue(any("private/get_margins" in r for r in report.rejection_reasons))

    def test_sell_with_sufficient_margin_is_approved(self):
        report = self.engine.process_option_order(
            make_order(action=ACTION_SELL), self.ticker,
            available_balance_coin=10.0,
            margin_quote=DeribitMarginQuote(initial_margin_coin=1.5, fee_coin=0.003))
        self.assertTrue(report.is_approved_for_dispatch)
        self.assertEqual(report.required_coin, 1.503)
        self.assertEqual(report.initial_margin_coin, 1.5)

    def test_sell_exceeding_balance_is_rejected(self):
        report = self.engine.process_option_order(
            make_order(action=ACTION_SELL), self.ticker,
            available_balance_coin=1.0,
            margin_quote=DeribitMarginQuote(initial_margin_coin=1.5))
        self.assertFalse(report.is_approved_for_dispatch)

    def test_negative_margin_quote_raises(self):
        with self.assertRaises(DeribitApiError):
            self.engine.process_option_order(
                make_order(action=ACTION_SELL), self.ticker,
                available_balance_coin=10.0,
                margin_quote=DeribitMarginQuote(initial_margin_coin=-1.0))


class TestUtilisationPolicy(unittest.TestCase):

    def test_order_within_balance_but_over_policy_is_rejected(self):
        # Regression: standards.md documented an 80% ceiling that no code
        # enforced. Premium 0.50 BTC against a 0.60 BTC balance is 83%.
        engine = DeribitCryptoOptionsApiEngine(max_equity_utilisation=0.80)
        report = engine.process_option_order(
            make_order(), make_ticker(), available_balance_coin=0.60)
        self.assertFalse(report.is_approved_for_dispatch)
        self.assertTrue(any("utilisation policy" in r for r in report.rejection_reasons))

    def test_policy_can_be_disabled(self):
        engine = DeribitCryptoOptionsApiEngine(max_equity_utilisation=1.0)
        report = engine.process_option_order(
            make_order(), make_ticker(), available_balance_coin=0.60)
        self.assertTrue(report.is_approved_for_dispatch)

    def test_order_exactly_at_the_policy_ceiling_is_approved(self):
        # 0.50 BTC required against a 0.625 BTC balance is exactly 80%.
        engine = DeribitCryptoOptionsApiEngine(max_equity_utilisation=0.80)
        report = engine.process_option_order(
            make_order(), make_ticker(), available_balance_coin=0.625)
        self.assertTrue(report.is_approved_for_dispatch)


class TestPostOnlyAndPriceBand(unittest.TestCase):

    def setUp(self):
        self.engine = DeribitCryptoOptionsApiEngine(max_equity_utilisation=1.0)
        self.ticker = make_ticker()

    def test_crossing_post_only_buy_is_warned_about(self):
        report = self.engine.process_option_order(
            make_order(price_coin=0.052, post_only=True), self.ticker,
            available_balance_coin=5.0)
        self.assertTrue(report.is_approved_for_dispatch)
        self.assertTrue(any("reprice it away" in w for w in report.warnings))

    def test_passive_post_only_buy_is_not_warned_about(self):
        report = self.engine.process_option_order(
            make_order(price_coin=0.048, post_only=True), self.ticker,
            available_balance_coin=5.0)
        self.assertFalse(any("reprice it away" in w for w in report.warnings))

    def test_crossing_order_with_post_only_disabled_is_not_warned_about(self):
        report = self.engine.process_option_order(
            make_order(price_coin=0.052, post_only=False), self.ticker,
            available_balance_coin=5.0)
        self.assertFalse(any("reprice it away" in w for w in report.warnings))

    def test_price_outside_the_venue_band_is_rejected(self):
        low = self.engine.process_option_order(
            make_order(price_coin=0.001), self.ticker, available_balance_coin=5.0,
            margin_quote=DeribitMarginQuote(min_price_coin=0.01, max_price_coin=0.10))
        self.assertFalse(low.is_approved_for_dispatch)

        high = self.engine.process_option_order(
            make_order(price_coin=0.5), self.ticker, available_balance_coin=50.0,
            margin_quote=DeribitMarginQuote(min_price_coin=0.01, max_price_coin=0.10))
        self.assertFalse(high.is_approved_for_dispatch)


class TestTickerIntegrity(unittest.TestCase):

    def setUp(self):
        self.engine = DeribitCryptoOptionsApiEngine()

    def test_ticker_for_a_different_instrument_raises(self):
        wrong = make_ticker(instrument_name="ETH-26JUN26-4000-P")
        with self.assertRaises(DeribitApiError):
            self.engine.process_option_order(
                make_order(), wrong, available_balance_coin=5.0)

    def test_non_finite_ticker_fields_raise(self):
        for override in ({"index_price_usd": float("nan")}, {"index_price_usd": 0.0},
                         {"delta": float("nan")}, {"gamma": float("inf")},
                         {"mark_price_coin": float("nan")}):
            with self.subTest(override=override):
                with self.assertRaises(DeribitApiError):
                    self.engine.process_option_order(
                        make_order(), make_ticker(**override),
                        available_balance_coin=5.0)

    def test_out_of_range_delta_raises(self):
        with self.assertRaises(DeribitApiError):
            self.engine.process_option_order(
                make_order(), make_ticker(delta=1.4), available_balance_coin=5.0)

    def test_market_order_estimates_from_mark_and_says_so(self):
        report = self.engine.process_option_order(
            make_order(order_type="market", price_coin=None), make_ticker(),
            available_balance_coin=5.0)
        self.assertIsNone(report.price_usd_equivalent)
        self.assertEqual(report.total_premium_coin, 0.50)  # from mark 0.05
        self.assertTrue(any("estimated from mark price" in w for w in report.warnings))


class TestPortfolioGreeks(unittest.TestCase):

    def setUp(self):
        self.engine = DeribitCryptoOptionsApiEngine()

    def test_aggregates_signed_greeks_across_positions(self):
        long_call = DeribitOptionPosition(make_ticker(), size_coin=10.0)
        short_put = DeribitOptionPosition(
            make_ticker(instrument_name="BTC-26JUN26-50000-P", delta=-0.30,
                        gamma=0.0002, vega=8.0, theta=-0.9, mark_price_coin=0.02),
            size_coin=-4.0)
        agg = self.engine.aggregate_portfolio_greeks([long_call, short_put])

        # Delta: 10*0.60 + (-4)*(-0.30) = 6.0 + 1.2 = 7.2 BTC -> $432,000.
        self.assertAlmostEqual(agg.delta_coin, 7.2, places=8)
        self.assertAlmostEqual(agg.delta_usd, 432_000.0, places=2)
        # Gamma: 10*0.0001 + (-4)*0.0002 = 0.001 - 0.0008 = 0.0002.
        self.assertAlmostEqual(agg.gamma_coin, 0.0002, places=8)
        # Vega: 10*12.5 + (-4)*8.0 = 125 - 32 = 93.
        self.assertAlmostEqual(agg.vega_coin, 93.0, places=8)
        # Theta: 10*(-1.5) + (-4)*(-0.9) = -15 + 3.6 = -11.4.
        self.assertAlmostEqual(agg.theta_coin, -11.4, places=8)
        self.assertEqual(agg.position_count, 2)

    def test_net_coin_delta_accounts_for_each_premium(self):
        # 10*(0.60-0.05) + (-4)*(-0.30-0.02) = 5.5 + 1.28 = 6.78.
        positions = [
            DeribitOptionPosition(make_ticker(), size_coin=10.0),
            DeribitOptionPosition(
                make_ticker(instrument_name="BTC-26JUN26-50000-P", delta=-0.30,
                            mark_price_coin=0.02),
                size_coin=-4.0),
        ]
        agg = self.engine.aggregate_portfolio_greeks(positions)
        self.assertAlmostEqual(agg.net_coin_delta_after_premium, 6.78, places=8)

    def test_empty_portfolio_is_all_zero(self):
        agg = self.engine.aggregate_portfolio_greeks([])
        self.assertEqual(agg.position_count, 0)
        self.assertEqual(agg.delta_coin, 0.0)
        self.assertEqual(agg.delta_usd, 0.0)

    def test_mixed_index_prices_raise(self):
        positions = [
            DeribitOptionPosition(make_ticker(), size_coin=1.0),
            DeribitOptionPosition(make_ticker(index_price_usd=61_000.0), size_coin=1.0),
        ]
        with self.assertRaises(DeribitApiError):
            self.engine.aggregate_portfolio_greeks(positions)


class TestEndpointSelection(unittest.TestCase):

    def test_testnet_is_the_default(self):
        self.assertEqual(
            DeribitCryptoOptionsApiEngine().endpoint_url, WS_ENDPOINT_TESTNET)

    def test_mainnet_is_opt_in(self):
        self.assertEqual(
            DeribitCryptoOptionsApiEngine(is_testnet=False).endpoint_url,
            WS_ENDPOINT_MAINNET)


if __name__ == "__main__":
    unittest.main()
