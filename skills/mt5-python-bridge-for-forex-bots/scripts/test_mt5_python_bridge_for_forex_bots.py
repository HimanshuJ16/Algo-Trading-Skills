import logging
import unittest
from unittest.mock import Mock

from mt5_python_bridge_for_forex_bots import (
    DISPOSITION_AMBIGUOUS,
    DISPOSITION_COMPLETE,
    DISPOSITION_NOT_SENT,
    DISPOSITION_RETRYABLE,
    DISPOSITION_TERMINAL,
    ORDER_FILLING_FOK,
    ORDER_FILLING_IOC,
    ORDER_TYPE_BUY,
    ORDER_TYPE_SELL,
    SYMBOL_FILLING_BOC,
    SYMBOL_FILLING_FOK,
    SYMBOL_FILLING_IOC,
    TRADE_RETCODE_CONNECTION,
    TRADE_RETCODE_DONE,
    TRADE_RETCODE_DONE_PARTIAL,
    TRADE_RETCODE_INVALID_FILL,
    TRADE_RETCODE_INVALID_STOPS,
    TRADE_RETCODE_INVALID_VOLUME,
    TRADE_RETCODE_NO_MONEY,
    TRADE_RETCODE_PLACED,
    TRADE_RETCODE_REQUOTE,
    TRADE_RETCODE_TIMEOUT,
    MT5BridgeError,
    MT5Config,
    MT5OrderRequest,
    MT5PythonBridgeEngine,
    MT5SymbolSpec,
    classify_retcode,
    resolve_filling_mode,
)

logging.disable(logging.CRITICAL)


def eurusd_spec(**overrides):
    """A standard 5-digit EURUSD spec as a retail MT5 broker reports it."""
    defaults = dict(
        symbol="EURUSD",
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
        digits=5,
        point=0.00001,
        trade_stops_level=0,
        trade_freeze_level=0,
        filling_mode=SYMBOL_FILLING_FOK | SYMBOL_FILLING_IOC,
        volume_limit=0.0,
    )
    defaults.update(overrides)
    return MT5SymbolSpec(**defaults)


def make_adapter(result, spec=None):
    """Adapter whose order_send returns `result` and symbol_info returns `spec`."""
    adapter = Mock()
    adapter.order_send.return_value = result
    adapter.symbol_info.return_value = spec if spec is not None else {
        "name": "EURUSD", "volume_min": 0.01, "volume_max": 100.0, "volume_step": 0.01,
        "digits": 5, "point": 0.00001, "trade_stops_level": 0, "trade_freeze_level": 0,
        "filling_mode": SYMBOL_FILLING_FOK | SYMBOL_FILLING_IOC, "volume_limit": 0.0,
    }
    return adapter


class TestMT5Config(unittest.TestCase):

    def test_password_is_excluded_from_repr(self):
        cfg = MT5Config(login=123456, password="hunter2-secret", server="DemoServer")
        self.assertNotIn("hunter2-secret", repr(cfg))
        self.assertEqual(cfg.password, "hunter2-secret")

    def test_zero_magic_number_is_rejected(self):
        # magic 0 cannot be distinguished from a manual trade, so an ambiguous
        # order_send could never be reconciled to this strategy.
        with self.assertRaises(MT5BridgeError):
            MT5Config(login=1, password="p", server="S", magic_number=0)

    def test_invalid_preferred_filling_is_rejected(self):
        with self.assertRaises(MT5BridgeError):
            MT5Config(login=1, password="p", server="S", preferred_filling="RETURN")

    def test_non_positive_login_is_rejected(self):
        with self.assertRaises(MT5BridgeError):
            MT5Config(login=0, password="p", server="S")


