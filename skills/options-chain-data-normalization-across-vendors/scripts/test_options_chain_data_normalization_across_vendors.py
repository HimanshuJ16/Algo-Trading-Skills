"""
Behavioural tests for options-chain-data-normalization-across-vendors.

Expected OSI strings are taken from independently published examples of the OCC
symbology (``SPX   141122P01950000``, ``LAMR  150117C00052500``) rather than by
re-running the module's own construction, so a wrong encoding fails instead of agreeing
with itself. Expected midpoints are arithmetic done by hand in the test.

Tests marked ``REGRESSION`` each fail against the pre-2.0 implementation and pass
against the fix:

  * ``right='CALL'`` -- an IBKR-documented value -- used to normalize to a **put**.
  * Polygon's documented ``contract_type='other'`` used to normalize to a **put**.
  * A missing underlying / expiry / strike used to be defaulted to a real AAPL contract.
  * IBKR's ``-1`` no-data sentinel used to produce ``mid_price = -1.0`` on a chain
    reporting ``DATA_INTEGRITY_OK``.
  * A zero bid used to discard the real midpoint in favour of the last trade price.
  * A crossed quote used to report a clamped ``spread = 0.0`` and a computed midpoint.
  * An over-long root used to be truncated into a different contract's symbol; an
    over-large or negative strike used to emit a symbol that was not 21 characters.
  * An unknown vendor used to fall through to the Polygon parser.
  * One malformed record used to abort the whole chain.
"""
import logging
import unittest

from options_chain_data_normalization_across_vendors import (
    NormalizationConfig,
    NormalizationError,
    NormalizedOptionContract,
    OptionRight,
    OptionsChainNormalizationEngine,
    OptionsNormalizationReport,
    QualityFlag,
)


def polygon_record(**overrides):
    """A well-formed Polygon options-contract record."""
    record = {
        "ticker": "O:AAPL240119C00150000",
        "underlying_ticker": "AAPL",
        "expiration_date": "2024-01-19",
        "contract_type": "call",
        "strike_price": 150.0,
        "shares_per_contract": 100,
        "bid": 5.20,
        "ask": 5.40,
    }
    record.update(overrides)
    return {k: v for k, v in record.items() if v is not None}


def ibkr_record(**overrides):
    """A well-formed IBKR option contract record."""
    record = {
        "symbol": "AAPL",
        "lastTradeDateOrContractMonth": "20240119",
        "right": "C",
        "strike": 150.0,
        "multiplier": 100,
        "bid": 5.20,
        "ask": 5.40,
    }
    record.update(overrides)
    return {k: v for k, v in record.items() if v is not None}


def bloomberg_record(**overrides):
    """A well-formed Bloomberg equity-option record."""
    record = {
        "ticker": "AAPL US 01/19/24 C150 Equity",
        "bid": 5.20,
        "ask": 5.40,
    }
    record.update(overrides)
    return {k: v for k, v in record.items() if v is not None}


def opra_record(**overrides):
    """A well-formed OPRA record keyed by its OSI symbol."""
    record = {
        "osi_symbol": "AAPL  240119C00150000",
        "bid": 5.20,
        "ask": 5.40,
    }
    record.update(overrides)
    return {k: v for k, v in record.items() if v is not None}


class BaseCase(unittest.TestCase):
    def setUp(self):
        self.engine = OptionsChainNormalizationEngine()
        # Parsers log warnings on every rejection; silence them so a passing run is quiet.
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)


