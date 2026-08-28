"""
reference-data-symbol-mapping-across-vendors: a cross-vendor symbol cross-reference
table resolving a vendor-specific instrument identifier to one canonical internal
symbol, and back again.

The engine holds mappings, not identifiers. It does not validate check digits --
that is `isin-cusip-sedol-cross-reference-service` -- and it does not decide which
vendor is authoritative for a field -- that is
`reference-data-golden-source-designation`.

Three properties drive the whole design, and all three are routinely assumed away:

1.  **A vendor symbol is unique only inside a time window.** Exchange tickers are
    recycled. The NYSE ticker ``S`` was Sprint Corporation's until the NYSE removed
    Sprint's securities from listing on 13 April 2020 (SEC Form 25-NSE); SentinelOne
    listed under ``S`` on the NYSE on 30 June 2021. A table keyed on ``("NYSE", "S")``
    alone resolves a 2019 tick to SentinelOne. Lookups therefore take ``as_of``, and
    entries carry ``effective_from`` / ``effective_to``.
2.  **One canonical symbol can have several legitimate symbols at the same vendor.**
    Bloomberg quotes Apple as ``AAPL US Equity`` (the *composite* exchange code) and
    ``AAPL UW Equity`` (the *primary* exchange code, Nasdaq). Both are correct and they
    are not interchangeable. Exactly one entry per (canonical, vendor, window) may be
    ``is_primary``; the rest are reachable via ``reverse_lookup_all``.
3.  **A mapping conflict is a data defect, not a lookup problem.** Registering a second
    canonical symbol against a vendor key that already resolves silently repoints every
    join downstream. The default is to raise. ``allow_ambiguous=True`` downgrades that
    to a logged error, keeps the *first* registration as the resolved answer, and lists
    the conflict in the coverage report -- it never resolves the conflict silently.

Normalisation is applied to lookup *keys* only. The registered strings are returned
verbatim: a reverse lookup exists to be handed back to a vendor API or an order router,
and ``AAPL US EQUITY`` is not a Bloomberg ticker.

Verified 2026-08 against primary sources:

- **Ticker recycling** -- Sprint Corporation traded on the NYSE under ``S``; the merger
  with T-Mobile US became effective 1 April 2020 and the NYSE removed the class from
  listing and registration at the opening of business on 13 April 2020.
  https://www.sec.gov/Archives/edgar/data/101830/000087666120000282/ruleprovisionnotice.htm
  SentinelOne, Inc. listed on the NYSE under ``S`` at its IPO on 30 June 2021.
  https://www.sentinelone.com/press/sentinelone-announces-pricing-of-initial-public-offering/
- **Ticker rename leaves the structured identifiers alone** -- Meta Platforms' Class A
  common stock began trading under ``META`` before market open on 9 June 2022, replacing
  ``FB``; the listing and the CUSIP were unchanged.
  https://www.sec.gov/Archives/edgar/data/1326801/000132680122000070/may312022-exhibit991.htm
- **RIC structure** -- a RIC root plus ``.`` plus a one- or two-character exchange code,
  so ``AAPL.O`` is Apple on Nasdaq and the root alone does not identify a listing.
  https://en.wikipedia.org/wiki/Refinitiv_Identification_Code
- **Bloomberg composite vs primary exchange code** -- ``AAPL US Equity`` is the composite
  (``EQY_PRIM_SECURITY_COMP_EXCH``), ``AAPL UW Equity`` the Nasdaq primary
  (``EQY_PRIM_SECURITY_PRIM_EXCH``).
  https://assets.bbhub.io/professional/sites/10/Bloomberg-US-Domestic-Equity-Indices-Methodology.pdf

Limitations (read before relying on an output):

- **The table is only as current as what was registered.** Nothing here polls a vendor,
  a corporate-action feed or an exchange notice. A rename nobody entered is a mapping
  that stays wrong and never warns. Drive registration from
  `reference-data-change-notification-pipeline` /
  `corporate-action-event-calendar-integration`.
- **Effective dating is date-granular and half-open** (``[effective_from, effective_to)``).
  It models listing-lifecycle events, which happen between sessions. It cannot express a
  symbol that changed meaning intraday.
- **No identifier validation.** ``US0378331005`` and ``US0378331009`` are both accepted as
  vendor symbols; only the check-digit service can tell them apart.
- **RIC and Bloomberg ticker strings are licensed vendor symbology.** Holding them in an
  internal cross-reference table is a contractual question separate from this code; see
  `data-vendor-contractual-usage-restriction-tracking`.
"""
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Dict, List, Optional, Sequence, Set, Tuple