class TestEngineConstruction(unittest.TestCase):

    def setUp(self):
        self.config = MT5Config(login=123456, password="secret_password", server="DemoServer")

    def test_engine_without_adapter_or_dry_run_refuses_to_construct(self):
        # Regression: the previous build silently simulated TRADE_RETCODE_DONE
        # with a hard-coded ticket when no adapter was supplied.
        with self.assertRaises(MT5BridgeError):
            MT5PythonBridgeEngine(self.config)

    def test_dry_run_serialises_without_submitting(self):
        engine = MT5PythonBridgeEngine(self.config, dry_run=True)
        order = MT5OrderRequest("EURUSD", "BUY", 0.1, 1.0850, 1.0800, 1.0950)

        report = engine.execute_forex_order(order, symbol_spec=eurusd_spec())

        self.assertFalse(report.is_executed)
        self.assertEqual(report.status, "MT5_DRY_RUN_VALIDATED")
        self.assertEqual(report.order_id, 0)
        self.assertEqual(report.retry_disposition, DISPOSITION_NOT_SENT)
        self.assertEqual(report.mql_trade_request["volume"], 0.1)


class TestRequestSerialisation(unittest.TestCase):

    def setUp(self):
        self.config = MT5Config(login=123456, password="secret_password", server="DemoServer")

    def test_valid_buy_order_execution(self):
        # EURUSD Buy 0.1 lots @ 1.0850, SL 1.0800, TP 1.0950 -> filled.
        adapter = make_adapter({
            "retcode": TRADE_RETCODE_DONE, "order": 98765432, "deal": 55501,
            "volume": 0.1, "price": 1.08502, "comment": "Request executed",
        })
        engine = MT5PythonBridgeEngine(self.config, mock_ipc_adapter=adapter)
        order = MT5OrderRequest("EURUSD", "BUY", 0.1, 1.0850, 1.0800, 1.0950)

        report = engine.execute_forex_order(order)

        self.assertTrue(report.is_executed)
        self.assertEqual(report.status, "MT5_ORDER_EXECUTED_SUCCESS")
        self.assertEqual(report.retcode, TRADE_RETCODE_DONE)
        self.assertEqual(report.retry_disposition, DISPOSITION_COMPLETE)
        self.assertFalse(report.requires_reconciliation)
        self.assertEqual(report.order_id, 98765432)
        self.assertEqual(report.deal_id, 55501)
        self.assertEqual(report.filled_volume_lots, 0.1)
        # Broker-confirmed price, not the requested one.
        self.assertEqual(report.execution_price, 1.08502)

        req = report.mql_trade_request
        self.assertEqual(req["action"], 1)
        self.assertEqual(req["type"], ORDER_TYPE_BUY)
        self.assertIsInstance(req["volume"], float)
        self.assertEqual(req["volume"], 0.1)
        self.assertEqual(req["magic"], 234000)
        self.assertEqual(req["deviation"], 10)
        self.assertEqual(req["type_filling"], ORDER_FILLING_IOC)

    def test_sell_order_sets_sell_type_and_absent_levels_serialise_as_zero(self):
        adapter = make_adapter({"retcode": TRADE_RETCODE_DONE, "order": 1, "volume": 0.5,
                                "price": 1.2700})
        engine = MT5PythonBridgeEngine(self.config, mock_ipc_adapter=adapter)

        report = engine.execute_forex_order(
            MT5OrderRequest("EURUSD", "sell", 0.5, 1.2700),
            symbol_spec=eurusd_spec(),
        )

        self.assertEqual(report.mql_trade_request["type"], ORDER_TYPE_SELL)
        self.assertEqual(report.mql_trade_request["sl"], 0.0)
        self.assertEqual(report.mql_trade_request["tp"], 0.0)

    def test_prices_are_rounded_to_symbol_digits(self):
        adapter = make_adapter({"retcode": TRADE_RETCODE_DONE, "order": 1, "volume": 0.1})
        engine = MT5PythonBridgeEngine(self.config, mock_ipc_adapter=adapter)

        report = engine.execute_forex_order(
            MT5OrderRequest("USDJPY", "BUY", 0.1, 157.123456789, 156.5, 158.0),
            symbol_spec=eurusd_spec(symbol="USDJPY", digits=3, point=0.001),
        )

        self.assertEqual(report.mql_trade_request["price"], 157.123)