class TestOsiConstruction(BaseCase):

    def test_build_osi_symbol_formatting(self):
        osi = self.engine.build_osi_symbol("AAPL", "2024-01-19", "CALL", 150.0)
        self.assertEqual(len(osi), 21)
        self.assertEqual(osi, "AAPL  240119C00150000")

        osi_nvda = self.engine.build_osi_symbol("NVDA", "2024-06-21", "PUT", 450.0)
        self.assertEqual(len(osi_nvda), 21)
        self.assertEqual(osi_nvda, "NVDA  240621P00450000")

    def test_published_occ_examples_reproduce_exactly(self):
        # Both strings are published OCC symbology examples, not values this module
        # produced. The LAMR case pins the mill digits on a non-integer strike.
        self.assertEqual(
            self.engine.build_osi_symbol("SPX", "2014-11-22", "P", 1950.0),
            "SPX   141122P01950000",
        )
        self.assertEqual(
            self.engine.build_osi_symbol("LAMR", "2015-01-17", "C", 52.5),
            "LAMR  150117C00052500",
        )

    def test_root_is_left_justified_and_space_padded(self):
        osi = self.engine.build_osi_symbol("F", "2024-01-19", "C", 12.0)
        self.assertEqual(osi[:6], "F     ")
        self.assertEqual(len(osi), 21)

    def test_six_character_root_consumes_the_whole_field(self):
        osi = self.engine.build_osi_symbol("ABCDEF", "2024-01-19", "C", 12.0)
        self.assertEqual(osi[:6], "ABCDEF")
        self.assertEqual(len(osi), 21)

    def test_sub_penny_strike_encodes_mills(self):
        # 0.125 -> 125 mills -> '00000125'. Verified by hand, not by the module.
        osi = self.engine.build_osi_symbol("ABC", "2024-01-19", "C", 0.125)
        self.assertEqual(osi[-8:], "00000125")

    def test_root_longer_than_six_is_rejected(self):
        # REGRESSION: the old implementation sliced [:6], turning 'GOOGLE1' into
        # 'GOOGLE' -- a well-formed symbol naming a different contract.
        with self.assertRaises(NormalizationError):
            self.engine.build_osi_symbol("GOOGLE1", "2024-01-19", "C", 150.0)

    def test_strike_above_osi_field_capacity_is_rejected(self):
        # REGRESSION: f"{strike_int:08d}" silently widened to 9 digits, emitting a
        # 22-character symbol that no OSI consumer can parse.
        with self.assertRaises(NormalizationError):
            self.engine.build_osi_symbol("SPX", "2024-01-19", "C", 100_000.0)

    def test_largest_representable_strike_still_builds(self):
        osi = self.engine.build_osi_symbol("SPX", "2024-01-19", "C", 99_999.999)
        self.assertEqual(len(osi), 21)
        self.assertEqual(osi[-8:], "99999999")

    def test_negative_strike_is_rejected(self):
        # REGRESSION: f"{-150000:08d}" is '-0150000' -- a minus sign inside the numeric
        # field, still 21 characters overall, so no length check could catch it.
        with self.assertRaises(NormalizationError):
            self.engine.build_osi_symbol("AAPL", "2024-01-19", "C", -150.0)

    def test_zero_strike_is_rejected(self):
        with self.assertRaises(NormalizationError):
            self.engine.build_osi_symbol("AAPL", "2024-01-19", "C", 0.0)

    def test_sub_mill_strike_is_rejected_not_rounded(self):
        # 150.0005 would round to 150001 mills, i.e. a different listed contract.
        with self.assertRaises(NormalizationError):
            self.engine.build_osi_symbol("AAPL", "2024-01-19", "C", 150.0005)

    def test_unrecognized_right_is_rejected(self):
        # REGRESSION: the old `'C' if type.startswith('C') else 'P'` idiom made every
        # unrecognized token a put.
        with self.assertRaises(NormalizationError):
            self.engine.build_osi_symbol("AAPL", "2024-01-19", "OTHER", 150.0)

    def test_non_iso_expiration_is_rejected(self):
        with self.assertRaises(NormalizationError):
            self.engine.build_osi_symbol("AAPL", "01/19/24", "C", 150.0)


