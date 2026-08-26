"""Unit tests for the ISIN/CUSIP/SEDOL/FIGI cross-reference service.

Expected check digits below are derived by hand from the published algorithms (the
arithmetic is written out in each test) or taken from a standard's own worked
example, never by re-running the implementation's formula. Tests marked REGRESSION
fail against the behaviour this module shipped with before the 2026-08 audit.
"""
import logging
import unittest

from isin_cusip_sedol_cross_reference_service import (
    CUSIP,
    FIGI,
    ISIN,
    SEDOL,
    STATUS_INVALID_CHECKSUM,
    STATUS_MATCH_FOUND,
    STATUS_NOT_FOUND,
    TICKER,
    IdentifierCrossReferenceReport,
    IsinCusipSedolCrossReferenceEngine,
    SecurityMasterRecord,
)

# Silence the engine's audit logging for the duration of the suite.
logging.getLogger(
    "isin_cusip_sedol_cross_reference_service"
).setLevel(logging.CRITICAL)


class TestIsinChecksum(unittest.TestCase):
    """ISO 6166: Luhn over the character-expanded digit string."""

    def setUp(self):
        self.engine = IsinCusipSedolCrossReferenceEngine()

    def test_accepts_real_isins_across_jurisdictions(self):
        # Real, published ISINs. A single-jurisdiction test would not exercise the
        # letter expansion (a letter becomes two digits and shifts every Luhn
        # doubling position after it).
        for isin in (
            "US0378331005",   # Apple Inc
            "US5949181045",   # Microsoft Corp
            "US30303M1027",   # Meta Platforms, letter inside the NSIN
            "GB0002634946",   # BAE Systems
            "DE000BAY0017",   # Bayer AG, three letters in the NSIN
            "AU0000XVGZA3",   # letters late in the NSIN
            "NL0000009165",
            "FR0000120271",
            "JP3633400001",
            "CH0038863350",
            "IE00B4L5Y983",
            "CA0679011084",
            "KYG875721634",   # Tencent Holdings, Cayman Islands
            "XS0629974352",   # Euroclear/Clearstream, not an ISO 3166 country
            "US0231351067",   # Amazon.com Inc
        ):
            with self.subTest(isin=isin):
                self.assertTrue(self.engine.validate_isin_checksum(isin))

    def test_rejects_single_character_corruption(self):
        for isin in (
            "US0378331009", "GB0002634947", "DE000BAY0018",
            "AU0000XVGZA4", "IE00B4L5Y984",
        ):
            with self.subTest(isin=isin):
                self.assertFalse(self.engine.validate_isin_checksum(isin))

    def test_rejects_malformed_format(self):
        for bad in (
            "US037833100",     # 11 characters
            "US03783310055",   # 13 characters
            "0S0378331005",    # prefix must be two letters
            "US037833100X",    # check digit must be numeric
            "",
            "US 0378331005",   # embedded space is not stripped by the validator
        ):
            with self.subTest(value=bad):
                self.assertFalse(self.engine.validate_isin_checksum(bad))

    def test_us_isin_is_us_plus_cusip_plus_new_check_digit(self):
        # The documented construction, exercised end to end: Apple's CUSIP is
        # 037833100 and its ISIN is that CUSIP wrapped in "US" + a *new* check digit.
        self.assertTrue(self.engine.validate_cusip_checksum("037833100"))
        self.assertTrue(self.engine.validate_isin_checksum("US" + "037833100" + "5"))
        # The CUSIP's own check digit (0) is not reusable as the ISIN's.
        self.assertFalse(self.engine.validate_isin_checksum("US" + "037833100" + "0"))