class TestOrderTypeValidation(unittest.TestCase):

    def setUp(self):
        self.config = MT5Config(login=1, password="p", server="S")
        self.adapter = make_adapter({"retcode": TRADE_RETCODE_DONE, "order": 1})
        self.engine = MT5PythonBridgeEngine(self.config, mock_ipc_adapter=self.adapter)

    def test_unrecognised_side_is_rejected_and_never_sent(self):
        # Regression: 'LONG' previously fell through to the else-branch and was
        # serialised as ORDER_TYPE_SELL - a live order in the wrong direction.
        report = self.engine.execute_forex_order(
            MT5OrderRequest("EURUSD", "LONG", 0.1, 1.0850, 1.0800, 1.0950),
            symbol_spec=eurusd_spec(),
        )

        self.assertFalse(report.is_executed)
        self.assertEqual(report.status, "MT5_INVALID_ORDER_TYPE")
        self.assertEqual(report.mql_trade_request, {})
        self.adapter.order_send.assert_not_called()

    def test_pending_order_type_is_rejected(self):
        report = self.engine.execute_forex_order(
            MT5OrderRequest("EURUSD", "BUY_LIMIT", 0.1, 1.0850),
            symbol_spec=eurusd_spec(),
        )
        self.assertEqual(report.status, "MT5_INVALID_ORDER_TYPE")
        self.adapter.order_send.assert_not_called()

    def test_non_positive_price_is_rejected(self):
        report = self.engine.execute_forex_order(
            MT5OrderRequest("EURUSD", "BUY", 0.1, 0.0),
            symbol_spec=eurusd_spec(),
        )
        self.assertEqual(report.status, "MT5_INVALID_PRICE")
        self.adapter.order_send.assert_not_called()


class TestVolumeValidation(unittest.TestCase):

    def setUp(self):
        self.config = MT5Config(login=1, password="p", server="S")
        self.adapter = make_adapter({"retcode": TRADE_RETCODE_DONE, "order": 1})
        self.engine = MT5PythonBridgeEngine(self.config, mock_ipc_adapter=self.adapter)

    def _run(self, volume, spec=None):
        return self.engine.execute_forex_order(
            MT5OrderRequest("EURUSD", "BUY", volume, 1.0850),
            symbol_spec=spec or eurusd_spec(),
        )

    def test_off_step_volume_is_rejected(self):
        # Regression: `round(v * 100) % 1 != 0` is always False because round()
        # returns an int, so 0.015 lots used to pass the step check untouched.
        report = self._run(0.015)
        self.assertFalse(report.is_executed)
        self.assertEqual(report.status, "MT5_INVALID_VOLUME")
        self.assertEqual(report.retcode, TRADE_RETCODE_INVALID_VOLUME)
        self.adapter.order_send.assert_not_called()

    def test_volume_below_symbol_minimum_is_rejected(self):
        self.assertEqual(self._run(0.005).status, "MT5_INVALID_VOLUME")

    def test_volume_above_symbol_maximum_is_rejected(self):
        self.assertEqual(self._run(250.0).status, "MT5_INVALID_VOLUME")

    def test_volume_above_symbol_volume_limit_is_rejected(self):
        report = self._run(30.0, spec=eurusd_spec(volume_limit=20.0))
        self.assertEqual(report.status, "MT5_INVALID_VOLUME")

    def test_micro_lot_step_from_broker_metadata_is_honoured(self):
        # 0.001 is a valid volume on a micro account and must not be rejected
        # by a hard-coded 0.01 floor.
        micro = eurusd_spec(volume_min=0.001, volume_step=0.001)
        report = self._run(0.001, spec=micro)
        self.assertTrue(report.is_executed)
        self.assertEqual(report.mql_trade_request["volume"], 0.001)

    def test_coarse_step_instrument_rejects_fractional_lots(self):
        # An index CFD with a 1.0 lot step must reject 0.5.
        index = eurusd_spec(volume_min=1.0, volume_step=1.0, digits=1, point=0.1)
        self.assertEqual(self._run(0.5, spec=index).status, "MT5_INVALID_VOLUME")

    def test_zero_volume_is_rejected_even_if_the_spec_reports_no_minimum(self):
        report = self._run(0.0, spec=eurusd_spec(volume_min=0.0))
        self.assertEqual(report.status, "MT5_INVALID_VOLUME")
        self.adapter.order_send.assert_not_called()

    def test_negative_volume_is_rejected(self):
        self.assertEqual(self._run(-0.1).status, "MT5_INVALID_VOLUME")

    def test_float_representation_noise_does_not_reject_a_valid_volume(self):
        # 0.07 / 0.01 == 6.999999999999999 in IEEE-754; the step check must
        # tolerate that rather than rejecting a legitimate order.
        report = self._run(0.07)
        self.assertTrue(report.is_executed)
        self.assertEqual(report.mql_trade_request["volume"], 0.07)

    def test_nan_volume_is_rejected(self):
        self.assertEqual(self._run(float("nan")).status, "MT5_INVALID_VOLUME")

    def test_broker_reporting_zero_step_blocks_the_order(self):
        report = self._run(0.1, spec=eurusd_spec(volume_step=0.0))
        self.assertEqual(report.status, "MT5_INVALID_VOLUME")
        self.adapter.order_send.assert_not_called()


