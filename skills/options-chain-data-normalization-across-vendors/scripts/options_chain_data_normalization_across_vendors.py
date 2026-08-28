"""
options-chain-data-normalization-across-vendors:
Maps heterogeneous options-chain payloads (Polygon, IBKR, Bloomberg, OPRA) onto one
canonical ``NormalizedOptionContract`` keyed by the OCC Options Symbology Initiative
(OSI) 21-character symbol.

Design rule: never fabricate a contract
---------------------------------------
Every field that cannot be read raises ``NormalizationError``; the offending record is
quarantined in ``OptionsNormalizationReport.rejected_records`` and the rest of the chain
still normalizes. Nothing in this module substitutes a plausible-looking default,
because in options data every tempting default silently produces a *different, real,
tradable contract* rather than an obvious error:

  * A defaulted underlying, expiry or strike yields a well-formed OSI symbol for a
    contract the vendor never sent. It joins cleanly against position and risk tables
    and there is no downstream check that can detect it.
  * A defaulted right (call/put) inverts the sign of every Greek on that line.
  * A midpoint synthesised from the last trade price when the quote is missing reports a
    tradable price for a series that has no market, which is exactly the series where a
    marketable order does the most damage.

Quote handling
--------------
``mid_price`` is ``(bid + ask) / 2`` and nothing else. It is ``None`` whenever a
two-sided uncrossed quote is unavailable, and consumers must handle ``None``:

  * **A zero bid is a real quote, not a missing one.** ``bid = 0.00`` against
    ``ask = 0.05`` is the normal state of every deep out-of-the-money series; the
    midpoint is 0.025, and discarding it in favour of a stale last trade is a
    quantitative error rather than a safety measure. The contract is flagged
    ``ZERO_BID`` so consumers can decline to quote it, but the number is preserved.
  * **A negative price is a vendor sentinel, not a price.** Interactive Brokers'
    market-data documentation states that "When IBApi::EWrapper::tickPrice and
    IBApi::EWrapper::tickSize are reported as -1, this indicates that there is no data
    currently available." Averaging that sentinel yields ``mid = -1.0`` on a contract
    that still satisfies every ``bid <= ask`` integrity check, so ``-1`` is mapped to
    "absent" before any arithmetic happens.
  * **A crossed quote yields no midpoint.** ``bid > ask`` is flagged
    ``INVALID_BID_ASK`` and ``mid_price`` stays ``None``; ``spread`` is reported
    **signed** so the size of the inversion is visible. Clamping the spread at zero
    hides the defect in the one field an integrity audit would look at.
  * ``last_price`` carries the vendor's last trade separately. Both numbers are exposed;
    they are never blended.

Symbology
---------
The OSI symbol is Root(6, left-justified, space-padded) + YYMMDD + C/P +
Strike x 1000 (8 digits, zero-padded) = 21 characters. Two consequences are enforced
rather than assumed:

  * The strike field holds 5 dollar digits and 3 mill digits, so only
    ``0 < strike <= 99999.999`` in whole mills is representable. A larger strike
    overflows to a 22-character string and a negative one emits a ``-`` inside the
    numeric field; both are rejected instead of emitted.
  * The root field is 6 bytes, so a root longer than 6 characters is invalid input.
    Truncating it produces a valid-looking symbol for a *different* contract.

Where a vendor supplies its own OSI string, it is decoded and cross-checked against the
symbol rebuilt from that same vendor's component fields; a disagreement is flagged
``OSI_MISMATCH`` rather than resolved by preferring one side. This round-trip is the
single highest-value check in a cross-vendor options normalizer.

Adjusted (non-standard) contracts
---------------------------------
After a corporate action the OCC appends a numeric suffix to the root (``AAPL1``) to
mark a non-standard deliverable. IBKR exposes this as ``tradingClass``, not ``symbol``,
and its own documentation notes that "It is not unusual to find many option contracts
with an almost identical description (i.e. underlying symbol, strike, last trading date,
multiplier, etc.)" and that adding the trading class disambiguates them. Building the
OSI symbol from ``symbol`` therefore names the *standard* series while quoting the
*adjusted* one. This module prefers an explicit root (``tradingClass`` / ``localSymbol``
/ ``osi_root``) over the underlying ticker, and flags ``NON_STANDARD_DELIVERABLE`` when
the root carries a suffix, the multiplier is not the standard 100, or the vendor reports
additional deliverables.

Scope
-----
Chain normalization only. This module does not fetch data, does not price options, does
not build an implied-volatility surface (see
``options-implied-volatility-surface-construction``), and holds no cross-snapshot state,
so it cannot detect staleness or sequence gaps.
"""
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
import logging
import math
import re
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

logger = logging.getLogger(__name__)

__all__ = [
    "NormalizationError",
    "OptionRight",
    "QualityFlag",
    "NormalizationConfig",
    "OsiComponents",
    "NormalizedOptionContract",
    "RejectedRecord",
    "OptionsNormalizationReport",
    "OptionsChainNormalizationEngine",
]

#: Widths of the four OSI fields, in bytes: root, YYMMDD, C/P, strike-in-mills.
_OSI_ROOT_WIDTH = 6
_OSI_DATE_WIDTH = 6
_OSI_RIGHT_WIDTH = 1
_OSI_STRIKE_WIDTH = 8
_OSI_LENGTH = _OSI_ROOT_WIDTH + _OSI_DATE_WIDTH + _OSI_RIGHT_WIDTH + _OSI_STRIKE_WIDTH