class TestCusipChecksum(unittest.TestCase):
    """ANSI X9.6: double-add-double over characters 1-8."""

    def setUp(self):
        self.engine = IsinCusipSedolCrossReferenceEngine()

    def test_hand_derived_check_digit(self):
        # 037833100, positions 1-8 = 0 3 7 8 3 3 1 0, doubling 1-indexed even
        # positions (2, 4, 6, 8):
        #   0, 3*2=6, 7, 8*2=16->1+6=7, 3, 3*2=6, 1, 0*2=0
        #   total = 0+6+7+7+3+6+1+0 = 30  ->  (10 - 30 % 10) % 10 = 0
        self.assertEqual(0, int("037833100"[8]))
        self.assertTrue(self.engine.validate_cusip_checksum("037833100"))
        self.assertTrue(self.engine.validate_cusip_checksum("594918104"))
        self.assertTrue(self.engine.validate_cusip_checksum("30303M102"))

    def test_rejects_wrong_check_digit(self):
        for bad in ("037833109", "594918105", "30303M103"):
            with self.subTest(value=bad):
                self.assertFalse(self.engine.validate_cusip_checksum(bad))

    def test_accepts_private_placement_characters(self):
        # '*' = 36, '@' = 37, '#' = 38 per ANSI X9.6.
        # "0000000*": only position 8 is non-zero and it is doubled ->
        #   36*2 = 72 -> 7+2 = 9  ->  check digit (10 - 9) % 10 = 1
        self.assertTrue(self.engine.validate_cusip_checksum("0000000*1"))
        self.assertFalse(self.engine.validate_cusip_checksum("0000000*2"))
        # "@0000000": only position 1 is non-zero and it is NOT doubled ->
        #   37 -> 3+7 = 10  ->  check digit (10 - 0) % 10 = 0
        self.assertTrue(self.engine.validate_cusip_checksum("@00000000"))
        # "#0000000": 38 -> 3+8 = 11  ->  check digit (10 - 1) % 10 = 9
        self.assertTrue(self.engine.validate_cusip_checksum("#00000009"))

    def test_rejects_non_numeric_check_digit_without_raising(self):
        # REGRESSION: the previous implementation matched ^[A-Z0-9]{9}$ and then
        # called int() on character 9, so a vendor row ending in a letter raised an
        # uncaught ValueError and killed the ingest loop instead of being rejected.
        for bad in ("03783310A", "AAAAAAAAA", "30303M10Z"):
            with self.subTest(value=bad):
                self.assertFalse(self.engine.validate_cusip_checksum(bad))

    def test_rejects_wrong_length(self):
        for bad in ("03783310", "0378331000", ""):
            with self.subTest(value=bad):
                self.assertFalse(self.engine.validate_cusip_checksum(bad))


class TestSedolChecksum(unittest.TestCase):
    """LSE: weighted sum (1, 3, 1, 7, 3, 9), nothing doubled."""

    def setUp(self):
        self.engine = IsinCusipSedolCrossReferenceEngine()

    def test_hand_derived_check_digit(self):
        # B7TL820 with letter values B=11, T=29, L=21:
        #   11*1 + 7*3 + 29*1 + 21*7 + 8*3 + 2*9
        # =   11 +  21 +   29 +  147 +  24 +  18 = 250
        #   -> (10 - 250 % 10) % 10 = 0, and the seventh character is 0.
        self.assertEqual(
            250,
            11 * 1 + 7 * 3 + 29 * 1 + 21 * 7 + 8 * 3 + 2 * 9,
        )
        self.assertTrue(self.engine.validate_sedol_checksum("B7TL820"))
        # 2046251: 2*1 + 0*3 + 4*1 + 6*7 + 2*3 + 5*9 = 2+0+4+42+6+45 = 99
        #   -> (10 - 9) % 10 = 1, and the seventh character is 1.
        self.assertEqual(99, 2 * 1 + 0 * 3 + 4 * 1 + 6 * 7 + 2 * 3 + 5 * 9)
        self.assertTrue(self.engine.validate_sedol_checksum("2046251"))
        self.assertTrue(self.engine.validate_sedol_checksum("2588173"))

    def test_is_not_luhn(self):
        # A guard against someone "unifying" the four algorithms: the SEDOL routine
        # must not accept a string merely because it satisfies Luhn, and the ISIN
        # routine must not be reachable with a 7-character input.
        self.assertFalse(self.engine.validate_sedol_checksum("2046252"))
        self.assertFalse(self.engine.validate_isin_checksum("2046251"))

    def test_rejects_vowels(self):
        # REGRESSION: the previous regex was ^[B-Z0-9]{6}[0-9]$, which admits the
        # vowels E, I, O and U. Vowels are never used in a SEDOL, so admitting them
        # let an 'I'-for-'1' or 'O'-for-'0' typo through whenever the weighted sum
        # happened to land on the stated check digit -- as it does here:
        #   B=11, E=14, I=18, O=24, U=30, Z=35
        #   11*1 + 14*3 + 18*1 + 24*7 + 30*3 + 35*9 = 11+42+18+168+90+315 = 644
        #   -> check digit (10 - 4) % 10 = 6, so "BEIOUZ6" passed the old validator.
        self.assertEqual(
            644, 11 * 1 + 14 * 3 + 18 * 1 + 24 * 7 + 30 * 3 + 35 * 9
        )
        self.assertFalse(self.engine.validate_sedol_checksum("BEIOUZ6"))
        for bad in ("B7TA820", "2O46251", "2I46251"):
            with self.subTest(value=bad):
                self.assertFalse(self.engine.validate_sedol_checksum(bad))

    def test_rejects_malformed_format(self):
        for bad in ("204625", "20462511", "204625B", ""):
            with self.subTest(value=bad):
                self.assertFalse(self.engine.validate_sedol_checksum(bad))