class TestStopValidation(unittest.TestCase):

    def setUp(self):
        self.config = MT5Config(login=1, password="p", server="S")
        self.adapter = make_adapter({"retcode": TRADE_RETCODE_DONE, "order": 1})
        self.engine = MT5PythonBridgeEngine(self.config, mock_ipc_adapter=self.adapter)

    def _run(self, side, price, sl=None, tp=None, spec=None):
        return self.engine.execute_forex_order(
            MT5OrderRequest("EURUSD", side, 0.1, price, sl, tp),
            symbol_spec=spec or eurusd_spec(),
        )

    def test_invalid_stop_loss_rejection(self):
        report = self._run("BUY", 1.0850, sl=1.0900, tp=1.0950)
        self.assertFalse(report.is_executed)
        self.assertEqual(report.status, "MT5_INVALID_STOPS")
        self.assertEqual(report.retcode, TRADE_RETCODE_INVALID_STOPS)

    def test_buy_take_profit_below_entry_is_rejected(self):
        # Regression: TP direction was documented but never validated, so a Buy
        # with TP below entry was serialised and sent.
        report = self._run("BUY", 1.0850, sl=1.0800, tp=1.0700)
        self.assertEqual(report.status, "MT5_INVALID_STOPS")
        self.adapter.order_send.assert_not_called()

    def test_sell_take_profit_above_entry_is_rejected(self):
        report = self._run("SELL", 1.0850, sl=1.0900, tp=1.0950)
        self.assertEqual(report.status, "MT5_INVALID_STOPS")

    def test_sell_stop_loss_below_entry_is_rejected(self):
        report = self._run("SELL", 1.0850, sl=1.0800)
        self.assertEqual(report.status, "MT5_INVALID_STOPS")

    def test_stop_inside_trade_stops_level_is_rejected(self):
        # 20 points on a 5-digit symbol = 0.00020; a 10-point stop is inside it.
        spec = eurusd_spec(trade_stops_level=20)
        report = self._run("BUY", 1.08500, sl=1.08490, spec=spec)
        self.assertEqual(report.status, "MT5_INVALID_STOPS")
        self.assertIn("trade_stops_level", report.audit_notes)

    def test_stop_exactly_at_trade_stops_level_is_accepted(self):
        spec = eurusd_spec(trade_stops_level=20)
        report = self._run("BUY", 1.08500, sl=1.08480, spec=spec)
        self.assertTrue(report.is_executed)

    def test_zero_stops_level_disables_the_distance_check(self):
        report = self._run("BUY", 1.08500, sl=1.08499)
        self.assertTrue(report.is_executed)

    def test_zero_priced_levels_mean_no_level_set(self):
        report = self._run("BUY", 1.0850, sl=0.0, tp=0.0)
        self.assertTrue(report.is_executed)
        self.assertEqual(report.mql_trade_request["sl"], 0.0)


