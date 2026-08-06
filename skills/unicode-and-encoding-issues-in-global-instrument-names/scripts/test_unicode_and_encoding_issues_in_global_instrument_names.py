
import unittest
from unicode_and_encoding_issues_in_global_instrument_names import (
    NormalizationForm,
    InstrumentSanitizerConfig,
    SanitizedInstrumentName,
    GlobalInstrumentNameSanitizer,
    UnicodeProcessingError,
)


class TestGlobalInstrumentNameSanitizer(unittest.TestCase):
    def setUp(self):
        self.sanitizer = GlobalInstrumentNameSanitizer()

    def test_decode_bytes_utf8_bom(self):
        bom_bytes = b"\xef\xbb\xbfSociete Generale"
        decoded, enc = self.sanitizer.decode_bytes(bom_bytes)
        self.assertEqual(decoded, "Societe Generale")
        self.assertEqual(enc, "utf-8-sig")

    def test_decode_bytes_fallback_shift_jis(self):
        # Shift-JIS bytes for "トヨタ"
        sjis_bytes = "トヨタ".encode("shift_jis")
        decoded, enc = self.sanitizer.decode_bytes(sjis_bytes)
        self.assertEqual(decoded, "トヨタ")
        self.assertEqual(enc, "shift_jis")

    def test_mojibake_repair(self):
        # Corrupted "Société Générale" as "SociÃ©tÃ© GÃ©nÃ©rale"
        mojibake = "SociÃ©tÃ© GÃ©nÃ©rale"
        repaired, was_repaired = GlobalInstrumentNameSanitizer.repair_mojibake(mojibake)
        self.assertTrue(was_repaired)
        self.assertEqual(repaired, "Société Générale")

    def test_strip_control_and_zero_width_characters(self):
        # "AAPL" with zero-width space U+200B and null control character
        dirty = "AA\u200BPL\x00"
        cleaned, modified = GlobalInstrumentNameSanitizer.strip_control_and_zero_width_chars(dirty)
        self.assertTrue(modified)
        self.assertEqual(cleaned, "AAPL")

    def test_unicode_normalization_nfc(self):
        # NFD decomposed "e" + acute accent vs NFC composed "é"
        nfd_str = "e\u0301"  # NFD
        nfc_str = "é"        # NFC
        self.assertNotEqual(nfd_str, nfc_str)

        normalized = GlobalInstrumentNameSanitizer.normalize_unicode(nfd_str, NormalizationForm.NFC)
        self.assertEqual(normalized, nfc_str)

    def test_generate_ascii_slug_transliteration(self):
        french_name = "Société Générale S.A."
        slug = GlobalInstrumentNameSanitizer.generate_ascii_slug(french_name)
        self.assertEqual(slug, "SOCIETE GENERALE S.A.")

        german_name = "Münchener Rückversicherungs-AG"
        slug_de = GlobalInstrumentNameSanitizer.generate_ascii_slug(german_name)
        self.assertEqual(slug_de, "MUNCHENER RUCKVERSICHERUNGS-AG")

    def test_full_sanitize_pipeline_cjk_and_european(self):
        # Raw bytes for "Tencent 腾讯" in UTF-8 with zero-width space
        raw = "Tencent \u200B腾讯".encode("utf-8")
        res = self.sanitizer.sanitize_instrument_name(raw)

        self.assertEqual(res.cleaned_name, "Tencent 腾讯")
        self.assertEqual(res.ascii_slug, "TENCENT")
        self.assertEqual(res.normalization_form, NormalizationForm.NFC)
        self.assertTrue(len(res.audit_actions) >= 2)

    def test_empty_input_raises_error(self):
        with self.assertRaises(UnicodeProcessingError):
            self.sanitizer.sanitize_instrument_name("")


if __name__ == "__main__":
    unittest.main()

