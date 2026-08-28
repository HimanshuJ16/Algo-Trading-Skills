"""Golden source designation for multi-vendor instrument reference data.

This module answers one question: *when several vendors describe the same instrument
and disagree, which value goes into the instrument master, and can you prove why?*

Three things about that question are easy to get wrong, and this module is built
around them.

**A value that no rule selected is not a golden value.** The prior revision of this
module fell back to "the first non-null value from any vendor" whenever a field had no
priority rule, or whenever none of the ruled vendors supplied a value. That iterated a
dict built from the caller's ``vendor_data`` list, so the winner was decided by argument
order -- two services loading the same vendors in a different order produced different
instrument masters from identical inputs. Worse, the result was stamped into
``golden_record`` with a ``golden_vendor`` attached and a ``RESOLVED`` status, making an
arbitrary pick indistinguishable in the report from a designated one. That is precisely
the failure this skill exists to prevent, so it is now off by default:
:class:`GoldenSourceConfig` refuses to guess unless ``allow_undesignated_fallback`` is
explicitly set, and when it is set the pick is deterministic *and* labelled
``UNGOVERNED_FALLBACK`` with ``is_governed=False``.

**"Non-null" is not the same as "populated".** Real vendor feeds encode absence as an
empty string, whitespace, ``"N/A"``, ``"NULL"`` or ``"-"`` at least as often as they
encode it as SQL NULL. A top-priority vendor returning ``""`` for an ISIN would beat a
lower-priority vendor's real ISIN under a plain ``is not None`` test -- the documented
"ignoring null values" pitfall reintroduced through the back door.

**Priority is not recency.** A priority rule ranks vendors, not snapshots. A rule that
puts Bloomberg first will select a three-month-old Bloomberg record over this morning's
exchange record unless the age of each record is checked, so ``max_staleness`` gates
records by age before the ranking is applied. The engine reads no clock: the caller
passes ``evaluation_time`` explicitly, so the same inputs always produce the same
report.

**Regulatory framing.** BCBS 239 (BCBS, January 2013) para 36(d) says a bank "should
*strive towards*" a single authoritative source for risk data per type of risk -- an
aspiration for G-SIBs and (at national discretion) D-SIBs, not a hard mandate and not a
rule about instrument reference data. What *is* hard law, for the entities it binds, is
MiFIR RTS 23 (Commission Delegated Regulation (EU) 2017/585): Article 3 designates the
authoritative source for two specific fields (ISO 6166 ISIN, ISO 17442 LEI listed in the
GLEIF database), and Article 6(2) obliges trading venues and systematic internalisers to
maintain arrangements that *identify* previously submitted reference data that was
incomplete or inaccurate and to correct it without undue delay. That obligation is why
every resolution here carries its rule, its rejected alternatives and the reason each
vendor was skipped: a report you cannot replay is a report you cannot correct.
See ``references/standards.md`` for the full citations and their limits.

Requires Python 3.7+ for ``dataclasses``. Report ordering does not rely on dict insertion
order: fields are emitted sorted, and the opt-in fallback picks the lowest-sorting vendor,
so a report is reproducible regardless of the order the caller assembled its inputs in.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, FrozenSet, List, Optional, Sequence, Set, Tuple

logger = logging.getLogger(__name__)

__all__ = [
    "GoldenSourceError",
    "GoldenSourceInputError",
    "GoldenSourceConfigError",
    "GoldenSourceConfig",
    "VendorFieldData",
    "FieldResolution",
    "GoldenSourceFinding",
    "GoldenRecordReport",
    "GoldenSourceDesignationEngine",
    "STATUS_RESOLVED",
    "STATUS_CONFLICTS_FOUND",
    "STATUS_MISSING_DATA",
    "STATUS_UNGOVERNED_FIELDS",
    "RULE_PRIORITY",
    "RULE_UNGOVERNED_FALLBACK",
    "RULE_NO_VALUE",
    "COMPARE_EXACT",
    "COMPARE_CASEFOLD_STRIP",
    # Finding codes and skip reasons are part of the contract: consumers branch on them.
    "FINDING_NO_PRIORITY_RULE",
    "FINDING_NO_RULED_VENDOR_SUPPLIED_VALUE",
    "FINDING_UNGOVERNED_FALLBACK",
    "FINDING_FIELD_HAS_NO_USABLE_VALUE",
    "FINDING_VENDOR_DISAGREEMENT",
    "FINDING_VENDOR_RECORD_STALE",
    "FINDING_VENDOR_AS_OF_MISSING",
    "FINDING_VENDOR_AS_OF_IN_FUTURE",
    "FINDING_UNKNOWN_VENDOR_IN_RULE",
    "SKIP_NULL",
    "SKIP_BLANK",
    "SKIP_SENTINEL",
    "SKIP_STALE",
    "SKIP_AS_OF_MISSING",
    "SKIP_AS_OF_IN_FUTURE",
]

# --- Record statuses -------------------------------------------------------
# Precedence when several apply, most to least severe. The single status string is a
# summary; `findings` is the actionable output and `is_fully_governed` is the boolean a
# caller should branch on.
STATUS_UNGOVERNED_FIELDS = "UNGOVERNED_FIELDS"   # a value was written that no rule chose
STATUS_MISSING_DATA = "MISSING_DATA"             # a field has no value in the record
STATUS_CONFLICTS_FOUND = "CONFLICTS_FOUND"       # vendors disagreed; rules resolved it
STATUS_RESOLVED = "RESOLVED"                     # every field governed, no disagreement

# --- How each field's value was chosen -------------------------------------
RULE_PRIORITY = "PRIORITY_RULE"                  # a designated golden source supplied it
RULE_UNGOVERNED_FALLBACK = "UNGOVERNED_FALLBACK"  # no rule applied; opt-in guess
RULE_NO_VALUE = "NO_VALUE"                       # nothing usable was available

# --- Finding codes ---------------------------------------------------------
FINDING_NO_PRIORITY_RULE = "NO_PRIORITY_RULE"
FINDING_NO_RULED_VENDOR_SUPPLIED_VALUE = "NO_RULED_VENDOR_SUPPLIED_VALUE"
FINDING_UNGOVERNED_FALLBACK = "UNGOVERNED_FALLBACK"
FINDING_FIELD_HAS_NO_USABLE_VALUE = "FIELD_HAS_NO_USABLE_VALUE"
FINDING_VENDOR_DISAGREEMENT = "VENDOR_DISAGREEMENT"
FINDING_VENDOR_RECORD_STALE = "VENDOR_RECORD_STALE"
FINDING_VENDOR_AS_OF_MISSING = "VENDOR_AS_OF_MISSING"
FINDING_VENDOR_AS_OF_IN_FUTURE = "VENDOR_AS_OF_IN_FUTURE"
FINDING_UNKNOWN_VENDOR_IN_RULE = "UNKNOWN_VENDOR_IN_RULE"

# --- Why a vendor's value for a field was not eligible ---------------------
SKIP_NULL = "NULL"
SKIP_BLANK = "BLANK"
SKIP_SENTINEL = "SENTINEL"
SKIP_STALE = "STALE"
SKIP_AS_OF_MISSING = "AS_OF_MISSING"
SKIP_AS_OF_IN_FUTURE = "AS_OF_IN_FUTURE"

# --- Conflict comparison modes ---------------------------------------------
# These change what counts as a *disagreement*. They never change the value written to
# the golden record, which is always the selected vendor's string exactly as supplied.
COMPARE_EXACT = "EXACT"
COMPARE_CASEFOLD_STRIP = "CASEFOLD_STRIP"
_COMPARISON_MODES = (COMPARE_EXACT, COMPARE_CASEFOLD_STRIP)


class GoldenSourceError(ValueError):
    """Base class for golden source designation failures.

    Subclasses ``ValueError`` so callers written against a plain ``except ValueError``
    keep working.
    """


class GoldenSourceInputError(GoldenSourceError):
    """Raised when vendor data cannot support a defensible golden record."""


class GoldenSourceConfigError(GoldenSourceError):
    """Raised when a :class:`GoldenSourceConfig` cannot govern a resolution."""


@dataclass
class GoldenSourceConfig:
    """Designation rules and the guards applied before they are used.

    Attributes:
        priority_rules: ``field_name`` -> vendor names, highest priority first. A field
            absent from this mapping is *ungoverned*: the engine has been given no basis
            to prefer one vendor's value over another's.
        allow_undesignated_fallback: When ``False`` (the default) a field the priority
            rules do not resolve is left empty and reported, rather than filled from an
            arbitrary vendor. Set ``True`` only when a partially-guessed record is more
            useful to you than an explicit hole -- the guess is then deterministic
            (lowest vendor name) and flagged ``is_governed=False``, but it is still a
            guess, and it will be indistinguishable from a designated value to anything
            downstream that reads only ``golden_record``.
        treat_blank_as_missing: Treat a value that is empty or whitespace-only as not
            supplied. On by default: whitespace is never a meaningful reference data
            value, and letting it through lets a top-priority vendor's empty string beat
            a lower-priority vendor's real one.
        missing_sentinels: Additional literal strings a vendor uses to mean "absent"
            (``"N/A"``, ``"NULL"``, ``"-"``). Compared case-insensitively after
            stripping. Empty by default, because a sentinel is only ever safe to declare
            per feed -- ``"N/A"`` is absence in an ISIN column and could be a real value
            in a free-text one.
        conflict_comparison: ``COMPARE_EXACT`` (default) or ``COMPARE_CASEFOLD_STRIP``.
            Casefolding suppresses ``"USD"`` vs ``"usd"`` false conflicts; it does not
            reconcile ``"0.01"`` vs ``"0.0100"``, which needs a typed comparison this
            string-oriented engine deliberately does not attempt.
        max_staleness: Maximum age of a vendor record, measured from its ``as_of``
            against the ``evaluation_time`` passed to
            :meth:`GoldenSourceDesignationEngine.resolve_golden_record`. ``None``
            disables age gating entirely -- and a report produced with it disabled says
            nothing about whether the winning record was current.
    """

    priority_rules: Dict[str, List[str]] = field(default_factory=dict)
    allow_undesignated_fallback: bool = False
    treat_blank_as_missing: bool = True
    missing_sentinels: FrozenSet[str] = frozenset()
    conflict_comparison: str = COMPARE_EXACT
    max_staleness: Optional[timedelta] = None

    def __post_init__(self) -> None:
        if not isinstance(self.priority_rules, dict):
            raise GoldenSourceConfigError("priority_rules must be a dict of field -> vendor list.")
        for field_name, vendors in self.priority_rules.items():
            if not isinstance(field_name, str) or not field_name.strip():
                raise GoldenSourceConfigError(
                    f"priority_rules keys must be non-blank field names, got {field_name!r}."
                )
            if isinstance(vendors, str) or not isinstance(vendors, Sequence):
                raise GoldenSourceConfigError(
                    f"priority_rules[{field_name!r}] must be a sequence of vendor names, "
                    f"got {type(vendors).__name__}."
                )
            seen: Set[str] = set()
            for vendor in vendors:
                if not isinstance(vendor, str) or not vendor.strip():
                    raise GoldenSourceConfigError(
                        f"priority_rules[{field_name!r}] contains a blank or non-string "
                        f"vendor name: {vendor!r}."
                    )
                if vendor in seen:
                    # A vendor listed twice means the rule was edited without being read.
                    # The duplicate is unreachable, so the rule does not say what its
                    # author thinks it says.
                    raise GoldenSourceConfigError(
                        f"priority_rules[{field_name!r}] lists vendor {vendor!r} more than once."
                    )
                seen.add(vendor)
        if self.conflict_comparison not in _COMPARISON_MODES:
            raise GoldenSourceConfigError(
                f"conflict_comparison must be one of {_COMPARISON_MODES}, "
                f"got {self.conflict_comparison!r}."
            )
        if not isinstance(self.missing_sentinels, (frozenset, set)):
            raise GoldenSourceConfigError("missing_sentinels must be a set or frozenset of strings.")
        for sentinel in self.missing_sentinels:
            if not isinstance(sentinel, str):
                raise GoldenSourceConfigError(
                    f"missing_sentinels entries must be strings, got {sentinel!r}."
                )
        self.missing_sentinels = frozenset(s.strip().casefold() for s in self.missing_sentinels)
        if self.max_staleness is not None:
            if not isinstance(self.max_staleness, timedelta):
                raise GoldenSourceConfigError("max_staleness must be a timedelta or None.")
            if self.max_staleness <= timedelta(0):
                raise GoldenSourceConfigError(
                    f"max_staleness must be positive, got {self.max_staleness!r}. "
                    "A non-positive window rejects every record including a fresh one."
                )


@dataclass
class VendorFieldData:
    """One vendor's snapshot of one instrument.

    Attributes:
        vendor_name: Vendor identity. Must be unique within a single call -- two
            snapshots from the same vendor are rejected rather than silently merged.
        fields: ``field_name`` -> value, where ``None`` means the vendor did not supply
            the field. Values must be strings or ``None``; a numeric ``0.01`` and the
            string ``"0.01"`` would otherwise read as a vendor disagreement.
        as_of: Timezone-aware instant the snapshot was current as of. Required when
            ``max_staleness`` is configured; ignored otherwise.
    """

    vendor_name: str
    fields: Dict[str, Optional[str]]
    as_of: Optional[datetime] = None


@dataclass
class GoldenSourceFinding:
    """One named, actionable observation about a record.

    ``field_name`` is ``None`` for findings about the record as a whole.
    """

    code: str
    field_name: Optional[str]
    detail: str


@dataclass
class FieldResolution:
    """How one field's golden value was arrived at, including what was rejected."""

    field_name: str
    golden_value: Optional[str]
    golden_vendor: Optional[str]
    all_vendor_values: Dict[str, Optional[str]]
    has_conflict: bool
    resolution_rule: str = RULE_NO_VALUE
    is_governed: bool = False
    # vendor -> why its value was not eligible (SKIP_* constant).
    skipped_vendors: Dict[str, str] = field(default_factory=dict)
    # Vendors that supplied a usable value which lost to the selected one.
    overridden_vendors: List[str] = field(default_factory=list)


