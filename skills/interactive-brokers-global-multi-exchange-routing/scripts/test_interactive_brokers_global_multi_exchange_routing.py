"""
Unit tests for the IBKR global multi-exchange routing pre-flight validator.

Tests assert *behaviour* against IBKR's documented contract semantics, not the shape of the
implementation. Several are explicit regression tests against defects in the 1.x engine and
are annotated as such -- each of those fails on the old behaviour and passes on the new one.
"""
from decimal import Decimal
import unittest

from interactive_brokers_global_multi_exchange_routing import (
    IbkrContractSpec,
    IbkrGlobalRoutingEngine,
    IbkrOrderPayload,
    IbkrVenueProfile,
    VALID_SEC_TYPES,
)


def spec(**overrides: object) -> IbkrContractSpec:
    """A valid US SmartRouted equity contract, overridable field by field."""
    base = dict(
        symbol="AAPL", sec_type="STK", currency="USD", exchange="SMART",
        primary_exchange="NASDAQ", routing_mode="SMART_BEST_EXECUTION",
    )
    base.update(overrides)
    return IbkrContractSpec(**base)  # type: ignore[arg-type]


def order(contract: IbkrContractSpec, **overrides: object) -> IbkrOrderPayload:
    base = dict(action="BUY", order_type="LMT", quantity=100, lmt_price=150.0)
    base.update(overrides)
    return IbkrOrderPayload(contract=contract, **base)  # type: ignore[arg-type]


class RoutingEngineTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = IbkrGlobalRoutingEngine()

    def route(self, contract: IbkrContractSpec, **order_overrides: object):
        return self.engine.audit_and_route_order(order(contract, **order_overrides))


class TestHappyPaths(RoutingEngineTestCase):
    def test_us_equity_smart_routed_with_primary_exchange(self) -> None:
        report = self.route(spec())
        self.assertEqual(report.status, "IBKR_ROUTING_VALIDATED")
        self.assertEqual(report.symbol, "AAPL")
        self.assertEqual(report.target_exchange, "SMART")
        self.assertEqual(report.primary_exchange, "NASDAQ")
        self.assertEqual(report.resolved_venue, "NASDAQ")
        self.assertTrue(report.is_contract_valid)
        self.assertTrue(report.currency_check_performed)
        self.assertEqual(report.warnings, ())

    def test_direct_route_to_island(self) -> None:
        report = self.route(spec(exchange="ISLAND", routing_mode="DIRECT_EXCHANGE"))
        self.assertEqual(report.status, "IBKR_ROUTING_VALIDATED")
        self.assertEqual(report.resolved_venue, "ISLAND")

    def test_forex_pair_on_idealpro(self) -> None:
        # IBKR's own sample: symbol='EUR', secType='CASH', currency='GBP',
        # exchange='IDEALPRO'. No region/currency rule may fire here.
        report = self.route(
            spec(symbol="EUR", sec_type="CASH", currency="GBP", exchange="IDEALPRO",
                 primary_exchange="", routing_mode="DIRECT_EXCHANGE"),
            order_type="MKT", lmt_price=None, quantity=25_000,
        )
        self.assertEqual(report.status, "IBKR_ROUTING_VALIDATED")
        self.assertFalse(report.currency_check_performed)

    def test_eurex_index_future_in_eur(self) -> None:
        report = self.route(
            spec(symbol="DAX", sec_type="FUT", currency="EUR", exchange="DTB",
                 primary_exchange="", routing_mode="DIRECT_EXCHANGE"),
            order_type="MKT", lmt_price=None, quantity=2,
        )
        self.assertEqual(report.status, "IBKR_ROUTING_VALIDATED")

    def test_report_always_demands_contract_details_confirmation(self) -> None:
        # A local pass is never a statement that the contract exists.
        self.assertTrue(self.route(spec()).requires_contract_details_check)