class TestOsiParsing(BaseCase):

    def test_round_trip_padded_compact_and_prefixed_forms_agree(self):
        expected = "NVDA  240621P00450000"
        for form in ("NVDA  240621P00450000", "NVDA240621P00450000", "O:NVDA240621P00450000"):
            components = self.engine.parse_osi_symbol(form)
            self.assertEqual(components.root, "NVDA")
            self.assertEqual(components.expiration_date, "2024-06-21")
            self.assertIs(components.right, OptionRight.PUT)
            self.assertEqual(components.strike_price, 450.0)
            self.assertEqual(
                self.engine.build_osi_symbol(
                    components.root,
                    components.expiration_date,
                    components.right.value,
                    components.strike_price,
                ),
                expected,
            )

    def test_adjusted_root_survives_the_round_trip(self):
        components = self.engine.parse_osi_symbol("AAPL1 240119C00150000")
        self.assertEqual(components.root, "AAPL1")

    def test_non_numeric_fields_are_rejected(self):
        with self.assertRaises(NormalizationError):
            self.engine.parse_osi_symbol("AAPL  2401X9C00150000")

    def test_too_short_symbol_is_rejected(self):
        with self.assertRaises(NormalizationError):
            self.engine.parse_osi_symbol("240119C00150000")   # no root

    def test_invalid_calendar_date_is_rejected(self):
        with self.assertRaises(NormalizationError):
            self.engine.parse_osi_symbol("AAPL  241332C00150000")


class TestPolygonParsing(BaseCase):

    def test_component_fields_normalize(self):
        contract = self.engine.parse_polygon_contract(polygon_record())
        self.assertEqual(contract.standard_osi_symbol, "AAPL  240119C00150000")
        self.assertEqual(contract.option_type, "CALL")
        self.assertEqual(contract.mid_price, 5.30)      # (5.20 + 5.40) / 2, by hand
        self.assertEqual(contract.spread, 0.20)
        self.assertEqual(contract.quality_flags, [])

    def test_ticker_only_record_is_parsed_from_the_symbol(self):
        record = {"ticker": "O:NVDA240621P00450000", "bid": 1.0, "ask": 1.2}
        contract = self.engine.parse_polygon_contract(record)
        self.assertEqual(contract.standard_osi_symbol, "NVDA  240621P00450000")
        self.assertEqual(contract.underlying_ticker, "NVDA")
        self.assertEqual(contract.strike_price, 450.0)
        self.assertEqual(contract.option_type, "PUT")

    def test_contract_type_other_is_rejected(self):
        # REGRESSION: Polygon documents contract_type as "'put', 'call', or in some rare
        # cases, 'other'". The old idiom classified 'other' as a PUT.
        with self.assertRaises(NormalizationError):
            self.engine.parse_polygon_contract(
                polygon_record(ticker=None, contract_type="other")
            )

    def test_missing_underlying_is_rejected_not_defaulted(self):
        # REGRESSION: raw_data.get("underlying_ticker", "AAPL") invented an AAPL contract.
        with self.assertRaises(NormalizationError):
            self.engine.parse_polygon_contract(
                polygon_record(ticker=None, underlying_ticker=None)
            )

    def test_missing_expiration_is_rejected_not_defaulted(self):
        # REGRESSION: the old default was the literal date '2024-01-19'.
        with self.assertRaises(NormalizationError):
            self.engine.parse_polygon_contract(
                polygon_record(ticker=None, expiration_date=None)
            )

    def test_missing_strike_is_rejected_not_defaulted(self):
        # REGRESSION: the old default of 0.0 produced strike '00000000'.
        with self.assertRaises(NormalizationError):
            self.engine.parse_polygon_contract(
                polygon_record(ticker=None, strike_price=None)
            )

    def test_zero_bid_keeps_the_real_midpoint(self):
        # REGRESSION: bid == 0 used to fall back to the `close` last-trade price. A
        # 0.00 x 0.05 market is the normal state of a deep OTM series and its midpoint
        # is 0.025 -- computed by hand here, not by the module.
        contract = self.engine.parse_polygon_contract(
            polygon_record(bid=0.0, ask=0.05, close=9.99)
        )
        self.assertEqual(contract.mid_price, 0.025)
        self.assertEqual(contract.last_price, 9.99)
        self.assertIn(QualityFlag.ZERO_BID.value, contract.quality_flags)

    def test_last_trade_never_substitutes_for_a_missing_midpoint(self):
        contract = self.engine.parse_polygon_contract(
            polygon_record(bid=None, ask=None, close=9.99)
        )
        self.assertIsNone(contract.mid_price)
        self.assertEqual(contract.last_price, 9.99)
        self.assertFalse(contract.is_quotable)

    def test_vendor_symbol_disagreeing_with_fields_is_flagged(self):
        contract = self.engine.parse_polygon_contract(
            polygon_record(strike_price=155.0)   # ticker still says 150
        )
        self.assertIn(QualityFlag.OSI_MISMATCH.value, contract.quality_flags)

    def test_additional_underlyings_flags_a_non_standard_deliverable(self):
        contract = self.engine.parse_polygon_contract(
            polygon_record(additional_underlyings=[{"type": "equity", "amount": 30}])
        )
        self.assertIn(
            QualityFlag.NON_STANDARD_DELIVERABLE.value, contract.quality_flags
        )

    def test_non_standard_shares_per_contract_is_flagged_not_rejected(self):
        contract = self.engine.parse_polygon_contract(
            polygon_record(shares_per_contract=10)
        )
        self.assertEqual(contract.contract_multiplier, 10.0)
        self.assertIn(
            QualityFlag.NON_STANDARD_DELIVERABLE.value, contract.quality_flags
        )


