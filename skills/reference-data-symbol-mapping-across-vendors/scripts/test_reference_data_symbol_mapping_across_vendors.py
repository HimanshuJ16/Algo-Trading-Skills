"""Unit tests for the cross-vendor symbol mapping engine.

Expected values are real vendor symbology (Bloomberg composite and primary tickers,
Refinitiv RICs, ISINs) and dated corporate-action facts sourced from SEC filings and
issuer press releases -- never a value read back out of the implementation.

Tests marked REGRESSION fail against the behaviour this module shipped with before the
2026-08 audit:
  * reverse lookups returned an upper-cased vendor symbol ('AAPL US EQUITY'),
  * a conflicting registration logged a warning and then silently overwrote,
  * a second symbol for one (canonical, vendor) silently replaced the first,
  * there was no point-in-time resolution at all, so a recycled ticker resolved to
    whichever issuer happened to be registered last.
"""
import logging
import unittest
from datetime import date, datetime

from reference_data_symbol_mapping_across_vendors import (
    BBG,
    ISIN,
    RIC,
    STATUS_FULL_COVERAGE,
    STATUS_PARTIAL_COVERAGE,
    TICKER,
    AmbiguousMappingError,
    SymbolMappingConfig,
    SymbolMappingEngine,
    VendorSymbolEntry,
)

# Silence the engine's audit logging for the duration of the suite.
logging.getLogger(
    "reference_data_symbol_mapping_across_vendors"
).setLevel(logging.CRITICAL)


def apple_entries():
    """Apple Inc across four vendors. Every string is real vendor symbology.

    'AAPL US Equity' is Bloomberg's composite ticker and 'AAPL UW Equity' its Nasdaq
    primary-exchange ticker -- both correct, not interchangeable. 'AAPL.O' is the
    Refinitiv RIC (root + '.' + exchange code, O = Nasdaq). 'US0378331005' is Apple's
    ISIN.
    """
    return [
        VendorSymbolEntry("AAPL", "Bloomberg", "AAPL US Equity", BBG),
        VendorSymbolEntry("AAPL", "Bloomberg", "AAPL UW Equity", BBG, is_primary=False),
        VendorSymbolEntry("AAPL", "Refinitiv", "AAPL.O", RIC),
        VendorSymbolEntry("AAPL", "ISIN_DB", "US0378331005", ISIN),
    ]


class TestLookupFidelity(unittest.TestCase):
    """What comes back out is what was registered."""

    def setUp(self):
        self.engine = SymbolMappingEngine()
        for entry in apple_entries():
            self.engine.register_mapping(entry)

    def test_reverse_lookup_returns_the_vendor_spelling_verbatim(self):
        # REGRESSION: previously returned 'AAPL US EQUITY', which is not a Bloomberg
        # ticker. The reverse lookup exists to be handed to a vendor API or router.
        self.assertEqual(
            self.engine.reverse_lookup("AAPL", "Bloomberg"), "AAPL US Equity"
        )

    def test_forward_lookup_returns_the_canonical_spelling_verbatim(self):
        # REGRESSION: the canonical symbol was upper-cased on the way out, so a
        # lower-case internal convention silently changed shape.
        engine = SymbolMappingEngine()
        engine.register_mapping(
            VendorSymbolEntry("us.aapl", "Refinitiv", "AAPL.O", RIC)
        )
        self.assertEqual(engine.forward_lookup("Refinitiv", "AAPL.O"), "us.aapl")

    def test_forward_lookup_resolves_each_vendor(self):
        self.assertEqual(self.engine.forward_lookup("Refinitiv", "AAPL.O"), "AAPL")
        self.assertEqual(
            self.engine.forward_lookup("Bloomberg", "AAPL US Equity"), "AAPL"
        )
        self.assertEqual(
            self.engine.forward_lookup("ISIN_DB", "US0378331005"), "AAPL"
        )

    def test_secondary_symbol_resolves_forward_but_is_not_the_reverse_answer(self):
        # The Nasdaq primary-exchange ticker must still map *in*; it must not be what
        # comes back *out* when the caller asks for "Apple at Bloomberg".
        self.assertEqual(
            self.engine.forward_lookup("Bloomberg", "AAPL UW Equity"), "AAPL"
        )
        self.assertEqual(
            self.engine.reverse_lookup("AAPL", "Bloomberg"), "AAPL US Equity"
        )

    def test_reverse_lookup_all_exposes_every_vendor_symbol(self):
        symbols = {
            e.vendor_symbol
            for e in self.engine.reverse_lookup_all("AAPL", "Bloomberg")
        }
        self.assertEqual(symbols, {"AAPL US Equity", "AAPL UW Equity"})

    def test_lookup_misses_return_none(self):
        self.assertIsNone(self.engine.forward_lookup("Refinitiv", "UNKNOWN.X"))
        self.assertIsNone(self.engine.reverse_lookup("AAPL", "FactSet"))
        self.assertIsNone(self.engine.reverse_lookup("MSFT", "Bloomberg"))

    def test_translate_routes_through_the_canonical_symbol(self):
        self.assertEqual(
            self.engine.translate("Refinitiv", "AAPL.O", "Bloomberg"),
            "AAPL US Equity",
        )

    def test_translate_returns_none_rather_than_a_canonical_consolation(self):
        # Second leg missing: a caller expecting a FactSet symbol would route on
        # whatever it got back.
        self.assertIsNone(
            self.engine.translate("Refinitiv", "AAPL.O", "FactSet")
        )
        self.assertIsNone(
            self.engine.translate("Refinitiv", "NOPE.O", "Bloomberg")
        )