class TestHongKongSymbolHandling(RoutingEngineTestCase):
    def test_hkex_symbol_is_never_zero_padded(self) -> None:
        # REGRESSION (1.x): the old engine rewrote '700' to '00700'. IBKR's shipped SEHK
        # sample uses symbol='1' for HKEX code 00001, so padding produces a symbol IBKR
        # cannot resolve (error 200). The symbol must survive the audit untouched.
        report = self.route(
            spec(symbol="700", currency="HKD", exchange="SEHK", primary_exchange="SEHK",
                 routing_mode="DIRECT_EXCHANGE"),
            quantity=500, lmt_price=300.0,
        )
        self.assertEqual(report.status, "IBKR_ROUTING_VALIDATED")
        self.assertEqual(report.symbol, "700")

    def test_single_digit_hkex_code_accepted(self) -> None:
        # IBKR's HKStk() sample is literally symbol='1'.
        report = self.route(
            spec(symbol="1", currency="HKD", exchange="SEHK", primary_exchange="SEHK",
                 routing_mode="DIRECT_EXCHANGE"),
            quantity=1_000, lmt_price=40.0,
        )
        self.assertEqual(report.status, "IBKR_ROUTING_VALIDATED")
        self.assertEqual(report.symbol, "1")

    def test_zero_padded_input_passes_but_warns(self) -> None:
        report = self.route(
            spec(symbol="00700", currency="HKD", exchange="SEHK", primary_exchange="SEHK",
                 routing_mode="DIRECT_EXCHANGE"),
            quantity=500, lmt_price=300.0,
        )
        self.assertEqual(report.status, "IBKR_ROUTING_VALIDATED")
        self.assertEqual(report.symbol, "00700")
        self.assertTrue(any("zero-padded" in w for w in report.warnings))

    def test_hkd_futures_are_not_forced_into_the_equity_code_rule(self) -> None:
        # REGRESSION (1.x): the numeric-code rule fired on currency=='HKD' alone, so an HSI
        # future on HKFE was rejected as a malformed stock code.
        report = self.route(
            spec(symbol="HSI", sec_type="FUT", currency="HKD", exchange="HKFE",
                 primary_exchange="", routing_mode="DIRECT_EXCHANGE"),
            order_type="MKT", lmt_price=None, quantity=1,
        )
        self.assertEqual(report.status, "IBKR_ROUTING_VALIDATED")

    def test_non_numeric_sehk_equity_symbol_rejected(self) -> None:
        report = self.route(
            spec(symbol="TENCENT", currency="HKD", exchange="SEHK", primary_exchange="SEHK",
                 routing_mode="DIRECT_EXCHANGE"),
        )
        self.assertEqual(report.status, "REJECTED_INVALID_SYMBOL_FORMAT")

    def test_stock_connect_uses_six_digit_mainland_codes_in_cnh(self) -> None:
        # REGRESSION (1.x): SEHK-family venues were hard-wired to HKD and 5-digit codes, so
        # a CNH Stock Connect line (symbol='603737', SEHKNTL) was rejected twice over.
        report = self.route(
            spec(symbol="603737", currency="CNH", exchange="SEHKNTL", primary_exchange="SEHKNTL",
                 routing_mode="DIRECT_EXCHANGE"),
            quantity=200, lmt_price=30.0,
        )
        self.assertEqual(report.status, "IBKR_ROUTING_VALIDATED")

    def test_five_digit_code_rejected_on_stock_connect_venue(self) -> None:
        report = self.route(
            spec(symbol="00700", currency="CNH", exchange="SEHKNTL",
                 primary_exchange="SEHKNTL", routing_mode="DIRECT_EXCHANGE"),
        )
        self.assertEqual(report.status, "REJECTED_INVALID_SYMBOL_FORMAT")

    def test_validate_hkex_symbol_returns_input_unchanged(self) -> None:
        self.assertEqual(self.engine.validate_hkex_symbol("700"), "700")
        self.assertEqual(self.engine.validate_hkex_symbol("  5  "), "5")
        with self.assertRaises(ValueError):
            self.engine.validate_hkex_symbol("700000")


