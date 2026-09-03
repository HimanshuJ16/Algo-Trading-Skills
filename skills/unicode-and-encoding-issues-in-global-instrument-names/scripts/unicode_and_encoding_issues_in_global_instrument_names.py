"""
unicode-and-encoding-issues-in-global-instrument-names: decode, repair and normalise
instrument names arriving from global exchange and vendor reference-data feeds, and
derive a stable ASCII secondary key from them.

The engine sanitises *names*, not identifiers. It does not map a vendor symbol to a
canonical one -- that is `reference-data-symbol-mapping-across-vendors` -- and it does
not decide which vendor's spelling wins -- that is
`reference-data-golden-source-designation`.

Four properties drive the design, and all four are routinely assumed away:

1.  **Encoding detection is a guess, and a wrong guess is silent.** The CJK legacy
    codecs overlap: a CP932 encoding of ``髙島屋`` (Takashimaya, TSE 8233) is a *valid*
    GBK byte sequence that decodes, without error, to ``钹搰壆``. No amount of fallback
    ordering fixes this, because both decodes succeed. So ``source_encoding`` lets a
    caller declare the encoding a venue actually publishes, every result carries a
    ``decode_confidence``, and a guessed or lossy decode is logged at WARNING rather
    than presented as fact.
2.  **Mojibake repair is a byte-exact round trip, not a substitution table.** A table of
    ``"Ã©" -> "é"`` pairs cannot tell mojibake from correctly encoded text: ``SÃO
    MARTINHO S.A.`` (B3: SMTO3) is valid, correctly encoded Portuguese, and a table
    containing a bare ``"Ã"`` key silently rewrites it to ``SÁO MARTINHO S.A.``. Repair
    therefore re-encodes to CP1252/Latin-1 and re-decodes as *strict* UTF-8; the strict
    decode is the guard. The table survives only as a residual pass for mixed strings.
3.  **Transliteration must not delete letters.** NFD decomposes ``Ü`` into ``U`` plus a
    combining mark, but it does not decompose ``Ø``, ``ß``, ``Ł``, ``Æ`` or ``Đ`` at
    all -- so "decompose, drop combining marks, encode ASCII with errors='ignore'" turns
    ``Ørsted A/S`` (CPH: ORSTED) into ``RSTED A/S``. Non-decomposable letters get an
    explicit replacement, and characters that genuinely cannot be transliterated (CJK,
    Cyrillic, Hebrew) are reported rather than silently dropped.
4.  **Mojibake repair must run before control-character stripping.** The Latin-1
    mojibake of ``L'Oréal`` is ``Lâ\\x80\\x99Oréal``: two of its three leading bytes are
    C1 control characters. Strip controls first and the repair has nothing to work with.

Verified 2026-09 against primary and vendor-documentation sources:

- **Unicode normalization** -- UAX #15 defines NFC/NFD/NFKC/NFKD; canonically equivalent
  strings share a binary representation only after normalisation, which is why NFC is
  the form to key a security master on. https://unicode.org/reports/tr15/
- **Joining controls are orthographic, not noise** -- UAX #31 section 2.3: "The joining
  controls are used in the orthographies of some languages, as well as in emoji ZWJ
  sequences." Removing U+200C/U+200D changes Persian and Indic spellings, so it is
  configurable here. https://www.unicode.org/reports/tr31/
- **FIX carries the native name in an encoded field, not in Symbol(55)** --
  EncodedSecurityDesc(351) is the "Encoded (non-ASCII characters) representation of the
  SecurityDesc <107> field in the encoded format specified via the MessageEncoding <347>
  field", and "If used, the ASCII (English) representation should also be specified in
  the SecurityDesc <107> field". MessageEncoding(347) takes ISO-2022-JP, EUC-JP,
  Shift_JIS or UTF-8. The ASCII slug produced here is that ASCII representation.
  https://www.onixs.biz/fix-dictionary/4.4/tagnum_351.html
  https://www.onixs.biz/fix-dictionary/4.4/tagnum_347.html
- **Transliteration of non-decomposable Latin letters** -- ICAO Doc 9303 Part 3 gives the
  machine-readable-zone transliterations this module follows for AE, OE, TH, D, L and
  SS. https://www.icao.int/publications/pages/publication.aspx?docnum=9303
- **GB 18030 is a mandatory PRC national standard** and a superset of GBK and GB 2312;
  the GB 18030-2022 revision became compulsory on 1 August 2023.
  https://en.wikipedia.org/wiki/GB_18030

Limitations (read before relying on an output):

- **Fallback decoding is unreliable by construction.** The fallback order is a
  convention, not a detector. Any result carrying ``decode_confidence == "guessed"``
  should be treated as unverified. Declare ``source_encoding`` per venue in production.
- **``cp932`` is not a strict superset of ``shift_jis``.** It decodes roughly 4,000 more
  two-byte sequences (NEC/IBM extension kanji), but it maps six sequences differently --
  0x8160 is U+301C under ``shift_jis`` and U+FF5E under ``cp932`` (the "wave dash"
  problem), likewise 0x8161, 0x817C, 0x8191 and 0x8192. ``cp932`` is therefore ordered
  *after* ``shift_jis``, so bytes ``shift_jis`` accepts keep their JIS mappings and only
  the extension bytes reach ``cp932``.
- **Python's ``gb18030`` codec predates GB 18030-2022.** It implements the earlier
  revision, so the 2022 additions and the 51 changed characters are not reflected.
- **This module does not detect homoglyph or confusable attacks.** A ticker written with
  a Cyrillic "А" normalises and slugs cleanly. Confusable detection is UTS #39 work and
  is out of scope.
- **Transliteration is lossy for non-Latin scripts by design.** A Japanese or Hebrew name
  yields an empty or partial slug; ``ascii_slug_is_lossy`` says so, and an empty slug
  must never be written to Symbol(55) or SecurityDesc(107) unchecked.
"""
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple, Union

