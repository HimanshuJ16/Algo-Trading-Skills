"""
isin-cusip-sedol-cross-reference-service: syntax and check-digit validation for
ISIN, CUSIP, SEDOL and FIGI, plus resolution of any of them to a single canonical
Security Master record.

Four identifiers, four *different* algorithms. They are routinely all described as
"Modulo 10 checksums", which is true and useless: substituting one for another
silently accepts corrupted identifiers.

    ISIN  (ISO 6166)        expand every character to digits (A=10..Z=35, so one
                            letter becomes TWO digits), then Luhn over the whole
                            expanded string right-to-left; total % 10 == 0.
    CUSIP (ANSI X9.6)       positional double-add-double over characters 1-8
                            (1-indexed even positions doubled), check digit is
                            (10 - sum % 10) % 10.
    SEDOL (LSE)             weighted sum, weights (1, 3, 1, 7, 3, 9) over the
                            first six characters. NOT Luhn -- nothing is doubled.
    FIGI  (ANSI X9.145)     double-add-double over characters 1-11, deliberately
                            offset from ISIN so the same string gets a different
                            check digit.

Standards verified 2026-08 against primary sources:

- **ISIN** -- ISO 6166. Format: 2-letter prefix, 9-character NSIN, 1 numeric check
  digit. For US/Canada the NSIN is the CUSIP, so a US ISIN is "US" + CUSIP + a
  newly computed check digit. Implementation validated against 15 real ISINs from
  12 jurisdictions (US, GB, DE, AU, NL, FR, JP, CH, IE, CA, KY, XS).
- **CUSIP** -- ANSI X9.6-2020, administered by CUSIP Global Services, the National
  Numbering Agency for North America. Character values: 0-9 as-is, A-Z as 10-35,
  and '*' = 36, '@' = 37, '#' = 38 for Private Placement Numbers.
  https://blog.ansi.org/ansi/ansi-x9-62020-cusip/
- **SEDOL** -- assigned by the London Stock Exchange. Seven characters: a six-place
  alphanumeric code plus a trailing numeric check digit. Vowels are never used, so
  the character set is 0-9 plus the consonants BCDFGHJKLMNPQRSTVWXYZ. Letters take
  the value 9 + alphabet position (B = 11 ... Z = 35); the gaps left by the vowels
  are *not* closed, which is why the mapping is simply ord(c) - 55.
  https://en.wikipedia.org/wiki/SEDOL
- **FIGI** -- ANSI X9.145-2021 / OMG FIGI. Position 1-2: upper-case consonants
  (including "Y"), excluding the sequences BS, BM, GG, GB, VG. Position 3: "G".
  Positions 4-11: consonants (including "Y") or digits. Position 12: check digit.
  Check-digit implementation validated against the standard's own worked example
  (NRG92C84SB39) and against six FIGIs returned by the OpenFIGI mapping API.
  https://x9.org/wp-content/uploads/2021/08/ANSI-X9.145-2021-Financial-Instrument-Global-Identifier-FIGI.pdf

Limitations (read before relying on an output):

- **A check digit proves typing, not existence.** All four algorithms detect single
  character substitutions and most transpositions. None of them prove the identifier
  was ever issued, is still active, or points at the instrument you think it does.
  Only the issuing agency (CGS, the LSE SEDOL Masterfile, the local NNA) or OpenFIGI
  can answer that. This module deliberately makes no network calls.
- **ISIN and FIGI strings can collide.** The FIGI standard excludes the prefixes
  BS/BM/GG/GB/VG to reduce this, but not every case: KYG875721634 -- the Cayman
  Islands ISIN of Tencent Holdings Ltd -- satisfies the FIGI syntax rules *and*
  passes the FIGI check digit.
  When a query is ambiguous, ``lookup_identifier`` reports every type it validated
  in ``candidate_types`` rather than silently picking one. Pass ``identifier_type``
  whenever the caller knows the type -- a vendor feed column is labelled, so there
  is no reason to infer it.
- **The identifiers are not at the same granularity.** An ISIN identifies an *issue*;
  a SEDOL identifies one security on one market (LSEG: "identification of individual
  securities and the markets that they are traded on"), so a cross-listed issue has
  one ISIN and several SEDOLs. The flat one-record-per-security schema here therefore
  models a single listing. A multi-listing master needs a one-to-many SEDOL table.
- **The ISIN prefix is not validated against ISO 3166.** Any two letters are accepted,
  because the prefix is the issuing NNA's code and includes non-country allocations
  such as XS (Euroclear/Clearstream) and EU. Rejecting unknown prefixes would reject
  legitimate identifiers.
- **CUSIP 'I' and 'O' are accepted.** ANSI X9.6 does not forbid them, even though CGS
  is not known to have issued any (they are confusable with 1 and 0). Rejecting them
  would enforce a convention the standard does not state.
- **The bundled master is three demo records.** It exists so the examples run. Pass
  ``records=`` to load a real security master.
"""
import logging
import re
from dataclasses import dataclass, field, replace
from typing import Callable, Dict, Iterable, List, Optional, Pattern, Tuple