class TestCurrencyValidation(RoutingEngineTestCase):
    def test_eur_on_a_us_direct_venue_is_rejected(self) -> None:
        report = self.route(
            spec(currency="EUR", exchange="ISLAND", routing_mode="DIRECT_EXCHANGE"),
        )
        self.assertEqual(report.status, "REJECTED_CURRENCY_MISMATCH")
        self.assertFalse(report.is_currency_matched)
        self.assertTrue(report.currency_check_performed)

    def test_smart_routed_currency_mismatch_is_caught_via_primary_exchange(self) -> None:
        # REGRESSION (1.x): the currency rule only looked at direct venue codes, so a
        # EUR-denominated contract SmartRouted with primaryExchange='NASDAQ' -- the exact
        # pitfall the skill documents -- passed as VALIDATED.
        report = self.route(spec(currency="EUR"))
        self.assertEqual(report.status, "REJECTED_CURRENCY_MISMATCH")

    def test_sehk_accepts_cnh_dual_counter(self) -> None:
        # REGRESSION (1.x): SEHK was hard-wired to HKD. HKEX runs the HKD-RMB Dual Counter
        # Model, so an RMB counter on SEHK is a real, routable line.
        report = self.route(
            spec(symbol="87001", currency="CNH", exchange="SEHK", primary_exchange="SEHK",
                 routing_mode="DIRECT_EXCHANGE"),
            quantity=100, lmt_price=300.0,
        )
        self.assertEqual(report.status, "IBKR_ROUTING_VALIDATED")

    def test_eurex_accepts_chf_denominated_products(self) -> None:
        # REGRESSION (1.x): DTB was hard-wired to EUR, rejecting Eurex's CHF-denominated
        # SMI contracts.
        report = self.route(
            spec(symbol="SMI", sec_type="FUT", currency="CHF", exchange="DTB",
                 primary_exchange="", routing_mode="DIRECT_EXCHANGE"),
            order_type="MKT", lmt_price=None, quantity=1,
        )
        self.assertEqual(report.status, "IBKR_ROUTING_VALIDATED")

    def test_unknown_venue_warns_rather_than_rejecting(self) -> None:
        # No local table covers 170+ markets; an unknown code must not block a valid order.
        report = self.route(
            spec(symbol="6758", sec_type="STK", currency="JPY", exchange="TSEJ",
                 primary_exchange="TSEJ", routing_mode="DIRECT_EXCHANGE"),
            quantity=100, lmt_price=3000.0,
        )
        self.assertEqual(report.status, "IBKR_ROUTING_VALIDATED")
        self.assertFalse(report.currency_check_performed)
        self.assertTrue(any("not in the local registry" in w for w in report.warnings))

    def test_malformed_currency_code_rejected(self) -> None:
        self.assertEqual(
            self.route(spec(currency="US$")).status, "REJECTED_INVALID_CURRENCY",
        )
        self.assertEqual(
            self.route(spec(currency="")).status, "REJECTED_INVALID_CURRENCY",
        )

    def test_injected_venue_registry_is_authoritative_for_the_instance(self) -> None:
        engine = IbkrGlobalRoutingEngine(venue_registry={
            "XCUSTOM": IbkrVenueProfile("XCUSTOM", "TEST", frozenset({"SGD"})),
        })
        payload = order(spec(symbol="D05", currency="SGD", exchange="XCUSTOM",
                             primary_exchange="", routing_mode="DIRECT_EXCHANGE"))
        self.assertEqual(
            engine.audit_and_route_order(payload).status, "IBKR_ROUTING_VALIDATED",
        )
        bad = order(spec(symbol="D05", currency="USD", exchange="XCUSTOM",
                         primary_exchange="", routing_mode="DIRECT_EXCHANGE"))
        self.assertEqual(
            engine.audit_and_route_order(bad).status, "REJECTED_CURRENCY_MISMATCH",
        )