#: The strike field carries 5 dollar digits and 3 mill digits, so the largest
#: representable strike is 99,999.999 and the smallest positive one is 0.001.
_MAX_STRIKE = 99_999.999
_MIN_STRIKE_MILLS = 1

#: Tolerance, in mills, for accepting a float strike as a whole number of mills.
#: Double precision on a strike below 1e5 carries error far under 1e-6 mills, so this
#: admits representation noise while rejecting a genuinely sub-mill strike (150.0005),
#: which OSI cannot encode and which would otherwise be silently rounded onto a
#: different listed contract.
_STRIKE_MILL_TOLERANCE = 1e-6

#: A valid OSI root: alphanumeric, 1-6 bytes, starting with a letter. Trailing digits
#: cover OCC adjustment suffixes (``AAPL1``) and mini-option roots (``AAPL7``).
_ROOT_RE = re.compile(r"^[A-Z][A-Z0-9]{0,5}$")

#: Bloomberg equity-option ticker:
#: ``"<root> <exchange> MM/DD/YY <C|P><strike> <yellow key>"``,
#: e.g. ``'AAPL US 01/19/24 C150 Equity'``.
_BLOOMBERG_RE = re.compile(
    r"^(?P<root>[A-Za-z0-9./]+)\s+"
    r"(?P<exchange>[A-Za-z]{1,3})\s+"
    r"(?P<month>\d{2})/(?P<day>\d{2})/(?P<year>\d{2})\s+"
    r"(?P<right>[CPcp])(?P<strike>\d+(?:\.\d+)?)\s+"
    r"(?P<yellow_key>[A-Za-z]+)$"
)

#: OSI encodes a two-digit year with no century. Every OSI-bearing contract post-dates
#: the February 2010 symbology cutover, so ``YY`` resolves into the 2000s. This module
#: does not guess outside that window.
_OSI_CENTURY = 2000


class NormalizationError(ValueError):
    """
    Raised when an options-chain record cannot be normalized.

    Subclasses ``ValueError`` so existing ``except ValueError`` handlers keep working.
    Every failure path in this module raises this type specifically, so a chain ingester
    can catch one class at the vendor boundary and quarantine the record instead of
    guessing which of ``KeyError`` / ``TypeError`` / ``ValueError`` a given vendor parser
    might leak.
    """


class OptionRight(str, Enum):
    """Option right. There is no ``UNKNOWN`` member: an unreadable right is a rejection."""

    CALL = "CALL"
    PUT = "PUT"


class QualityFlag(str, Enum):
    """
    Per-contract data-quality observations.

    ``ZERO_BID`` and ``NON_STANDARD_DELIVERABLE`` describe legitimate market states, not
    errors, and deliberately do **not** degrade the chain-level ``quality_status``: most
    strikes in a real chain are bid-less, and a status that read ``DEGRADED`` on every
    snapshot would train its operators to ignore the field.
    """

    INVALID_BID_ASK = "INVALID_BID_ASK"           # bid > ask (crossed / inverted quote)
    MISSING_QUOTE = "MISSING_QUOTE"               # no usable two-sided quote
    ZERO_BID = "ZERO_BID"                         # bid == 0 against a valid ask
    OSI_MISMATCH = "OSI_MISMATCH"                 # vendor OSI != OSI rebuilt from fields
    NON_STANDARD_DELIVERABLE = "NON_STANDARD_DELIVERABLE"


#: Chain-level status, worst-first. ``normalize_chain`` reports the first status whose
#: condition holds; per-flag counts and the rejected records are always reported
#: alongside, so nothing is hidden by the collapse to a single string.
_STATUS_RECORDS_REJECTED = "RECORDS_REJECTED"
_STATUS_SYMBOLOGY_MISMATCH = "SYMBOLOGY_MISMATCH"
_STATUS_INVALID_QUOTE = "INVALID_QUOTE_DETECTED"
_STATUS_DEGRADED = "DEGRADED_QUOTES"
_STATUS_OK = "DATA_INTEGRITY_OK"


@dataclass
class NormalizationConfig:
    """
    Normalization policy. Every default is the conservative setting.

    Attributes:
        strict_osi_cross_check: Cross-check a vendor-supplied OSI string against the
            symbol rebuilt from that vendor's own component fields. Disable only when a
            vendor is known to ship components and symbol from different snapshots.
        standard_contract_multiplier: Shares per contract for a standard listed equity
            option. A contract reporting anything else is flagged
            ``NON_STANDARD_DELIVERABLE``; it is not rejected, because adjusted and mini
            contracts are tradable.
        reject_on_error: Quarantine an unparseable record and continue the chain (the
            default). Set ``False`` to re-raise, which suits a batch job that must fail
            loudly rather than deliver a partial chain.
    """

    strict_osi_cross_check: bool = True
    standard_contract_multiplier: float = 100.0
    reject_on_error: bool = True


@dataclass(frozen=True)
class OsiComponents:
    """The four fields decoded from an OSI symbol."""

    root: str                            # 'AAPL', unpadded
    expiration_date: str                 # ISO 'YYYY-MM-DD'
    right: OptionRight
    strike_price: float


