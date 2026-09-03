"""Unit tests for the global instrument-name Unicode sanitizer.

Expected values are real listed-company names spelled the way their home market spells
them -- Ørsted A/S (CPH: ORSTED), São Martinho S.A. (B3: SMTO3), 髙島屋 (TSE: 8233),
L'Oréal (EPA: OR), Société Générale (EPA: GLE) -- and byte sequences produced by
Python's own codecs from those names. No expected value is read back out of the
implementation.

Tests marked REGRESSION fail against the behaviour this module shipped with before the
2026-09 audit:
  * a bare "Ã" -> "Á" entry in the mojibake substitution table rewrote correctly encoded
    uppercase Portuguese ("SÃO MARTINHO S.A." -> "SÁO MARTINHO S.A.") and, being matched
    before the two-character keys, turned "Ã‰" into "Á‰";
  * a partial table hit set was_repaired and suppressed the round trip, so only some of
    a corrupted string was repaired;
  * only a Latin-1 round trip was attempted, so CP1252 mojibake ("Lâ€™OrÃ©al") was
    unrepairable;
  * control stripping ran before mojibake repair and destroyed the C1 bytes the repair
    needed;
  * the ASCII slug deleted every Latin letter that NFD does not decompose, so
    "Ørsted A/S" became "RSTED A/S" and "Straße" became "STRAE";
  * only the UTF-8 BOM was recognised, so a UTF-16LE feed decoded as CP1252;
  * a lossy or guessed decode was reported as though it were authoritative;
  * only the narrow CJK codecs were tried, so CP932 extension kanji fell through to GBK
    and decoded, without error, to unrelated Chinese characters;
  * a name consisting only of zero-width characters produced an empty cleaned_name
    instead of an error.
"""
import logging
import unittest

from unicode_and_encoding_issues_in_global_instrument_names import (
    ASCII_TRANSLITERATION_MAP,
    CONFIDENCE_BOM,
    CONFIDENCE_DECLARED,
    CONFIDENCE_DEFAULT,
    CONFIDENCE_GUESSED,
    CONFIDENCE_LOSSY,
    GlobalInstrumentNameSanitizer,
    InstrumentSanitizerConfig,
    NormalizationForm,
    SanitizedInstrumentName,
    UnicodeProcessingError,
)

# Silence the engine's audit and warning logging for the duration of the suite.
logging.getLogger(
    "unicode_and_encoding_issues_in_global_instrument_names"
).setLevel(logging.CRITICAL)

# Real names, spelled as their home market spells them.
SOCIETE_GENERALE = "Société Générale"
LOREAL = "L’Oréal"           # with U+2019 RIGHT SINGLE QUOTATION MARK
SAO_MARTINHO = "SÃO MARTINHO S.A."  # B3: SMTO3, correctly encoded Portuguese
ORSTED = "Ørsted A/S"             # CPH: ORSTED
TAKASHIMAYA = "髙島屋"             # TSE: 8233; 髙 U+9AD9 is a CP932 extension kanji
TOYOTA_JP = "トヨタ自動車"
SAMSUNG_KR = "삼성전자"
TENCENT_CN = "腾讯控股"