logger = logging.getLogger(__name__)

__all__ = [
    "NormalizationForm",
    "UnicodeProcessingError",
    "InstrumentSanitizerConfig",
    "DecodedPayload",
    "SanitizedInstrumentName",
    "GlobalInstrumentNameSanitizer",
    "ASCII_TRANSLITERATION_MAP",
    "BOM_SIGNATURES",
    "CONFIDENCE_DECLARED",
    "CONFIDENCE_BOM",
    "CONFIDENCE_DEFAULT",
    "CONFIDENCE_GUESSED",
    "CONFIDENCE_LOSSY",
    "CONFIDENCE_NATIVE_STR",
]

# --- Decode confidence ---------------------------------------------------------

#: The caller declared the encoding; it was applied strictly.
CONFIDENCE_DECLARED = "declared"
#: A byte-order mark identified the encoding unambiguously.
CONFIDENCE_BOM = "bom"
#: The configured default encoding decoded the payload strictly.
CONFIDENCE_DEFAULT = "default"
#: A fallback codec accepted the bytes. It may still be the wrong codec -- see the
#: module docstring. Never persist a guessed name without review.
CONFIDENCE_GUESSED = "guessed"
#: No configured codec accepted the bytes; U+FFFD replacements were substituted.
CONFIDENCE_LOSSY = "lossy"
#: Input was already a ``str``; no decoding was performed.
CONFIDENCE_NATIVE_STR = "native-str"

#: Byte-order marks, longest signature first. The order is load-bearing: the UTF-32-LE
#: BOM (FF FE 00 00) begins with the UTF-16-LE BOM (FF FE), so testing two bytes first
#: would misidentify every UTF-32-LE payload. Each entry is (signature, reported name,
#: codec applied after the signature bytes are removed).
BOM_SIGNATURES: Tuple[Tuple[bytes, str, str], ...] = (
    (b"\x00\x00\xfe\xff", "utf-32-be", "utf-32-be"),
    (b"\xff\xfe\x00\x00", "utf-32-le", "utf-32-le"),
    (b"\xef\xbb\xbf", "utf-8-sig", "utf-8"),
    (b"\xfe\xff", "utf-16-be", "utf-16-be"),
    (b"\xff\xfe", "utf-16-le", "utf-16-le"),
)

# --- Character classes ---------------------------------------------------------

#: Removed as invisible regardless of Unicode category: the BOM when it survives into
#: the decoded string, the zero-width space, and the word joiner.
INVISIBLE_CODEPOINTS = frozenset({0xFEFF, 0x200B, 0x2060})