class TestIbkrParsing(BaseCase):

    def test_right_c_and_right_call_agree(self):
        # REGRESSION: IBKR documents `right` as "Valid values are P, PUT, C, CALL".
        # The old `== "C"` test silently classified the literal 'CALL' as a PUT.
        for token in ("C", "c", "CALL", "Call"):
            contract = self.engine.parse_ibkr_contract(ibkr_record(right=token))
            self.assertEqual(contract.option_type, "CALL", msg=token)
        for token in ("P", "PUT", "put"):
            contract = self.engine.parse_ibkr_contract(ibkr_record(right=token))
            self.assertEqual(contract.option_type, "PUT", msg=token)

    def test_unknown_right_is_rejected(self):
        with self.assertRaises(NormalizationError):
            self.engine.parse_ibkr_contract(ibkr_record(right="X"))

    def test_minus_one_no_data_sentinel_yields_no_quote(self):
        # REGRESSION: IBKR documents that a tickPrice of -1 "indicates that there is no
        # data currently available". Averaging it produced mid_price = -1.0 on a
        # contract that passed the bid <= ask check.
        contract = self.engine.parse_ibkr_contract(ibkr_record(bid=-1, ask=-1))
        self.assertIsNone(contract.bid)
        self.assertIsNone(contract.ask)
        self.assertIsNone(contract.mid_price)
        self.assertIsNone(contract.spread)
        self.assertIn(QualityFlag.MISSING_QUOTE.value, contract.quality_flags)

    def test_one_sided_minus_one_still_yields_no_midpoint(self):
        contract = self.engine.parse_ibkr_contract(ibkr_record(bid=-1, ask=5.40))
        self.assertIsNone(contract.mid_price)
        self.assertEqual(contract.ask, 5.40)
        self.assertIn(QualityFlag.MISSING_QUOTE.value, contract.quality_flags)

    def test_legacy_expiry_key_is_still_accepted(self):
        contract = self.engine.parse_ibkr_contract(
            ibkr_record(lastTradeDateOrContractMonth=None, expiry="20240119")
        )
        self.assertEqual(contract.expiration_date, "2024-01-19")

    def test_yyyymm_contract_month_is_rejected(self):
        # IBKR documents that YYYYMM is a contract month, not a last trading day.
        with self.assertRaises(NormalizationError):
            self.engine.parse_ibkr_contract(
                ibkr_record(lastTradeDateOrContractMonth="202401")
            )

    def test_invalid_expiry_is_rejected(self):
        with self.assertRaises(NormalizationError):
            self.engine.parse_ibkr_contract(
                ibkr_record(lastTradeDateOrContractMonth="20241332")
            )

    def test_trading_class_supplies_the_adjusted_root(self):
        # REGRESSION: building the symbol from `symbol` names the standard AAPL series
        # while quoting the adjusted AAPL1 one.
        contract = self.engine.parse_ibkr_contract(ibkr_record(tradingClass="AAPL1"))
        self.assertEqual(contract.standard_osi_symbol, "AAPL1 240119C00150000")
        self.assertEqual(contract.osi_root, "AAPL1")
        self.assertEqual(contract.underlying_ticker, "AAPL")
        self.assertIn(
            QualityFlag.NON_STANDARD_DELIVERABLE.value, contract.quality_flags
        )

    def test_local_symbol_agreeing_with_fields_raises_no_flag(self):
        contract = self.engine.parse_ibkr_contract(
            ibkr_record(localSymbol="AAPL  240119C00150000")
        )
        self.assertNotIn(QualityFlag.OSI_MISMATCH.value, contract.quality_flags)

    def test_local_symbol_disagreeing_with_fields_is_flagged(self):
        contract = self.engine.parse_ibkr_contract(
            ibkr_record(localSymbol="AAPL  240119P00150000")   # put vs the record's call
        )
        self.assertIn(QualityFlag.OSI_MISMATCH.value, contract.quality_flags)

    def test_missing_symbol_is_rejected(self):
        with self.assertRaises(NormalizationError):
            self.engine.parse_ibkr_contract(ibkr_record(symbol=None))