@dataclass
class GoldenRecordReport:
    """The reconciled record plus the evidence for every choice made building it."""

    instrument_id: str
    golden_record: Dict[str, Optional[str]]
    resolutions: List[FieldResolution]
    total_fields: int
    conflicts_detected: int
    fields_without_data: int
    status: str
    audit_notes: str
    findings: List[GoldenSourceFinding] = field(default_factory=list)
    ungoverned_field_count: int = 0
    # True only when every field in the record was filled by a designated golden source.
    # This, not `status`, is the gate to promote a record into an instrument master.
    is_fully_governed: bool = False
    evaluation_time: Optional[datetime] = None
    staleness_gated: bool = False


class GoldenSourceDesignationEngine:
    """Resolves multi-vendor reference data into one record with an auditable basis.

    The engine performs no I/O, reads no clock and holds no state between calls, so a
    given ``(config, vendor_data, evaluation_time)`` always produces an identical report.
    """

    def __init__(self, config: Optional[GoldenSourceConfig] = None):
        self.config = config if config is not None else GoldenSourceConfig()
        if not isinstance(self.config, GoldenSourceConfig):
            raise GoldenSourceConfigError(
                f"config must be a GoldenSourceConfig, got {type(self.config).__name__}."
            )

    # -- eligibility ------------------------------------------------------

    def _skip_reason_for_value(self, value: Optional[str]) -> Optional[str]:
        """Return why ``value`` cannot be used, or ``None`` if it is usable."""
        if value is None:
            return SKIP_NULL
        stripped = value.strip()
        if self.config.treat_blank_as_missing and not stripped:
            return SKIP_BLANK
        if stripped.casefold() in self.config.missing_sentinels:
            return SKIP_SENTINEL
        return None

    def _comparison_key(self, value: str) -> str:
        """Key two vendor values are compared on when deciding whether they disagree."""
        if self.config.conflict_comparison == COMPARE_CASEFOLD_STRIP:
            return value.strip().casefold()
        return value

    # -- validation -------------------------------------------------------

    def _validate_inputs(
        self,
        instrument_id: str,
        vendor_data: Sequence[VendorFieldData],
        evaluation_time: Optional[datetime],
    ) -> None:
        if not isinstance(instrument_id, str) or not instrument_id.strip():
            raise GoldenSourceInputError(
                f"instrument_id must be a non-blank string, got {instrument_id!r}."
            )
        if isinstance(vendor_data, (str, bytes)) or not isinstance(vendor_data, Sequence):
            raise GoldenSourceInputError("vendor_data must be a sequence of VendorFieldData.")
        if not vendor_data:
            # Returning an empty record with a RESOLVED status would report "everything
            # reconciled" for an instrument nobody supplied any data for.
            raise GoldenSourceInputError(
                f"vendor_data is empty for {instrument_id!r}: there is nothing to reconcile. "
                "An absent upstream feed is an ingestion failure, not a resolved record."
            )

        seen_vendors: Set[str] = set()
        for entry in vendor_data:
            if not isinstance(entry, VendorFieldData):
                raise GoldenSourceInputError(
                    f"vendor_data entries must be VendorFieldData, got {type(entry).__name__}."
                )
            if not isinstance(entry.vendor_name, str) or not entry.vendor_name.strip():
                raise GoldenSourceInputError(
                    f"vendor_name must be a non-blank string, got {entry.vendor_name!r}."
                )
            if entry.vendor_name in seen_vendors:
                # Last-wins merging of two snapshots from one vendor destroys the earlier
                # one silently. Which snapshot is authoritative is the caller's decision.
                raise GoldenSourceInputError(
                    f"vendor {entry.vendor_name!r} appears more than once for "
                    f"{instrument_id!r}. Deduplicate upstream: the engine will not choose "
                    "between two snapshots from the same vendor."
                )
            seen_vendors.add(entry.vendor_name)

            if not isinstance(entry.fields, dict):
                raise GoldenSourceInputError(
                    f"vendor {entry.vendor_name!r}: fields must be a dict, "
                    f"got {type(entry.fields).__name__}."
                )
            for field_name, value in entry.fields.items():
                if not isinstance(field_name, str) or not field_name.strip():
                    raise GoldenSourceInputError(
                        f"vendor {entry.vendor_name!r}: field names must be non-blank "
                        f"strings, got {field_name!r}."
                    )
                if value is not None and not isinstance(value, str):
                    raise GoldenSourceInputError(
                        f"vendor {entry.vendor_name!r}, field {field_name!r}: values must "
                        f"be str or None, got {type(value).__name__} ({value!r}). Normalise "
                        "to strings upstream so that 0.01 and '0.01' cannot read as a "
                        "vendor disagreement."
                    )
            if entry.as_of is not None:
                if not isinstance(entry.as_of, datetime):
                    raise GoldenSourceInputError(
                        f"vendor {entry.vendor_name!r}: as_of must be a datetime, "
                        f"got {type(entry.as_of).__name__}."
                    )
                if entry.as_of.tzinfo is None or entry.as_of.utcoffset() is None:
                    raise GoldenSourceInputError(
                        f"vendor {entry.vendor_name!r}: as_of must be timezone-aware. A naive "
                        "timestamp from a vendor in another timezone silently misstates the "
                        "record's age by the offset between them."
                    )

        if self.config.max_staleness is not None:
            if evaluation_time is None:
                raise GoldenSourceInputError(
                    "max_staleness is configured, so evaluation_time is required. The engine "
                    "does not read the clock: pass the instant the record is being resolved "
                    "as of, so the same inputs always produce the same report."
                )
        if evaluation_time is not None:
            if not isinstance(evaluation_time, datetime):
                raise GoldenSourceInputError(
                    f"evaluation_time must be a datetime, got {type(evaluation_time).__name__}."
                )
            if evaluation_time.tzinfo is None or evaluation_time.utcoffset() is None:
                raise GoldenSourceInputError("evaluation_time must be timezone-aware.")

    # -- staleness --------------------------------------------------------

    def _gate_vendors_by_age(
        self,
        vendor_data: Sequence[VendorFieldData],
        evaluation_time: Optional[datetime],
    ) -> Tuple[Dict[str, str], List[GoldenSourceFinding]]:
        """Return ``{vendor: SKIP_*}`` for records too old (or undateable) to use."""
        rejected: Dict[str, str] = {}
        findings: List[GoldenSourceFinding] = []
        if self.config.max_staleness is None or evaluation_time is None:
            return rejected, findings

        for entry in vendor_data:
            if entry.as_of is None:
                # An undateable record cannot be shown to be current, and a priority rule
                # would otherwise rank it ahead of a record that can be.
                rejected[entry.vendor_name] = SKIP_AS_OF_MISSING
                findings.append(GoldenSourceFinding(
                    FINDING_VENDOR_AS_OF_MISSING, None,
                    f"Vendor {entry.vendor_name!r} supplied no as_of while max_staleness is "
                    "set; its whole snapshot was excluded."))
                continue
            age = evaluation_time - entry.as_of
            if age < timedelta(0):
                # A snapshot dated after the evaluation instant means the vendor's clock or
                # the timestamp field is wrong, so its age is unknown in an unknown direction.
                rejected[entry.vendor_name] = SKIP_AS_OF_IN_FUTURE
                findings.append(GoldenSourceFinding(
                    FINDING_VENDOR_AS_OF_IN_FUTURE, None,
                    f"Vendor {entry.vendor_name!r} as_of {entry.as_of.isoformat()} is after "
                    f"evaluation_time {evaluation_time.isoformat()}; snapshot excluded."))
            elif age > self.config.max_staleness:
                rejected[entry.vendor_name] = SKIP_STALE
                findings.append(GoldenSourceFinding(
                    FINDING_VENDOR_RECORD_STALE, None,
                    f"Vendor {entry.vendor_name!r} snapshot is {age} old, over the "
                    f"{self.config.max_staleness} limit; snapshot excluded."))
        return rejected, findings

    # -- resolution -------------------------------------------------------

    def resolve_golden_record(
        self,
        instrument_id: str,
        vendor_data: Sequence[VendorFieldData],
        evaluation_time: Optional[datetime] = None,
    ) -> GoldenRecordReport:
        """Resolve one instrument's fields into a golden record with full provenance.

        Args:
            instrument_id: Internal identifier of the instrument being reconciled.
            vendor_data: One :class:`VendorFieldData` per vendor. Vendor names must be
                unique.
            evaluation_time: Timezone-aware instant the record is resolved as of.
                Required when ``config.max_staleness`` is set, ignored otherwise.

        Returns:
            A :class:`GoldenRecordReport`. Check ``is_fully_governed`` before promoting
            ``golden_record`` into an instrument master; ``status`` is a summary and
            ``findings`` carries the actionable detail.

        Raises:
            GoldenSourceInputError: If the inputs cannot support a defensible record --
                no vendors, a duplicated vendor, a non-string value, a naive timestamp.
        """
        self._validate_inputs(instrument_id, vendor_data, evaluation_time)

        findings: List[GoldenSourceFinding] = []
        stale_vendors, stale_findings = self._gate_vendors_by_age(vendor_data, evaluation_time)
        findings.extend(stale_findings)

        all_fields: Set[str] = set()
        for entry in vendor_data:
            all_fields.update(entry.fields.keys())
        if not all_fields:
            # Same failure as an empty vendor_data list wearing a different shape: with no
            # fields there is nothing to reconcile, and a RESOLVED status over zero fields
            # reads downstream as "this instrument reconciled cleanly".
            raise GoldenSourceInputError(
                f"No vendor supplied any field for {instrument_id!r}: there is nothing to "
                "reconcile. An empty snapshot is an ingestion failure, not a resolved record."
            )

        supplied_vendors = {entry.vendor_name for entry in vendor_data}
        for field_name in sorted(all_fields):
            ranked = self.config.priority_rules.get(field_name) or []
            # Scoped to fields this instrument actually reported. Checking every rule in the
            # config would emit a finding for each field the instrument simply does not have,
            # burying the real ones -- a governance control nobody reads governs nothing.
            if ranked and all(v not in supplied_vendors for v in ranked):
                # Every vendor the rule names is absent, so the rule cannot govern this
                # field at all -- usually a vendor rename the rules were not updated for.
                findings.append(GoldenSourceFinding(
                    FINDING_UNKNOWN_VENDOR_IN_RULE, field_name,
                    f"No vendor named in the priority rule for {field_name!r} "
                    f"({', '.join(ranked)}) supplied data for this instrument."))

        vendor_map: Dict[str, Dict[str, Optional[str]]] = {}
        for field_name in all_fields:
            vendor_map[field_name] = {
                entry.vendor_name: entry.fields[field_name]
                for entry in vendor_data
                if field_name in entry.fields
            }

        resolutions: List[FieldResolution] = []
        golden_record: Dict[str, Optional[str]] = {}
        conflicts = 0
        missing = 0
        ungoverned = 0

        for field_name in sorted(all_fields):
            raw_values = vendor_map[field_name]
            skipped: Dict[str, str] = {}
            usable: Dict[str, str] = {}

            for vendor, value in raw_values.items():
                if vendor in stale_vendors:
                    skipped[vendor] = stale_vendors[vendor]
                    continue
                reason = self._skip_reason_for_value(value)
                if reason is not None:
                    skipped[vendor] = reason
                else:
                    # `value` is not None here: _skip_reason_for_value returns SKIP_NULL for None.
                    usable[vendor] = value  # type: ignore[assignment]

            distinct = {self._comparison_key(v) for v in usable.values()}
            has_conflict = len(distinct) > 1
            if has_conflict:
                conflicts += 1

            golden_value: Optional[str] = None
            golden_vendor: Optional[str] = None
            resolution_rule = RULE_NO_VALUE

            ranked = self.config.priority_rules.get(field_name) or []
            for vendor in ranked:
                if vendor in usable:
                    golden_value = usable[vendor]
                    golden_vendor = vendor
                    resolution_rule = RULE_PRIORITY
                    break

            if golden_vendor is None:
                if not ranked:
                    findings.append(GoldenSourceFinding(
                        FINDING_NO_PRIORITY_RULE, field_name,
                        f"No priority rule is defined for {field_name!r}, so no vendor is "
                        "designated as its golden source."))
                elif usable:
                    findings.append(GoldenSourceFinding(
                        FINDING_NO_RULED_VENDOR_SUPPLIED_VALUE, field_name,
                        f"None of the ranked vendors ({', '.join(ranked)}) supplied a usable "
                        f"value for {field_name!r}; supplied by undesignated vendors: "
                        f"{', '.join(sorted(usable))}."))

                if usable and self.config.allow_undesignated_fallback:
                    # Deterministic, but deterministic is not the same as correct: nothing
                    # here says this vendor is right, only that the same input always picks
                    # the same one.
                    golden_vendor = sorted(usable)[0]
                    golden_value = usable[golden_vendor]
                    resolution_rule = RULE_UNGOVERNED_FALLBACK
                    ungoverned += 1
                    findings.append(GoldenSourceFinding(
                        FINDING_UNGOVERNED_FALLBACK, field_name,
                        f"{field_name!r} was filled from {golden_vendor!r} by undesignated "
                        "fallback, not by a golden source rule."))

            if golden_value is None:
                missing += 1
                if not usable:
                    findings.append(GoldenSourceFinding(
                        FINDING_FIELD_HAS_NO_USABLE_VALUE, field_name,
                        f"No vendor supplied a usable value for {field_name!r} "
                        f"(skipped: {skipped or 'none'})."))

            if has_conflict:
                findings.append(GoldenSourceFinding(
                    FINDING_VENDOR_DISAGREEMENT, field_name,
                    f"Vendors disagree on {field_name!r}: "
                    + ", ".join(f"{v}={usable[v]!r}" for v in sorted(usable))
                    + (f"; selected {golden_vendor!r} by {resolution_rule}."
                       if golden_vendor else "; no value selected.")))

            overridden = sorted(v for v in usable if v != golden_vendor)

            golden_record[field_name] = golden_value
            resolutions.append(FieldResolution(
                field_name=field_name,
                golden_value=golden_value,
                golden_vendor=golden_vendor,
                all_vendor_values=dict(raw_values),
                has_conflict=has_conflict,
                resolution_rule=resolution_rule,
                is_governed=resolution_rule == RULE_PRIORITY,
                skipped_vendors=skipped,
                overridden_vendors=overridden,
            ))

        total = len(all_fields)
        if ungoverned > 0:
            status = STATUS_UNGOVERNED_FIELDS
        elif missing > 0:
            status = STATUS_MISSING_DATA
        elif conflicts > 0:
            status = STATUS_CONFLICTS_FOUND
        else:
            status = STATUS_RESOLVED

        is_fully_governed = ungoverned == 0 and missing == 0

        notes = (
            f"GOLDEN SOURCE [{status}] {instrument_id}: "
            f"Fields = {total}, Conflicts = {conflicts}, Missing = {missing}, "
            f"Ungoverned = {ungoverned}."
        )

        if status == STATUS_RESOLVED:
            logger.info(notes)
        else:
            logger.warning(notes)

        return GoldenRecordReport(
            instrument_id=instrument_id,
            golden_record=golden_record,
            resolutions=resolutions,
            total_fields=total,
            conflicts_detected=conflicts,
            fields_without_data=missing,
            status=status,
            audit_notes=notes,
            findings=findings,
            ungoverned_field_count=ungoverned,
            is_fully_governed=is_fully_governed,
            evaluation_time=evaluation_time,
            staleness_gated=self.config.max_staleness is not None,
        )