class TestFillingModeResolution(unittest.TestCase):

    def test_ioc_preferred_when_symbol_allows_both(self):
        spec = eurusd_spec(filling_mode=SYMBOL_FILLING_FOK | SYMBOL_FILLING_IOC)
        self.assertEqual(resolve_filling_mode(spec, "IOC"), ORDER_FILLING_IOC)
        self.assertEqual(resolve_filling_mode(spec, "FOK"), ORDER_FILLING_FOK)

    def test_falls_back_to_fok_when_ioc_is_not_permitted(self):
        # SYMBOL_FILLING_FOK is bit 1 but ORDER_FILLING_FOK is 0 - the two
        # enumerations do not share a numbering.
        spec = eurusd_spec(filling_mode=SYMBOL_FILLING_FOK)
        self.assertEqual(resolve_filling_mode(spec, "IOC"), ORDER_FILLING_FOK)

    def test_falls_back_to_ioc_when_fok_is_not_permitted(self):
        spec = eurusd_spec(filling_mode=SYMBOL_FILLING_IOC)
        self.assertEqual(resolve_filling_mode(spec, "FOK"), ORDER_FILLING_IOC)

    def test_returns_none_when_neither_market_filling_mode_is_permitted(self):
        spec = eurusd_spec(filling_mode=SYMBOL_FILLING_BOC)
        self.assertIsNone(resolve_filling_mode(spec, "IOC"))

    def test_unreported_mask_falls_back_to_preference(self):
        spec = eurusd_spec(filling_mode=0)
        self.assertEqual(resolve_filling_mode(spec, "FOK"), ORDER_FILLING_FOK)

    def test_unsupported_preference_raises_rather_than_guessing(self):
        with self.assertRaises(MT5BridgeError):
            resolve_filling_mode(eurusd_spec(), "RETURN")

    def test_engine_rejects_symbol_with_no_usable_filling_mode(self):
        config = MT5Config(login=1, password="p", server="S")
        adapter = make_adapter({"retcode": TRADE_RETCODE_DONE, "order": 1})
        engine = MT5PythonBridgeEngine(config, mock_ipc_adapter=adapter)

        report = engine.execute_forex_order(
            MT5OrderRequest("EURUSD", "BUY", 0.1, 1.0850),
            symbol_spec=eurusd_spec(filling_mode=SYMBOL_FILLING_BOC),
        )

        self.assertEqual(report.status, "MT5_INVALID_FILLING")
        self.assertEqual(report.retcode, TRADE_RETCODE_INVALID_FILL)
        adapter.order_send.assert_not_called()

    def test_fok_only_symbol_serialises_order_filling_fok(self):
        config = MT5Config(login=1, password="p", server="S")
        adapter = make_adapter({"retcode": TRADE_RETCODE_DONE, "order": 1, "volume": 0.1})
        engine = MT5PythonBridgeEngine(config, mock_ipc_adapter=adapter)

        report = engine.execute_forex_order(
            MT5OrderRequest("EURUSD", "BUY", 0.1, 1.0850),
            symbol_spec=eurusd_spec(filling_mode=SYMBOL_FILLING_FOK),
        )

        self.assertEqual(report.mql_trade_request["type_filling"], ORDER_FILLING_FOK)


class TestSymbolMetadata(unittest.TestCase):

    def setUp(self):
        self.config = MT5Config(login=1, password="p", server="S")

    def test_unknown_symbol_blocks_the_order(self):
        adapter = Mock()
        adapter.symbol_info.return_value = None  # what MetaTrader5 returns on error
        adapter.order_send.return_value = {"retcode": TRADE_RETCODE_DONE}
        engine = MT5PythonBridgeEngine(self.config, mock_ipc_adapter=adapter)

        report = engine.execute_forex_order(MT5OrderRequest("NOPE", "BUY", 0.1, 1.0))

        self.assertEqual(report.status, "MT5_SYMBOL_UNAVAILABLE")
        adapter.order_send.assert_not_called()

    def test_symbol_info_raising_blocks_the_order(self):
        adapter = Mock()
        adapter.symbol_info.side_effect = OSError("terminal IPC down")
        adapter.order_send.return_value = {"retcode": TRADE_RETCODE_DONE}
        engine = MT5PythonBridgeEngine(self.config, mock_ipc_adapter=adapter)

        report = engine.execute_forex_order(MT5OrderRequest("EURUSD", "BUY", 0.1, 1.0850))

        self.assertEqual(report.status, "MT5_SYMBOL_UNAVAILABLE")
        adapter.order_send.assert_not_called()

    def test_supplied_spec_for_a_different_symbol_blocks_the_order(self):
        # The request is serialised from spec.symbol, so a mismatched spec would
        # otherwise route the deal to a different instrument.
        adapter = make_adapter({"retcode": TRADE_RETCODE_DONE, "order": 1})
        engine = MT5PythonBridgeEngine(self.config, mock_ipc_adapter=adapter)

        report = engine.execute_forex_order(
            MT5OrderRequest("EURUSD", "BUY", 0.1, 1.0850),
            symbol_spec=eurusd_spec(symbol="US30"),
        )

        self.assertEqual(report.status, "MT5_SYMBOL_MISMATCH")
        adapter.order_send.assert_not_called()

    def test_spec_is_built_from_a_namedtuple_style_symbol_info(self):
        class Info:
            name = "GBPUSD"
            volume_min = 0.01
            volume_max = 50.0
            volume_step = 0.01
            digits = 5
            point = 0.00001
            trade_stops_level = 0
            trade_freeze_level = 0
            filling_mode = SYMBOL_FILLING_IOC
            volume_limit = 0.0

        adapter = Mock()
        adapter.symbol_info.return_value = Info()
        adapter.order_send.return_value = {"retcode": TRADE_RETCODE_DONE, "order": 7,
                                           "volume": 0.2, "price": 1.27}
        engine = MT5PythonBridgeEngine(self.config, mock_ipc_adapter=adapter)

        report = engine.execute_forex_order(MT5OrderRequest("GBPUSD", "SELL", 0.2, 1.2700))

        self.assertTrue(report.is_executed)
        self.assertEqual(report.mql_trade_request["symbol"], "GBPUSD")
        self.assertEqual(report.mql_trade_request["type_filling"], ORDER_FILLING_IOC)