class TestFigiChecksum(unittest.TestCase):
    """ANSI X9.145-2021: double-add-double over characters 1-11."""

    def setUp(self):
        self.engine = IsinCusipSedolCrossReferenceEngine()

    def test_matches_standard_worked_example(self):
        # NRG92C84SB39 is the worked example carried in ANSI X9.145-2021 itself.
        # Values after doubling the 1-indexed even positions are given there as
        # 23, 54, 16, 18, 2, 24, 8, 8, 28, 22, 3, summing digit-wise to 71, so the
        # check digit is 80 - 71 = 9.
        self.assertTrue(self.engine.validate_figi_checksum("NRG92C84SB39"))
        for wrong in "012345678":
            with self.subTest(check_digit=wrong):
                self.assertFalse(
                    self.engine.validate_figi_checksum("NRG92C84SB3" + wrong)
                )

    def test_accepts_real_figis(self):
        # Composite and share-class FIGIs returned by the OpenFIGI mapping API.
        for figi in (
            "BBG000B9XRY4", "BBG000BPH459", "BBG000MM2P62",
            "BBG001S5N8V8", "BBG001SQCQC5", "BBG001S5TD05",
        ):
            with self.subTest(figi=figi):
                self.assertTrue(self.engine.validate_figi_checksum(figi))

    def test_is_not_the_isin_algorithm(self):
        # The standard offsets the FIGI doubling from ISIN's on purpose. Apple's
        # composite FIGI is a valid FIGI and is not a valid ISIN.
        self.assertTrue(self.engine.validate_figi_checksum("BBG000B9XRY4"))
        self.assertFalse(self.engine.validate_isin_checksum("BBG000B9XRY4"))

    def test_enforces_structural_rules(self):
        self.assertFalse(self.engine.validate_figi_checksum("BBX000B9XRY4"))  # pos 3 not G
        self.assertFalse(self.engine.validate_figi_checksum("BAG000B9XRY4"))  # vowel at pos 2
        self.assertFalse(self.engine.validate_figi_checksum("BBG000A9XRY4"))  # vowel in body
        self.assertFalse(self.engine.validate_figi_checksum("BBG000B9XRYZ"))  # non-numeric CD
        self.assertFalse(self.engine.validate_figi_checksum("BBG000B9XRY"))   # 11 characters

    def test_rejects_isin_reserved_prefixes(self):
        # ANSI X9.145 forbids BS/BM/GG/GB/VG in positions 1-2 so a FIGI cannot be
        # confused with a UK-family ISIN.
        for prefix in ("BS", "BM", "GG", "GB", "VG"):
            with self.subTest(prefix=prefix):
                self.assertFalse(
                    self.engine.validate_figi_checksum(prefix + "G000B9XRY4")
                )