class TestKeyNormalisation(unittest.TestCase):
    """Keys are normalised; stored values are not."""

    def setUp(self):
        self.engine = SymbolMappingEngine()
        for entry in apple_entries():
            self.engine.register_mapping(entry)

    def test_lookup_is_case_insensitive_by_default(self):
        self.assertEqual(self.engine.forward_lookup("refinitiv", "aapl.o"), "AAPL")
        self.assertEqual(
            self.engine.reverse_lookup("aapl", "BLOOMBERG"), "AAPL US Equity"
        )

    def test_surrounding_and_repeated_whitespace_is_tolerated(self):
        # CSV extracts arrive padded; 'AAPL  US Equity' is a formatting artefact.
        self.assertEqual(self.engine.forward_lookup("Refinitiv", "  AAPL.O "), "AAPL")
        self.assertEqual(
            self.engine.forward_lookup("Bloomberg", "AAPL  US Equity"), "AAPL"
        )

    def test_case_sensitive_config_distinguishes_case(self):
        engine = SymbolMappingEngine(SymbolMappingConfig(case_sensitive=True))
        engine.register_mapping(VendorSymbolEntry("AAPL", "Refinitiv", "AAPL.O", RIC))
        self.assertEqual(engine.forward_lookup("Refinitiv", "AAPL.O"), "AAPL")
        self.assertIsNone(engine.forward_lookup("refinitiv", "aapl.o"))


class TestConflictDetection(unittest.TestCase):
    """A key that would resolve two ways is a data defect, not a lookup problem."""

    def test_forward_conflict_raises_by_default(self):
        # REGRESSION: previously logged a warning and overwrote, silently repointing
        # every join already keyed on ('NYSE', 'S').
        engine = SymbolMappingEngine()
        engine.register_mapping(VendorSymbolEntry("SPRINT", "NYSE", "S", TICKER))
        with self.assertRaises(AmbiguousMappingError):
            engine.register_mapping(
                VendorSymbolEntry("SENTINELONE", "NYSE", "S", TICKER)
            )
        self.assertEqual(engine.forward_lookup("NYSE", "S"), "SPRINT")

    def test_second_primary_symbol_for_one_vendor_raises(self):
        # REGRESSION: previously the second registration silently became the reverse
        # answer, so a router asking for "Apple at Bloomberg" could get the
        # primary-exchange ticker where the composite was intended, or the reverse.
        engine = SymbolMappingEngine()
        engine.register_mapping(
            VendorSymbolEntry("AAPL", "Bloomberg", "AAPL US Equity", BBG)
        )
        with self.assertRaises(AmbiguousMappingError):
            engine.register_mapping(
                VendorSymbolEntry("AAPL", "Bloomberg", "AAPL UW Equity", BBG)
            )
        self.assertEqual(
            engine.reverse_lookup("AAPL", "Bloomberg"), "AAPL US Equity"
        )

    def test_identical_re_registration_is_idempotent(self):
        # Re-running an ingest must not raise, and must not inflate the mapping count.
        engine = SymbolMappingEngine()
        entry = VendorSymbolEntry("AAPL", "Refinitiv", "AAPL.O", RIC)
        engine.register_mapping(entry)
        engine.register_mapping(
            VendorSymbolEntry("AAPL", "Refinitiv", "AAPL.O", RIC)
        )
        self.assertEqual(engine.get_coverage_report().total_mappings, 1)

    def test_case_variant_re_registration_is_idempotent_not_conflicting(self):
        engine = SymbolMappingEngine()
        engine.register_mapping(VendorSymbolEntry("AAPL", "Refinitiv", "AAPL.O", RIC))
        engine.register_mapping(VendorSymbolEntry("aapl", "refinitiv", "aapl.o", RIC))
        self.assertEqual(engine.get_coverage_report().total_mappings, 1)

    def test_allow_ambiguous_keeps_the_first_registration_and_reports_it(self):
        engine = SymbolMappingEngine(SymbolMappingConfig(allow_ambiguous=True))
        engine.register_mapping(VendorSymbolEntry("SPRINT", "NYSE", "S", TICKER))
        engine.register_mapping(VendorSymbolEntry("SENTINELONE", "NYSE", "S", TICKER))

        self.assertEqual(engine.forward_lookup("NYSE", "S"), "SPRINT")
        self.assertEqual(len(engine.registered_conflicts()), 1)

        report = engine.get_coverage_report()
        self.assertEqual(report.ambiguous_mappings, ["NYSE/S"])
        self.assertEqual(report.status, STATUS_PARTIAL_COVERAGE)

    def test_allow_ambiguous_reports_a_reverse_conflict_separately(self):
        engine = SymbolMappingEngine(SymbolMappingConfig(allow_ambiguous=True))
        engine.register_mapping(
            VendorSymbolEntry("AAPL", "Bloomberg", "AAPL US Equity", BBG)
        )
        engine.register_mapping(
            VendorSymbolEntry("AAPL", "Bloomberg", "AAPL UW Equity", BBG)
        )
        report = engine.get_coverage_report()
        self.assertEqual(report.ambiguous_reverse_mappings, ["AAPL@BLOOMBERG"])
        self.assertEqual(report.ambiguous_mappings, [])

    def test_secondary_registration_is_not_a_conflict(self):
        engine = SymbolMappingEngine()
        engine.register_mapping(
            VendorSymbolEntry("AAPL", "Bloomberg", "AAPL US Equity", BBG)
        )
        engine.register_mapping(
            VendorSymbolEntry("AAPL", "Bloomberg", "AAPL UW Equity", BBG,
                              is_primary=False)
        )
        self.assertEqual(engine.get_coverage_report().ambiguous_reverse_mappings, [])