class TestAsciiTransliteration(unittest.TestCase):
    """The slug must fold letters, never delete them."""

    def test_diacritics_are_stripped_from_decomposable_letters(self):
        self.assertEqual(
            GlobalInstrumentNameSanitizer.generate_ascii_slug("Société Générale S.A."),
            "SOCIETE GENERALE S.A.",
        )
        self.assertEqual(
            GlobalInstrumentNameSanitizer.generate_ascii_slug(
                "Münchener Rückversicherungs-AG"
            ),
            "MUNCHENER RUCKVERSICHERUNGS-AG",
        )

    def test_non_decomposable_latin_letters_are_transliterated(self):
        """REGRESSION: NFD does not decompose Ø/ß/Ł/Æ/Đ, so they used to be deleted."""
        cases = {
            ORSTED: "ORSTED A/S",
            "Deutsche Straße AG": "DEUTSCHE STRASSE AG",
            "Łukasiewicz S.A.": "LUKASIEWICZ S.A.",
            "Đuro Đaković Grupa": "DURO DAKOVIC GRUPA",
            "Carlsberg Æ A/S": "CARLSBERG AE A/S",
            "Þórs Ísland hf.": "THORS ISLAND HF.",
        }
        for name, expected in cases.items():
            with self.subTest(name=name):
                self.assertEqual(
                    GlobalInstrumentNameSanitizer.generate_ascii_slug(name), expected
                )

    def test_capital_sharp_s_and_dotless_i_fold(self):
        self.assertEqual(
            GlobalInstrumentNameSanitizer.generate_ascii_slug("ẞTRASSE"), "SSTRASSE"
        )
        # Turkish dotless i (U+0131) and dotted capital I (U+0130) both fold to "I".
        self.assertEqual(
            GlobalInstrumentNameSanitizer.generate_ascii_slug("ıİ"), "II"
        )

    def test_untransliterable_characters_are_reported_not_silently_dropped(self):
        slug, dropped = GlobalInstrumentNameSanitizer.transliterate_to_ascii(TOYOTA_JP)
        self.assertEqual(slug, "")
        self.assertEqual("".join(dropped), TOYOTA_JP)

    def test_mixed_script_slug_reports_only_the_dropped_run(self):
        slug, dropped = GlobalInstrumentNameSanitizer.transliterate_to_ascii(
            "Tencent " + TENCENT_CN
        )
        self.assertEqual(slug, "TENCENT")
        self.assertEqual("".join(dropped), TENCENT_CN)

    def test_empty_slug_from_a_non_empty_name_is_always_lossy(self):
        """Combining marks are skipped by the fold loop, so they were not recorded.

        An empty slug reported as non-lossy is exactly the value that reaches Symbol(55)
        unchecked, whatever removed the characters.
        """
        slug, dropped = GlobalInstrumentNameSanitizer.transliterate_to_ascii("́̂")
        self.assertEqual(slug, "")
        self.assertTrue(dropped)

    def test_transliteration_map_has_no_ascii_keys(self):
        """Every key must be a character the ASCII filter would otherwise delete."""
        for key in ASCII_TRANSLITERATION_MAP:
            with self.subTest(key=key):
                self.assertGreaterEqual(ord(key), 128)
                self.assertTrue(ASCII_TRANSLITERATION_MAP[key].isascii())