class TestRetcodeHandling(unittest.TestCase):

    def setUp(self):
        self.config = MT5Config(login=123456, password="secret_password", server="DemoServer")

    def _report_for(self, result):
        adapter = make_adapter(result)
        engine = MT5PythonBridgeEngine(self.config, mock_ipc_adapter=adapter)
        return engine.execute_forex_order(
            MT5OrderRequest("EURUSD", "BUY", 1.0, 1.0850),
            symbol_spec=eurusd_spec(),
        )

    def test_insufficient_funds_retcode_failure(self):
        report = self._report_for({"retcode": TRADE_RETCODE_NO_MONEY,
                                   "comment": "No money for deal"})
        self.assertFalse(report.is_executed)
        self.assertEqual(report.status, "MT5_EXECUTION_FAILED")
        self.assertEqual(report.retcode, TRADE_RETCODE_NO_MONEY)
        self.assertEqual(report.retry_disposition, DISPOSITION_TERMINAL)
        self.assertFalse(report.requires_reconciliation)

    def test_partial_fill_is_reported_as_executed_with_the_confirmed_volume(self):
        # Regression: 10010 used to be classified MT5_EXECUTION_FAILED with
        # is_executed=False and order_id=0, hiding a live position and inviting
        # a duplicate resend of the full size.
        report = self._report_for({
            "retcode": TRADE_RETCODE_DONE_PARTIAL, "order": 4242, "deal": 99,
            "volume": 0.4, "price": 1.08512, "comment": "Partial fill",
        })
        self.assertTrue(report.is_executed)
        self.assertEqual(report.status, "MT5_ORDER_PARTIALLY_FILLED")
        self.assertEqual(report.filled_volume_lots, 0.4)
        self.assertEqual(report.volume_lots, 1.0)
        self.assertEqual(report.order_id, 4242)
        self.assertEqual(report.deal_id, 99)
        self.assertEqual(report.retry_disposition, DISPOSITION_COMPLETE)

    def test_order_placed_but_not_filled_keeps_the_ticket_and_claims_no_fill(self):
        report = self._report_for({"retcode": TRADE_RETCODE_PLACED, "order": 777})
        self.assertFalse(report.is_executed)
        self.assertEqual(report.status, "MT5_ORDER_PLACED")
        self.assertEqual(report.order_id, 777)
        self.assertEqual(report.filled_volume_lots, 0.0)

    def test_requote_is_retryable_not_ambiguous(self):
        report = self._report_for({"retcode": TRADE_RETCODE_REQUOTE, "comment": "Requote"})
        self.assertEqual(report.retry_disposition, DISPOSITION_RETRYABLE)
        self.assertFalse(report.requires_reconciliation)

    def test_timeout_retcode_demands_reconciliation(self):
        report = self._report_for({"retcode": TRADE_RETCODE_TIMEOUT, "comment": "Timeout"})
        self.assertEqual(report.status, "MT5_EXECUTION_AMBIGUOUS")
        self.assertTrue(report.requires_reconciliation)
        self.assertEqual(report.retry_disposition, DISPOSITION_AMBIGUOUS)
        self.assertIn("234000", report.audit_notes)

    def test_order_send_returning_none_demands_reconciliation(self):
        # MetaTrader5.order_send() returns None when the terminal cannot process
        # the call; the previous build raised AttributeError on res.get().
        report = self._report_for(None)
        self.assertEqual(report.status, "MT5_EXECUTION_AMBIGUOUS")
        self.assertTrue(report.requires_reconciliation)
        self.assertEqual(report.retcode, TRADE_RETCODE_CONNECTION)

    def test_order_send_raising_demands_reconciliation(self):
        adapter = make_adapter(None)
        adapter.order_send.side_effect = OSError("named pipe closed")
        engine = MT5PythonBridgeEngine(self.config, mock_ipc_adapter=adapter)

        report = engine.execute_forex_order(
            MT5OrderRequest("EURUSD", "BUY", 1.0, 1.0850),
            symbol_spec=eurusd_spec(),
        )

        self.assertEqual(report.status, "MT5_EXECUTION_AMBIGUOUS")
        self.assertTrue(report.requires_reconciliation)
        self.assertIn("history_deals_get", report.audit_notes)

    def test_success_without_a_confirmed_volume_falls_back_to_the_request(self):
        # 10009 means the request completed in full; reporting 0.0 filled would
        # corrupt position accounting.
        report = self._report_for({"retcode": TRADE_RETCODE_DONE, "order": 5})
        self.assertTrue(report.is_executed)
        self.assertEqual(report.filled_volume_lots, 1.0)
        self.assertEqual(report.execution_price, 1.085)

    def test_partial_fill_without_a_confirmed_volume_is_ambiguous(self):
        # "Part of the request was completed" with no volume: the true fill is
        # unknown, so it must not be reported as a known quantity.
        report = self._report_for({"retcode": TRADE_RETCODE_DONE_PARTIAL, "order": 6})
        self.assertEqual(report.status, "MT5_EXECUTION_AMBIGUOUS")
        self.assertTrue(report.requires_reconciliation)
        self.assertFalse(report.is_executed)

    def test_unknown_retcode_defaults_to_terminal_not_retryable(self):
        report = self._report_for({"retcode": 19999, "comment": "???"})
        self.assertEqual(report.retry_disposition, DISPOSITION_TERMINAL)
        self.assertFalse(report.requires_reconciliation)

    def test_the_engine_sends_exactly_once(self):
        adapter = make_adapter({"retcode": TRADE_RETCODE_REQUOTE, "comment": "Requote"})
        engine = MT5PythonBridgeEngine(self.config, mock_ipc_adapter=adapter)
        engine.execute_forex_order(
            MT5OrderRequest("EURUSD", "BUY", 0.1, 1.0850),
            symbol_spec=eurusd_spec(),
        )
        self.assertEqual(adapter.order_send.call_count, 1)


class TestClassifyRetcode(unittest.TestCase):

    def test_known_dispositions(self):
        self.assertEqual(classify_retcode(TRADE_RETCODE_DONE), DISPOSITION_COMPLETE)
        self.assertEqual(classify_retcode(TRADE_RETCODE_DONE_PARTIAL), DISPOSITION_COMPLETE)
        self.assertEqual(classify_retcode(TRADE_RETCODE_PLACED), DISPOSITION_COMPLETE)
        self.assertEqual(classify_retcode(TRADE_RETCODE_CONNECTION), DISPOSITION_AMBIGUOUS)
        self.assertEqual(classify_retcode(TRADE_RETCODE_TIMEOUT), DISPOSITION_AMBIGUOUS)
        self.assertEqual(classify_retcode(TRADE_RETCODE_REQUOTE), DISPOSITION_RETRYABLE)
        self.assertEqual(classify_retcode(TRADE_RETCODE_NO_MONEY), DISPOSITION_TERMINAL)
        self.assertEqual(classify_retcode(TRADE_RETCODE_INVALID_FILL), DISPOSITION_TERMINAL)


if __name__ == '__main__':
    unittest.main()