class TestPointInTimeResolution(unittest.TestCase):
    """Ticker recycling: ('NYSE', 'S') is unique only inside a window.

    Sprint Corporation traded on the NYSE under 'S'; the NYSE removed the class from
    listing and registration at the opening of business on 13 April 2020 (SEC Form
    25-NSE). SentinelOne, Inc. listed on the NYSE under 'S' at its IPO on 30 June 2021.
    """

    def setUp(self):
        self.engine = SymbolMappingEngine()
        self.engine.register_mapping(VendorSymbolEntry(
            "SPRINT_CORP", "NYSE", "S", TICKER,
            effective_to=date(2020, 4, 13),
        ))
        self.engine.register_mapping(VendorSymbolEntry(
            "SENTINELONE", "NYSE", "S", TICKER,
            effective_from=date(2021, 6, 30),
        ))

    def test_non_overlapping_windows_are_not_a_conflict(self):
        # Both registrations above succeeded; the same key legitimately carries two
        # issuers at different times.
        self.assertEqual(self.engine.get_coverage_report().ambiguous_mappings, [])

    def test_historical_date_resolves_to_the_issuer_of_the_day(self):
        # REGRESSION: without as_of, a 2019 tick resolved to whichever issuer was
        # registered last -- SentinelOne, which did not exist as a listed company.
        self.assertEqual(
            self.engine.forward_lookup("NYSE", "S", as_of=date(2019, 6, 1)),
            "SPRINT_CORP",
        )

    def test_current_date_resolves_to_the_current_issuer(self):
        self.assertEqual(
            self.engine.forward_lookup("NYSE", "S", as_of=date(2021, 7, 1)),
            "SENTINELONE",
        )

    def test_omitting_as_of_resolves_the_currently_effective_mapping(self):
        self.assertEqual(self.engine.forward_lookup("NYSE", "S"), "SENTINELONE")

    def test_the_gap_between_windows_is_a_miss_not_a_stale_answer(self):
        # Between the delisting and the IPO the NYSE ticker 'S' mapped to nothing.
        self.assertIsNone(
            self.engine.forward_lookup("NYSE", "S", as_of=date(2020, 6, 1))
        )

    def test_window_is_half_open_at_both_ends(self):
        # effective_to is exclusive: 13 April 2020 is the first day Sprint's mapping is
        # not valid. effective_from is inclusive: 30 June 2021 is SentinelOne's first.
        self.assertIsNone(
            self.engine.forward_lookup("NYSE", "S", as_of=date(2020, 4, 13))
        )
        self.assertEqual(
            self.engine.forward_lookup("NYSE", "S", as_of=date(2020, 4, 12)),
            "SPRINT_CORP",
        )
        self.assertEqual(
            self.engine.forward_lookup("NYSE", "S", as_of=date(2021, 6, 30)),
            "SENTINELONE",
        )
        self.assertIsNone(
            self.engine.forward_lookup("NYSE", "S", as_of=date(2021, 6, 29))
        )

    def test_reverse_lookup_is_also_point_in_time(self):
        self.assertEqual(
            self.engine.reverse_lookup("SPRINT_CORP", "NYSE", as_of=date(2019, 6, 1)),
            "S",
        )
        self.assertIsNone(self.engine.reverse_lookup("SPRINT_CORP", "NYSE"))