class TestMojibakeRepair(unittest.TestCase):
    """Repair is a strict round trip; the table is a residual pass only."""

    def test_latin1_mojibake_is_repaired(self):
        corrupted = SOCIETE_GENERALE.encode("utf-8").decode("latin-1")
        self.assertEqual(corrupted, "SociÃ©tÃ© GÃ©nÃ©rale")
        repaired, was_repaired = GlobalInstrumentNameSanitizer.repair_mojibake(corrupted)
        self.assertTrue(was_repaired)
        self.assertEqual(repaired, SOCIETE_GENERALE)

    def test_cp1252_mojibake_is_repaired(self):
        """REGRESSION: only a Latin-1 round trip was attempted, so this was unfixable.

        CP1252 is what Windows vendor tooling actually applies. U+2019 becomes the
        three characters "â€™", and € / ™ have no Latin-1 encoding at all.
        """
        corrupted = LOREAL.encode("utf-8").decode("cp1252")
        self.assertEqual(corrupted, "Lâ€™OrÃ©al")
        repaired, was_repaired = GlobalInstrumentNameSanitizer.repair_mojibake(corrupted)
        self.assertTrue(was_repaired)
        self.assertEqual(repaired, LOREAL)

    def test_cjk_mojibake_is_repaired(self):
        """REGRESSION: repair was gated on the text containing 'Ã' or 'Â'.

        Mojibake of Japanese produces lowercase 'ã', so it never triggered.
        """
        corrupted = TOYOTA_JP.encode("utf-8").decode("latin-1")
        repaired, was_repaired = GlobalInstrumentNameSanitizer.repair_mojibake(corrupted)
        self.assertTrue(was_repaired)
        self.assertEqual(repaired, TOYOTA_JP)

    def test_doubly_encoded_mojibake_is_repaired_and_terminates(self):
        once = SOCIETE_GENERALE.encode("utf-8").decode("cp1252")
        twice = once.encode("utf-8").decode("cp1252")
        self.assertNotEqual(once, twice)
        repaired, was_repaired = GlobalInstrumentNameSanitizer.repair_mojibake(twice)
        self.assertTrue(was_repaired)
        self.assertEqual(repaired, SOCIETE_GENERALE)

    def test_corruption_beyond_the_round_limit_is_reported_not_hidden(self):
        """Repair is bounded, so a deeply corrupted name can come back still corrupt.

        Reporting it as repaired would be the same silent-partial-correctness failure
        the round trip replaced the substitution table to avoid.
        """
        deep = SOCIETE_GENERALE
        for _ in range(6):
            deep = deep.encode("utf-8").decode("cp1252")
        repaired, _was_repaired = GlobalInstrumentNameSanitizer.repair_mojibake(deep)
        self.assertNotEqual(repaired, SOCIETE_GENERALE)
        self.assertTrue(GlobalInstrumentNameSanitizer.has_residual_mojibake(repaired))

        result = GlobalInstrumentNameSanitizer().sanitize_instrument_name(deep)
        self.assertFalse(result.is_trustworthy)
        self.assertTrue(any("Mojibake remains" in w for w in result.warnings))

    def test_fully_repaired_name_reports_no_residual(self):
        once = SOCIETE_GENERALE.encode("utf-8").decode("cp1252")
        result = GlobalInstrumentNameSanitizer().sanitize_instrument_name(once)
        self.assertEqual(result.cleaned_name, SOCIETE_GENERALE)
        self.assertTrue(result.contains_mojibake)
        self.assertTrue(result.is_trustworthy)

    def test_correctly_encoded_portuguese_is_not_rewritten(self):
        """REGRESSION: a bare "Ã" -> "Á" table entry corrupted this real B3 name."""
        repaired, was_repaired = GlobalInstrumentNameSanitizer.repair_mojibake(
            SAO_MARTINHO
        )
        self.assertFalse(was_repaired)
        self.assertEqual(repaired, SAO_MARTINHO)

    def test_substitution_table_has_no_single_character_key(self):
        """REGRESSION: the one-character "Ã" key pre-empted "Ã‰", "Ãˆ", "Ã–", "Ãœ"."""
        for key in GlobalInstrumentNameSanitizer.MOJIBAKE_REPLACEMENT_MAP:
            with self.subTest(key=key):
                self.assertGreater(len(key), 1)

    def test_no_false_positive_on_correctly_encoded_latin1_range_names(self):
        for name in [
            "Ângelo Holdings",
            "AÇÃO S.A.",
            "NÉSTLÉ S.A.",
            "ÅF Pöyry AB",
            "Telefónica S.A.",
            "Saïd Group",
            "ÖBB Holding",
            ORSTED,
        ]:
            with self.subTest(name=name):
                repaired, was_repaired = GlobalInstrumentNameSanitizer.repair_mojibake(
                    name
                )
                self.assertFalse(was_repaired)
                self.assertEqual(repaired, name)


class TestControlAndZeroWidthStripping(unittest.TestCase):
    def test_zero_width_space_and_nul_are_removed(self):
        cleaned, modified = GlobalInstrumentNameSanitizer.strip_control_and_zero_width_chars(
            "AA​PL\x00"
        )
        self.assertTrue(modified)
        self.assertEqual(cleaned, "AAPL")

    def test_bom_and_word_joiner_are_removed(self):
        cleaned, modified = GlobalInstrumentNameSanitizer.strip_control_and_zero_width_chars(
            "﻿AA⁠PL"
        )
        self.assertTrue(modified)
        self.assertEqual(cleaned, "AAPL")

    def test_clean_text_is_reported_unmodified(self):
        cleaned, modified = GlobalInstrumentNameSanitizer.strip_control_and_zero_width_chars(
            SOCIETE_GENERALE
        )
        self.assertFalse(modified)
        self.assertEqual(cleaned, SOCIETE_GENERALE)

    def test_joining_controls_can_be_preserved_for_persian_orthography(self):
        """UAX #31 s2.3: joining controls are used in the orthographies of some languages.

        Persian "شرکت‌ها" spells the plural with a ZWNJ; removing it changes the word.
        """
        persian = "شرکت‌ها"
        stripped, _ = GlobalInstrumentNameSanitizer.strip_control_and_zero_width_chars(
            persian
        )
        self.assertNotIn("‌", stripped)

        preserved, modified = (
            GlobalInstrumentNameSanitizer.strip_control_and_zero_width_chars(
                persian, strip_joiner_controls=False
            )
        )
        self.assertFalse(modified)
        self.assertEqual(preserved, persian)

    def test_joiner_config_reaches_the_pipeline(self):
        persian = "شرکت‌ها"
        engine = GlobalInstrumentNameSanitizer(
            InstrumentSanitizerConfig(strip_joiner_controls=False)
        )
        self.assertEqual(engine.sanitize_instrument_name(persian).cleaned_name, persian)