class TestShippedMasterData(unittest.TestCase):
    """The bundled records must survive the engine's own validators."""

    def test_default_master_is_self_consistent(self):
        # REGRESSION: the shipped Meta row carried SEDOL 'BNPYS71', which fails the
        # engine's own SEDOL check digit, and FIGI 'BBG000MM82B1', which is not
        # Meta's FIGI. A lookup by ISIN returned that corrupted row as a match.
        engine = IsinCusipSedolCrossReferenceEngine()
        self.assertEqual((), engine.validate_master_data())

        for record in engine.master_db:
            with self.subTest(ticker=record.ticker_symbol):
                self.assertTrue(engine.validate_isin_checksum(record.isin))
                self.assertTrue(engine.validate_cusip_checksum(record.cusip))
                self.assertTrue(engine.validate_sedol_checksum(record.sedol))
                self.assertTrue(engine.validate_figi_checksum(record.figi))
                # Every identifier on the row must round-trip back to that row.
                for identifier in (
                    record.isin, record.cusip, record.sedol,
                    record.figi, record.ticker_symbol,
                ):
                    report = engine.lookup_identifier(identifier)
                    self.assertEqual(STATUS_MATCH_FOUND, report.status)
                    self.assertIs(record, report.matched_record)

    def test_meta_row_carries_the_verified_identifiers(self):
        engine = IsinCusipSedolCrossReferenceEngine()
        meta = engine.lookup_identifier("META").matched_record
        self.assertIsNotNone(meta)
        self.assertEqual("US30303M1027", meta.isin)
        self.assertEqual("30303M102", meta.cusip)
        self.assertEqual("B7TL820", meta.sedol)
        self.assertEqual("BBG000MM2P62", meta.figi)

    def test_strict_validation_rejects_a_corrupted_row(self):
        corrupted = [SecurityMasterRecord(
            isin="US30303M1027", cusip="30303M102", sedol="BNPYS71",
            figi="BBG000MM2P62", ticker_symbol="META",
            asset_name="Meta Platforms Inc", country_code="US",
        )]
        with self.assertRaises(ValueError) as ctx:
            IsinCusipSedolCrossReferenceEngine(records=corrupted)
        self.assertIn("BNPYS71", str(ctx.exception))

    def test_lax_validation_reports_but_still_indexes(self):
        corrupted = [SecurityMasterRecord(
            isin="US30303M1027", cusip="30303M102", sedol="BNPYS71",
            figi="BBG000MM2P62", ticker_symbol="META",
            asset_name="Meta Platforms Inc", country_code="US",
        )]
        engine = IsinCusipSedolCrossReferenceEngine(
            records=corrupted, strict_validation=False
        )
        problems = engine.validate_master_data()
        self.assertEqual(1, len(problems))
        self.assertIn("SEDOL", problems[0])
        # The row stays reachable by its good identifiers.
        self.assertEqual(
            STATUS_MATCH_FOUND,
            engine.lookup_identifier("US30303M1027").status,
        )

    def test_duplicate_identifier_is_reported(self):
        rows = [
            SecurityMasterRecord(
                isin="US0378331005", cusip="037833100", sedol="2046251",
                figi="BBG000B9XRY4", ticker_symbol="AAPL",
                asset_name="Apple Inc", country_code="US",
            ),
            SecurityMasterRecord(
                isin="US0378331005", cusip="", sedol="", figi="",
                ticker_symbol="AAPL2", asset_name="Apple Inc duplicate",
                country_code="US",
            ),
        ]
        with self.assertRaises(ValueError) as ctx:
            IsinCusipSedolCrossReferenceEngine(records=rows)
        self.assertIn("duplicate", str(ctx.exception))

    def test_absent_identifiers_are_allowed(self):
        # A UK-only line has no CUSIP; an unlisted line has no SEDOL. Empty is not
        # a check-digit failure.
        rows = [SecurityMasterRecord(
            isin="GB0002634946", cusip="", sedol="0263494", figi="",
            ticker_symbol="BA.", asset_name="BAE Systems plc", country_code="GB",
        )]
        engine = IsinCusipSedolCrossReferenceEngine(records=rows)
        self.assertEqual((), engine.validate_master_data())
        self.assertEqual(
            STATUS_MATCH_FOUND, engine.lookup_identifier("GB0002634946").status
        )
        self.assertEqual(
            STATUS_MATCH_FOUND,
            engine.lookup_identifier("BA.", identifier_type=TICKER).status,
        )

    def test_default_records_are_not_shared_between_engines(self):
        first = IsinCusipSedolCrossReferenceEngine()
        first.master_db[0].asset_name = "MUTATED"
        second = IsinCusipSedolCrossReferenceEngine()
        self.assertEqual("Apple Inc", second.master_db[0].asset_name)