class TestBloombergParsing(BaseCase):

    def test_bloomberg_ticker_normalizes_to_osi(self):
        # REGRESSION: SKILL.md documented this translation but no parser existed; the
        # record fell through to the Polygon parser and produced a defaulted contract.
        contract = self.engine.parse_bloomberg_contract(bloomberg_record())
        self.assertEqual(contract.standard_osi_symbol, "AAPL  240119C00150000")
        self.assertEqual(contract.option_type, "CALL")
        self.assertEqual(contract.expiration_date, "2024-01-19")
        self.assertEqual(contract.strike_price, 150.0)

    def test_put_with_fractional_strike(self):
        contract = self.engine.parse_bloomberg_contract(
            bloomberg_record(ticker="LAMR US 01/17/15 C52.5 Equity")
        )
        self.assertEqual(contract.standard_osi_symbol, "LAMR  150117C00052500")

    def test_malformed_ticker_is_rejected(self):
        with self.assertRaises(NormalizationError):
            self.engine.parse_bloomberg_contract(
                bloomberg_record(ticker="AAPL US C150 Equity")   # no expiry
            )

    def test_bloomberg_ticker_is_not_cross_checked_as_osi(self):
        contract = self.engine.parse_bloomberg_contract(bloomberg_record())
        self.assertNotIn(QualityFlag.OSI_MISMATCH.value, contract.quality_flags)


class TestOpraParsing(BaseCase):

    def test_osi_keyed_record_normalizes(self):
        contract = self.engine.parse_opra_contract(opra_record())
        self.assertEqual(contract.standard_osi_symbol, "AAPL  240119C00150000")
        self.assertEqual(contract.mid_price, 5.30)

    def test_undecodable_symbol_is_rejected(self):
        with self.assertRaises(NormalizationError):
            self.engine.parse_opra_contract(opra_record(osi_symbol="AAPL-JAN24-150-C"))