#: Joining controls. UAX #31 section 2.3: "The joining controls are used in the
#: orthographies of some languages". Stripped by default because they break
#: security-master index lookups, but ``strip_joiner_controls=False`` preserves them for
#: Persian, Arabic and Indic names where they are part of the spelling.
JOINER_CODEPOINTS = frozenset({0x200C, 0x200D})

#: Control characters that are legitimate whitespace and are kept by the stripper.
_PRESERVED_WHITESPACE = ("\t", "\n", "\r")

#: The characters CP1252 assigns to 0x80-0x9F, where Latin-1 has C1 controls. Their
#: presence in a decoded string is the strongest single indicator of UTF-8 bytes having
#: been read as CP1252.
_CP1252_HIGH_PUNCTUATION = frozenset(
    "€‚ƒ„…†‡ˆ‰Š‹ŒŽ"
    "‘’“”•–—˜™š›œž"
    "Ÿ"
)

#: Encodings tried, in order, when repairing mojibake by round trip. CP1252 first: it is
#: what Windows-based vendor tooling actually applies when it mislabels UTF-8, and it is
#: the only one of the two that can represent the punctuation above.
_MOJIBAKE_ROUNDTRIP_ENCODINGS: Tuple[str, ...] = ("cp1252", "latin-1")

#: Bound on repeated round trips. Doubly-encoded mojibake does occur when a feed is
#: re-ingested through the same broken pipeline twice, but the loop must not be
#: open-ended.
_MAX_MOJIBAKE_ROUNDS = 3

#: Latin letters that canonical decomposition does NOT split into a base letter plus
#: combining marks. Without an explicit replacement the ASCII filter deletes them
#: outright and ``Ørsted A/S`` becomes ``RSTED A/S``. Replacements follow the ICAO
#: Doc 9303 Part 3 machine-readable-zone table for AE, OE, TH, D, L and SS.
#:
#: One deliberate departure from ICAO: Ø maps to "O", not ICAO's "OE". This module
#: strips diacritics elsewhere (Ö to O, Ü to U, Å to A, via NFD), and mapping Ø to OE
#: while Ö maps to O would transliterate the same Nordic vowel two different ways and
#: split its security-master key. Callers needing MRZ-identical output should override
#: this mapping.
ASCII_TRANSLITERATION_MAP: Dict[str, str] = {
    "Æ": "AE", "æ": "AE",
    "Ø": "O", "ø": "O",
    "Œ": "OE", "œ": "OE",
    "Ð": "D", "ð": "D",
    "Đ": "D", "đ": "D",
    "Þ": "TH", "þ": "TH",
    "Ł": "L", "ł": "L",
    "ẞ": "SS", "ß": "SS",
    "Ħ": "H", "ħ": "H",
    "Ŧ": "T", "ŧ": "T",
    "Ŋ": "NG", "ŋ": "NG",
    "Ə": "E", "ə": "E",
    "Ĳ": "IJ", "ĳ": "IJ",
    "ı": "I", "İ": "I",
}

_WHITESPACE_RUN_RE = re.compile(r"\s+")


class NormalizationForm(Enum):
    NFC = "NFC"    # Canonical Composition (default for security-master keys, UAX #15)
    NFD = "NFD"    # Canonical Decomposition
    NFKC = "NFKC"  # Compatibility Composition
    NFKD = "NFKD"  # Compatibility Decomposition


class UnicodeProcessingError(Exception):
    """A name could not be sanitised into something safe to persist.

    Raised when the input is empty, when a declared ``source_encoding`` does not decode
    the payload, or when sanitisation consumed the whole name -- a string of nothing but
    zero-width spaces leaves no name behind, and writing the empty string into a
    security master is worse than failing the record.
    """