class TestRetirement(unittest.TestCase):
    """FB -> META: the ticker moved on 9 June 2022, the listing and CUSIP did not."""

    def setUp(self):
        self.engine = SymbolMappingEngine()
        self.engine.register_mapping(
            VendorSymbolEntry("META_PLATFORMS", "Nasdaq", "FB", TICKER)
        )

    def test_retiring_then_re_registering_abuts_without_conflict(self):
        changeover = date(2022, 6, 9)
        self.assertEqual(self.engine.retire_mapping("Nasdaq", "FB", changeover), 1)
        self.engine.register_mapping(VendorSymbolEntry(
            "META_PLATFORMS", "Nasdaq", "META", TICKER, effective_from=changeover,
        ))

        self.assertEqual(
            self.engine.forward_lookup("Nasdaq", "FB", as_of=date(2022, 6, 8)),
            "META_PLATFORMS",
        )
        self.assertIsNone(self.engine.forward_lookup("Nasdaq", "FB"))
        self.assertEqual(self.engine.forward_lookup("Nasdaq", "META"), "META_PLATFORMS")
        self.assertEqual(self.engine.reverse_lookup("META_PLATFORMS", "Nasdaq"), "META")

    def test_the_isin_mapping_is_untouched_by_the_ticker_change(self):
        # The rename moved the ticker only; joins keyed on the ISIN never break.
        self.engine.register_mapping(VendorSymbolEntry(
            "META_PLATFORMS", "ISIN_DB", "US30303M1027", ISIN,
        ))
        self.engine.retire_mapping("Nasdaq", "FB", date(2022, 6, 9))
        self.assertEqual(
            self.engine.forward_lookup("ISIN_DB", "US30303M1027"), "META_PLATFORMS"
        )

    def test_retiring_an_unknown_symbol_reports_zero(self):
        self.assertEqual(
            self.engine.retire_mapping("Nasdaq", "NOSUCH", date(2022, 6, 9)), 0
        )

    def test_retiring_an_already_closed_mapping_is_not_repeated(self):
        self.engine.retire_mapping("Nasdaq", "FB", date(2022, 6, 9))
        self.assertEqual(
            self.engine.retire_mapping("Nasdaq", "FB", date(2023, 1, 1)), 0
        )

    def test_retirement_before_the_start_of_the_window_raises(self):
        engine = SymbolMappingEngine()
        engine.register_mapping(VendorSymbolEntry(
            "SENTINELONE", "NYSE", "S", TICKER, effective_from=date(2021, 6, 30),
        ))
        with self.assertRaises(ValueError):
            engine.retire_mapping("NYSE", "S", date(2020, 1, 1))