class TestCrossVendorConsistency(BaseCase):

    def test_all_four_vendors_agree_on_the_same_contract(self):
        """The whole point of the skill: one contract, one key, one midpoint."""
        reports = {
            "POLYGON": self.engine.normalize_chain("POLYGON", [polygon_record()]),
            "IBKR": self.engine.normalize_chain("IBKR", [ibkr_record()]),
            "BLOOMBERG": self.engine.normalize_chain("BLOOMBERG", [bloomberg_record()]),
            "OPRA": self.engine.normalize_chain("OPRA", [opra_record()]),
        }
        contracts = [r.normalized_contracts[0] for r in reports.values()]
        self.assertEqual(
            {c.standard_osi_symbol for c in contracts}, {"AAPL  240119C00150000"}
        )
        self.assertEqual({c.mid_price for c in contracts}, {5.30})
        self.assertEqual({c.spread for c in contracts}, {0.20})
        self.assertEqual({c.option_type for c in contracts}, {"CALL"})
        self.assertEqual({c.expiration_date for c in contracts}, {"2024-01-19"})

    def test_zero_bid_midpoint_agrees_across_vendors(self):
        # REGRESSION: the Polygon path substituted `close` on a zero bid while the IBKR
        # path averaged, so the same contract carried two different midpoints.
        polygon = self.engine.parse_polygon_contract(
            polygon_record(bid=0.0, ask=0.05, close=9.99)
        )
        ibkr = self.engine.parse_ibkr_contract(ibkr_record(bid=0.0, ask=0.05, last=9.99))
        self.assertEqual(polygon.mid_price, ibkr.mid_price)


class TestChainNormalization(BaseCase):

    def test_normalize_ibkr_and_polygon_chains(self):
        raw_ibkr = [
            {"symbol": "AAPL", "expiry": "20240119", "right": "C", "strike": 150.0,
             "bid": 5.20, "ask": 5.40}
        ]
        report = self.engine.normalize_chain("IBKR", raw_ibkr)
        self.assertEqual(report.quality_status, "DATA_INTEGRITY_OK")
        contract = report.normalized_contracts[0]
        self.assertEqual(contract.standard_osi_symbol, "AAPL  240119C00150000")
        self.assertEqual(contract.mid_price, 5.30)
        self.assertAlmostEqual(contract.spread, 0.20, delta=0.001)

    def test_invalid_quote_detection(self):
        raw_bad = [polygon_record(ticker=None, bid=6.00, ask=5.00)]
        report = self.engine.normalize_chain("POLYGON", raw_bad)
        self.assertEqual(report.quality_status, "INVALID_QUOTE_DETECTED")
        contract = report.normalized_contracts[0]
        self.assertIn(QualityFlag.INVALID_BID_ASK.value, contract.quality_flags)

    def test_crossed_quote_reports_signed_spread_and_no_midpoint(self):
        # REGRESSION: spread was clamped with max(0.0, ask - bid), hiding the size of the
        # inversion in the one field an integrity audit inspects, and a midpoint was
        # still computed from a book that cannot trade at it.
        report = self.engine.normalize_chain(
            "POLYGON", [polygon_record(ticker=None, bid=6.00, ask=5.00)]
        )
        contract = report.normalized_contracts[0]
        self.assertEqual(contract.spread, -1.00)
        self.assertIsNone(contract.mid_price)

    def test_unknown_vendor_raises_instead_of_falling_through(self):
        # REGRESSION: any vendor other than 'IBKR' fell through to the Polygon parser,
        # so a Bloomberg chain normalized into defaulted AAPL contracts.
        with self.assertRaises(NormalizationError):
            self.engine.normalize_chain("TRADIER", [polygon_record()])

    def test_one_bad_record_does_not_discard_the_chain(self):
        # REGRESSION: an unparseable expiry raised out of normalize_chain and lost every
        # good contract in the snapshot.
        records = [
            polygon_record(),
            polygon_record(ticker=None, expiration_date="19/01/2024"),
            polygon_record(ticker="O:NVDA240621P00450000", underlying_ticker="NVDA",
                           expiration_date="2024-06-21", contract_type="put",
                           strike_price=450.0),
        ]
        report = self.engine.normalize_chain("POLYGON", records)
        self.assertEqual(len(report.normalized_contracts), 2)
        self.assertEqual(len(report.rejected_records), 1)
        self.assertEqual(report.rejected_records[0].index, 1)
        self.assertEqual(report.quality_status, "RECORDS_REJECTED")

    def test_record_counts_always_reconcile(self):
        records = [polygon_record(), polygon_record(ticker=None, strike_price=None)]
        report = self.engine.normalize_chain("POLYGON", records)
        self.assertEqual(
            report.total_records_processed,
            len(report.normalized_contracts) + len(report.rejected_records),
        )

    def test_non_mapping_record_is_quarantined(self):
        report = self.engine.normalize_chain("POLYGON", [polygon_record(), "AAPL"])
        self.assertEqual(len(report.normalized_contracts), 1)
        self.assertEqual(len(report.rejected_records), 1)

    def test_reject_on_error_false_reraises(self):
        engine = OptionsChainNormalizationEngine(
            NormalizationConfig(reject_on_error=False)
        )
        with self.assertRaises(NormalizationError):
            engine.normalize_chain(
                "POLYGON", [polygon_record(ticker=None, strike_price=None)]
            )

    def test_zero_bid_alone_does_not_degrade_chain_status(self):
        # Most strikes in a real chain are bid-less; a status that read DEGRADED on every
        # snapshot would be ignored by its operators.
        report = self.engine.normalize_chain(
            "POLYGON", [polygon_record(bid=0.0, ask=0.05)]
        )
        self.assertEqual(report.quality_status, "DATA_INTEGRITY_OK")
        self.assertEqual(report.flag_counts, {QualityFlag.ZERO_BID.value: 1})

    def test_missing_quote_degrades_chain_status(self):
        report = self.engine.normalize_chain(
            "IBKR", [ibkr_record(bid=-1, ask=-1)]
        )
        self.assertEqual(report.quality_status, "DEGRADED_QUOTES")

    def test_symbology_mismatch_outranks_quote_status(self):
        report = self.engine.normalize_chain(
            "POLYGON", [polygon_record(strike_price=155.0, bid=0.0, ask=0.05)]
        )
        self.assertEqual(report.quality_status, "SYMBOLOGY_MISMATCH")

    def test_empty_chain_is_reported_clean(self):
        report = self.engine.normalize_chain("POLYGON", [])
        self.assertEqual(report.total_records_processed, 0)
        self.assertEqual(report.quality_status, "DATA_INTEGRITY_OK")
        self.assertEqual(report.normalized_contracts, [])

    def test_report_is_the_documented_dataclass(self):
        report = self.engine.normalize_chain("POLYGON", [polygon_record()])
        self.assertIsInstance(report, OptionsNormalizationReport)
        self.assertIsInstance(report.normalized_contracts[0], NormalizedOptionContract)

    def test_registered_parser_extends_vendor_coverage(self):
        def tradier(raw):
            return self.engine.parse_osi_symbol(raw["option_symbol"]) and \
                self.engine.parse_opra_contract({"osi_symbol": raw["option_symbol"],
                                                 "bid": raw["bid"], "ask": raw["ask"]})

        self.engine.register_parser("TRADIER", tradier)
        report = self.engine.normalize_chain(
            "TRADIER",
            [{"option_symbol": "AAPL240119C00150000", "bid": 5.20, "ask": 5.40}],
        )
        self.assertEqual(
            report.normalized_contracts[0].standard_osi_symbol, "AAPL  240119C00150000"
        )