class TestPrimaryExchangeSemantics(RoutingEngineTestCase):
    def test_smart_routed_stock_without_primary_exchange_is_accepted_with_a_warning(self) -> None:
        # REGRESSION (1.x): this was a hard rejection. IBKR's own USStockAtSmart sample
        # smart-routes IBKR/USD with no primaryExchange at all, and the Contract reference
        # scopes the field to resolving ambiguity.
        report = self.route(spec(primary_exchange=""))
        self.assertEqual(report.status, "IBKR_ROUTING_VALIDATED")
        self.assertTrue(any("good practice" in w for w in report.warnings))

    def test_smart_routed_option_does_not_need_a_primary_exchange(self) -> None:
        # REGRESSION (1.x): every SMART order was forced to carry primaryExchange, which
        # rejected valid smart-routed options and futures outright.
        report = self.route(
            spec(symbol="SPY", sec_type="OPT", primary_exchange=""),
            quantity=1, lmt_price=2.5,
        )
        self.assertEqual(report.status, "IBKR_ROUTING_VALIDATED")
        self.assertFalse(any("good practice" in w for w in report.warnings))

    def test_primary_exchange_with_a_period_is_trimmed_with_a_warning(self) -> None:
        # "For exchanges which contain a period in name, will only be part of exchange name
        # prior to period, i.e. ENEXT for ENEXT.BE."
        report = self.route(
            spec(symbol="AGS", currency="EUR", primary_exchange="ENEXT.BE"),
            lmt_price=40.0,
        )
        self.assertEqual(report.primary_exchange, "ENEXT")
        self.assertTrue(any("ENEXT.BE" in w for w in report.warnings))

    def test_primary_exchange_of_smart_is_rejected(self) -> None:
        report = self.route(spec(primary_exchange="SMART"))
        self.assertEqual(report.status, "REJECTED_ROUTING_MODE_CONFLICT")

    def test_period_trimmed_primary_exchange_is_used_by_the_symbol_rule(self) -> None:
        # The symbol rule and the currency rule must resolve the listing venue from the
        # same normalised primaryExchange, so 'SEHK.HK' still binds the HKEX code rule.
        report = self.route(
            spec(symbol="TENCENT", currency="HKD", primary_exchange="SEHK.HK"),
        )
        self.assertEqual(report.status, "REJECTED_INVALID_SYMBOL_FORMAT")
        self.assertEqual(report.primary_exchange, "SEHK")

    def test_primary_exchange_on_a_future_warns(self) -> None:
        report = self.route(
            spec(symbol="DAX", sec_type="FUT", currency="EUR", exchange="DTB",
                 primary_exchange="DTB", routing_mode="DIRECT_EXCHANGE"),
            order_type="MKT", lmt_price=None, quantity=1,
        )
        self.assertEqual(report.status, "IBKR_ROUTING_VALIDATED")
        self.assertTrue(any("carries no meaning" in w for w in report.warnings))


class TestRoutingModeConsistency(RoutingEngineTestCase):
    def test_direct_mode_with_smart_destination_is_rejected(self) -> None:
        # REGRESSION (1.x): VALID_ROUTING_MODES was defined but never consulted, so this
        # self-contradicting pair was accepted silently.
        report = self.route(spec(routing_mode="DIRECT_EXCHANGE"))
        self.assertEqual(report.status, "REJECTED_ROUTING_MODE_CONFLICT")

    def test_smart_mode_with_direct_destination_is_rejected(self) -> None:
        report = self.route(spec(exchange="ISLAND", routing_mode="SMART_BEST_EXECUTION"))
        self.assertEqual(report.status, "REJECTED_ROUTING_MODE_CONFLICT")

    def test_unknown_routing_mode_is_rejected(self) -> None:
        report = self.route(spec(routing_mode="SMART_FASTEST"))
        self.assertEqual(report.status, "REJECTED_ROUTING_MODE_CONFLICT")

    def test_max_rebate_passes_but_flags_that_it_is_not_a_wire_field(self) -> None:
        report = self.route(spec(routing_mode="SMART_MAX_REBATE"))
        self.assertEqual(report.status, "IBKR_ROUTING_VALIDATED")
        self.assertTrue(any("not an order-level TWS API field" in w for w in report.warnings))

    def test_empty_exchange_is_rejected(self) -> None:
        report = self.route(spec(exchange=""))
        self.assertEqual(report.status, "REJECTED_MISSING_EXCHANGE")