logger = logging.getLogger(__name__)

# --- Identifier type constants -------------------------------------------------

TICKER = "TICKER"
RIC = "RIC"
ISIN = "ISIN"
CUSIP = "CUSIP"
SEDOL = "SEDOL"
BBG = "BBG"
FIGI = "FIGI"

#: Types this module recognises. An unrecognised type is logged and still accepted --
#: venues and vendors carry symbologies beyond this list (OSI option symbols, MIC-scoped
#: local codes) and rejecting them would block legitimate mappings.
KNOWN_IDENTIFIER_TYPES: Tuple[str, ...] = (TICKER, RIC, ISIN, CUSIP, SEDOL, BBG, FIGI)

STATUS_FULL_COVERAGE = "FULL_COVERAGE"
STATUS_PARTIAL_COVERAGE = "PARTIAL_COVERAGE"

#: Vendor feeds arrive as CSV; runs of whitespace are formatting, not symbology.
#: Applied to lookup keys only -- registered strings are stored verbatim.
_WHITESPACE_RUN_RE = re.compile(r"\s+")


class AmbiguousMappingError(ValueError):
    """A registration would make an existing lookup key resolve two different ways.

    Raised by :meth:`SymbolMappingEngine.register_mapping` unless
    ``SymbolMappingConfig.allow_ambiguous`` is set.
    """


# --- Data model ----------------------------------------------------------------

@dataclass
class SymbolMappingConfig:
    """Engine behaviour.

    Args:
        case_sensitive: Match lookup keys case-sensitively. Default False, which suits
            equity symbology (tickers, RICs and Bloomberg tickers are conventionally
            upper-case). Set True only where a vendor's namespace genuinely
            distinguishes case -- some crypto venues do.
        allow_ambiguous: Downgrade a conflicting registration from
            :class:`AmbiguousMappingError` to a logged error. The *first* registration
            still wins every lookup and the conflict is reported by
            :meth:`SymbolMappingEngine.get_coverage_report`. Intended for bulk-loading
            known-dirty legacy data that must be audited, not repaired, in place.
    """
    case_sensitive: bool = False
    allow_ambiguous: bool = False


@dataclass
class VendorSymbolEntry:
    """One vendor's symbol for one canonical instrument, over one validity window.

    Args:
        canonical_symbol: The internal symbol every system joins on.
        vendor_name: The vendor or venue owning the symbology ('Bloomberg', 'NYSE').
        vendor_symbol: The symbol as that vendor writes it, verbatim
            ('AAPL US Equity'). Returned unchanged by :meth:`reverse_lookup`.
        identifier_type: One of :data:`KNOWN_IDENTIFIER_TYPES`, or any other non-blank
            label (logged, still accepted).
        effective_from: First date the mapping is valid. ``None`` means open-ended in
            the past.
        effective_to: First date the mapping is **no longer** valid -- the window is
            half-open, ``[effective_from, effective_to)``. ``None`` means currently
            effective. For Meta this is 2022-06-09 on the ``FB`` entry and the
            ``effective_from`` of the ``META`` entry: one date, no gap, no overlap.
        is_primary: This is the symbol :meth:`reverse_lookup` returns for
            (canonical, vendor). Exactly one entry per (canonical, vendor) window may
            set it. Secondary symbols -- Bloomberg's primary-exchange ticker alongside
            its composite, an alternate venue's RIC -- register with False and are
            reachable via :meth:`reverse_lookup_all`.
    """
    canonical_symbol: str
    vendor_name: str
    vendor_symbol: str
    identifier_type: str
    effective_from: Optional[date] = None
    effective_to: Optional[date] = None
    is_primary: bool = True


@dataclass
class SymbolMappingCoverageReport:
    """Coverage of the mapping table, evaluated at one point in time.

    ``total_mappings`` and the ambiguity lists count only entries effective at
    ``as_of`` -- a retired ticker is not a gap and not a conflict.
    """
    total_canonical_symbols: int
    total_mappings: int
    vendors_covered: List[str]
    unmapped_canonical: List[str]
    ambiguous_mappings: List[str]
    status: str                          # STATUS_FULL_COVERAGE | STATUS_PARTIAL_COVERAGE
    audit_notes: str
    #: (canonical, vendor) keys carrying more than one primary symbol at ``as_of``.
    ambiguous_reverse_mappings: List[str] = field(default_factory=list)
    #: 'CANONICAL@VENDOR' pairs the caller expected and the table cannot serve.
    missing_vendor_coverage: List[str] = field(default_factory=list)
    #: The date coverage was evaluated at. ``None`` means "currently effective".
    as_of: Optional[date] = None


