import unittest

from coinbase_advanced_trade_api_migration import (
    AdvancedTradeOrderRejected,
    CoinbaseAdvancedTradeAdapter,
    LegacyProOrderRequest,
)


class TestLimitOrderTranslation(unittest.TestCase):

    def setUp(self):
        self.adapter = CoinbaseAdvancedTradeAdapter()

    def test_translate_limit_order(self):
        legacy_req = LegacyProOrderRequest(
            product_id="BTC-USD",
            side="buy",
            type="limit",
            size="0.25",
            price="65000.50",
            post_only=True,
            client_oid="MOCK_CLIENT_OID_123",
        )

        v3_payload = self.adapter.translate_order_request(legacy_req)

        self.assertEqual(v3_payload["client_order_id"], "MOCK_CLIENT_OID_123")
        self.assertEqual(v3_payload["product_id"], "BTC-USD")
        self.assertEqual(v3_payload["side"], "BUY")  # converted to upper
        self.assertEqual(list(v3_payload["order_configuration"]), ["limit_limit_gtc"])

        limit_config = v3_payload["order_configuration"]["limit_limit_gtc"]
        self.assertEqual(limit_config["base_size"], "0.25")
        self.assertEqual(limit_config["limit_price"], "65000.50")  # trailing zero preserved
        self.assertTrue(limit_config["post_only"])

    def test_limit_fok_maps_to_limit_limit_fok(self):
        """A legacy FOK limit order must not become a resting GTC order."""
        legacy_req = LegacyProOrderRequest(
            product_id="BTC-USD", side="buy", type="limit", size="1", price="50000",
            time_in_force="fok", client_oid="FOK-1",
        )
        config = self.adapter.translate_order_request(legacy_req)["order_configuration"]

        self.assertEqual(list(config), ["limit_limit_fok"])
        self.assertNotIn("post_only", config["limit_limit_fok"])

    def test_limit_gtt_maps_to_gtd_with_end_time(self):
        legacy_req = LegacyProOrderRequest(
            product_id="BTC-USD", side="sell", type="limit", size="1", price="50000",
            time_in_force="GTT", end_time="2026-08-21T15:00:00Z", client_oid="GTT-1",
        )
        config = self.adapter.translate_order_request(legacy_req)["order_configuration"]

        self.assertEqual(list(config), ["limit_limit_gtd"])
        self.assertEqual(config["limit_limit_gtd"]["end_time"], "2026-08-21T15:00:00Z")

    def test_limit_gtt_without_end_time_is_rejected(self):
        legacy_req = LegacyProOrderRequest(
            product_id="BTC-USD", side="sell", type="limit", size="1", price="50000",
            time_in_force="GTT", client_oid="GTT-2",
        )
        with self.assertRaises(ValueError) as ctx:
            self.adapter.translate_order_request(legacy_req)
        self.assertIn("end_time", str(ctx.exception))

    def test_limit_ioc_is_rejected_rather_than_downgraded_to_gtc(self):
        """
        Regression: dropping time_in_force turned an IOC order into a resting
        GTC order, leaving unintended exposure on the book.
        """
        legacy_req = LegacyProOrderRequest(
            product_id="BTC-USD", side="buy", type="limit", size="1", price="50000",
            time_in_force="IOC", client_oid="IOC-1",
        )
        with self.assertRaises(ValueError) as ctx:
            self.adapter.translate_order_request(legacy_req)
        self.assertIn("sor_limit_ioc", str(ctx.exception))

    def test_post_only_with_fok_is_rejected(self):
        legacy_req = LegacyProOrderRequest(
            product_id="BTC-USD", side="buy", type="limit", size="1", price="50000",
            time_in_force="FOK", post_only=True, client_oid="FOK-2",
        )
        with self.assertRaises(ValueError):
            self.adapter.translate_order_request(legacy_req)

    def test_limit_order_without_size_is_rejected(self):
        legacy_req = LegacyProOrderRequest(
            product_id="BTC-USD", side="buy", type="limit", price="50000", client_oid="L-1",
        )
        with self.assertRaises(ValueError):
            self.adapter.translate_order_request(legacy_req)