class TestSecurityTypeValidation(RoutingEngineTestCase):
    def test_unknown_sec_type_rejected(self) -> None:
        self.assertEqual(self.route(spec(sec_type="EQUITY")).status, "REJECTED_INVALID_SEC_TYPE")

    def test_documented_sec_types_are_accepted(self) -> None:
        # REGRESSION (1.x): FOP, WAR, BOND, CMDTY, FUND and BAG were all rejected despite
        # being enumerated in the TWS API Contract reference.
        for sec_type in ("FOP", "WAR", "BOND", "CMDTY", "FUND", "BAG"):
            with self.subTest(sec_type=sec_type):
                self.assertIn(sec_type, VALID_SEC_TYPES)
                report = self.route(
                    spec(sec_type=sec_type, exchange="ISLAND", primary_exchange="",
                         routing_mode="DIRECT_EXCHANGE"),
                )
                self.assertNotEqual(report.status, "REJECTED_INVALID_SEC_TYPE")

    def test_sec_type_is_case_and_whitespace_insensitive(self) -> None:
        self.assertEqual(self.route(spec(sec_type="  stk ")).status, "IBKR_ROUTING_VALIDATED")


class TestForexContracts(RoutingEngineTestCase):
    def test_non_currency_forex_symbol_rejected(self) -> None:
        report = self.route(
            spec(symbol="EURUSD", sec_type="CASH", currency="USD", exchange="IDEALPRO",
                 primary_exchange="", routing_mode="DIRECT_EXCHANGE"),
            order_type="MKT", lmt_price=None, quantity=25_000,
        )
        self.assertEqual(report.status, "REJECTED_INVALID_SYMBOL_FORMAT")

    def test_degenerate_pair_rejected(self) -> None:
        report = self.route(
            spec(symbol="USD", sec_type="CASH", currency="USD", exchange="IDEALPRO",
                 primary_exchange="", routing_mode="DIRECT_EXCHANGE"),
            order_type="MKT", lmt_price=None, quantity=25_000,
        )
        self.assertEqual(report.status, "REJECTED_INVALID_SYMBOL_FORMAT")