@dataclass
class NormalizedOptionContract:
    """
    One canonical option contract.

    Attributes:
        standard_osi_symbol: 21-character OSI string, e.g. ``'AAPL  240119C00150000'``.
        underlying_ticker: Underlying instrument symbol, e.g. ``'AAPL'``. For an adjusted
            series this is *not* the OSI root -- see ``osi_root``.
        option_type: ``'CALL'`` or ``'PUT'``.
        expiration_date: ISO ``'YYYY-MM-DD'``.
        strike_price: Strike in the contract's quote currency, snapped to whole mills.
        bid: Best bid, or ``None`` when the vendor reported no bid. ``0.0`` is a real
            quote and is preserved as ``0.0``, never as ``None``.
        ask: Best offer, or ``None`` when the vendor reported no offer.
        mid_price: ``(bid + ask) / 2``, or ``None`` when a two-sided uncrossed quote is
            unavailable. Never synthesised from the last trade.
        spread: Signed ``ask - bid``, or ``None``. A negative value means a crossed
            market and is reported rather than clamped.
        osi_root: The 6-byte OSI root, unpadded. Equals ``underlying_ticker`` for a
            standard series and carries the OCC adjustment suffix otherwise
            (``'AAPL1'``).
        last_price: Vendor last trade price, carried separately from the quote.
        contract_multiplier: Shares (or units) per contract as reported by the vendor.
        quality_flags: Per-contract observations; see ``QualityFlag``.
        vendor_symbol: The vendor's own contract identifier, retained for audit.
    """

    standard_osi_symbol: str
    underlying_ticker: str
    option_type: str
    expiration_date: str
    strike_price: float
    bid: Optional[float]
    ask: Optional[float]
    mid_price: Optional[float]
    spread: Optional[float]
    osi_root: str = ""
    last_price: Optional[float] = None
    contract_multiplier: Optional[float] = None
    implied_volatility: Optional[float] = None
    open_interest: Optional[int] = None
    volume: Optional[int] = None
    quality_flags: List[str] = field(default_factory=list)
    vendor_symbol: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.osi_root:
            self.osi_root = self.underlying_ticker

    @property
    def is_quotable(self) -> bool:
        """True when the contract carries a usable, uncrossed two-sided midpoint."""
        return self.mid_price is not None


@dataclass(frozen=True)
class RejectedRecord:
    """A record that could not be normalized, retained for the dead-letter path."""

    index: int
    reason: str
    raw: Mapping[str, Any]


@dataclass
class OptionsNormalizationReport:
    """
    Result of normalizing one vendor chain snapshot.

    ``total_records_processed`` counts every record *offered*, so
    ``len(normalized_contracts) + len(rejected_records) == total_records_processed``
    always holds. A caller reading only the contract list and the total therefore cannot
    mistake a partially rejected chain for a complete one.
    """

    vendor_name: str
    total_records_processed: int
    normalized_contracts: List[NormalizedOptionContract]
    quality_status: str
    audit_notes: str
    rejected_records: List[RejectedRecord] = field(default_factory=list)
    flag_counts: Dict[str, int] = field(default_factory=dict)


def _coerce_price(value: Any, field_name: str) -> Optional[float]:
    """
    Coerces a vendor price field to a float, or ``None`` when it carries no price.

    ``None``, empty strings, non-numeric values, NaN/Inf and **negative** values all
    become ``None``. The negative case is the load-bearing one: IBKR publishes ``-1`` for
    "no data currently available", and a ``-1`` that survives into the midpoint produces
    a negative price on a contract that still satisfies ``bid <= ask``.
    """
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        logger.debug("Unparseable %s value %r; treated as absent.", field_name, value)
        return None
    if not math.isfinite(numeric):
        logger.debug("Non-finite %s value %r; treated as absent.", field_name, value)
        return None
    if numeric < 0:
        logger.debug(
            "Negative %s value %r treated as a no-data sentinel.", field_name, value
        )
        return None
    return numeric


def _first_present(raw: Mapping[str, Any], keys: Tuple[str, ...], field_name: str) -> Optional[float]:
    """
    Coerces the first of ``keys`` carrying a value to a price.

    A key present with a ``None`` or empty value does not stop the search -- vendors
    routinely emit both an alias and its canonical name with only one populated -- but a
    key carrying an actual no-data sentinel (``-1``) does: that is the vendor stating
    there is no quote, not an absent field.
    """
    for key in keys:
        if key not in raw:
            continue
        value = raw.get(key)
        if value is None or value == "":
            continue
        return _coerce_price(value, field_name)
    return None


def _require_str(raw: Mapping[str, Any], *keys: str) -> str:
    """Returns the first non-empty value among ``keys`` as a string, or raises."""
    for key in keys:
        value = raw.get(key)
        if value is None:
            continue
        text = value.strip() if isinstance(value, str) else str(value).strip()
        if text:
            return text
    raise NormalizationError(
        f"Record is missing a value for required field(s) {list(keys)!r}. The contract "
        "cannot be identified and will not be defaulted."
    )


def _require_number(raw: Mapping[str, Any], *keys: str) -> float:
    """Returns the first finite numeric value among ``keys``, or raises."""
    for key in keys:
        value = raw.get(key)
        if value is None or value == "":
            continue
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            raise NormalizationError(
                f"Field {key!r} has non-numeric value {value!r}."
            ) from None
        if not math.isfinite(numeric):
            raise NormalizationError(f"Field {key!r} has non-finite value {value!r}.")
        return numeric
    raise NormalizationError(
        f"Record is missing a value for required numeric field(s) {list(keys)!r}."
    )


def _parse_right(value: Any, *, source: str) -> OptionRight:
    """
    Maps a vendor right/type token onto ``OptionRight``, raising on anything unrecognized.

    The permissive ``CALL if token.startswith('C') else PUT`` idiom is a defect generator
    on both vendors this module targets: IBKR documents ``right`` as "Valid values are P,
    PUT, C, CALL", and Polygon documents ``contract_type`` as "'put', 'call', or in some
    rare cases, 'other'". Under that idiom the literal string ``'CALL'`` normalizes to a
    put on the IBKR path, and ``'other'`` becomes a put on the Polygon path.
    """
    if value is None:
        raise NormalizationError(f"{source}: option right is missing.")
    token = str(value).strip().upper()
    if token in ("C", "CALL"):
        return OptionRight.CALL
    if token in ("P", "PUT"):
        return OptionRight.PUT
    raise NormalizationError(
        f"{source}: unrecognized option right {value!r}; expected one of C/CALL/P/PUT. "
        "Polygon's documented 'other' contract_type reaches here and is rejected "
        "deliberately -- it is not a put."
    )