@dataclass
class InstrumentSanitizerConfig:
    """Engine behaviour.

    Args:
        default_encoding: Tried first, strictly, on byte input carrying no BOM.
        fallback_encodings: Tried in order after ``default_encoding`` fails. Results are
            marked ``CONFIDENCE_GUESSED``; see the module docstring on why this ordering
            cannot be made reliable. Each legacy CJK codec is followed by its Microsoft
            superset so that extension characters (NEC/IBM kanji, UHC Hangul, GB 18030
            ideographs) are decoded by the right family rather than falling through to
            an unrelated one.
        source_encoding: Declared venue encoding. When set it is applied strictly and no
            guessing happens at all: a decode failure raises instead of silently
            producing a plausible wrong name. This is the production path.
        target_normalization: Form applied to ``cleaned_name``. NFC unless you have a
            specific reason; NFKC additionally folds full-width Latin and half-width
            katakana, which is usually wanted for matching and unwanted for display.
        strip_control_chars: Remove control, format and invisible characters.
        strip_joiner_controls: Also remove U+200C/U+200D. Set False for Persian, Arabic
            and Indic names, where the joining controls are part of the spelling
            (UAX #31 section 2.3).
        auto_repair_mojibake: Attempt round-trip mojibake repair.
        generate_ascii_slug: Derive the ASCII secondary key.
    """

    default_encoding: str = "utf-8"
    fallback_encodings: List[str] = field(
        default_factory=lambda: [
            "utf-8-sig",
            "shift_jis", "cp932",
            "gbk", "gb18030",
            "euc-kr", "cp949",
            "cp1252", "latin-1",
        ]
    )
    source_encoding: Optional[str] = None
    target_normalization: NormalizationForm = NormalizationForm.NFC
    strip_control_chars: bool = True
    strip_joiner_controls: bool = True
    auto_repair_mojibake: bool = True
    generate_ascii_slug: bool = True


@dataclass
class DecodedPayload:
    """Result of turning raw feed bytes into a ``str``."""

    text: str
    encoding: str
    confidence: str
    replacement_chars: int = 0

    @property
    def is_lossy(self) -> bool:
        return self.replacement_chars > 0


@dataclass
class SanitizedInstrumentName:
    """Sanitised name plus everything a reviewer needs to judge whether to trust it.

    ``cleaned_name`` is the value to key the security master on. ``ascii_slug`` is the
    ASCII representation for SecurityDesc(107)/Symbol(55) -- but only when
    ``ascii_slug_is_lossy`` is False, or after a human has accepted the loss.
    """

    original_input: str
    detected_encoding: str
    cleaned_name: str
    ascii_slug: str
    normalization_form: NormalizationForm
    contains_mojibake: bool
    audit_actions: List[str] = field(default_factory=list)
    decode_confidence: str = CONFIDENCE_NATIVE_STR
    lossy_decode: bool = False
    ascii_slug_is_lossy: bool = False
    dropped_characters: Tuple[str, ...] = ()
    warnings: List[str] = field(default_factory=list)

    @property
    def is_trustworthy(self) -> bool:
        """True when nothing about this result needs a human to look at it.

        A guessed encoding, a lossy decode and an untransliterable slug are each
        individually enough to make an unattended security-master write unsafe.
        """
        return not self.warnings