class TestOrderFieldValidation(RoutingEngineTestCase):
    def test_limit_order_without_price_rejected(self) -> None:
        # REGRESSION (1.x): no order field was validated at all, so an LMT order with a
        # None limit price passed the audit and failed only at IBKR.
        report = self.route(spec(), order_type="LMT", lmt_price=None)
        self.assertEqual(report.status, "REJECTED_INVALID_ORDER_PARAMS")

    def test_non_positive_quantity_rejected(self) -> None:
        for quantity in (0, -100, Decimal("-0.5")):
            with self.subTest(quantity=quantity):
                self.assertEqual(
                    self.route(spec(), quantity=quantity).status,
                    "REJECTED_INVALID_ORDER_PARAMS",
                )

    def test_non_finite_values_rejected(self) -> None:
        self.assertEqual(
            self.route(spec(), quantity=float("nan")).status, "REJECTED_INVALID_ORDER_PARAMS",
        )
        self.assertEqual(
            self.route(spec(), lmt_price=float("inf")).status, "REJECTED_INVALID_ORDER_PARAMS",
        )
        self.assertEqual(
            self.route(spec(), lmt_price=float("nan")).status, "REJECTED_INVALID_ORDER_PARAMS",
        )

    def test_decimal_nan_limit_price_rejected_without_raising(self) -> None:
        # Decimal('NaN') <= 0 raises InvalidOperation, so finiteness must be settled first.
        self.assertEqual(
            self.route(spec(), lmt_price=Decimal("NaN")).status,
            "REJECTED_INVALID_ORDER_PARAMS",
        )
        self.assertEqual(
            self.route(spec(), lmt_price=Decimal("Infinity")).status,
            "REJECTED_INVALID_ORDER_PARAMS",
        )

    def test_non_numeric_quantity_rejected(self) -> None:
        self.assertEqual(
            self.route(spec(), quantity="lots").status, "REJECTED_INVALID_ORDER_PARAMS",
        )
        self.assertEqual(
            self.route(spec(), quantity=True).status, "REJECTED_INVALID_ORDER_PARAMS",
        )

    def test_zero_or_negative_limit_price_rejected(self) -> None:
        for price in (0.0, -1.0):
            with self.subTest(price=price):
                self.assertEqual(
                    self.route(spec(), lmt_price=price).status,
                    "REJECTED_INVALID_ORDER_PARAMS",
                )

    def test_invalid_action_rejected(self) -> None:
        self.assertEqual(self.route(spec(), action="LONG").status, "REJECTED_INVALID_ORDER_PARAMS")

    def test_institutional_action_warns(self) -> None:
        report = self.route(spec(), action="SSHORT")
        self.assertEqual(report.status, "IBKR_ROUTING_VALIDATED")
        self.assertTrue(any("institutional" in w for w in report.warnings))

    def test_fractional_quantity_accepted_with_a_warning(self) -> None:
        # TWS API v10 types totalQuantity as Decimal; an int-only payload cannot express it.
        report = self.route(spec(), quantity=Decimal("0.5"))
        self.assertEqual(report.status, "IBKR_ROUTING_VALIDATED")
        self.assertTrue(any("Fractional quantity" in w for w in report.warnings))

    def test_limit_price_on_a_market_order_warns(self) -> None:
        report = self.route(spec(), order_type="MKT", lmt_price=150.0)
        self.assertEqual(report.status, "IBKR_ROUTING_VALIDATED")
        self.assertTrue(any("lmtPrice" in w for w in report.warnings))

    def test_unknown_order_type_warns_rather_than_rejecting(self) -> None:
        report = self.route(spec(), order_type="SNAP MID", lmt_price=None)
        self.assertEqual(report.status, "IBKR_ROUTING_VALIDATED")
        self.assertTrue(any("known set" in w for w in report.warnings))

    def test_audit_notes_do_not_call_every_size_shares(self) -> None:
        # 'shares' is wrong for futures lots and forex notional.
        report = self.route(
            spec(symbol="DAX", sec_type="FUT", currency="EUR", exchange="DTB",
                 primary_exchange="", routing_mode="DIRECT_EXCHANGE"),
            order_type="MKT", lmt_price=None, quantity=2,
        )
        self.assertNotIn("shares", report.audit_notes)

    def test_round_quantity_is_not_mangled_by_formatting(self) -> None:
        report = self.route(spec(), quantity=100)
        self.assertIn("100", report.audit_notes)
        self.assertNotIn(" 1 @", report.audit_notes)


class TestDeterminismAndPurity(RoutingEngineTestCase):
    def test_repeated_audits_are_identical(self) -> None:
        payload = order(spec())
        first = self.engine.audit_and_route_order(payload)
        second = self.engine.audit_and_route_order(payload)
        self.assertEqual(first, second)

    def test_engine_holds_no_cross_call_state(self) -> None:
        self.route(spec(currency="EUR"))  # a rejection
        self.assertEqual(self.route(spec()).status, "IBKR_ROUTING_VALIDATED")

    def test_input_payload_is_not_mutated(self) -> None:
        contract = spec(symbol=" aapl ", primary_exchange="ENEXT.BE", currency="EUR")
        payload = order(contract)
        self.engine.audit_and_route_order(payload)
        self.assertEqual(payload.contract.symbol, " aapl ")
        self.assertEqual(payload.contract.primary_exchange, "ENEXT.BE")


if __name__ == "__main__":
    unittest.main()