class TestRegistrationValidation(unittest.TestCase):

    def setUp(self):
        self.engine = SymbolMappingEngine()

    def test_blank_fields_are_rejected(self):
        # A blank symbol registers the key ('', ''), which then answers every blank
        # lookup an upstream feed produces.
        for entry in (
            VendorSymbolEntry("", "Refinitiv", "AAPL.O", RIC),
            VendorSymbolEntry("AAPL", "   ", "AAPL.O", RIC),
            VendorSymbolEntry("AAPL", "Refinitiv", "", RIC),
            VendorSymbolEntry("AAPL", "Refinitiv", "AAPL.O", ""),
        ):
            with self.subTest(entry=entry):
                with self.assertRaises(ValueError):
                    self.engine.register_mapping(entry)

    def test_non_string_symbol_is_rejected_not_crashed_on(self):
        with self.assertRaises(ValueError):
            self.engine.register_mapping(
                VendorSymbolEntry("AAPL", "Refinitiv", None, RIC)
            )

    def test_inverted_effective_window_is_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.register_mapping(VendorSymbolEntry(
                "AAPL", "Refinitiv", "AAPL.O", RIC,
                effective_from=date(2022, 1, 1), effective_to=date(2021, 1, 1),
            ))

    def test_empty_effective_window_is_rejected(self):
        # from == to is a half-open window covering no days at all.
        with self.assertRaises(ValueError):
            self.engine.register_mapping(VendorSymbolEntry(
                "AAPL", "Refinitiv", "AAPL.O", RIC,
                effective_from=date(2022, 1, 1), effective_to=date(2022, 1, 1),
            ))

    def test_a_datetime_is_accepted_where_a_date_is_expected(self):
        # datetime subclasses date but comparing the two raises TypeError, so an
        # unguarded timestamp from a tick loop would crash the lookup rather than miss.
        engine = SymbolMappingEngine()
        engine.register_mapping(VendorSymbolEntry(
            "SPRINT_CORP", "NYSE", "S", TICKER,
            effective_to=datetime(2020, 4, 13, 9, 30),
        ))
        self.assertEqual(
            engine.forward_lookup("NYSE", "S", as_of=datetime(2019, 6, 1, 14, 0)),
            "SPRINT_CORP",
        )
        self.assertIsNone(engine.forward_lookup("NYSE", "S"))

    def test_unrecognised_identifier_type_is_accepted(self):
        # OSI option symbols and MIC-scoped local codes are legitimate symbology.
        self.engine.register_mapping(VendorSymbolEntry(
            "AAPL_20260918_C200", "OPRA", "AAPL  260918C00200000", "OSI",
        ))
        self.assertEqual(
            self.engine.forward_lookup("OPRA", "AAPL  260918C00200000"),
            "AAPL_20260918_C200",
        )


class TestCoverageReport(unittest.TestCase):

    def setUp(self):
        self.engine = SymbolMappingEngine()
        for entry in apple_entries():
            self.engine.register_mapping(entry)

    def test_full_coverage_when_nothing_is_missing(self):
        report = self.engine.get_coverage_report()
        self.assertEqual(report.status, STATUS_FULL_COVERAGE)
        self.assertEqual(report.total_canonical_symbols, 1)
        self.assertEqual(report.total_mappings, 4)
        self.assertEqual(
            report.vendors_covered, ["BLOOMBERG", "ISIN_DB", "REFINITIV"]
        )

    def test_expected_universe_exposes_unmapped_symbols(self):
        report = self.engine.get_coverage_report(expected_canonical=["AAPL", "MSFT"])
        self.assertEqual(report.unmapped_canonical, ["MSFT"])
        self.assertEqual(report.status, STATUS_PARTIAL_COVERAGE)

    def test_expected_vendors_exposes_per_vendor_gaps(self):
        report = self.engine.get_coverage_report(
            expected_canonical=["AAPL"],
            expected_vendors=["Bloomberg", "FactSet"],
        )
        self.assertEqual(report.missing_vendor_coverage, ["AAPL@FACTSET"])
        self.assertEqual(report.status, STATUS_PARTIAL_COVERAGE)

    def test_coverage_counts_only_mappings_effective_at_as_of(self):
        engine = SymbolMappingEngine()
        engine.register_mapping(VendorSymbolEntry(
            "SPRINT_CORP", "NYSE", "S", TICKER, effective_to=date(2020, 4, 13),
        ))
        engine.register_mapping(VendorSymbolEntry(
            "SENTINELONE", "NYSE", "S", TICKER, effective_from=date(2021, 6, 30),
        ))

        historical = engine.get_coverage_report(as_of=date(2019, 6, 1))
        self.assertEqual(historical.total_mappings, 1)
        self.assertEqual(historical.total_canonical_symbols, 1)
        self.assertEqual(historical.as_of, date(2019, 6, 1))

        current = engine.get_coverage_report()
        self.assertEqual(current.total_mappings, 1)
        self.assertEqual(current.vendors_covered, ["NYSE"])

    def test_canonical_symbols_is_point_in_time(self):
        engine = SymbolMappingEngine()
        engine.register_mapping(VendorSymbolEntry(
            "SPRINT_CORP", "NYSE", "S", TICKER, effective_to=date(2020, 4, 13),
        ))
        self.assertEqual(engine.canonical_symbols(), set())
        self.assertEqual(
            engine.canonical_symbols(as_of=date(2019, 6, 1)), {"SPRINT_CORP"}
        )

    def test_empty_engine_reports_no_gaps_and_no_coverage(self):
        report = SymbolMappingEngine().get_coverage_report()
        self.assertEqual(report.total_mappings, 0)
        self.assertEqual(report.total_canonical_symbols, 0)
        self.assertEqual(report.status, STATUS_FULL_COVERAGE)


if __name__ == "__main__":
    unittest.main()