class TestMarketOrderTranslation(unittest.TestCase):

    def setUp(self):
        self.adapter = CoinbaseAdvancedTradeAdapter()

    def test_translate_market_sell_uses_base_size(self):
        legacy_req = LegacyProOrderRequest(
            product_id="ETH-USD", side="sell", type="market", size="1.5", client_oid="M-1",
        )
        v3_payload = self.adapter.translate_order_request(legacy_req)

        self.assertEqual(v3_payload["side"], "SELL")
        self.assertEqual(
            v3_payload["order_configuration"], {"market_market_ioc": {"base_size": "1.5"}}
        )

    def test_market_buy_with_funds_maps_to_quote_size(self):
        """Legacy 'funds' is a quote amount and must never land in base_size."""
        legacy_req = LegacyProOrderRequest(
            product_id="BTC-USD", side="buy", type="market", funds="500.00", client_oid="M-2",
        )
        config = self.adapter.translate_order_request(legacy_req)["order_configuration"]

        self.assertEqual(config, {"market_market_ioc": {"quote_size": "500.00"}})

    def test_market_buy_with_size_maps_to_base_size(self):
        legacy_req = LegacyProOrderRequest(
            product_id="BTC-USD", side="buy", type="market", size="0.01", client_oid="M-3",
        )
        config = self.adapter.translate_order_request(legacy_req)["order_configuration"]

        self.assertEqual(config, {"market_market_ioc": {"base_size": "0.01"}})

    def test_market_sell_with_funds_is_rejected(self):
        legacy_req = LegacyProOrderRequest(
            product_id="BTC-USD", side="sell", type="market", funds="500", client_oid="M-4",
        )
        with self.assertRaises(ValueError):
            self.adapter.translate_order_request(legacy_req)

    def test_market_order_with_both_size_and_funds_is_rejected(self):
        legacy_req = LegacyProOrderRequest(
            product_id="BTC-USD", side="buy", type="market", size="1", funds="500", client_oid="M-5",
        )
        with self.assertRaises(ValueError):
            self.adapter.translate_order_request(legacy_req)

    def test_market_order_without_any_size_is_rejected(self):
        legacy_req = LegacyProOrderRequest(
            product_id="BTC-USD", side="buy", type="market", client_oid="M-6",
        )
        with self.assertRaises(ValueError):
            self.adapter.translate_order_request(legacy_req)

    def test_post_only_market_order_is_rejected(self):
        legacy_req = LegacyProOrderRequest(
            product_id="BTC-USD", side="buy", type="market", size="1",
            post_only=True, client_oid="M-7",
        )
        with self.assertRaises(ValueError):
            self.adapter.translate_order_request(legacy_req)


