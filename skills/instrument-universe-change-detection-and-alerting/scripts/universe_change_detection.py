"""Instrument universe change detection and alerting.

Cross-matches two tradable-universe snapshots keyed by a *permanent* instrument
identifier (FIGI or ISIN) and classifies the delta into additions, deletions,
ticker renames, venue migrations and trading-status transitions, emitting a
recommended downstream action per change.

Identifier semantics this module relies on (verified against primary sources):

* FIGI - "A FIGI is never reused and remains with the instrument in perpetuity.
  A FIGI does not change as a result of any corporate action." and "When Ticker
  Symbol changes, FIGI stays under new Ticker Symbol"
  (OMG/Bloomberg, *Allocation Rules for the FIGI Standard* v29.9, Sec. 1.2.1 and
  Sec. 3.1). A delisted instrument keeps its FIGI and stays in the security
  master (Sec. 3.2.4), so delistings surface as a *status transition*, not
  necessarily as a set difference.
* ISIN - "The allocation of an ISIN represents the identification of a financial
  instrument rather than the market a financial instrument trades on" and
  "ISINs should never be re-used" (ANNA, *ISIN Uniform Guidelines 2025*,
  Sec. 1.1 and Sec. 6). An ISIN is therefore **not** venue-granular: one ISIN
  covers every listing of the same fungible security, so an ISIN-keyed
  multi-venue snapshot produces duplicate keys (see ``_index_snapshot``).

No third-party dependencies; standard library only.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date
from typing import Dict, FrozenSet, List, Optional, Sequence, Set, Tuple

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Identifier schemes
# --------------------------------------------------------------------------- #

ID_SCHEME_FIGI = "FIGI"
ID_SCHEME_ISIN = "ISIN"
ID_SCHEME_OPAQUE = "OPAQUE"

VALID_ID_SCHEMES: FrozenSet[str] = frozenset(
    {ID_SCHEME_FIGI, ID_SCHEME_ISIN, ID_SCHEME_OPAQUE}
)

# FIGI structure (Allocation Rules v29.9, Sec. 1.1.2): twelve characters; the
# first two are upper-case consonants (including "Y"); the third is "G";
# characters 4-11 are upper-case consonants or digits; the twelfth is a numeric
# check digit. The check-digit *algorithm* is not published in the allocation
# rules, so this validation is structural only.
_FIGI_CONSONANTS = "BCDFGHJKLMNPQRSTVWXYZ"
_FIGI_PATTERN = re.compile(
    "^[{c}]{{2}}G[{c}0-9]{{8}}[0-9]$".format(c=_FIGI_CONSONANTS)
)

# ISIN structure (ANNA ISIN Uniform Guidelines 2025, Sec. 7 / ISO 6166): twelve
# alphanumeric characters, first two alpha (the ISIN prefix), last a modulus-10
# "Double-Add-Double" check digit.
_ISIN_PATTERN = re.compile("^[A-Z]{2}[A-Z0-9]{9}[0-9]$")


def is_valid_figi(value: str) -> bool:
    """Return True if ``value`` is structurally a FIGI.

    Structural check only: it confirms the character classes mandated by the
    FIGI allocation rules. It does **not** verify the check digit (the
    algorithm is not published in the allocation rules) and does not prove the
    FIGI has actually been allocated - resolve against OpenFIGI for that.
    """
    return isinstance(value, str) and bool(_FIGI_PATTERN.match(value))


def is_valid_isin(value: str) -> bool:
    """Return True if ``value`` is a structurally valid ISIN with a correct check digit.

    Implements the ISO 6166 Annex C modulus-10 "Double-Add-Double" check digit
    referenced by ANNA ISIN Uniform Guidelines 2025, Sec. 7. Letters expand to
    two-digit values (A=10 ... Z=35) before the doubling pass.
    """
    if not isinstance(value, str) or not _ISIN_PATTERN.match(value):
        return False

    digits = "".join(str(int(ch, 36)) if ch.isalpha() else ch for ch in value[:-1])

    total = 0
    # Double every second digit counting from the right of the expanded body.
    for position, ch in enumerate(reversed(digits)):
        digit = int(ch)
        if position % 2 == 0:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit

    return (10 - (total % 10)) % 10 == int(value[-1])


# --------------------------------------------------------------------------- #
# Trading status vocabulary
# --------------------------------------------------------------------------- #

STATUS_ACTIVE = "ACTIVE"
STATUS_HALTED = "HALTED"
STATUS_SUSPENDED = "SUSPENDED"
STATUS_DELISTED = "DELISTED"

#: Statuses this engine maps to a specific action. Any other status string is
#: accepted but routed to ``ACTION_REVIEW_STATUS_CHANGE`` rather than guessed at.
KNOWN_STATUSES: FrozenSet[str] = frozenset(
    {STATUS_ACTIVE, STATUS_HALTED, STATUS_SUSPENDED, STATUS_DELISTED}
)

#: Statuses in which an instrument must not be traded.
NON_TRADABLE_STATUSES: FrozenSet[str] = frozenset(
    {STATUS_HALTED, STATUS_SUSPENDED, STATUS_DELISTED}
)

# --------------------------------------------------------------------------- #
# Change types and recommended actions
# --------------------------------------------------------------------------- #

CHANGE_ADDITION = "ADDITION"
CHANGE_DELETION = "DELETION"
CHANGE_TICKER_RENAME = "TICKER_RENAME"
CHANGE_STATUS_CHANGE = "STATUS_CHANGE"
CHANGE_EXCHANGE_MIGRATION = "EXCHANGE_MIGRATION"

ACTION_INITIATE_COVERAGE = "INITIATE_COVERAGE"
ACTION_LIQUIDATE = "LIQUIDATE_POSITION_AND_UNSUBSCRIBE"
ACTION_UPDATE_SYMBOL_MAPPER = "UPDATE_SYMBOL_MAPPER"
ACTION_FREEZE_TRADING = "FREEZE_TRADING_ALERTS"
ACTION_RESUME_ELIGIBILITY = "RESUME_TRADING_ELIGIBILITY"
ACTION_UPDATE_ROUTING = "UPDATE_ROUTING_TABLE"
ACTION_REVIEW_STATUS_CHANGE = "REVIEW_STATUS_CHANGE"
ACTION_HOLD_FOR_MANUAL_REVIEW = "HOLD_FOR_MANUAL_REVIEW"

REPORT_NO_CHANGES = "UNIVERSE_NO_CHANGES"
REPORT_CHANGES_DETECTED = "UNIVERSE_CHANGES_DETECTED"
REPORT_SNAPSHOT_SUSPECT = "UNIVERSE_SNAPSHOT_SUSPECT"

#: Emission order. Risk-reducing alerts are emitted before risk-increasing ones
#: so a consumer that processes the list sequentially and fails part-way through
#: has already applied the protective actions.
_CHANGE_TYPE_ORDER: Tuple[str, ...] = (
    CHANGE_DELETION,
    CHANGE_STATUS_CHANGE,
    CHANGE_EXCHANGE_MIGRATION,
    CHANGE_TICKER_RENAME,
    CHANGE_ADDITION,
)


def _normalize(value: str) -> str:
    """Upper-case and strip a comparison field. Vendors pad and re-case freely."""
    return value.strip().upper()


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #


@dataclass
class InstrumentRecord:
    """One instrument row of a universe snapshot.

    ``permanent_id`` must use the same identifier scheme in both snapshots and
    the same FIGI granularity level (share class / country composite / exchange
    level). Mixing granularity levels between snapshots makes every identifier
    differ and produces a full-universe delete-and-re-add.
    """

    permanent_id: str
    ticker_symbol: str
    asset_name: str
    exchange: str
    status: str = STATUS_ACTIVE

    def __post_init__(self) -> None:
        for name in (
            "permanent_id",
            "ticker_symbol",
            "asset_name",
            "exchange",
            "status",
        ):
            value = getattr(self, name)
            if not isinstance(value, str):
                raise TypeError(
                    f"InstrumentRecord.{name} must be a string, "
                    f"got {type(value).__name__}"
                )
        if not self.permanent_id.strip():
            raise ValueError(
                "InstrumentRecord.permanent_id must be a non-blank permanent "
                "identifier (FIGI / ISIN); universe diffs must never be keyed on "
                "a ticker symbol or an empty string"
            )
        if not self.status.strip():
            raise ValueError("InstrumentRecord.status must be a non-blank string")


@dataclass
class UniverseChangeAlert:
    """A single classified universe change and the action it implies."""

    change_type: str
    permanent_id: str
    previous_ticker: str
    new_ticker: str
    recommended_action: str
    audit_notes: str
    #: True when the snapshot failed the churn guard and the action below was
    #: downgraded to ``HOLD_FOR_MANUAL_REVIEW``.
    requires_manual_review: bool = False
    #: The action that *would* have been recommended had the snapshot passed the
    #: churn guard. Empty when nothing was suppressed.
    suppressed_action: str = ""


@dataclass
class UniverseChangeReport:
    """Aggregate result of one snapshot comparison."""

    total_previous_count: int
    total_current_count: int
    additions_count: int
    deletions_count: int
    renames_count: int
    status_changes_count: int
    alerts: List[UniverseChangeAlert]
    status: str
    audit_notes: str
    exchange_migrations_count: int = 0
    #: Deletions as a fraction of the previous universe. 0.0 when the previous
    #: snapshot is empty.
    deletion_ratio: float = 0.0
    #: True when the current snapshot looks truncated or corrupt and every
    #: action was downgraded to manual review.
    snapshot_is_suspect: bool = False


# --------------------------------------------------------------------------- #
# Engine
# --------------------------------------------------------------------------- #


class UniverseChangeDetectionEngine:
    """Detects tradable-universe changes between two snapshots.

    Args:
        id_scheme: ``"FIGI"``, ``"ISIN"`` or ``"OPAQUE"``. FIGI and ISIN enforce
            the identifier format on every record, which is what catches a
            snapshot accidentally keyed on ticker symbols - the precise failure
            this skill exists to prevent. ``"OPAQUE"`` (the default, for
            in-house permanent keys) validates only that identifiers are
            non-blank and unique.
        max_deletion_ratio: Fraction of the previous universe that may disappear
            in one comparison before the current snapshot is treated as
            truncated or corrupt rather than as a genuine mass delisting. This
            is a **library default, not an industry standard** - calibrate it
            against the observed daily churn of your own universe.
    """

    def __init__(
        self,
        id_scheme: str = ID_SCHEME_OPAQUE,
        max_deletion_ratio: float = 0.10,
    ) -> None:
        if id_scheme not in VALID_ID_SCHEMES:
            raise ValueError(
                f"id_scheme must be one of {sorted(VALID_ID_SCHEMES)}, "
                f"got {id_scheme!r}"
            )
        if isinstance(max_deletion_ratio, bool) or not isinstance(
            max_deletion_ratio, (int, float)
        ):
            raise TypeError("max_deletion_ratio must be a number between 0.0 and 1.0")
        if not 0.0 <= float(max_deletion_ratio) <= 1.0:
            raise ValueError(
                "max_deletion_ratio must be between 0.0 and 1.0, "
                f"got {max_deletion_ratio}"
            )

        self.id_scheme = id_scheme
        self.max_deletion_ratio = float(max_deletion_ratio)

    # -- snapshot indexing -------------------------------------------------- #

    def _validate_identifier(
        self, identifier: str, record: InstrumentRecord, label: str
    ) -> None:
        """Validate the *keying* form of the identifier under the active scheme."""
        if self.id_scheme == ID_SCHEME_FIGI and not is_valid_figi(identifier):
            raise ValueError(
                f"{label} snapshot: {record.permanent_id!r} (ticker "
                f"{record.ticker_symbol!r}) is not a structurally valid FIGI. "
                "A universe diff keyed on anything other than a permanent "
                "identifier misclassifies renames as delete+add."
            )
        if self.id_scheme == ID_SCHEME_ISIN and not is_valid_isin(identifier):
            raise ValueError(
                f"{label} snapshot: {record.permanent_id!r} (ticker "
                f"{record.ticker_symbol!r}) is not a valid ISIN "
                "(ISO 6166 structure / check digit)."
            )

    def _index_snapshot(
        self, snapshot: Sequence[InstrumentRecord], label: str
    ) -> Dict[str, InstrumentRecord]:
        """Key a snapshot by permanent identifier, rejecting duplicates.

        A duplicate key is never silently collapsed: it means either a corrupt
        extract or - the common case - an ISIN-keyed universe spanning several
        venues, since one ISIN covers every listing of a fungible security
        (ANNA ISIN Uniform Guidelines 2025, Sec. 1.1). Silently keeping the last
        row would hide a listing and skew every count in the report.
        """
        if snapshot is None:
            raise TypeError(f"{label} snapshot must be a sequence of InstrumentRecord")

        indexed: Dict[str, InstrumentRecord] = {}
        duplicates: Set[str] = set()

        for record in snapshot:
            if not isinstance(record, InstrumentRecord):
                raise TypeError(
                    f"{label} snapshot contains {type(record).__name__}, "
                    "expected InstrumentRecord"
                )
            key = record.permanent_id.strip()
            self._validate_identifier(key, record, label)
            if key in indexed:
                duplicates.add(key)
            indexed[key] = record

        if duplicates:
            raise ValueError(
                f"{label} snapshot contains duplicate permanent identifiers: "
                f"{sorted(duplicates)}. Key each row uniquely - for a multi-venue "
                "universe use exchange-level FIGIs or a composite (ISIN, MIC) key."
            )

        return indexed

    # -- classification ----------------------------------------------------- #

    @staticmethod
    def _status_transition_action(previous_status: str, new_status: str) -> str:
        """Map a normalized status transition to an action.

        Mapping every transition to ``FREEZE_TRADING_ALERTS`` is wrong in both
        directions: a delisting needs the position closed out, and a resumption
        must not freeze trading.
        """
        if new_status == STATUS_DELISTED:
            return ACTION_LIQUIDATE
        if new_status in (STATUS_HALTED, STATUS_SUSPENDED):
            return ACTION_FREEZE_TRADING
        if new_status == STATUS_ACTIVE and previous_status in NON_TRADABLE_STATUSES:
            return ACTION_RESUME_ELIGIBILITY
        return ACTION_REVIEW_STATUS_CHANGE

    def detect_universe_changes(
        self,
        previous_snapshot: Sequence[InstrumentRecord],
        current_snapshot: Sequence[InstrumentRecord],
        previous_as_of: Optional[date] = None,
        current_as_of: Optional[date] = None,
    ) -> UniverseChangeReport:
        """Cross-match two snapshots by permanent identifier.

        Args:
            previous_snapshot: The earlier universe (U_{t-1}).
            current_snapshot: The later universe (U_t).
            previous_as_of: Optional as-of date of ``previous_snapshot``.
            current_as_of: Optional as-of date of ``current_snapshot``. When both
                dates are supplied they are checked for ordering: swapping the
                arguments inverts additions and deletions and would turn every
                new listing into a liquidation instruction.

        Raises:
            TypeError: A snapshot is not a sequence of ``InstrumentRecord``, or a
                supplied as-of value is not a ``date``.
            ValueError: An identifier fails the configured scheme, a snapshot
                contains duplicate identifiers, or the as-of dates are not
                strictly increasing.
        """
        for name, value in (
            ("previous_as_of", previous_as_of),
            ("current_as_of", current_as_of),
        ):
            if value is not None and not isinstance(value, date):
                raise TypeError(f"{name} must be a datetime.date or None")
        if (
            previous_as_of is not None
            and current_as_of is not None
            and current_as_of <= previous_as_of
        ):
            raise ValueError(
                f"current_as_of ({current_as_of}) must be strictly later than "
                f"previous_as_of ({previous_as_of}); comparing snapshots in "
                "reverse order inverts additions and deletions"
            )

        prev_map = self._index_snapshot(previous_snapshot, "previous")
        curr_map = self._index_snapshot(current_snapshot, "current")

        prev_ids: Set[str] = set(prev_map)
        curr_ids: Set[str] = set(curr_map)

        added_ids = curr_ids - prev_ids
        deleted_ids = prev_ids - curr_ids
        common_ids = prev_ids & curr_ids

        # --- churn guard: is the current snapshot believable at all? -------- #
        deletion_ratio = len(deleted_ids) / len(prev_ids) if prev_ids else 0.0
        empty_current = bool(prev_ids) and not curr_ids
        snapshot_is_suspect = empty_current or (
            bool(prev_ids) and deletion_ratio > self.max_deletion_ratio
        )

        by_type: Dict[str, List[UniverseChangeAlert]] = {
            change_type: [] for change_type in _CHANGE_TYPE_ORDER
        }
        renames_count = 0
        status_changes_count = 0
        exchange_migrations_count = 0

        # 1. Additions.
        for pid in sorted(added_ids):
            rec = curr_map[pid]
            if _normalize(rec.status) == STATUS_ACTIVE:
                action = ACTION_INITIATE_COVERAGE
                notes = (
                    f"UNIVERSE ADDITION [{rec.ticker_symbol} / {pid}]: newly present in "
                    "the universe with ACTIVE status. Confirm whether this is a new "
                    "listing, an index addition or a spin-off entitlement before sizing."
                )
            else:
                action = ACTION_REVIEW_STATUS_CHANGE
                notes = (
                    f"UNIVERSE ADDITION [{rec.ticker_symbol} / {pid}]: newly present but "
                    f"in non-tradable status '{rec.status}'. Do not initiate coverage."
                )
            by_type[CHANGE_ADDITION].append(
                UniverseChangeAlert(
                    change_type=CHANGE_ADDITION,
                    permanent_id=pid,
                    previous_ticker="",
                    new_ticker=rec.ticker_symbol,
                    recommended_action=action,
                    audit_notes=notes,
                )
            )

        # 2. Deletions - absence from the current file, which is not itself proof
        #    of a delisting (a delisted instrument keeps its FIGI and normally
        #    stays in the security master; FIGI Allocation Rules Sec. 3.2.4).
        for pid in sorted(deleted_ids):
            rec = prev_map[pid]
            notes = (
                f"UNIVERSE DELETION [{rec.ticker_symbol} / {pid}]: present in the "
                f"previous snapshot (status '{rec.status}') and absent from the current "
                "one. Absence is not proof of a delisting - confirm the reason "
                "(delisting, merger completion, index removal, truncated vendor file) "
                "before trading on this alert."
            )
            if _normalize(rec.status) in NON_TRADABLE_STATUSES:
                notes += " Instrument was already non-tradable before it disappeared."
            by_type[CHANGE_DELETION].append(
                UniverseChangeAlert(
                    change_type=CHANGE_DELETION,
                    permanent_id=pid,
                    previous_ticker=rec.ticker_symbol,
                    new_ticker="",
                    recommended_action=ACTION_LIQUIDATE,
                    audit_notes=notes,
                )
            )

        # 3. Attribute changes on instruments present in both snapshots.
        for pid in sorted(common_ids):
            p_rec = prev_map[pid]
            c_rec = curr_map[pid]

            # 3a. Ticker rename - the FIGI is unchanged by a ticker change
            #     (FIGI Allocation Rules Sec. 3.1 / 3.2.1), so cross-matching on
            #     the permanent id is the only reliable way to see one.
            if _normalize(p_rec.ticker_symbol) != _normalize(c_rec.ticker_symbol):
                renames_count += 1
                by_type[CHANGE_TICKER_RENAME].append(
                    UniverseChangeAlert(
                        change_type=CHANGE_TICKER_RENAME,
                        permanent_id=pid,
                        previous_ticker=p_rec.ticker_symbol,
                        new_ticker=c_rec.ticker_symbol,
                        recommended_action=ACTION_UPDATE_SYMBOL_MAPPER,
                        audit_notes=(
                            f"TICKER RENAME [{pid}]: symbol changed from "
                            f"'{p_rec.ticker_symbol}' to '{c_rec.ticker_symbol}'. Update "
                            "the symbol mapper and every live subscription; the retired "
                            "ticker may be reassigned to an unrelated instrument."
                        ),
                    )
                )

            # 3b. Venue migration. The FIGI does not change on a ticker change,
            #     but the exchange code carried by a composite-level FIGI can
            #     change (FIGI Allocation Rules Sec. 3.2.2 / 3.2.3), and routing
            #     to the old venue fails.
            if _normalize(p_rec.exchange) != _normalize(c_rec.exchange):
                exchange_migrations_count += 1
                by_type[CHANGE_EXCHANGE_MIGRATION].append(
                    UniverseChangeAlert(
                        change_type=CHANGE_EXCHANGE_MIGRATION,
                        permanent_id=pid,
                        previous_ticker=p_rec.ticker_symbol,
                        new_ticker=c_rec.ticker_symbol,
                        recommended_action=ACTION_UPDATE_ROUTING,
                        audit_notes=(
                            f"VENUE MIGRATION [{c_rec.ticker_symbol} / {pid}]: exchange "
                            f"changed from '{p_rec.exchange}' to '{c_rec.exchange}'. "
                            "Update routing and market-data entitlements before the "
                            "next order."
                        ),
                    )
                )

            # 3c. Trading-status transition.
            p_status = _normalize(p_rec.status)
            c_status = _normalize(c_rec.status)
            if p_status != c_status:
                status_changes_count += 1
                action = self._status_transition_action(p_status, c_status)
                notes = (
                    f"STATUS CHANGE [{c_rec.ticker_symbol} / {pid}]: status moved from "
                    f"'{p_rec.status}' to '{c_rec.status}'."
                )
                if c_status == STATUS_DELISTED:
                    notes += (
                        " Close the position and unsubscribe. If the delisting is a "
                        "merger completion the holding may already have converted to "
                        "cash or acquirer shares - reconcile with the corporate-action "
                        "feed rather than sending an order into a dead symbol."
                    )
                elif c_status not in KNOWN_STATUSES:
                    notes += (
                        " Status is outside the recognised vocabulary "
                        f"({sorted(KNOWN_STATUSES)}); routed to manual review rather "
                        "than guessed at."
                    )
                    logger.warning(
                        "Unrecognised instrument status %r for %s; routed to %s",
                        c_rec.status,
                        pid,
                        ACTION_REVIEW_STATUS_CHANGE,
                    )
                by_type[CHANGE_STATUS_CHANGE].append(
                    UniverseChangeAlert(
                        change_type=CHANGE_STATUS_CHANGE,
                        permanent_id=pid,
                        previous_ticker=p_rec.ticker_symbol,
                        new_ticker=c_rec.ticker_symbol,
                        recommended_action=action,
                        audit_notes=notes,
                    )
                )

        alerts: List[UniverseChangeAlert] = [
            alert
            for change_type in _CHANGE_TYPE_ORDER
            for alert in by_type[change_type]
        ]

        # --- downgrade every action when the snapshot itself is not credible - #
        if snapshot_is_suspect:
            reason = (
                "current snapshot is empty while the previous one was not"
                if empty_current
                else (
                    f"deletion ratio {deletion_ratio:.1%} exceeds the configured "
                    f"maximum of {self.max_deletion_ratio:.1%}"
                )
            )
            for alert in alerts:
                alert.suppressed_action = alert.recommended_action
                alert.recommended_action = ACTION_HOLD_FOR_MANUAL_REVIEW
                alert.requires_manual_review = True
                alert.audit_notes += (
                    f" [SUPPRESSED: {reason}; original action "
                    f"'{alert.suppressed_action}' held for manual review.]"
                )

        total_changes = (
            len(added_ids)
            + len(deleted_ids)
            + renames_count
            + status_changes_count
            + exchange_migrations_count
        )
        if snapshot_is_suspect:
            status = REPORT_SNAPSHOT_SUSPECT
        elif total_changes > 0:
            status = REPORT_CHANGES_DETECTED
        else:
            status = REPORT_NO_CHANGES

        notes = (
            f"UNIVERSE CHANGE REPORT: Previous = {len(prev_ids)}, "
            f"Current = {len(curr_ids)}. Additions = {len(added_ids)}, "
            f"Deletions = {len(deleted_ids)}, Ticker Renames = {renames_count}, "
            f"Venue Migrations = {exchange_migrations_count}, "
            f"Status Changes = {status_changes_count}. "
            f"Deletion ratio = {deletion_ratio:.1%}."
        )
        if previous_as_of is not None or current_as_of is not None:
            notes += f" As-of: {previous_as_of} -> {current_as_of}."
        if snapshot_is_suspect:
            notes += (
                " SNAPSHOT SUSPECT: every recommended action downgraded to "
                f"{ACTION_HOLD_FOR_MANUAL_REVIEW}; escalate to a human before acting."
            )
            logger.warning(notes)
        else:
            logger.info(notes)

        return UniverseChangeReport(
            total_previous_count=len(prev_ids),
            total_current_count=len(curr_ids),
            additions_count=len(added_ids),
            deletions_count=len(deleted_ids),
            renames_count=renames_count,
            status_changes_count=status_changes_count,
            exchange_migrations_count=exchange_migrations_count,
            alerts=alerts,
            status=status,
            audit_notes=notes,
            deletion_ratio=deletion_ratio,
            snapshot_is_suspect=snapshot_is_suspect,
        )