class GlobalInstrumentNameSanitizer:
    """Global instrument-name Unicode decoder, mojibake repairer and normaliser.

    Pipeline order is fixed and load-bearing:

    1. decode bytes (declared encoding, else BOM, else default, else fallback guess);
    2. repair mojibake -- **before** stripping controls, because the Latin-1 mojibake of
       typographic punctuation *is* C1 control characters;
    3. strip invisible and control characters;
    4. normalise (NFC by default);
    5. transliterate to an ASCII slug, reporting what could not be transliterated.
    """

    #: Residual repair pairs, applied only when the round trip in
    #: :meth:`repair_mojibake` fails -- which happens on strings where only part of the
    #: text is corrupt. Applied longest key first so a two-character sequence is never
    #: pre-empted by a one-character prefix of itself.
    #:
    #: Deliberately contains no bare "Ã" entry: U+00C3 is a letter in its own right
    #: (uppercase Portuguese "SÃO"), and rewriting it unconditionally corrupts correctly
    #: encoded names.
    MOJIBAKE_REPLACEMENT_MAP: Dict[str, str] = {
        "Ã©": "é", "Ã¨": "è", "Ã ": "à", "Ã§": "ç", "Ã´": "ô", "Ã«": "ë",
        "Ã¯": "ï", "Ã®": "î", "Ã¼": "ü", "Ã¶": "ö", "Ã¤": "ä", "ÃŸ": "ß",
        "Ã‰": "É", "Ãˆ": "È", "Ã–": "Ö", "Ãœ": "Ü", "Ã‡": "Ç", "Ã‘": "Ñ",
    }

    def __init__(self, config: Optional[InstrumentSanitizerConfig] = None):
        self.config = config or InstrumentSanitizerConfig()
        logger.debug(
            "Initialised instrument-name sanitizer (default=%s, declared=%s, fallbacks=%s)",
            self.config.default_encoding,
            self.config.source_encoding,
            ",".join(self.config.fallback_encodings),
        )

    # --- Decoding ---------------------------------------------------------------

    def decode_payload(
        self, raw_bytes: bytes, source_encoding: Optional[str] = None
    ) -> DecodedPayload:
        """Decode raw feed bytes, recording how much the result can be trusted.

        Resolution order: declared encoding (strict, raises on failure), then byte-order
        mark, then the configured default (strict), then the fallback codecs (guess),
        then UTF-8 with U+FFFD replacement (lossy).

        Args:
            raw_bytes: Bytes as received from the feed.
            source_encoding: Overrides ``config.source_encoding`` for this call.

        Raises:
            UnicodeProcessingError: A declared ``source_encoding`` did not decode the
                payload, or is not a codec this Python knows. Declaring an encoding is
                an assertion; a failed assertion must not fall through to a guess.
        """
        declared = source_encoding or self.config.source_encoding
        if declared:
            try:
                return DecodedPayload(
                    raw_bytes.decode(declared), declared, CONFIDENCE_DECLARED
                )
            except (UnicodeDecodeError, LookupError) as exc:
                raise UnicodeProcessingError(
                    f"Declared source encoding {declared!r} failed to decode "
                    f"{len(raw_bytes)} byte(s): {exc}"
                ) from exc

        for signature, reported, codec in BOM_SIGNATURES:
            if raw_bytes.startswith(signature):
                body = raw_bytes[len(signature):]
                try:
                    return DecodedPayload(body.decode(codec), reported, CONFIDENCE_BOM)
                except UnicodeDecodeError:
                    text = body.decode(codec, errors="replace")
                    count = text.count("�")
                    logger.warning(
                        "Payload carries a %s BOM but is not valid %s; %d character(s) "
                        "replaced with U+FFFD", reported, reported, count,
                    )
                    return DecodedPayload(
                        text, f"{reported} (replace)", CONFIDENCE_LOSSY, count
                    )

        try:
            return DecodedPayload(
                raw_bytes.decode(self.config.default_encoding),
                self.config.default_encoding,
                CONFIDENCE_DEFAULT,
            )
        except (UnicodeDecodeError, LookupError):
            pass

        for enc in self.config.fallback_encodings:
            try:
                decoded = raw_bytes.decode(enc)
            except (UnicodeDecodeError, LookupError):
                continue
            logger.warning(
                "Guessed encoding %r for a %d-byte instrument name; overlapping legacy "
                "codecs decode each other's bytes without error, so this result is "
                "unverified. Declare source_encoding for this venue.",
                enc, len(raw_bytes),
            )
            return DecodedPayload(decoded, enc, CONFIDENCE_GUESSED)

        text = raw_bytes.decode("utf-8", errors="replace")
        count = text.count("�")
        logger.warning(
            "No configured codec decoded a %d-byte instrument name; %d character(s) "
            "replaced with U+FFFD", len(raw_bytes), count,
        )
        return DecodedPayload(text, "utf-8 (replace)", CONFIDENCE_LOSSY, count)

    def decode_bytes(self, raw_bytes: bytes) -> Tuple[str, str]:
        """Decode raw bytes, returning ``(text, encoding)``.

        Thin wrapper over :meth:`decode_payload` for callers that do not need the
        confidence signal. Prefer :meth:`decode_payload` in production -- this form
        cannot distinguish an authoritative decode from a guess or a lossy one.
        """
        payload = self.decode_payload(raw_bytes)
        return payload.text, payload.encoding

    # --- Mojibake ---------------------------------------------------------------

    @staticmethod
    def _mojibake_score(text: str) -> int:
        """Count characters typical of UTF-8 bytes read as a single-byte codec.

        Used to reject a "repair" that does not actually make the string less corrupt.
        """
        return sum(
            1 for c in text
            if 0x80 <= ord(c) <= 0xFF or c in _CP1252_HIGH_PUNCTUATION
        )

    @classmethod
    def _try_roundtrip(cls, text: str) -> Optional[str]:
        """One round of round-trip repair, or None if none applies.

        A candidate is accepted only if the single-byte re-encode succeeds, the bytes
        decode as *strict* UTF-8, nothing became U+FFFD, and the mojibake score strictly
        falls. The strict decode is what keeps correctly encoded text intact.
        """
        score = cls._mojibake_score(text)
        if score == 0:
            return None
        for enc in _MOJIBAKE_ROUNDTRIP_ENCODINGS:
            try:
                candidate = text.encode(enc).decode("utf-8")
            except (UnicodeEncodeError, UnicodeDecodeError):
                continue
            if candidate == text or "�" in candidate:
                continue
            if cls._mojibake_score(candidate) < score:
                return candidate
        return None

    @classmethod
    def has_residual_mojibake(cls, text: str) -> bool:
        """True if ``text`` still looks repairable after :meth:`repair_mojibake` ran.

        Only reachable when a string was encoded through the broken pipeline more times
        than :data:`_MAX_MOJIBAKE_ROUNDS` allows to be undone. The pipeline turns this
        into a warning rather than presenting a partially repaired name as clean.
        """
        return cls._try_roundtrip(text) is not None

    @classmethod
    def repair_mojibake(cls, text: str) -> Tuple[str, bool]:
        """Repair UTF-8 text that was decoded as CP1252 or Latin-1.

        Works by round trip: re-encode to the single-byte codec that produced the
        corruption, then decode those bytes as *strict* UTF-8. The strict decode is the
        safety guard -- correctly encoded text such as "SÃO MARTINHO S.A." (B3: SMTO3)
        re-encodes to ``b"S\\xc3O ..."``, which is not valid UTF-8, so it is left alone.
        A repair is additionally rejected unless it strictly lowers the mojibake score,
        which stops a round trip trading one corruption for another.

        Repeats up to three times: a feed re-ingested through the same broken pipeline
        twice produces doubly-encoded mojibake.

        Returns:
            ``(repaired_text, was_repaired)``.
        """
        current = text
        was_repaired = False

        for _ in range(_MAX_MOJIBAKE_ROUNDS):
            candidate = cls._try_roundtrip(current)
            if candidate is None:
                break
            current = candidate
            was_repaired = True

        if was_repaired:
            return current, True

        # Residual pass: reached only when no whole-string round trip applies, i.e. the
        # string mixes already-correct and corrupt runs. Longest key first.
        for corrupted in sorted(cls.MOJIBAKE_REPLACEMENT_MAP, key=len, reverse=True):
            if corrupted in current:
                current = current.replace(
                    corrupted, cls.MOJIBAKE_REPLACEMENT_MAP[corrupted]
                )
                was_repaired = True

        return current, was_repaired

    # --- Invisible and control characters ---------------------------------------

    @staticmethod
    def strip_control_and_zero_width_chars(
        text: str, strip_joiner_controls: bool = True
    ) -> Tuple[str, bool]:
        """Remove control, format and zero-width characters.

        Always removes U+FEFF, U+200B, U+2060 and every Unicode ``C*`` character except
        tab, newline and carriage return.

        Args:
            strip_joiner_controls: Also remove U+200C/U+200D. Pass False for Persian,
                Arabic and Indic names: UAX #31 section 2.3 notes the joining controls
                are used in the orthographies of some languages, so removing them changes
                the spelling rather than cleaning it.

        Returns:
            ``(cleaned_text, was_modified)``.
        """
        kept: List[str] = []
        modified = False

        for char in text:
            code = ord(char)
            if code in INVISIBLE_CODEPOINTS:
                modified = True
                continue
            if code in JOINER_CODEPOINTS:
                if strip_joiner_controls:
                    modified = True
                    continue
                kept.append(char)
                continue
            if (
                unicodedata.category(char).startswith("C")
                and char not in _PRESERVED_WHITESPACE
            ):
                modified = True
                continue
            kept.append(char)

        return "".join(kept), modified

    # --- Normalisation and transliteration --------------------------------------

    @staticmethod
    def normalize_unicode(text: str, form: NormalizationForm) -> str:
        """Apply a Unicode normalisation form (UAX #15)."""
        return unicodedata.normalize(form.value, text)

    @classmethod
    def transliterate_to_ascii(cls, text: str) -> Tuple[str, Tuple[str, ...]]:
        """Fold a name to an uppercase ASCII slug, reporting what could not be folded.

        Order matters. Uppercasing happens *first* so Unicode's own SpecialCasing data
        does the work where it can -- ``"Straße".upper()`` is ``"STRASSE"``. Then NFD
        strips diacritics from letters that decompose, then
        :data:`ASCII_TRANSLITERATION_MAP` handles the letters that do not decompose at
        all. Only after all three does anything get dropped.

        Returns:
            ``(slug, dropped_characters)``. A non-empty ``dropped_characters`` means the
            slug does not represent the name -- a CJK, Cyrillic or Hebrew name typically
            drops everything and yields ``""``. Callers must not write such a slug to
            Symbol(55) or SecurityDesc(107) without handling the loss. An empty slug from
            a non-empty name is always reported as lossy, including when the only thing
            removed was combining marks.
        """
        upper = text.upper()
        decomposed = unicodedata.normalize("NFD", upper)

        out: List[str] = []
        dropped: List[str] = []
        for char in decomposed:
            if unicodedata.category(char) == "Mn":
                continue
            if ord(char) < 128:
                out.append(char)
                continue
            replacement = ASCII_TRANSLITERATION_MAP.get(char)
            if replacement is None:
                # The character may only be mapped in its composed form.
                replacement = ASCII_TRANSLITERATION_MAP.get(
                    unicodedata.normalize("NFC", char)
                )
            if replacement is not None:
                out.append(replacement)
                continue
            dropped.append(char)

        slug = _WHITESPACE_RUN_RE.sub(" ", "".join(out)).strip().upper()

        # A non-empty name that slugs to nothing is lossy even when every character
        # removed was a combining mark, which the loop above skips without recording.
        # An unflagged empty slug is exactly the value that must never reach Symbol(55).
        if not slug and text.strip() and not dropped:
            dropped = [c for c in text if not c.isspace()]

        return slug, tuple(dict.fromkeys(dropped))

    @staticmethod
    def generate_ascii_slug(text: str) -> str:
        """Return the uppercase ASCII slug for ``text``.

        Convenience wrapper over :meth:`transliterate_to_ascii` that discards the
        dropped-character report. Use :meth:`transliterate_to_ascii` when the caller has
        to decide what to do about a name that will not transliterate.
        """
        slug, _dropped = GlobalInstrumentNameSanitizer.transliterate_to_ascii(text)
        return slug

    # --- Pipeline ---------------------------------------------------------------

    def sanitize_instrument_name(
        self,
        input_data: Union[str, bytes, bytearray, memoryview],
        config: Optional[InstrumentSanitizerConfig] = None,
        source_encoding: Optional[str] = None,
    ) -> SanitizedInstrumentName:
        """Run the full pipeline over one instrument name.

        Args:
            input_data: A ``str`` already decoded upstream, or raw feed bytes.
            config: Overrides the engine's config for this call.
            source_encoding: Declared encoding for this payload; ignored for ``str``
                input.

        Raises:
            TypeError: ``input_data`` is neither text nor a bytes-like object.
            UnicodeProcessingError: The input is empty, a declared ``source_encoding``
                did not decode it, or sanitisation left no name behind.
        """
        cfg = config or self.config
        audit: List[str] = []
        warnings: List[str] = []

        if isinstance(input_data, (bytes, bytearray, memoryview)):
            raw_bytes = bytes(input_data)
            if not raw_bytes:
                raise UnicodeProcessingError("Input instrument name is empty.")
            payload = self.decode_payload(raw_bytes, source_encoding=source_encoding)
            raw_str = payload.text
            detected_enc = payload.encoding
            confidence = payload.confidence
            lossy_decode = payload.is_lossy
            original_display = raw_bytes.hex()
            audit.append(
                f"Decoded {len(raw_bytes)} byte(s) as '{detected_enc}' "
                f"(confidence: {confidence})"
            )
            if confidence == CONFIDENCE_GUESSED:
                warnings.append(
                    f"Encoding {detected_enc!r} was guessed, not declared; the decoded "
                    f"name is unverified."
                )
            if lossy_decode:
                warnings.append(
                    f"{payload.replacement_chars} undecodable byte sequence(s) were "
                    f"replaced with U+FFFD; the name is incomplete."
                )
        elif isinstance(input_data, str):
            raw_str = input_data
            detected_enc = "utf-8 (native str)"
            confidence = CONFIDENCE_NATIVE_STR
            lossy_decode = False
            original_display = input_data
        else:
            raise TypeError(
                f"input_data must be str or bytes-like, got {type(input_data).__name__}"
            )

        if not raw_str:
            raise UnicodeProcessingError("Input instrument name is empty.")

        current_str = raw_str

        # Mojibake repair precedes control stripping: the Latin-1 mojibake of typographic
        # punctuation is literally C1 control characters, and stripping those first
        # destroys the byte pattern the repair depends on.
        contains_mojibake = False
        if cfg.auto_repair_mojibake:
            current_str, contains_mojibake = self.repair_mojibake(current_str)
            if contains_mojibake:
                audit.append(
                    f"Repaired mojibake corruption: '{raw_str}' -> '{current_str}'"
                )
            if self.has_residual_mojibake(current_str):
                # More layers of corruption than _MAX_MOJIBAKE_ROUNDS can undo. Saying
                # "repaired" about a string that is still corrupt is the failure this
                # module exists to prevent.
                warnings.append(
                    f"Mojibake remains after {_MAX_MOJIBAKE_ROUNDS} repair round(s); "
                    f"the name is still corrupt."
                )
                logger.warning(
                    "Mojibake still detectable in %r after %d repair round(s)",
                    current_str, _MAX_MOJIBAKE_ROUNDS,
                )

        if cfg.strip_control_chars:
            current_str, ctrl_stripped = self.strip_control_and_zero_width_chars(
                current_str, strip_joiner_controls=cfg.strip_joiner_controls
            )
            if ctrl_stripped:
                audit.append("Stripped invisible control/zero-width/BOM characters")

        normalized_str = self.normalize_unicode(current_str, cfg.target_normalization)
        if normalized_str != current_str:
            audit.append(
                f"Applied Unicode normalization ({cfg.target_normalization.value})"
            )

        if not normalized_str.strip():
            raise UnicodeProcessingError(
                f"Instrument name {raw_str!r} contained no printable characters after "
                f"sanitization; refusing to emit an empty security-master name."
            )

        ascii_slug = ""
        dropped: Tuple[str, ...] = ()
        if cfg.generate_ascii_slug:
            ascii_slug, dropped = self.transliterate_to_ascii(normalized_str)
            audit.append(f"Generated ASCII slug: '{ascii_slug}'")
            if dropped:
                warnings.append(
                    "ASCII slug is lossy: "
                    + ("no character transliterated" if not ascii_slug
                       else f"{len(dropped)} distinct character(s) dropped")
                    + f" ({''.join(dropped)!r})"
                )
                logger.warning(
                    "ASCII slug for %r dropped %d untransliterable character(s) (%r); "
                    "do not write it to Symbol(55)/SecurityDesc(107) unchecked",
                    normalized_str, len(dropped), "".join(dropped),
                )

        logger.info(
            "Sanitized instrument name -> %r (slug %r, encoding %s, confidence %s)",
            normalized_str, ascii_slug, detected_enc, confidence,
        )

        return SanitizedInstrumentName(
            original_input=original_display,
            detected_encoding=detected_enc,
            cleaned_name=normalized_str,
            ascii_slug=ascii_slug,
            normalization_form=cfg.target_normalization,
            contains_mojibake=contains_mojibake,
            audit_actions=audit,
            decode_confidence=confidence,
            lossy_decode=lossy_decode,
            ascii_slug_is_lossy=bool(dropped),
            dropped_characters=dropped,
            warnings=warnings,
        )