class TestCrossReferenceLookup(unittest.TestCase):

    def setUp(self):
        self.engine = IsinCusipSedolCrossReferenceEngine()

    def test_resolves_apple_by_every_identifier(self):
        for query, expected_type in (
            ("US0378331005", ISIN),
            ("037833100", CUSIP),
            ("2046251", SEDOL),
            ("BBG000B9XRY4", FIGI),
            ("AAPL", TICKER),
        ):
            with self.subTest(query=query):
                report = self.engine.lookup_identifier(query)
                self.assertIsInstance(report, IdentifierCrossReferenceReport)
                self.assertEqual(STATUS_MATCH_FOUND, report.status)
                self.assertEqual(expected_type, report.query_type)
                self.assertTrue(report.is_checksum_valid)
                self.assertIsNotNone(report.matched_record)
                self.assertEqual("AAPL", report.matched_record.ticker_symbol)
                self.assertEqual("BBG000B9XRY4", report.matched_record.figi)

    def test_figi_query_resolves(self):
        # REGRESSION: a 12-character FIGI was classified as an ISIN before the FIGI
        # branch was ever reached, so every FIGI lookup returned INVALID_CHECKSUM
        # and the FIGI branch was unreachable code.
        report = self.engine.lookup_identifier("BBG000MM2P62")
        self.assertEqual(FIGI, report.query_type)
        self.assertEqual(STATUS_MATCH_FOUND, report.status)
        self.assertEqual("META", report.matched_record.ticker_symbol)

    def test_case_and_separators_are_normalised(self):
        # Vendors ship CUSIPs hyphenated 6-2-1 and ISINs with stray whitespace.
        self.assertEqual(
            "AAPL",
            self.engine.lookup_identifier("037833-10-0").matched_record.ticker_symbol,
        )
        self.assertEqual(
            "AAPL",
            self.engine.lookup_identifier("  us0378331005 ").matched_record.ticker_symbol,
        )

    def test_rejects_corrupted_isin(self):
        report = self.engine.lookup_identifier("US0378331009")
        self.assertEqual(STATUS_INVALID_CHECKSUM, report.status)
        self.assertEqual(ISIN, report.query_type)
        self.assertFalse(report.is_checksum_valid)
        self.assertIsNone(report.matched_record)

    def test_ambiguous_isin_figi_string_reports_both_candidates(self):
        # KYG875721634 is the real ISIN of Tencent Holdings Ltd. It also satisfies
        # the FIGI syntax rules and passes the FIGI check digit, so the engine must
        # surface the ambiguity rather than quietly choosing one reading.
        report = self.engine.lookup_identifier("KYG875721634")
        self.assertEqual((ISIN, FIGI), report.candidate_types)
        self.assertEqual(ISIN, report.query_type)
        self.assertEqual(STATUS_NOT_FOUND, report.status)
        self.assertIn("AMBIGUOUS", report.audit_notes)

    def test_explicit_type_overrides_inference(self):
        # REGRESSION: a 7-character ticker was force-classified as a SEDOL and
        # rejected with INVALID_CHECKSUM, with no way for the caller to say
        # otherwise even though a feed column is labelled.
        self.assertEqual(
            STATUS_INVALID_CHECKSUM,
            self.engine.lookup_identifier("BCDFGH1").status,
        )
        self.assertEqual(
            STATUS_NOT_FOUND,
            self.engine.lookup_identifier("BCDFGH1", identifier_type=TICKER).status,
        )
        self.assertEqual(
            STATUS_INVALID_CHECKSUM,
            self.engine.lookup_identifier("2046251", identifier_type=ISIN).status,
        )

    def test_type_scoped_index_prevents_cross_field_matching(self):
        # REGRESSION: matching tested the query against the union of all five
        # fields, so a query validated as a CUSIP could resolve on some other row's
        # *ticker*. Here the decoy row is first, so a union scan returns it.
        decoy = SecurityMasterRecord(
            isin="", cusip="", sedol="", figi="",
            ticker_symbol="594918104", asset_name="Decoy ticker", country_code="US",
        )
        microsoft = SecurityMasterRecord(
            isin="US5949181045", cusip="594918104", sedol="2588173",
            figi="BBG000BPH459", ticker_symbol="MSFT",
            asset_name="Microsoft Corp", country_code="US",
        )
        engine = IsinCusipSedolCrossReferenceEngine(records=[decoy, microsoft])
        self.assertEqual(
            "MSFT",
            engine.lookup_identifier("594918104").matched_record.ticker_symbol,
        )
        self.assertEqual(
            "594918104",
            engine.lookup_identifier(
                "594918104", identifier_type=TICKER
            ).matched_record.ticker_symbol,
        )

    def test_unknown_but_well_formed_identifier_is_not_found(self):
        report = self.engine.lookup_identifier("GB0002634946")
        self.assertEqual(STATUS_NOT_FOUND, report.status)
        self.assertEqual(ISIN, report.query_type)
        self.assertTrue(report.is_checksum_valid)
        self.assertIsNone(report.matched_record)

    def test_ticker_report_marks_that_no_checksum_exists(self):
        # is_checksum_valid=True on a ticker must not read as "checksum verified".
        report = self.engine.lookup_identifier("AAPL")
        self.assertTrue(report.is_checksum_valid)
        self.assertFalse(report.checksum_applied)
        self.assertEqual((), report.candidate_types)

        structured = self.engine.lookup_identifier("US0378331005")
        self.assertTrue(structured.checksum_applied)

    def test_ticker_punctuation_is_preserved(self):
        rows = [SecurityMasterRecord(
            isin="US0846707026", cusip="084670702", sedol="2073390",
            figi="BBG000DWG505", ticker_symbol="BRK.B",
            asset_name="Berkshire Hathaway Inc Class B", country_code="US",
        )]
        engine = IsinCusipSedolCrossReferenceEngine(records=rows)
        self.assertEqual(
            STATUS_MATCH_FOUND, engine.lookup_identifier("BRK.B").status
        )

    def test_input_validation(self):
        with self.assertRaises(TypeError):
            self.engine.lookup_identifier(None)
        with self.assertRaises(TypeError):
            self.engine.lookup_identifier(378331005)
        with self.assertRaises(ValueError):
            self.engine.lookup_identifier("   ")
        with self.assertRaises(ValueError):
            self.engine.lookup_identifier("AAPL", identifier_type="RIC")