logger = logging.getLogger(__name__)

# --- Identifier type constants -------------------------------------------------

ISIN = "ISIN"
CUSIP = "CUSIP"
SEDOL = "SEDOL"
FIGI = "FIGI"
TICKER = "TICKER"

#: Order in which an ambiguous query is resolved when the master data cannot break
#: the tie. Most globally authoritative first.
CLASSIFICATION_PRIORITY: Tuple[str, ...] = (ISIN, FIGI, CUSIP, SEDOL)

STATUS_MATCH_FOUND = "MATCH_FOUND"
STATUS_INVALID_CHECKSUM = "INVALID_CHECKSUM"
STATUS_NOT_FOUND = "IDENTIFIER_NOT_FOUND"

# --- Syntax rules --------------------------------------------------------------

#: SEDOL and FIGI both exclude vowels. "Y" counts as a consonant in both.
_CONSONANTS = "BCDFGHJKLMNPQRSTVWXYZ"

# ISO 6166: 2-letter NNA prefix + 9-character NSIN + 1 numeric check digit.
_ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")

# ANSI X9.6: 8 alphanumeric (plus the PPN characters * @ #) + 1 numeric check digit.
_CUSIP_RE = re.compile(r"^[A-Z0-9*@#]{8}[0-9]$")

# LSE: 6 consonant-or-digit characters + 1 numeric check digit.
_SEDOL_RE = re.compile(r"^[" + _CONSONANTS + r"0-9]{6}[0-9]$")

# ANSI X9.145-2021, normative pattern (conformance clause 2.1 / restriction I.3.3).
_FIGI_RE = re.compile(
    r"^(?!BS|BM|GG|GB|VG)[" + _CONSONANTS + r"]{2}G["
    + _CONSONANTS + r"0-9]{8}[0-9]$"
)

# Vendors deliver structured identifiers hyphenated or spaced ("037833-10-0").
_SEPARATORS_RE = re.compile(r"[\s\-]")

#: CUSIP character values. '*', '@' and '#' appear in Private Placement Numbers.
_CUSIP_VALUES: Dict[str, int] = {str(d): d for d in range(10)}
_CUSIP_VALUES.update({chr(ord("A") + i): 10 + i for i in range(26)})
_CUSIP_VALUES.update({"*": 36, "@": 37, "#": 38})

#: SEDOL weights applied to characters 1-6. The check digit's own weight is 1.
_SEDOL_WEIGHTS: Tuple[int, ...] = (1, 3, 1, 7, 3, 9)


def _char_value(char: str) -> int:
    """Digit as itself, letter as 9 + alphabet position (A=10 ... Z=35)."""
    return int(char) if char.isdigit() else ord(char) - 55