class TestNumericEdgeCases(BaseCase):

    def test_nan_quote_is_treated_as_absent(self):
        contract = self.engine.parse_polygon_contract(
            polygon_record(bid=float("nan"), ask=5.40)
        )
        self.assertIsNone(contract.bid)
        self.assertIsNone(contract.mid_price)

    def test_infinite_quote_is_treated_as_absent(self):
        contract = self.engine.parse_polygon_contract(
            polygon_record(bid=5.20, ask=float("inf"))
        )
        self.assertIsNone(contract.ask)
        self.assertIsNone(contract.mid_price)

    def test_nan_strike_is_rejected(self):
        with self.assertRaises(NormalizationError):
            self.engine.parse_polygon_contract(
                polygon_record(ticker=None, strike_price=float("nan"))
            )

    def test_string_quotes_are_coerced(self):
        contract = self.engine.parse_polygon_contract(
            polygon_record(bid="5.20", ask="5.40")
        )
        self.assertEqual(contract.mid_price, 5.30)

    def test_locked_market_is_a_valid_zero_spread_quote(self):
        contract = self.engine.parse_polygon_contract(
            polygon_record(bid=5.30, ask=5.30)
        )
        self.assertEqual(contract.mid_price, 5.30)
        self.assertEqual(contract.spread, 0.0)
        self.assertEqual(contract.quality_flags, [])

    def test_zero_ask_is_not_an_offer(self):
        contract = self.engine.parse_polygon_contract(
            polygon_record(bid=0.0, ask=0.0)
        )
        self.assertIsNone(contract.mid_price)
        self.assertIn(QualityFlag.MISSING_QUOTE.value, contract.quality_flags)

    def test_open_interest_and_volume_are_integers(self):
        contract = self.engine.parse_polygon_contract(
            polygon_record(open_interest=1234.0, volume="567")
        )
        self.assertEqual(contract.open_interest, 1234)
        self.assertEqual(contract.volume, 567)

    def test_null_alias_does_not_mask_a_populated_canonical_field(self):
        # Vendors routinely emit both an alias and its canonical name with only one
        # populated. A null alias must not stop the search.
        record = dict(polygon_record())
        record["bid"] = None
        record["bid_price"] = 5.20
        contract = self.engine.parse_polygon_contract(record)
        self.assertEqual(contract.bid, 5.20)
        self.assertEqual(contract.mid_price, 5.30)

    def test_null_alias_does_not_mask_a_populated_open_interest(self):
        record = dict(ibkr_record())
        record["openInterest"] = None
        record["open_interest"] = 4321
        self.assertEqual(self.engine.parse_ibkr_contract(record).open_interest, 4321)

    def test_populated_sentinel_alias_still_wins(self):
        # -1 is the vendor stating there is no quote, not an absent field, so it must not
        # fall through to a second key.
        record = dict(ibkr_record())
        record["bid"] = -1
        record["bidPrice"] = 5.20
        contract = self.engine.parse_ibkr_contract(record)
        self.assertIsNone(contract.bid)
        self.assertIsNone(contract.mid_price)