class TestNormalization(unittest.TestCase):
    def test_nfc_composes_decomposed_accents(self):
        self.assertNotEqual("é", "é")
        self.assertEqual(
            GlobalInstrumentNameSanitizer.normalize_unicode(
                "é", NormalizationForm.NFC
            ),
            "é",
        )

    def test_decomposed_and_composed_names_yield_one_security_master_key(self):
        engine = GlobalInstrumentNameSanitizer()
        composed = engine.sanitize_instrument_name(SOCIETE_GENERALE).cleaned_name
        decomposed_input = GlobalInstrumentNameSanitizer.normalize_unicode(
            SOCIETE_GENERALE, NormalizationForm.NFD
        )
        self.assertNotEqual(decomposed_input, SOCIETE_GENERALE)
        self.assertEqual(
            engine.sanitize_instrument_name(decomposed_input).cleaned_name, composed
        )

    def test_nfkc_folds_fullwidth_latin(self):
        engine = GlobalInstrumentNameSanitizer(
            InstrumentSanitizerConfig(target_normalization=NormalizationForm.NFKC)
        )
        # Full-width "AAPL" as published by some JP vendor files.
        self.assertEqual(
            engine.sanitize_instrument_name("ＡＡＰＬ").cleaned_name,
            "AAPL",
        )