class TestStopOrderTranslation(unittest.TestCase):

    def setUp(self):
        self.adapter = CoinbaseAdvancedTradeAdapter()

    def test_stop_loss_maps_to_stop_down(self):
        legacy_req = LegacyProOrderRequest(
            product_id="BTC-USD", side="sell", type="stop", size="0.5",
            price="49000", stop_price="49500", stop="loss", client_oid="S-1",
        )
        config = self.adapter.translate_order_request(legacy_req)["order_configuration"]

        self.assertEqual(list(config), ["stop_limit_stop_limit_gtc"])
        leg = config["stop_limit_stop_limit_gtc"]
        self.assertEqual(leg["stop_direction"], "STOP_DIRECTION_STOP_DOWN")
        self.assertEqual(leg["stop_price"], "49500")
        self.assertEqual(leg["limit_price"], "49000")
        self.assertEqual(leg["base_size"], "0.5")

    def test_sell_stop_entry_maps_to_stop_up(self):
        """
        Regression: stop_direction was derived from side (SELL -> STOP_DOWN),
        which inverts the trigger on a sell stop-entry sitting above the market.
        """
        legacy_req = LegacyProOrderRequest(
            product_id="BTC-USD", side="sell", type="stop", size="0.5",
            price="71000", stop_price="70500", stop="entry", client_oid="S-2",
        )
        config = self.adapter.translate_order_request(legacy_req)["order_configuration"]

        self.assertEqual(
            config["stop_limit_stop_limit_gtc"]["stop_direction"], "STOP_DIRECTION_STOP_UP"
        )

    def test_buy_stop_loss_maps_to_stop_down(self):
        """The mirror case: a BUY whose legacy stop kind is 'loss' triggers downward."""
        legacy_req = LegacyProOrderRequest(
            product_id="BTC-USD", side="buy", type="stop", size="0.5",
            price="49000", stop_price="49500", stop="loss", client_oid="S-3",
        )
        config = self.adapter.translate_order_request(legacy_req)["order_configuration"]

        self.assertEqual(
            config["stop_limit_stop_limit_gtc"]["stop_direction"], "STOP_DIRECTION_STOP_DOWN"
        )

    def test_stop_order_without_stop_kind_is_rejected(self):
        legacy_req = LegacyProOrderRequest(
            product_id="BTC-USD", side="sell", type="stop", size="0.5",
            price="49000", stop_price="49500", client_oid="S-4",
        )
        with self.assertRaises(ValueError) as ctx:
            self.adapter.translate_order_request(legacy_req)
        self.assertIn("stop_direction", str(ctx.exception))

    def test_stop_gtt_maps_to_stop_limit_gtd(self):
        legacy_req = LegacyProOrderRequest(
            product_id="BTC-USD", side="sell", type="stop", size="0.5",
            price="49000", stop_price="49500", stop="loss",
            time_in_force="GTT", end_time="2026-08-21T15:00:00Z", client_oid="S-5",
        )
        config = self.adapter.translate_order_request(legacy_req)["order_configuration"]

        self.assertEqual(list(config), ["stop_limit_stop_limit_gtd"])
        self.assertEqual(config["stop_limit_stop_limit_gtd"]["end_time"], "2026-08-21T15:00:00Z")

    def test_stop_fok_has_no_equivalent(self):
        legacy_req = LegacyProOrderRequest(
            product_id="BTC-USD", side="sell", type="stop", size="0.5",
            price="49000", stop_price="49500", stop="loss",
            time_in_force="FOK", client_oid="S-6",
        )
        with self.assertRaises(ValueError):
            self.adapter.translate_order_request(legacy_req)


class TestNumericFormattingAndValidation(unittest.TestCase):

    def setUp(self):
        self.adapter = CoinbaseAdvancedTradeAdapter()

    def test_small_float_size_is_not_serialized_in_scientific_notation(self):
        """str(1e-08) is '1e-08', which Advanced Trade does not accept as a size."""
        self.assertEqual(str(1e-08), "1e-08")  # guards the premise of this test
        legacy_req = LegacyProOrderRequest(
            product_id="BTC-USD", side="sell", type="market", size=1e-08, client_oid="N-1",
        )
        config = self.adapter.translate_order_request(legacy_req)["order_configuration"]

        self.assertEqual(config["market_market_ioc"]["base_size"], "0.00000001")

    def test_negative_size_is_rejected(self):
        legacy_req = LegacyProOrderRequest(
            product_id="BTC-USD", side="sell", type="market", size="-1", client_oid="N-2",
        )
        with self.assertRaises(ValueError):
            self.adapter.translate_order_request(legacy_req)

    def test_zero_size_is_rejected(self):
        legacy_req = LegacyProOrderRequest(
            product_id="BTC-USD", side="sell", type="market", size="0", client_oid="N-3",
        )
        with self.assertRaises(ValueError):
            self.adapter.translate_order_request(legacy_req)

    def test_nan_price_is_rejected(self):
        legacy_req = LegacyProOrderRequest(
            product_id="BTC-USD", side="buy", type="limit", size="1", price="NaN", client_oid="N-4",
        )
        with self.assertRaises(ValueError):
            self.adapter.translate_order_request(legacy_req)

    def test_non_numeric_price_is_rejected(self):
        legacy_req = LegacyProOrderRequest(
            product_id="BTC-USD", side="buy", type="limit", size="1",
            price="fifty thousand", client_oid="N-5",
        )
        with self.assertRaises(ValueError):
            self.adapter.translate_order_request(legacy_req)

    def test_invalid_side_is_rejected(self):
        legacy_req = LegacyProOrderRequest(
            product_id="BTC-USD", side="long", type="market", size="1", client_oid="N-6",
        )
        with self.assertRaises(ValueError):
            self.adapter.translate_order_request(legacy_req)

    def test_unknown_time_in_force_is_rejected(self):
        legacy_req = LegacyProOrderRequest(
            product_id="BTC-USD", side="buy", type="limit", size="1", price="50000",
            time_in_force="DAY", client_oid="N-7",
        )
        with self.assertRaises(ValueError):
            self.adapter.translate_order_request(legacy_req)

    def test_missing_client_oid_is_autogenerated_with_a_warning(self):
        legacy_req = LegacyProOrderRequest(
            product_id="BTC-USD", side="buy", type="market", size="1",
        )
        with self.assertLogs("coinbase_advanced_trade_api_migration", level="WARNING") as logs:
            payload = self.adapter.translate_order_request(legacy_req)

        self.assertTrue(payload["client_order_id"])
        self.assertIn("idempotent", "\n".join(logs.output))