class TestCenturyAndAdjustedRoots(BaseCase):

    def test_pre_2000_expiration_is_rejected(self):
        # The OSI date field has no century: a 1999 expiry would encode as '99' and
        # decode back as 2099.
        with self.assertRaises(NormalizationError):
            self.engine.build_osi_symbol("AAPL", "1999-01-15", "C", 150.0)

    def test_post_2099_expiration_is_rejected(self):
        with self.assertRaises(NormalizationError):
            self.engine.build_osi_symbol("AAPL", "2100-01-15", "C", 150.0)

    def test_century_boundaries_are_accepted(self):
        self.assertEqual(
            self.engine.build_osi_symbol("AAPL", "2099-01-15", "C", 150.0)[6:12], "990115"
        )

    def test_adjusted_root_is_flagged_even_when_it_is_the_underlying(self):
        # An OCC numeric suffix marks a non-standard deliverable regardless of whether
        # the vendor also reports a distinct underlying ticker.
        contract = self.engine.parse_polygon_contract(
            polygon_record(ticker="O:AAPL1240119C00150000", underlying_ticker="AAPL1")
        )
        self.assertEqual(contract.osi_root, "AAPL1")
        self.assertEqual(contract.underlying_ticker, "AAPL1")
        self.assertIn(
            QualityFlag.NON_STANDARD_DELIVERABLE.value, contract.quality_flags
        )

    def test_mini_option_root_is_flagged(self):
        contract = self.engine.parse_opra_contract(
            opra_record(osi_symbol="AAPL7 240119C00150000")
        )
        self.assertIn(
            QualityFlag.NON_STANDARD_DELIVERABLE.value, contract.quality_flags
        )

    def test_generator_input_is_counted_correctly(self):
        # A generator consumed by the loop would report zero records offered, making a
        # fully rejected chain indistinguishable from an empty one.
        report = self.engine.normalize_chain(
            "POLYGON", (r for r in [polygon_record(), polygon_record()])
        )
        self.assertEqual(report.total_records_processed, 2)
        self.assertEqual(len(report.normalized_contracts), 2)


if __name__ == '__main__':
    unittest.main()