def _strike_to_mills(strike_price: Any) -> int:
    """
    Converts a strike to the whole number of mills the OSI strike field encodes.

    Raises when the strike falls outside the field's range or is not a whole number of
    mills.
    """
    try:
        strike = float(strike_price)
    except (TypeError, ValueError):
        raise NormalizationError(f"Strike {strike_price!r} is not numeric.") from None
    if not math.isfinite(strike):
        raise NormalizationError(f"Strike {strike_price!r} is not a finite number.")
    if strike <= 0:
        raise NormalizationError(
            f"Strike must be positive; got {strike}. OSI encodes the strike as unsigned "
            "digits, so a negative value would emit '-' inside the numeric field."
        )
    if strike > _MAX_STRIKE:
        raise NormalizationError(
            f"Strike {strike} exceeds the OSI maximum of {_MAX_STRIKE}. The strike field "
            f"holds {_OSI_STRIKE_WIDTH} digits (5 dollar + 3 mill); a larger value would "
            "overflow the symbol to 22 characters."
        )
    mills_float = strike * 1000.0
    mills = int(round(mills_float))
    if abs(mills_float - mills) > _STRIKE_MILL_TOLERANCE:
        raise NormalizationError(
            f"Strike {strike} is not a whole number of mills and cannot be encoded in "
            "OSI without silently renaming the contract to a different listed strike."
        )
    if mills < _MIN_STRIKE_MILLS:
        raise NormalizationError(f"Strike {strike} rounds to zero mills.")
    return mills


def _validate_root(root: Any) -> str:
    """Validates and returns an unpadded OSI root."""
    candidate = str(root).strip().upper()
    if not candidate:
        raise NormalizationError("OSI root is empty.")
    if len(candidate) > _OSI_ROOT_WIDTH:
        raise NormalizationError(
            f"OSI root {candidate!r} is {len(candidate)} characters; the field is "
            f"{_OSI_ROOT_WIDTH} bytes. Truncating it would emit a well-formed symbol for "
            "a different contract."
        )
    if not _ROOT_RE.match(candidate):
        raise NormalizationError(
            f"OSI root {candidate!r} is not alphanumeric starting with a letter."
        )
    return candidate