class TestResponseParsing(unittest.TestCase):

    def setUp(self):
        self.adapter = CoinbaseAdvancedTradeAdapter()

    def test_parse_v3_response_success(self):
        mock_response = {
            "success": True,
            "success_response": {
                "order_id": "V3_ORDER_999",
                "client_order_id": "CLIENT_123",
                "product_id": "BTC-USD",
                "side": "BUY",
            },
            "order_configuration": {"limit_limit_gtc": {"base_size": "1", "limit_price": "50000"}},
        }
        res = self.adapter.parse_v3_response(mock_response)

        self.assertEqual(res.order_id, "V3_ORDER_999")
        self.assertEqual(res.client_order_id, "CLIENT_123")
        self.assertEqual(res.side, "BUY")
        # Acceptance, not a live order state - the create response says nothing
        # about whether the order is open, filled or already cancelled.
        self.assertEqual(res.status, "ACCEPTED")
        self.assertEqual(res.order_type, "limit_limit_gtc")
        self.assertEqual(res.raw_response, mock_response)

    def test_parse_v3_response_failure(self):
        mock_response = {
            "success": False,
            "error_response": {
                "message": "INSUFFICIENT_FUNDS",
                "error_details": "Not enough USD to place this order",
                "new_order_failure_reason": "INSUFFICIENT_FUND",
            },
        }
        with self.assertRaises(AdvancedTradeOrderRejected) as ctx:
            self.adapter.parse_v3_response(mock_response)

        self.assertIn("INSUFFICIENT_FUNDS", str(ctx.exception))
        self.assertEqual(ctx.exception.failure_reason, "INSUFFICIENT_FUND")
        self.assertEqual(ctx.exception.error_details, "Not enough USD to place this order")
        self.assertEqual(ctx.exception.raw_response, mock_response)

    def test_rejection_is_still_a_runtime_error(self):
        """Callers written against the previous RuntimeError contract keep working."""
        with self.assertRaises(RuntimeError):
            self.adapter.parse_v3_response({"success": False})

    def test_failure_without_error_block_still_raises(self):
        with self.assertRaises(AdvancedTradeOrderRejected) as ctx:
            self.adapter.parse_v3_response({"success": False, "error_response": None})
        self.assertIn("Unknown Advanced Trade API Error", str(ctx.exception))

    def test_success_without_order_id_is_treated_as_unresolved(self):
        """An empty order_id would otherwise be recorded as a phantom order."""
        with self.assertRaises(AdvancedTradeOrderRejected) as ctx:
            self.adapter.parse_v3_response({"success": True, "success_response": {}})
        self.assertIn("order_id", str(ctx.exception))

    def test_non_dict_response_is_a_type_error(self):
        with self.assertRaises(TypeError):
            self.adapter.parse_v3_response(["not", "an", "object"])


if __name__ == "__main__":
    unittest.main()