class SymbolMappingEngine:
    """
    Cross-vendor symbol cross-reference: vendor symbol to canonical symbol and back,
    resolved at a point in time.

    Args:
        config: See :class:`SymbolMappingConfig`. Defaults to case-insensitive keys and
            strict conflict rejection.

    Raises:
        AmbiguousMappingError: From :meth:`register_mapping`, when a registration would
            make a key resolve two ways and ``allow_ambiguous`` is not set.
        ValueError: From :meth:`register_mapping`, on a blank field or an inverted
            effective window.
    """

    def __init__(self, config: Optional[SymbolMappingConfig] = None) -> None:
        self.config = config or SymbolMappingConfig()
        # (vendor_name, vendor_symbol) -> entries, in registration order.
        self._forward: Dict[Tuple[str, str], List[VendorSymbolEntry]] = {}
        # (canonical_symbol, vendor_name) -> entries, in registration order.
        self._reverse: Dict[Tuple[str, str], List[VendorSymbolEntry]] = {}
        self._entries: List[VendorSymbolEntry] = []
        self._conflicts: List[str] = []

    # -- normalisation ----------------------------------------------------------

    def _normalize(self, value: str) -> str:
        """Lookup-key form of a symbol: trimmed, internal whitespace collapsed, and
        upper-cased unless ``case_sensitive``. Never applied to a stored value."""
        collapsed = _WHITESPACE_RUN_RE.sub(" ", value.strip())
        return collapsed if self.config.case_sensitive else collapsed.upper()

    # -- effective dating -------------------------------------------------------

    @staticmethod
    def _as_of_date(value: Optional[date]) -> Optional[date]:
        """Accept a ``datetime`` where a ``date`` is expected.

        ``datetime`` subclasses ``date`` but comparing the two raises ``TypeError``, so
        an unguarded ``datetime`` turns a lookup into a crash mid-ingest. The window is
        date-granular by design, so the time component is dropped -- convert to the
        venue's local date first if the caller holds a UTC timestamp.
        """
        if isinstance(value, datetime):
            return value.date()
        return value

    @classmethod
    def _is_effective(cls, entry: VendorSymbolEntry, as_of: Optional[date]) -> bool:
        """Is ``entry`` valid at ``as_of``? ``as_of=None`` means "currently effective",
        which is the open-ended end of the window -- never a window already closed."""
        as_of = cls._as_of_date(as_of)
        if as_of is None:
            return entry.effective_to is None
        if entry.effective_from is not None and as_of < entry.effective_from:
            return False
        if entry.effective_to is not None and as_of >= entry.effective_to:
            return False
        return True

    @staticmethod
    def _windows_overlap(left: VendorSymbolEntry, right: VendorSymbolEntry) -> bool:
        """Do two half-open windows share at least one day? ``None`` bounds are
        unbounded, so two undated entries always overlap."""
        left_end, right_start = left.effective_to, right.effective_from
        if left_end is not None and right_start is not None and right_start >= left_end:
            return False
        right_end, left_start = right.effective_to, left.effective_from
        if right_end is not None and left_start is not None and left_start >= right_end:
            return False
        return True

    # -- registration -----------------------------------------------------------

    def _validate(self, entry: VendorSymbolEntry) -> None:
        """Reject entries that would poison the table. A blank symbol registers the
        key ``("", "")``, which then swallows every blank-string lookup."""
        for field_name in ("canonical_symbol", "vendor_name", "vendor_symbol",
                           "identifier_type"):
            value = getattr(entry, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(
                    f"VendorSymbolEntry.{field_name} must be a non-blank string, "
                    f"got {value!r}"
                )

        if (entry.effective_from is not None and entry.effective_to is not None
                and entry.effective_from >= entry.effective_to):
            raise ValueError(
                f"effective_from ({entry.effective_from}) must precede effective_to "
                f"({entry.effective_to}); the window is half-open and would be empty"
            )

        if entry.identifier_type.strip().upper() not in KNOWN_IDENTIFIER_TYPES:
            logger.info(
                "Unrecognised identifier_type %r for %s/%s -- accepted, but it is not "
                "one of %s",
                entry.identifier_type, entry.vendor_name, entry.vendor_symbol,
                ", ".join(KNOWN_IDENTIFIER_TYPES),
            )

    def _find_conflict(self, entry: VendorSymbolEntry) -> Optional[str]:
        """Describe the first conflict ``entry`` would introduce, or None.

        Two kinds, both of which make a key resolve two ways over one window:
        a vendor symbol pointing at a second canonical symbol, and a second *primary*
        symbol for one (canonical, vendor). Re-registering an identical mapping is
        idempotent, not a conflict -- re-running an ingest must not raise.
        """
        canonical = self._normalize(entry.canonical_symbol)
        vendor = self._normalize(entry.vendor_name)
        vendor_symbol = self._normalize(entry.vendor_symbol)

        for incumbent in self._forward.get((vendor, vendor_symbol), ()):
            if not self._windows_overlap(incumbent, entry):
                continue
            if self._normalize(incumbent.canonical_symbol) != canonical:
                return (
                    f"forward: {entry.vendor_name}/{entry.vendor_symbol!r} already "
                    f"resolves to {incumbent.canonical_symbol!r} over an overlapping "
                    f"window; cannot also resolve to {entry.canonical_symbol!r}"
                )

        if not entry.is_primary:
            return None

        for incumbent in self._reverse.get((canonical, vendor), ()):
            if not incumbent.is_primary or not self._windows_overlap(incumbent, entry):
                continue
            if self._normalize(incumbent.vendor_symbol) != vendor_symbol:
                return (
                    f"reverse: {entry.canonical_symbol!r} at {entry.vendor_name} "
                    f"already has primary symbol {incumbent.vendor_symbol!r} over an "
                    f"overlapping window; {entry.vendor_symbol!r} must register with "
                    f"is_primary=False"
                )

        return None

    def register_mapping(self, entry: VendorSymbolEntry) -> None:
        """Register one vendor symbol against one canonical symbol.

        Re-registering an identical mapping is a no-op. A conflicting one raises
        :class:`AmbiguousMappingError`, or -- under ``allow_ambiguous`` -- is logged,
        stored, and reported, with the first registration still winning every lookup.

        The entry is held by reference and its dates are normalised in place. Do not
        mutate a registered entry afterwards: the indexes are keyed on the values it
        carried at registration, and editing it silently desynchronises them. Close a
        window with :meth:`retire_mapping` instead.

        Raises:
            ValueError: Blank field, or ``effective_from >= effective_to``.
            AmbiguousMappingError: Conflict, and ``allow_ambiguous`` is not set.
        """
        entry.effective_from = self._as_of_date(entry.effective_from)
        entry.effective_to = self._as_of_date(entry.effective_to)
        self._validate(entry)

        canonical = self._normalize(entry.canonical_symbol)
        vendor = self._normalize(entry.vendor_name)
        vendor_symbol = self._normalize(entry.vendor_symbol)

        for existing in self._forward.get((vendor, vendor_symbol), ()):
            if (self._normalize(existing.canonical_symbol) == canonical
                    and existing.effective_from == entry.effective_from
                    and existing.effective_to == entry.effective_to
                    and existing.is_primary == entry.is_primary):
                logger.debug(
                    "Duplicate registration ignored: %s/%s -> %s",
                    entry.vendor_name, entry.vendor_symbol, entry.canonical_symbol,
                )
                return

        conflict = self._find_conflict(entry)
        if conflict is not None:
            message = f"SYMBOL MAPPING CONFLICT: {conflict}"
            if not self.config.allow_ambiguous:
                raise AmbiguousMappingError(message)
            logger.error("%s -- first registration retained", message)
            self._conflicts.append(conflict)

        self._forward.setdefault((vendor, vendor_symbol), []).append(entry)
        self._reverse.setdefault((canonical, vendor), []).append(entry)
        self._entries.append(entry)

    def retire_mapping(
        self, vendor_name: str, vendor_symbol: str, effective_to: date
    ) -> int:
        """Close the open-ended window on a vendor symbol from ``effective_to``.

        The operation a corporate action actually produces: the end date is learned
        after the mapping was registered open-ended. Retiring ``FB`` on 2022-06-09 and
        registering ``META`` from 2022-06-09 leaves the pair conflict-free, because the
        windows abut rather than overlap.

        Returns:
            How many open entries were closed. Zero means nothing matched -- treat that
            as a failed retirement, not a no-op.

        Raises:
            ValueError: ``effective_to`` precedes an ``effective_from`` it would close.
        """
        effective_to = self._as_of_date(effective_to)
        key = (self._normalize(vendor_name), self._normalize(vendor_symbol))
        closed = 0
        for entry in self._forward.get(key, ()):
            if entry.effective_to is not None:
                continue
            if entry.effective_from is not None and effective_to <= entry.effective_from:
                raise ValueError(
                    f"effective_to ({effective_to}) must follow effective_from "
                    f"({entry.effective_from}) for {vendor_name}/{vendor_symbol}"
                )
            entry.effective_to = effective_to
            closed += 1

        if closed == 0:
            logger.warning(
                "retire_mapping matched no open mapping for %s/%s",
                vendor_name, vendor_symbol,
            )
        else:
            logger.info(
                "Retired %d mapping(s) for %s/%s from %s",
                closed, vendor_name, vendor_symbol, effective_to,
            )
        return closed

    # -- lookup -----------------------------------------------------------------

    def _resolve(
        self,
        entries: Sequence[VendorSymbolEntry],
        as_of: Optional[date],
        resolved_value: str,
        description: str,
    ) -> List[VendorSymbolEntry]:
        """Entries effective at ``as_of``, first-registered first.

        Entries that agree on ``resolved_value`` are not ambiguous however many there
        are. Genuine disagreement can only survive registration under
        ``allow_ambiguous``, and is logged every time it is read rather than once when
        it was loaded.
        """
        effective = [e for e in entries if self._is_effective(e, as_of)]
        distinct = {self._normalize(getattr(e, resolved_value)) for e in effective}
        if len(distinct) > 1:
            logger.error(
                "Ambiguous resolution for %s at %s: %d conflicting mappings %s, "
                "returning the first registered",
                description, as_of or "now", len(distinct), sorted(distinct),
            )
        return effective

    def forward_lookup(
        self, vendor_name: str, vendor_symbol: str, as_of: Optional[date] = None
    ) -> Optional[str]:
        """Resolve a vendor symbol to its canonical symbol, as registered.

        Args:
            as_of: Point in time to resolve at. ``None`` means "currently effective" --
                a mapping whose window has closed is **not** returned, because a
                recycled ticker resolving to its previous issuer is worse than a miss.
                Pass the observation's own date when resolving historical data.
        """
        key = (self._normalize(vendor_name), self._normalize(vendor_symbol))
        effective = self._resolve(
            self._forward.get(key, ()), as_of,
            "canonical_symbol", f"{vendor_name}/{vendor_symbol}",
        )
        if not effective:
            logger.debug(
                "Forward lookup miss: %s/%s at %s",
                vendor_name, vendor_symbol, as_of or "now",
            )
            return None
        return effective[0].canonical_symbol

    def reverse_lookup(
        self, canonical_symbol: str, vendor_name: str, as_of: Optional[date] = None
    ) -> Optional[str]:
        """Resolve a canonical symbol to that vendor's **primary** symbol, verbatim.

        The returned string is what gets sent to a vendor API or an order router, so it
        is the registered spelling -- ``AAPL US Equity``, not a normalised form.
        Secondary symbols (Bloomberg's primary-exchange ticker alongside its composite)
        are returned only by :meth:`reverse_lookup_all`.
        """
        key = (self._normalize(canonical_symbol), self._normalize(vendor_name))
        # Filter to primary *before* resolving: a secondary symbol registered alongside
        # the primary is the documented model, not an ambiguity to warn about.
        primaries = [e for e in self._reverse.get(key, ()) if e.is_primary]
        effective = self._resolve(
            primaries, as_of, "vendor_symbol", f"{canonical_symbol}@{vendor_name}"
        )
        if not effective:
            logger.debug(
                "Reverse lookup miss: %s/%s at %s",
                canonical_symbol, vendor_name, as_of or "now",
            )
            return None
        return effective[0].vendor_symbol

    def reverse_lookup_all(
        self, canonical_symbol: str, vendor_name: str, as_of: Optional[date] = None
    ) -> Tuple[VendorSymbolEntry, ...]:
        """Every symbol this vendor carries for the canonical symbol at ``as_of``.

        Entries, not strings: the caller needs ``is_primary`` and ``identifier_type`` to
        tell a composite ticker from a primary-exchange one.
        """
        key = (self._normalize(canonical_symbol), self._normalize(vendor_name))
        return tuple(
            e for e in self._reverse.get(key, ())
            if self._is_effective(e, as_of)
        )

    def translate(
        self,
        source_vendor: str,
        vendor_symbol: str,
        target_vendor: str,
        as_of: Optional[date] = None,
    ) -> Optional[str]:
        """Vendor-to-vendor translation through the canonical symbol.

        Returns None if either leg misses -- the canonical symbol is deliberately not
        returned as a consolation, because a caller expecting a target-vendor symbol
        would route on it.
        """
        canonical = self.forward_lookup(source_vendor, vendor_symbol, as_of=as_of)
        if canonical is None:
            return None
        return self.reverse_lookup(canonical, target_vendor, as_of=as_of)

    # -- coverage ---------------------------------------------------------------

    def get_coverage_report(
        self,
        expected_canonical: Optional[Sequence[str]] = None,
        as_of: Optional[date] = None,
        expected_vendors: Optional[Sequence[str]] = None,
    ) -> SymbolMappingCoverageReport:
        """Coverage of the table as it stands at ``as_of``.

        Args:
            expected_canonical: The universe that must be mapped. Omitted, coverage is
                measured against whatever happens to be registered, which cannot report
                a gap -- pass the real universe to make ``unmapped_canonical`` mean
                anything.
            as_of: Point in time. ``None`` means "currently effective".
            expected_vendors: Vendors every expected canonical symbol must resolve to.
                Populates ``missing_vendor_coverage``.
        """
        as_of = self._as_of_date(as_of)
        effective_entries = [e for e in self._entries if self._is_effective(e, as_of)]
        canonical_present = {self._normalize(e.canonical_symbol) for e in effective_entries}
        vendors = sorted({self._normalize(e.vendor_name) for e in effective_entries})

        expected = (
            {self._normalize(s) for s in expected_canonical}
            if expected_canonical else canonical_present
        )
        unmapped = sorted(expected - canonical_present)

        ambiguous_forward = sorted(
            f"{vendor}/{symbol}"
            for (vendor, symbol), entries in self._forward.items()
            if len({
                self._normalize(e.canonical_symbol)
                for e in entries if self._is_effective(e, as_of)
            }) > 1
        )
        ambiguous_reverse = sorted(
            f"{canonical}@{vendor}"
            for (canonical, vendor), entries in self._reverse.items()
            if len({
                self._normalize(e.vendor_symbol)
                for e in entries if self._is_effective(e, as_of) and e.is_primary
            }) > 1
        )

        missing_vendor_coverage: List[str] = []
        if expected_vendors:
            for canonical in sorted(expected):
                for vendor in expected_vendors:
                    if self.reverse_lookup(canonical, vendor, as_of=as_of) is None:
                        missing_vendor_coverage.append(
                            f"{canonical}@{self._normalize(vendor)}"
                        )

        has_gaps = bool(
            unmapped or ambiguous_forward or ambiguous_reverse or missing_vendor_coverage
        )
        status = STATUS_PARTIAL_COVERAGE if has_gaps else STATUS_FULL_COVERAGE

        notes = (
            f"SYMBOL MAPPING [{status}] as of {as_of or 'now'}: "
            f"Canonical = {len(canonical_present)}, "
            f"Mappings = {len(effective_entries)}, Vendors = {len(vendors)}, "
            f"Unmapped = {len(unmapped)}, Ambiguous forward = {len(ambiguous_forward)}, "
            f"Ambiguous reverse = {len(ambiguous_reverse)}, "
            f"Missing vendor coverage = {len(missing_vendor_coverage)}."
        )
        if has_gaps:
            logger.warning(notes)
        else:
            logger.info(notes)

        return SymbolMappingCoverageReport(
            total_canonical_symbols=len(canonical_present),
            total_mappings=len(effective_entries),
            vendors_covered=vendors,
            unmapped_canonical=unmapped,
            ambiguous_mappings=ambiguous_forward,
            status=status,
            audit_notes=notes,
            ambiguous_reverse_mappings=ambiguous_reverse,
            missing_vendor_coverage=missing_vendor_coverage,
            as_of=as_of,
        )

    def registered_conflicts(self) -> Tuple[str, ...]:
        """Conflicts admitted under ``allow_ambiguous``. Empty under the default config,
        where a conflict raises instead."""
        return tuple(self._conflicts)

    def canonical_symbols(self, as_of: Optional[date] = None) -> Set[str]:
        """Normalised canonical symbols with at least one mapping effective at ``as_of``."""
        return {
            self._normalize(e.canonical_symbol)
            for e in self._entries if self._is_effective(e, as_of)
        }