class TestDecoding(unittest.TestCase):
    def setUp(self):
        self.sanitizer = GlobalInstrumentNameSanitizer()

    def test_utf8_bom_is_stripped(self):
        decoded, enc = self.sanitizer.decode_bytes(b"\xef\xbb\xbfSociete Generale")
        self.assertEqual(decoded, "Societe Generale")
        self.assertEqual(enc, "utf-8-sig")

    def test_utf16_and_utf32_boms_are_recognised(self):
        """REGRESSION: only the UTF-8 BOM was checked; UTF-16LE decoded as CP1252."""
        for codec, expected_name in [
            ("utf-16", "utf-16-le"),   # codec emits a little-endian BOM on x86
            ("utf-32", "utf-32-le"),
        ]:
            with self.subTest(codec=codec):
                payload = self.sanitizer.decode_payload(
                    SOCIETE_GENERALE.encode(codec)
                )
                self.assertEqual(payload.text, SOCIETE_GENERALE)
                self.assertEqual(payload.encoding, expected_name)
                self.assertEqual(payload.confidence, CONFIDENCE_BOM)

        for bom, codec, expected_name in [
            (b"\xfe\xff", "utf-16-be", "utf-16-be"),
            (b"\x00\x00\xfe\xff", "utf-32-be", "utf-32-be"),
        ]:
            with self.subTest(codec=codec):
                payload = self.sanitizer.decode_payload(
                    bom + SOCIETE_GENERALE.encode(codec)
                )
                self.assertEqual(payload.text, SOCIETE_GENERALE)
                self.assertEqual(payload.encoding, expected_name)

    def test_utf32le_bom_is_not_read_as_utf16le(self):
        """The UTF-32-LE BOM (FF FE 00 00) starts with the UTF-16-LE BOM (FF FE)."""
        raw = SOCIETE_GENERALE.encode("utf-32")
        self.assertEqual(raw[:4], b"\xff\xfe\x00\x00")
        self.assertEqual(self.sanitizer.decode_payload(raw).encoding, "utf-32-le")

    def test_plain_utf8_is_reported_as_authoritative(self):
        payload = self.sanitizer.decode_payload(SOCIETE_GENERALE.encode("utf-8"))
        self.assertEqual(payload.confidence, CONFIDENCE_DEFAULT)
        self.assertFalse(payload.is_lossy)

    def test_shift_jis_fallback_keeps_jis_mappings(self):
        payload = self.sanitizer.decode_payload("トヨタ".encode("shift_jis"))
        self.assertEqual(payload.text, "トヨタ")
        self.assertEqual(payload.encoding, "shift_jis")
        self.assertEqual(payload.confidence, CONFIDENCE_GUESSED)

    def test_cp932_extension_kanji_does_not_fall_through_to_gbk(self):
        """REGRESSION: 髙 (U+9AD9) is absent from Python's shift_jis codec.

        Its CP932 bytes are a valid GBK sequence, so the old fallback list decoded
        Takashimaya (TSE 8233) to unrelated Chinese characters without any error.
        """
        with self.assertRaises(UnicodeDecodeError):
            TAKASHIMAYA.encode("cp932").decode("shift_jis")
        payload = self.sanitizer.decode_payload(TAKASHIMAYA.encode("cp932"))
        self.assertEqual(payload.text, TAKASHIMAYA)
        self.assertEqual(payload.encoding, "cp932")

    def test_guessed_decode_is_flagged_as_unverified(self):
        payload = self.sanitizer.decode_payload(SAMSUNG_KR.encode("euc-kr"))
        self.assertEqual(payload.confidence, CONFIDENCE_GUESSED)

    def test_undecodable_bytes_are_flagged_lossy(self):
        """REGRESSION: replacement characters were emitted with a clean encoding name."""
        payload = self.sanitizer.decode_payload(b"\xef\xbb\xbfabc\xff\xfe")
        self.assertEqual(payload.confidence, CONFIDENCE_LOSSY)
        self.assertTrue(payload.is_lossy)
        self.assertGreater(payload.replacement_chars, 0)

    def test_declared_source_encoding_is_applied_strictly(self):
        payload = self.sanitizer.decode_payload(
            SAMSUNG_KR.encode("cp949"), source_encoding="cp949"
        )
        self.assertEqual(payload.text, SAMSUNG_KR)
        self.assertEqual(payload.confidence, CONFIDENCE_DECLARED)

    def test_declared_source_encoding_raises_rather_than_guessing(self):
        with self.assertRaises(UnicodeProcessingError):
            self.sanitizer.decode_payload(
                TAKASHIMAYA.encode("cp932"), source_encoding="utf-8"
            )

    def test_unknown_declared_codec_raises(self):
        with self.assertRaises(UnicodeProcessingError):
            self.sanitizer.decode_payload(b"AAPL", source_encoding="not-a-codec")