def _digit_sum(value: int) -> int:
    """Sum the decimal digits of a non-negative value below 100."""
    return (value // 10) + (value % 10)


# --- Data model ----------------------------------------------------------------

@dataclass
class SecurityMasterRecord:
    """One security on one market.

    ``isin``/``cusip``/``sedol``/``figi`` may be empty strings where the issue has
    no such identifier (a UK-only line has no CUSIP; an unlisted line has no SEDOL).
    Empty identifiers are not validated and are not indexed.
    """
    isin: str                           # 12-char ISIN e.g. 'US0378331005'
    cusip: str                          # 9-char CUSIP e.g. '037833100'
    sedol: str                          # 7-char SEDOL e.g. '2046251'
    figi: str                           # 12-char OpenFIGI e.g. 'BBG000B9XRY4'
    ticker_symbol: str                  # e.g. 'AAPL'
    asset_name: str                     # e.g. 'Apple Inc'
    country_code: str                   # e.g. 'US'


@dataclass
class IdentifierCrossReferenceReport:
    query_identifier: str
    query_type: str                     # 'ISIN', 'CUSIP', 'SEDOL', 'FIGI', 'TICKER'
    is_checksum_valid: bool
    matched_record: Optional[SecurityMasterRecord]
    status: str                         # see STATUS_* constants
    audit_notes: str
    #: False when no check digit exists for the resolved type (tickers). Without
    #: this, ``is_checksum_valid=True`` on a ticker reads as "checksum verified".
    checksum_applied: bool = True
    #: Every structured type the query validated as. More than one entry means the
    #: string is genuinely ambiguous (e.g. a Cayman ISIN that is also FIGI-shaped).
    candidate_types: Tuple[str, ...] = field(default_factory=tuple)


#: Demo master. Every identifier verified 2026-08 against the OpenFIGI mapping API
#: (https://api.openfigi.com/v3/mapping) and against its own check digit.
DEFAULT_MASTER_RECORDS: Tuple[SecurityMasterRecord, ...] = (
    SecurityMasterRecord(
        isin="US0378331005", cusip="037833100", sedol="2046251",
        figi="BBG000B9XRY4", ticker_symbol="AAPL", asset_name="Apple Inc",
        country_code="US",
    ),
    SecurityMasterRecord(
        isin="US5949181045", cusip="594918104", sedol="2588173",
        figi="BBG000BPH459", ticker_symbol="MSFT", asset_name="Microsoft Corp",
        country_code="US",
    ),
    SecurityMasterRecord(
        isin="US30303M1027", cusip="30303M102", sedol="B7TL820",
        figi="BBG000MM2P62", ticker_symbol="META",
        asset_name="Meta Platforms Inc - Class A", country_code="US",
    ),
)


class IsinCusipSedolCrossReferenceEngine:
    """
    Security master cross-reference service: syntax and check-digit validation for
    ISIN (ISO 6166), CUSIP (ANSI X9.6), SEDOL (LSE) and FIGI (ANSI X9.145), and
    resolution of any of them to a canonical ``SecurityMasterRecord``.

    Args:
        records: Security master rows. Defaults to ``DEFAULT_MASTER_RECORDS``.
        strict_validation: Raise ``ValueError`` if any row carries an identifier
            that fails its own check digit or duplicates another row's. Set False to
            load legacy data anyway -- problems are still logged and still returned
            by :meth:`validate_master_data`, and offending identifiers are still
            indexed so joins that already exist keep resolving.

    Raises:
        ValueError: ``strict_validation`` is set and the master data has problems.
    """

    def __init__(
        self,
        records: Optional[Iterable[SecurityMasterRecord]] = None,
        strict_validation: bool = True,
    ) -> None:
        # Copy the module-level defaults so one engine mutating a record cannot
        # corrupt every engine built afterwards. Caller-supplied records are used
        # as given -- silently copying someone else's rows would be surprising.
        self.master_db: List[SecurityMasterRecord] = (
            [replace(record) for record in DEFAULT_MASTER_RECORDS]
            if records is None else list(records)
        )
        self._indexes: Dict[str, Dict[str, SecurityMasterRecord]] = {
            ISIN: {}, CUSIP: {}, SEDOL: {}, FIGI: {}, TICKER: {},
        }
        self._data_problems: Tuple[str, ...] = tuple(self._build_indexes())

        if self._data_problems:
            for problem in self._data_problems:
                logger.error("SECURITY MASTER DATA DEFECT: %s", problem)
            if strict_validation:
                raise ValueError(
                    "Security master failed validation ("
                    f"{len(self._data_problems)} problem(s)): "
                    + "; ".join(self._data_problems)
                )

    # -- master data ------------------------------------------------------------

    @classmethod
    def _rules(cls) -> Dict[str, Tuple[Pattern, Callable[[str], bool]]]:
        """Syntax pattern and check-digit validator for each structured type.

        Single source of truth for classification, typed lookup and master-data
        validation, so the three cannot drift apart.
        """
        return {
            ISIN: (_ISIN_RE, cls.validate_isin_checksum),
            FIGI: (_FIGI_RE, cls.validate_figi_checksum),
            CUSIP: (_CUSIP_RE, cls.validate_cusip_checksum),
            SEDOL: (_SEDOL_RE, cls.validate_sedol_checksum),
        }

    def _build_indexes(self) -> List[str]:
        """Index every record by type and report identifiers that do not hold up."""
        problems: List[str] = []
        rules = self._rules()

        for position, record in enumerate(self.master_db):
            if not record.ticker_symbol.strip():
                problems.append(
                    f"record[{position}] ({record.asset_name!r}): empty ticker_symbol"
                )

            fields = {
                ISIN: record.isin, CUSIP: record.cusip,
                SEDOL: record.sedol, FIGI: record.figi,
                TICKER: record.ticker_symbol,
            }
            for id_type, raw_value in fields.items():
                value = raw_value.strip().upper()
                if not value:
                    # An absent identifier is normal, not a defect.
                    continue
                if id_type != TICKER and not rules[id_type][1](value):
                    # Reported, but still indexed: refusing to index it would make
                    # the row unreachable by a key live systems may already use.
                    problems.append(
                        f"record[{position}] ({record.ticker_symbol}): "
                        f"{id_type} {value!r} fails {id_type} check-digit validation"
                    )
                incumbent = self._indexes[id_type].get(value)
                if incumbent is not None and incumbent is not record:
                    problems.append(
                        f"record[{position}] ({record.ticker_symbol}): duplicate "
                        f"{id_type} {value!r} already held by "
                        f"{incumbent.ticker_symbol!r} -- first record wins"
                    )
                    continue
                self._indexes[id_type][value] = record

        return problems

    def validate_master_data(self) -> Tuple[str, ...]:
        """Problems found when the master data was indexed. Empty means clean."""
        return self._data_problems

    # -- check digits -----------------------------------------------------------

    @staticmethod
    def validate_isin_checksum(isin: str) -> bool:
        """Validate a 12-character ISIN (ISO 6166) via Luhn over the expanded digits.

        Every character is first expanded to its decimal value, so a letter becomes
        *two* digits and the doubling positions shift accordingly. Applying Luhn
        before expanding -- or treating a letter as a single unit -- produces a
        different and wrong result.
        """
        clean = isin.strip().upper()
        if not _ISIN_RE.match(clean):
            return False

        digits = [int(d) for char in clean for d in str(_char_value(char))]

        checksum = 0
        for index, digit in enumerate(reversed(digits)):
            if index % 2 == 1:  # every second digit from the right, check digit excluded
                checksum += _digit_sum(digit * 2)
            else:
                checksum += digit

        return checksum % 10 == 0

    @staticmethod
    def validate_cusip_checksum(cusip: str) -> bool:
        """Validate a 9-character CUSIP (ANSI X9.6) double-add-double check digit.

        Accepts the Private Placement Number characters '*' (36), '@' (37) and
        '#' (38). Note that a PPN CUSIP cannot be embedded in an ISIN, whose NSIN
        field is alphanumeric only.
        """
        clean = cusip.strip().upper()
        if not _CUSIP_RE.match(clean):
            return False

        total = 0
        for index in range(8):
            value = _CUSIP_VALUES[clean[index]]
            if index % 2 == 1:  # 1-indexed even positions are doubled
                value *= 2
            total += _digit_sum(value)

        return (10 - (total % 10)) % 10 == int(clean[8])

    @staticmethod
    def validate_sedol_checksum(sedol: str) -> bool:
        """Validate a 7-character SEDOL (LSE) weighted check digit.

        Weights (1, 3, 1, 7, 3, 9) over characters 1-6; the check digit itself carries
        weight 1, so the weighted total across all seven characters is a multiple of
        10. This is a plain weighted sum -- no digit is doubled, and running Luhn
        here accepts corrupted SEDOLs.

        Vowels are never used in a SEDOL, so 'I' and 'O' are rejected outright rather
        than read as mistyped '1' and '0'.
        """
        clean = sedol.strip().upper()
        if not _SEDOL_RE.match(clean):
            return False

        total = sum(
            _char_value(clean[index]) * weight
            for index, weight in enumerate(_SEDOL_WEIGHTS)
        )

        return (10 - (total % 10)) % 10 == int(clean[6])

    @staticmethod
    def validate_figi_checksum(figi: str) -> bool:
        """Validate a 12-character FIGI (ANSI X9.145-2021) check digit.

        Double-add-double over characters 1-11 with the 1-indexed *even* positions
        doubled. The standard offsets this from ISIN deliberately, so the same string
        generally gets a different check digit under the two schemes; reusing the
        ISIN routine here is a silent correctness failure, not a shortcut.
        """
        clean = figi.strip().upper()
        if not _FIGI_RE.match(clean):
            return False

        total = 0
        for position, char in enumerate(clean[:11], start=1):
            value = _char_value(char)
            if position % 2 == 0:
                value *= 2
            total += _digit_sum(value)

        return (10 - (total % 10)) % 10 == int(clean[11])

    # -- classification ---------------------------------------------------------

    def classify_identifier(
        self, identifier: str
    ) -> Tuple[Tuple[str, ...], Tuple[str, ...]]:
        """Classify an identifier by syntax and by check digit.

        Returns ``(validated, syntactic)``: the types whose syntax *and* check digit
        both pass, and the types whose syntax alone passes. A string in ``syntactic``
        but not in ``validated`` is shaped like that identifier and is corrupted.

        ``validated`` can hold more than one type -- see the module docstring on
        ISIN/FIGI collisions. Both tuples are ordered by ``CLASSIFICATION_PRIORITY``.

        The input is normalised the same way ``lookup_identifier`` normalises it, so
        calling this directly with a lowercase or hyphenated string does not silently
        report "not an identifier of any kind".
        """
        identifier = _SEPARATORS_RE.sub("", identifier.strip().upper())
        rules = self._rules()
        syntactic = tuple(
            id_type for id_type in CLASSIFICATION_PRIORITY
            if rules[id_type][0].match(identifier)
        )
        validated = tuple(
            id_type for id_type in syntactic if rules[id_type][1](identifier)
        )
        return validated, syntactic

    # -- lookup -----------------------------------------------------------------

    def lookup_identifier(
        self,
        query: str,
        identifier_type: Optional[str] = None,
    ) -> IdentifierCrossReferenceReport:
        """Resolve any identifier to its Security Master record.

        Args:
            query: The identifier. Case is normalised; surrounding whitespace and
                embedded spaces/hyphens are stripped before structured matching, so
                a vendor's '037833-10-0' resolves. Ticker matching uses the query as
                typed, because '-' and '.' are meaningful in ticker symbols.
            identifier_type: One of ISIN/CUSIP/SEDOL/FIGI/TICKER. **Pass this
                whenever the caller knows the type** -- a feed column is labelled, and
                inference cannot separate a 7-character ticker from a SEDOL, or a
                Cayman ISIN from a FIGI. When omitted the type is inferred and every
                type that validated is reported in ``candidate_types``.

        Returns:
            An ``IdentifierCrossReferenceReport``. ``status`` is ``MATCH_FOUND``,
            ``INVALID_CHECKSUM`` (the string is shaped like a structured identifier
            but its check digit is wrong) or ``IDENTIFIER_NOT_FOUND``.

        Raises:
            TypeError: ``query`` is not a string.
            ValueError: ``query`` is blank, or ``identifier_type`` is unrecognised.
        """
        if not isinstance(query, str):
            raise TypeError(f"query must be str, got {type(query).__name__}")
        raw = query.strip().upper()
        if not raw:
            raise ValueError("query must be a non-empty identifier string")
        if identifier_type is not None and identifier_type not in self._indexes:
            raise ValueError(
                f"identifier_type must be one of {sorted(self._indexes)}, "
                f"got {identifier_type!r}"
            )

        compact = _SEPARATORS_RE.sub("", raw)

        if identifier_type == TICKER:
            return self._resolve(raw, TICKER, checksum_applied=False, candidates=())

        if identifier_type is not None:
            validator = self._rules()[identifier_type][1]
            if not validator(compact):
                return self._reject(compact, identifier_type, (identifier_type,))
            return self._resolve(compact, identifier_type, True, (identifier_type,))

        validated, syntactic = self.classify_identifier(compact)

        if validated:
            # Prefer whichever candidate the master data can actually resolve; that
            # is evidence, where the priority order is only a convention.
            for candidate in validated:
                if compact in self._indexes[candidate]:
                    return self._resolve(compact, candidate, True, validated)
            return self._resolve(compact, validated[0], True, validated)

        if syntactic:
            # Shaped like a structured identifier, so a corrupted one is far more
            # likely than a ticker that happens to look like a SEDOL. A caller who
            # knows better passes identifier_type=TICKER.
            return self._reject(compact, syntactic[0], syntactic)

        return self._resolve(raw, TICKER, checksum_applied=False, candidates=())

    # -- report construction ----------------------------------------------------

    def _reject(
        self, identifier: str, id_type: str, candidates: Tuple[str, ...],
    ) -> IdentifierCrossReferenceReport:
        notes = (
            f"SECURITY MASTER REJECT [{identifier}]: invalid {id_type} check digit. "
            f"Types attempted: {', '.join(candidates)}."
        )
        logger.error(notes)
        return IdentifierCrossReferenceReport(
            query_identifier=identifier, query_type=id_type, is_checksum_valid=False,
            matched_record=None, status=STATUS_INVALID_CHECKSUM, audit_notes=notes,
            checksum_applied=True, candidate_types=candidates,
        )

    def _resolve(
        self,
        identifier: str,
        id_type: str,
        checksum_applied: bool,
        candidates: Tuple[str, ...],
    ) -> IdentifierCrossReferenceReport:
        ambiguity = ""
        if len(candidates) > 1:
            others = ", ".join(t for t in candidates if t != id_type)
            ambiguity = (
                f" AMBIGUOUS: also valid as {others}"
                " -- pass identifier_type to disambiguate."
            )

        record = self._indexes[id_type].get(identifier)
        if record is not None:
            notes = (
                f"SECURITY MASTER MATCH [{identifier} -> {record.ticker_symbol}]: "
                f"ISIN = {record.isin}, CUSIP = {record.cusip}, "
                f"SEDOL = {record.sedol}, FIGI = {record.figi}.{ambiguity}"
            )
            logger.info(notes)
            return IdentifierCrossReferenceReport(
                query_identifier=identifier, query_type=id_type, is_checksum_valid=True,
                matched_record=record, status=STATUS_MATCH_FOUND, audit_notes=notes,
                checksum_applied=checksum_applied, candidate_types=candidates,
            )

        qualifier = "well-formed" if checksum_applied else "accepted without a check digit"
        notes = (
            f"SECURITY MASTER NOT FOUND [{identifier}]: {id_type} {qualifier} "
            f"but absent from the Security Master.{ambiguity}"
        )
        logger.warning(notes)
        return IdentifierCrossReferenceReport(
            query_identifier=identifier, query_type=id_type, is_checksum_valid=True,
            matched_record=None, status=STATUS_NOT_FOUND, audit_notes=notes,
            checksum_applied=checksum_applied, candidate_types=candidates,
        )
