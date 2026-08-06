
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Dict, Optional, List, Tuple, Union
from enum import Enum

logger = logging.getLogger(__name__)


class NormalizationForm(Enum):
    NFC = "NFC"   # Canonical Composition (Default for Security Master database keys)
    NFD = "NFD"   # Canonical Decomposition
    NFKC = "NFKC" # Compatibility Composition
    NFKD = "NFKD" # Compatibility Decomposition


class UnicodeProcessingError(Exception):
    """Base exception for Unicode and character encoding processing errors."""
    pass


@dataclass
class InstrumentSanitizerConfig:
    default_encoding: str = "utf-8"
    fallback_encodings: List[str] = field(
        default_factory=lambda: ["utf-8-sig", "shift_jis", "gbk", "euc-kr", "cp1252", "latin-1"]
    )
    target_normalization: NormalizationForm = NormalizationForm.NFC
    strip_control_chars: bool = True
    auto_repair_mojibake: bool = True
    generate_ascii_slug: bool = True


@dataclass
class SanitizedInstrumentName:
    original_input: str
    detected_encoding: str
    cleaned_name: str
    ascii_slug: str
    normalization_form: NormalizationForm
    contains_mojibake: bool
    audit_actions: List[str] = field(default_factory=list)


class GlobalInstrumentNameSanitizer:
    """
    Institutional Global Instrument Name Unicode Sanitizer & Encoding Engine.
    
    Resolves multi-exchange global instrument name encoding issues including:
    - Byte-Order-Mark (BOM) stripping (`U+FEFF`).
    - Automatic multi-encoding detection and fallback decoding (UTF-8, Latin-1, Shift-JIS, GBK, EUC-KR).
    - Mojibake corruption detection and repair (e.g. `SociÃ©tÃ©` -> `Société`).
    - Unicode normalization (NFC/NFD/NFKC/NFKD) to prevent Security Master duplicate keys.
    - Zero-width space and invisible control character stripping (`U+200B`, `U+200C`).
    - ASCII slug generation for legacy FIX tag 55 and exchange interfaces.
    """

    # Common Mojibake signature patterns (UTF-8 bytes misdecoded as Latin-1/Windows-1252)
    MOJIBAKE_REPLACEMENT_MAP = {
        "Ã©": "é", "Ã¨": "è", "Ã ": "à", "Ã§": "ç", "Ã´": "ô", "Ã«": "ë",
        "Ã¯": "ï", "Ã®": "î", "Ã¼": "ü", "Ã¶": "ö", "Ã¤": "ä", "ÃŸ": "ß",
        "Ã": "Á", "Ã‰": "É", "Ãˆ": "È", "Ã–": "Ö", "Ãœ": "Ü",
    }

    def __init__(self, config: Optional[InstrumentSanitizerConfig] = None):
        self.config = config or InstrumentSanitizerConfig()
        logger.info("Initialized Global Instrument Name Unicode Sanitizer Engine")

    def decode_bytes(self, raw_bytes: bytes) -> Tuple[str, str]:
        """
        Decodes raw byte sequences into Unicode string using default and fallback encoding rules.
        """
        # 1. Check UTF-8 BOM
        if raw_bytes.startswith(b"\xef\xbb\xbf"):
            return raw_bytes[3:].decode("utf-8", errors="replace"), "utf-8-sig"

        # 2. Try primary default encoding
        try:
            return raw_bytes.decode(self.config.default_encoding), self.config.default_encoding
        except UnicodeDecodeError:
            pass

        # 3. Try fallback encodings
        for enc in self.config.fallback_encodings:
            try:
                decoded = raw_bytes.decode(enc)
                return decoded, enc
            except (UnicodeDecodeError, LookupError):
                continue

        # 4. Final fallback with replacement characters
        return raw_bytes.decode("utf-8", errors="replace"), "utf-8 (replace)"

    @classmethod
    def repair_mojibake(cls, text: str) -> Tuple[str, bool]:
        """
        Detects and repairs common UTF-8 -> Latin-1 Mojibake character corruptions.
        """
        repaired = text
        was_repaired = False

        for corrupted, fixed in cls.MOJIBAKE_REPLACEMENT_MAP.items():
            if corrupted in repaired:
                repaired = repaired.replace(corrupted, fixed)
                was_repaired = True

        # Secondary heuristic: try encoding as Latin-1 and re-decoding as UTF-8
        if not was_repaired and any(c in text for c in ["Ã", "Â"]):
            try:
                candidate = text.encode("latin-1").decode("utf-8")
                if candidate != text:
                    repaired = candidate
                    was_repaired = True
            except (UnicodeEncodeError, UnicodeDecodeError):
                pass

        return repaired, was_repaired

    @staticmethod
    def strip_control_and_zero_width_chars(text: str) -> Tuple[str, bool]:
        """
        Strips non-printable C0/C1 control characters, zero-width spaces (U+200B, U+200C),
        and byte order marks (U+FEFF).
        """
        stripped_chars = []
        modified = False

        for char in text:
            code = ord(char)
            # Remove BOM U+FEFF, Zero-width space U+200B, U+200C, U+200D, U+2060
            if code in (0xFEFF, 0x200B, 0x200C, 0x200D, 0x2060):
                modified = True
                continue
            # Remove control characters except standard whitespace
            if unicodedata.category(char).startswith("C") and char not in ("\t", "\n", "\r"):
                modified = True
                continue
            stripped_chars.append(char)

        return "".join(stripped_chars), modified

    @staticmethod
    def normalize_unicode(text: str, form: NormalizationForm) -> str:
        """Applies explicit Unicode normalization (NFC, NFD, NFKC, NFKD)."""
        return unicodedata.normalize(form.value, text)

    @staticmethod
    def generate_ascii_slug(text: str) -> str:
        """
        Generates a clean ASCII transliterated slug for legacy FIX tag 55 or exchange systems.
        E.g. "Société Générale" -> "SOCIETE GENERALE"
        """
        # Decompose accented characters to base + accent
        nfd_str = unicodedata.normalize("NFD", text)
        # Filter out combining marks
        ascii_chars = [c for c in nfd_str if unicodedata.category(c) != "Mn"]
        ascii_text = "".join(ascii_chars)
        # Convert non-ASCII characters to closest representation or strip
        ascii_clean = ascii_text.encode("ascii", "ignore").decode("ascii")
        # Standardize spaces and uppercase
        slug = re.sub(r"\s+", " ", ascii_clean).strip().upper()
        return slug

    def sanitize_instrument_name(
        self, input_data: Union[str, bytes], config: Optional[InstrumentSanitizerConfig] = None
    ) -> SanitizedInstrumentName:
        """
        Executes full sanitization pipeline on raw string or bytes input.
        """
        cfg = config or self.config
        audit = []

        # 1. Decode bytes if input is raw byte stream
        if isinstance(input_data, bytes):
            raw_str, detected_enc = self.decode_bytes(input_data)
            audit.append(f"Decoded bytes using encoding '{detected_enc}'")
            original_display = input_data.hex()
        else:
            raw_str = input_data
            detected_enc = "utf-8 (native str)"
            original_display = input_data

        if not raw_str:
            raise UnicodeProcessingError("Input instrument name is empty.")

        current_str = raw_str

        # 2. Control and Zero-Width Character Removal
        if cfg.strip_control_chars:
            current_str, ctrl_stripped = self.strip_control_and_zero_width_chars(current_str)
            if ctrl_stripped:
                audit.append("Stripped invisible control/zero-width/BOM characters")

        # 3. Mojibake Detection and Repair
        contains_mojibake = False
        if cfg.auto_repair_mojibake:
            current_str, contains_mojibake = self.repair_mojibake(current_str)
            if contains_mojibake:
                audit.append(f"Repaired Mojibake corruption: '{raw_str}' -> '{current_str}'")

        # 4. Unicode Normalization
        normalized_str = self.normalize_unicode(current_str, cfg.target_normalization)
        if normalized_str != current_str:
            audit.append(f"Applied Unicode normalization ({cfg.target_normalization.value})")

        # 5. ASCII Slug Generation
        ascii_slug = ""
        if cfg.generate_ascii_slug:
            ascii_slug = self.generate_ascii_slug(normalized_str)
            audit.append(f"Generated ASCII slug: '{ascii_slug}'")

        logger.info(f"Sanitized Instrument Name [{original_display}] -> '{normalized_str}' (ASCII: '{ascii_slug}')")

        return SanitizedInstrumentName(
            original_input=original_display,
            detected_encoding=detected_enc,
            cleaned_name=normalized_str,
            ascii_slug=ascii_slug,
            normalization_form=cfg.target_normalization,
            contains_mojibake=contains_mojibake,
            audit_actions=audit,
        )