class TestClassification(unittest.TestCase):

    def setUp(self):
        self.engine = IsinCusipSedolCrossReferenceEngine()

    def test_reports_syntactic_and_validated_types_separately(self):
        validated, syntactic = self.engine.classify_identifier("US0378331005")
        self.assertEqual((ISIN,), validated)
        self.assertEqual((ISIN,), syntactic)

        # Shaped like an ISIN, check digit wrong: syntactic but not validated.
        validated, syntactic = self.engine.classify_identifier("US0378331009")
        self.assertEqual((), validated)
        self.assertEqual((ISIN,), syntactic)

        # Not shaped like anything structured.
        self.assertEqual(((), ()), self.engine.classify_identifier("AAPL"))

    def test_normalises_its_own_input(self):
        # Called directly with a lowercase or hyphenated string, this must not report
        # "not an identifier of any kind" -- that reads as a clean negative.
        self.assertEqual((ISIN,), self.engine.classify_identifier(" us0378331005 ")[0])
        self.assertEqual((CUSIP,), self.engine.classify_identifier("037833-10-0")[0])

    def test_candidates_are_ordered_by_priority(self):
        validated, _ = self.engine.classify_identifier("KYG875721634")
        self.assertEqual((ISIN, FIGI), validated)


if __name__ == "__main__":
    unittest.main()