class TestSanitizePipeline(unittest.TestCase):
    def setUp(self):
        self.sanitizer = GlobalInstrumentNameSanitizer()

    def test_mojibake_is_repaired_before_controls_are_stripped(self):
        """REGRESSION: stripping first destroyed the C1 bytes the repair needed.

        The Latin-1 mojibake of U+2019 is "â" plus U+0080 and U+0099, both C1 controls.
        The old pipeline stripped those and left "LâOréal".
        """
        corrupted = LOREAL.encode("utf-8").decode("latin-1")
        self.assertIn("\x80", corrupted)
        result = self.sanitizer.sanitize_instrument_name(corrupted)
        self.assertEqual(result.cleaned_name, LOREAL)
        self.assertTrue(result.contains_mojibake)

    def test_full_pipeline_on_utf8_bytes_with_zero_width_space(self):
        raw = ("Tencent ​" + TENCENT_CN).encode("utf-8")
        result = self.sanitizer.sanitize_instrument_name(raw)
        self.assertEqual(result.cleaned_name, "Tencent " + TENCENT_CN)
        self.assertEqual(result.ascii_slug, "TENCENT")
        self.assertEqual(result.normalization_form, NormalizationForm.NFC)
        self.assertGreaterEqual(len(result.audit_actions), 2)

    def test_lossy_slug_is_surfaced_on_the_result(self):
        """An empty Symbol(55)/SecurityDesc(107) must never be written silently."""
        result = self.sanitizer.sanitize_instrument_name(TOYOTA_JP)
        self.assertEqual(result.cleaned_name, TOYOTA_JP)
        self.assertEqual(result.ascii_slug, "")
        self.assertTrue(result.ascii_slug_is_lossy)
        self.assertEqual("".join(result.dropped_characters), TOYOTA_JP)
        self.assertFalse(result.is_trustworthy)

    def test_clean_latin_name_is_trustworthy(self):
        result = self.sanitizer.sanitize_instrument_name(SOCIETE_GENERALE)
        self.assertTrue(result.is_trustworthy)
        self.assertFalse(result.ascii_slug_is_lossy)
        self.assertEqual(result.ascii_slug, "SOCIETE GENERALE")

    def test_guessed_encoding_makes_the_result_untrustworthy(self):
        result = self.sanitizer.sanitize_instrument_name("トヨタ".encode("shift_jis"))
        self.assertEqual(result.decode_confidence, CONFIDENCE_GUESSED)
        self.assertFalse(result.is_trustworthy)

    def test_declared_encoding_reaches_the_pipeline(self):
        result = self.sanitizer.sanitize_instrument_name(
            SAMSUNG_KR.encode("cp949"), source_encoding="cp949"
        )
        self.assertEqual(result.cleaned_name, SAMSUNG_KR)
        self.assertEqual(result.decode_confidence, CONFIDENCE_DECLARED)

    def test_correctly_encoded_names_pass_through_unchanged(self):
        for name in [SOCIETE_GENERALE, SAO_MARTINHO, ORSTED, TOYOTA_JP, LOREAL]:
            with self.subTest(name=name):
                self.assertEqual(
                    self.sanitizer.sanitize_instrument_name(name).cleaned_name, name
                )

    def test_sanitization_is_idempotent(self):
        for name in [SOCIETE_GENERALE, SAO_MARTINHO, ORSTED, "Deutsche Straße AG"]:
            with self.subTest(name=name):
                once = self.sanitizer.sanitize_instrument_name(name).cleaned_name
                twice = self.sanitizer.sanitize_instrument_name(once).cleaned_name
                self.assertEqual(once, twice)

    def test_bytes_like_inputs_are_accepted(self):
        for payload in [b"AAPL", bytearray(b"AAPL"), memoryview(b"AAPL")]:
            with self.subTest(kind=type(payload).__name__):
                self.assertEqual(
                    self.sanitizer.sanitize_instrument_name(payload).cleaned_name, "AAPL"
                )

    def test_result_is_the_documented_dataclass(self):
        self.assertIsInstance(
            self.sanitizer.sanitize_instrument_name("AAPL"), SanitizedInstrumentName
        )


class TestInputRejection(unittest.TestCase):
    def setUp(self):
        self.sanitizer = GlobalInstrumentNameSanitizer()

    def test_empty_input_raises(self):
        for empty in ["", b""]:
            with self.subTest(kind=type(empty).__name__):
                with self.assertRaises(UnicodeProcessingError):
                    self.sanitizer.sanitize_instrument_name(empty)

    def test_name_consumed_by_sanitization_raises(self):
        """REGRESSION: these produced an empty cleaned_name instead of an error."""
        for hostile in ["​​", "\x00\x01\x02", "   ", "﻿"]:
            with self.subTest(value=repr(hostile)):
                with self.assertRaises(UnicodeProcessingError):
                    self.sanitizer.sanitize_instrument_name(hostile)

    def test_non_text_input_raises_typeerror(self):
        for bad in [12345, None, 3.5, ["AAPL"]]:
            with self.subTest(value=repr(bad)):
                with self.assertRaises(TypeError):
                    self.sanitizer.sanitize_instrument_name(bad)


if __name__ == "__main__":
    unittest.main()