def _parse_iso_date(value: Any, *, source: str) -> str:
    """Validates an ISO ``YYYY-MM-DD`` date string and returns it normalized."""
    try:
        parsed = datetime.strptime(str(value).strip(), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        raise NormalizationError(
            f"{source}: expiration {value!r} is not an ISO YYYY-MM-DD date."
        ) from None
    return parsed.isoformat()


def _yymmdd_to_iso(yymmdd: str, *, source: str) -> str:
    """
    Expands a two-digit-year OSI/Bloomberg date into ISO ``YYYY-MM-DD``.

    OSI carries no century. Every OSI-bearing contract post-dates the February 2010
    symbology cutover, so the year resolves into the 2000s; this module does not guess
    outside that window.
    """
    try:
        year = _OSI_CENTURY + int(yymmdd[0:2])
        month = int(yymmdd[2:4])
        day = int(yymmdd[4:6])
        return date(year, month, day).isoformat()
    except (TypeError, ValueError):
        raise NormalizationError(
            f"{source}: {yymmdd!r} is not a valid YYMMDD expiration date."
        ) from None


class OptionsChainNormalizationEngine:
    """
    Normalizes heterogeneous vendor options-chain payloads into OSI-keyed contracts.

    Vendor dispatch is a registry, not a branch: an unregistered vendor raises rather
    than silently falling through to another vendor's parser. Falling through is how a
    Bloomberg chain ends up parsed by a parser that reads none of its field names and
    emerges as a chain of plausible nonsense.
    """

    def __init__(self, config: Optional[NormalizationConfig] = None) -> None:
        self.config = config or NormalizationConfig()
        self._parsers: Dict[str, Callable[[Mapping[str, Any]], NormalizedOptionContract]] = {
            "POLYGON": self.parse_polygon_contract,
            "IBKR": self.parse_ibkr_contract,
            "BLOOMBERG": self.parse_bloomberg_contract,
            "OPRA": self.parse_opra_contract,
        }

    # ------------------------------------------------------------------ symbology

    @staticmethod
    def build_osi_symbol(
        ticker: str, expiration_date_iso: str, option_type: str, strike_price: float
    ) -> str:
        """
        Constructs the standardized 21-character OCC OSI option symbol.

        Format: Root(6, left-justified, space-padded) + YYMMDD + C/P + Strike x 1000
        (8 digits, zero-padded). ``'AAPL', '2024-01-19', 'CALL', 150.0`` yields
        ``'AAPL  240119C00150000'``.

        Args:
            ticker: OSI root, unpadded, at most 6 characters. For an adjusted series pass
                the adjusted root (``'AAPL1'``), not the underlying ticker.
            expiration_date_iso: Expiration as ISO ``YYYY-MM-DD``.
            option_type: ``C``/``CALL``/``P``/``PUT``, case-insensitive.
            strike_price: Strike in quote currency; a whole number of mills in
                ``(0, 99999.999]``.

        Raises:
            NormalizationError: On any input the OSI fields cannot represent.
        """
        root = _validate_root(ticker)
        iso = _parse_iso_date(expiration_date_iso, source="build_osi_symbol")
        right = _parse_right(option_type, source="build_osi_symbol")
        mills = _strike_to_mills(strike_price)

        expiry = datetime.strptime(iso, "%Y-%m-%d").date()
        if not _OSI_CENTURY <= expiry.year <= _OSI_CENTURY + 99:
            # The date field carries no century, so a 1999 expiry would encode as '99'
            # and decode back as 2099. Refuse rather than round-trip to the wrong year.
            raise NormalizationError(
                f"Expiration {iso} falls outside {_OSI_CENTURY}-{_OSI_CENTURY + 99}. The "
                "OSI date field has no century, so this year cannot be encoded "
                "unambiguously."
            )
        yymmdd = expiry.strftime("%y%m%d")
        symbol = (
            f"{root.ljust(_OSI_ROOT_WIDTH)}"
            f"{yymmdd}"
            f"{'C' if right is OptionRight.CALL else 'P'}"
            f"{mills:0{_OSI_STRIKE_WIDTH}d}"
        )
        if len(symbol) != _OSI_LENGTH:
            # Unreachable given the validation above; checked anyway because a symbol of
            # the wrong length is the one output this module must never emit silently.
            raise NormalizationError(
                f"Constructed symbol {symbol!r} is {len(symbol)} characters, expected "
                f"{_OSI_LENGTH}."
            )
        return symbol

    @staticmethod
    def parse_osi_symbol(symbol: str) -> OsiComponents:
        """
        Decodes an OSI symbol into its four fields.

        Accepts the padded 21-character form (``'AAPL  240119C00150000'``) and the
        compact vendor form with or without Polygon's ``O:`` prefix
        (``'O:AAPL240119C00150000'``). The fixed 15-byte tail is parsed from the right,
        so a variable-length root stays unambiguous.

        Raises:
            NormalizationError: If the symbol does not decode.
        """
        if not isinstance(symbol, str) or not symbol.strip():
            raise NormalizationError(f"OSI symbol {symbol!r} is empty.")
        text = symbol.strip().upper()
        if text.startswith("O:"):
            text = text[2:]

        tail_width = _OSI_DATE_WIDTH + _OSI_RIGHT_WIDTH + _OSI_STRIKE_WIDTH
        if len(text) <= tail_width:
            raise NormalizationError(
                f"OSI symbol {symbol!r} is too short to contain a root plus the "
                f"{tail_width}-byte date/right/strike tail."
            )
        root_part, tail = text[:-tail_width], text[-tail_width:]
        yymmdd = tail[:_OSI_DATE_WIDTH]
        right_char = tail[_OSI_DATE_WIDTH]
        strike_digits = tail[_OSI_DATE_WIDTH + _OSI_RIGHT_WIDTH:]

        if not yymmdd.isdigit() or not strike_digits.isdigit():
            raise NormalizationError(
                f"OSI symbol {symbol!r} has non-numeric date or strike fields."
            )
        root = _validate_root(root_part)
        iso = _yymmdd_to_iso(yymmdd, source=f"OSI symbol {symbol!r}")
        right = _parse_right(right_char, source=f"OSI symbol {symbol!r}")
        strike = int(strike_digits) / 1000.0
        if strike <= 0:
            raise NormalizationError(f"OSI symbol {symbol!r} encodes a zero strike.")
        return OsiComponents(
            root=root, expiration_date=iso, right=right, strike_price=strike
        )

    # ------------------------------------------------------------------ quotes

    def _build_quote(
        self,
        raw: Mapping[str, Any],
        bid_keys: Tuple[str, ...],
        ask_keys: Tuple[str, ...],
    ) -> Tuple[Optional[float], Optional[float], Optional[float], Optional[float], List[str]]:
        """
        Normalizes a vendor's bid/ask into ``(bid, ask, mid, spread, flags)``.

        The same routine serves every vendor. Per-vendor midpoint rules are exactly what
        make two feeds of the same contract disagree by a tick, which is the failure this
        skill exists to prevent.
        """
        bid = _first_present(raw, bid_keys, "bid")
        ask = _first_present(raw, ask_keys, "ask")
        flags: List[str] = []

        # An ask of 0 is not an offer anybody can lift; a bid of 0 is a real "no bid".
        if ask is not None and ask == 0.0:
            ask = None

        if bid is None or ask is None:
            flags.append(QualityFlag.MISSING_QUOTE.value)
            return bid, ask, None, None, flags

        spread = round(ask - bid, 6)
        if bid > ask:
            flags.append(QualityFlag.INVALID_BID_ASK.value)
            return bid, ask, None, spread, flags

        if bid == 0.0:
            flags.append(QualityFlag.ZERO_BID.value)
        return bid, ask, round((bid + ask) / 2.0, 6), spread, flags

    def _deliverable_flags(
        self,
        multiplier: Optional[float],
        root: str,
        underlying: str,
        raw: Mapping[str, Any],
    ) -> List[str]:
        """Flags a series whose deliverable is not the standard one."""
        non_standard = False
        if multiplier is not None and multiplier != self.config.standard_contract_multiplier:
            non_standard = True
        if root != underlying:
            non_standard = True
        if root[-1].isdigit():
            # The OCC appends a numeric suffix to mark a non-standard deliverable
            # (AAPL1) or a mini contract (AAPL7). The suffix says the series is
            # non-standard; it does not say how it was adjusted.
            non_standard = True
        if raw.get("additional_underlyings"):
            non_standard = True
        return [QualityFlag.NON_STANDARD_DELIVERABLE.value] if non_standard else []

    def _cross_check_osi(self, built: str, vendor_symbol: Optional[str]) -> List[str]:
        """
        Compares a vendor-supplied OSI symbol against the one rebuilt from its own fields.

        A mismatch is flagged, never resolved: preferring either side would hide a real
        disagreement inside the vendor's own payload.
        """
        if not self.config.strict_osi_cross_check or not vendor_symbol:
            return []
        try:
            components = self.parse_osi_symbol(vendor_symbol)
            rebuilt = self.build_osi_symbol(
                components.root,
                components.expiration_date,
                components.right.value,
                components.strike_price,
            )
        except NormalizationError as exc:
            logger.warning("Vendor symbol %r is not decodable OSI: %s", vendor_symbol, exc)
            return [QualityFlag.OSI_MISMATCH.value]
        if rebuilt != built:
            logger.warning(
                "OSI mismatch: vendor symbol %r decodes to %r but component fields build %r.",
                vendor_symbol, rebuilt, built,
            )
            return [QualityFlag.OSI_MISMATCH.value]
        return []

    # ------------------------------------------------------------------ vendor parsers

    def parse_polygon_contract(self, raw_data: Mapping[str, Any]) -> NormalizedOptionContract:
        """
        Parses a Polygon.io options contract record.

        Reads the documented field names ``underlying_ticker``, ``expiration_date``
        (``YYYY-MM-DD``), ``contract_type`` (``put`` / ``call`` / ``other``),
        ``strike_price``, ``shares_per_contract`` and ``additional_underlyings``. When the
        record carries a ``ticker`` (``'O:AAPL240119C00150000'``), the OSI string is
        decoded and cross-checked against those component fields.

        A record supplying only the ``ticker`` and no component fields is still parsed:
        the components come from the symbol itself.
        """
        vendor_symbol = raw_data.get("ticker") or raw_data.get("symbol")
        from_symbol: Optional[OsiComponents] = None
        if isinstance(vendor_symbol, str) and vendor_symbol.strip():
            from_symbol = self.parse_osi_symbol(vendor_symbol)
        elif vendor_symbol is not None:
            raise NormalizationError(
                f"Polygon: contract identifier {vendor_symbol!r} is not a string."
            )

        if from_symbol is not None and "underlying_ticker" not in raw_data:
            underlying = from_symbol.root
        else:
            underlying = _require_str(raw_data, "underlying_ticker").upper()

        if from_symbol is not None and "expiration_date" not in raw_data:
            expiration = from_symbol.expiration_date
        else:
            expiration = _parse_iso_date(
                _require_str(raw_data, "expiration_date"), source="Polygon"
            )

        if from_symbol is not None and "contract_type" not in raw_data:
            right = from_symbol.right
        else:
            right = _parse_right(raw_data.get("contract_type"), source="Polygon")

        if from_symbol is not None and "strike_price" not in raw_data:
            strike = from_symbol.strike_price
        else:
            strike = _require_number(raw_data, "strike_price")

        explicit_root = raw_data.get("osi_root")
        root = _validate_root(
            explicit_root or (from_symbol.root if from_symbol is not None else underlying)
        )
        multiplier = _coerce_price(
            raw_data.get("shares_per_contract"), "shares_per_contract"
        )

        return self._assemble(
            raw=raw_data,
            root=root,
            underlying=underlying,
            expiration=expiration,
            right=right,
            strike=strike,
            multiplier=multiplier,
            vendor_symbol=vendor_symbol if isinstance(vendor_symbol, str) else None,
            bid_keys=("bid", "bid_price"),
            ask_keys=("ask", "ask_price"),
            last_keys=("close", "last_price", "last"),
            iv_keys=("implied_volatility",),
            oi_keys=("open_interest",),
            volume_keys=("volume", "day_volume"),
        )

    def parse_ibkr_contract(self, raw_data: Mapping[str, Any]) -> NormalizedOptionContract:
        """
        Parses an Interactive Brokers option contract record.

        Accepts ``lastTradeDateOrContractMonth`` (the documented field name) as well as
        the shorthand ``expiry``. IBKR documents that field as "Strings with format YYYYMM
        will be interpreted as the Contract Month whereas YYYYMMDD will be interpreted as
        Last Trading Day" -- a ``YYYYMM`` value names no single expiration date, so it is
        rejected rather than guessed at.

        The OSI root comes from ``tradingClass`` when present, because IBKR uses the
        trading class to distinguish an adjusted series (``AAPL1``) from the standard one
        while ``symbol`` stays the underlying. ``localSymbol``, documented as "For
        options, this will be the OCC symbol", drives the OSI cross-check.
        """
        underlying = _require_str(raw_data, "symbol").upper()
        expiration = self._parse_ibkr_expiry(raw_data)
        right = _parse_right(
            raw_data.get("right", raw_data.get("option_type")), source="IBKR"
        )
        strike = _require_number(raw_data, "strike")

        trading_class = raw_data.get("tradingClass") or raw_data.get("trading_class")
        root = _validate_root(trading_class or underlying)
        multiplier = _coerce_price(raw_data.get("multiplier"), "multiplier")
        local_symbol = raw_data.get("localSymbol") or raw_data.get("local_symbol")

        return self._assemble(
            raw=raw_data,
            root=root,
            underlying=underlying,
            expiration=expiration,
            right=right,
            strike=strike,
            multiplier=multiplier,
            vendor_symbol=local_symbol if isinstance(local_symbol, str) else None,
            bid_keys=("bid", "bidPrice"),
            ask_keys=("ask", "askPrice"),
            last_keys=("last", "lastPrice", "close"),
            iv_keys=("impliedVol", "implied_volatility"),
            oi_keys=("openInterest", "open_interest"),
            volume_keys=("volume",),
        )

    @staticmethod
    def _parse_ibkr_expiry(raw_data: Mapping[str, Any]) -> str:
        """Resolves IBKR's ``lastTradeDateOrContractMonth`` to an ISO date, or raises."""
        raw_expiry = _require_str(
            raw_data, "lastTradeDateOrContractMonth", "expiry", "expiration"
        )
        digits = raw_expiry.replace("-", "").strip()
        if len(digits) == 6 and digits.isdigit():
            raise NormalizationError(
                f"IBKR expiry {raw_expiry!r} is a YYYYMM contract month, which names no "
                "single expiration date. Resolve the contract's last trading day "
                "(YYYYMMDD) before normalizing."
            )
        if len(digits) != 8 or not digits.isdigit():
            raise NormalizationError(
                f"IBKR expiry {raw_expiry!r} is not a YYYYMMDD last trading day."
            )
        try:
            return datetime.strptime(digits, "%Y%m%d").date().isoformat()
        except ValueError:
            raise NormalizationError(
                f"IBKR expiry {raw_expiry!r} is not a valid calendar date."
            ) from None

    def parse_bloomberg_contract(self, raw_data: Mapping[str, Any]) -> NormalizedOptionContract:
        """
        Parses a Bloomberg equity-option ticker record.

        The ticker is ``"<root> <exchange> MM/DD/YY <C|P><strike> <yellow key>"``, e.g.
        ``'AAPL US 01/19/24 C150 Equity'``. Bloomberg's two-digit year expands into the
        2000s, matching the OSI convention.
        """
        ticker = _require_str(raw_data, "ticker", "bloomberg_ticker", "symbol")
        match = _BLOOMBERG_RE.match(ticker.strip())
        if not match:
            raise NormalizationError(
                f"Bloomberg ticker {ticker!r} does not match "
                "'<root> <exchange> MM/DD/YY <C|P><strike> <yellow key>'."
            )
        root = _validate_root(match.group("root"))
        expiration = _yymmdd_to_iso(
            f"{match.group('year')}{match.group('month')}{match.group('day')}",
            source=f"Bloomberg ticker {ticker!r}",
        )
        right = _parse_right(match.group("right"), source="Bloomberg")
        strike = float(match.group("strike"))
        multiplier = _coerce_price(raw_data.get("multiplier"), "multiplier")

        return self._assemble(
            raw=raw_data,
            root=root,
            underlying=root,
            expiration=expiration,
            right=right,
            strike=strike,
            multiplier=multiplier,
            vendor_symbol=ticker,
            bid_keys=("bid", "PX_BID"),
            ask_keys=("ask", "PX_ASK"),
            last_keys=("last", "PX_LAST"),
            iv_keys=("implied_volatility", "IVOL_MID"),
            oi_keys=("open_interest", "OPEN_INT"),
            volume_keys=("volume", "PX_VOLUME"),
            cross_check=False,   # a Bloomberg ticker is not an OSI string
        )

    def parse_opra_contract(self, raw_data: Mapping[str, Any]) -> NormalizedOptionContract:
        """
        Parses an OPRA-style record keyed directly by its OSI symbol.

        OPRA disseminates under OSI symbology, so the contract's identity comes from the
        symbol itself; component fields, when present, are cross-checked against it.
        """
        symbol = _require_str(raw_data, "osi_symbol", "symbol", "ticker")
        components = self.parse_osi_symbol(symbol)
        multiplier = _coerce_price(raw_data.get("multiplier"), "multiplier")
        underlying = str(raw_data.get("underlying_ticker") or components.root).upper()

        return self._assemble(
            raw=raw_data,
            root=components.root,
            underlying=underlying,
            expiration=components.expiration_date,
            right=components.right,
            strike=components.strike_price,
            multiplier=multiplier,
            vendor_symbol=symbol,
            bid_keys=("bid", "bid_price"),
            ask_keys=("ask", "ask_price"),
            last_keys=("last", "last_price"),
            iv_keys=("implied_volatility",),
            oi_keys=("open_interest",),
            volume_keys=("volume",),
        )

    # ------------------------------------------------------------------ assembly

    def _assemble(
        self,
        *,
        raw: Mapping[str, Any],
        root: str,
        underlying: str,
        expiration: str,
        right: OptionRight,
        strike: float,
        multiplier: Optional[float],
        vendor_symbol: Optional[str],
        bid_keys: Tuple[str, ...],
        ask_keys: Tuple[str, ...],
        last_keys: Tuple[str, ...],
        iv_keys: Tuple[str, ...],
        oi_keys: Tuple[str, ...],
        volume_keys: Tuple[str, ...],
        cross_check: bool = True,
    ) -> NormalizedOptionContract:
        """Builds the canonical contract from already-validated vendor components."""
        osi = self.build_osi_symbol(root, expiration, right.value, strike)
        bid, ask, mid, spread, flags = self._build_quote(raw, bid_keys, ask_keys)
        flags.extend(self._deliverable_flags(multiplier, root, underlying, raw))
        if cross_check:
            flags.extend(self._cross_check_osi(osi, vendor_symbol))

        return NormalizedOptionContract(
            standard_osi_symbol=osi,
            underlying_ticker=underlying,
            osi_root=root,
            option_type=right.value,
            expiration_date=expiration,
            strike_price=_strike_to_mills(strike) / 1000.0,
            bid=bid,
            ask=ask,
            mid_price=mid,
            spread=spread,
            last_price=_first_present(raw, last_keys, "last"),
            contract_multiplier=multiplier,
            implied_volatility=_first_present(raw, iv_keys, "implied_volatility"),
            open_interest=_optional_int(raw, oi_keys),
            volume=_optional_int(raw, volume_keys),
            quality_flags=flags,
            vendor_symbol=vendor_symbol,
        )

    # ------------------------------------------------------------------ chain

    def register_parser(
        self,
        vendor_name: str,
        parser: Callable[[Mapping[str, Any]], NormalizedOptionContract],
    ) -> None:
        """Registers a parser for an additional vendor. Replacing an existing one warns."""
        key = str(vendor_name).strip().upper()
        if not key:
            raise NormalizationError("Vendor name must be a non-empty string.")
        if key in self._parsers:
            logger.warning("Replacing existing parser for vendor '%s'.", key)
        self._parsers[key] = parser

    def normalize_chain(
        self, vendor_name: str, raw_records: List[Mapping[str, Any]]
    ) -> OptionsNormalizationReport:
        """
        Normalizes a list of raw vendor option records into OSI-keyed contracts.

        A record that cannot be parsed is quarantined in ``rejected_records`` and the rest
        of the chain still normalizes, so one malformed strike does not discard an
        otherwise good snapshot. Set ``NormalizationConfig.reject_on_error=False`` to
        re-raise instead.

        Raises:
            NormalizationError: If ``vendor_name`` has no registered parser. Dispatching
                an unknown vendor to a default parser is how a Bloomberg chain gets parsed
                against Polygon's field names and emerges as a chain of plausible nonsense.
        """
        vendor_upper = str(vendor_name).strip().upper()
        parser = self._parsers.get(vendor_upper)
        if parser is None:
            raise NormalizationError(
                f"No parser registered for vendor {vendor_name!r}. Registered vendors: "
                f"{sorted(self._parsers)}. Use register_parser() to add one."
            )

        # Materialize once: a generator would be consumed by the loop and then report
        # zero records offered, making a fully rejected chain look like an empty one.
        records = list(raw_records)
        contracts: List[NormalizedOptionContract] = []
        rejected: List[RejectedRecord] = []
        flag_counts: Dict[str, int] = {}

        for index, raw in enumerate(records):
            if not isinstance(raw, Mapping):
                message = f"Record {index} is {type(raw).__name__}, expected a mapping."
                if not self.config.reject_on_error:
                    raise NormalizationError(message)
                logger.warning("Rejected %s record %d: %s", vendor_upper, index, message)
                rejected.append(RejectedRecord(index=index, reason=message, raw={}))
                continue
            try:
                contract = parser(raw)
            except NormalizationError as exc:
                if not self.config.reject_on_error:
                    raise
                logger.warning("Rejected %s record %d: %s", vendor_upper, index, exc)
                rejected.append(
                    RejectedRecord(index=index, reason=str(exc), raw=dict(raw))
                )
                continue

            for flag in contract.quality_flags:
                flag_counts[flag] = flag_counts.get(flag, 0) + 1
            if QualityFlag.INVALID_BID_ASK.value in contract.quality_flags:
                logger.warning(
                    "Crossed quote on %s: bid=%s > ask=%s (signed spread=%s).",
                    contract.standard_osi_symbol, contract.bid, contract.ask,
                    contract.spread,
                )
            contracts.append(contract)

        status = self._chain_status(rejected, flag_counts)
        notes = (
            f"OPTIONS CHAIN [{status}]: vendor='{vendor_name}', "
            f"offered={len(records)}, normalized={len(contracts)}, "
            f"rejected={len(rejected)}, flags={dict(sorted(flag_counts.items()))}."
        )
        if status == _STATUS_OK:
            logger.info(notes)
        else:
            logger.warning(notes)

        return OptionsNormalizationReport(
            vendor_name=vendor_name,
            total_records_processed=len(records),
            normalized_contracts=contracts,
            quality_status=status,
            audit_notes=notes,
            rejected_records=rejected,
            flag_counts=flag_counts,
        )

    @staticmethod
    def _chain_status(
        rejected: List[RejectedRecord], flag_counts: Mapping[str, int]
    ) -> str:
        """
        Collapses the chain's findings into one status string, worst-first.

        ``ZERO_BID`` and ``NON_STANDARD_DELIVERABLE`` never degrade the status: both are
        ordinary properties of a healthy chain, and a status that read ``DEGRADED`` on
        every snapshot would convey nothing.
        """
        if rejected:
            return _STATUS_RECORDS_REJECTED
        if flag_counts.get(QualityFlag.OSI_MISMATCH.value):
            return _STATUS_SYMBOLOGY_MISMATCH
        if flag_counts.get(QualityFlag.INVALID_BID_ASK.value):
            return _STATUS_INVALID_QUOTE
        if flag_counts.get(QualityFlag.MISSING_QUOTE.value):
            return _STATUS_DEGRADED
        return _STATUS_OK


def _optional_int(raw: Mapping[str, Any], keys: Tuple[str, ...]) -> Optional[int]:
    """Returns the first present, non-negative, integral value among ``keys``, else ``None``."""
    for key in keys:
        if key not in raw:
            continue
        value = raw.get(key)
        if value is None or value == "":
            continue
        if isinstance(value, bool):
            return None
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            logger.debug("Unparseable %s value %r; treated as absent.", key, value)
            return None
        if not math.isfinite(numeric) or numeric < 0:
            return None
        return int(numeric)
    return None
