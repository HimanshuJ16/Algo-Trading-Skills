"""
market-data-entitlement-and-licensing-per-venue: fail-closed pre-stream gate for
exchange market data entitlements.

What this module is and is not
------------------------------
It is a **fail-closed policy gate**. Given a subscriber's declared per-venue
entitlements and an inbound request to open a data stream, it decides whether the
firm has actually licensed *that venue*, at *that depth*, for *that usage*, by a
subscriber classified the way the venue's rules require -- and records an
auditable reason either way.

It is **not** a fee calculator, **not** an exchange usage declaration, and **not**
a substitute for the vendor's own permissioning system (LSEG DACS, Bloomberg
EMRS). Those enforce entitlements at the feed. This gate sits upstream of them, at
the point where an internal system asks for a stream, so an unlicensed
consumption pattern is refused before it becomes a reportable unit.

Why entitlement is per venue, per depth, and per activity
---------------------------------------------------------
The most common modelling error in this area is a firm-wide
``has_non_display_license`` boolean. No major venue licenses non-display that way:

* **CME Group** charges Non-Display licence fees on a per Designated Contract
  Market (DCM) basis. Automated trading using additional DCMs (CME, CBOT, COMEX,
  NYMEX) requires additional Category A licensing, and Category A itself splits
  into A1 (trading as principal), A2 (facilitating client business) and A3
  (trading on an alternative venue). Licensees must declare every Application
  that uses real-time or delayed CME Group Information.
* **London Stock Exchange** takes the declaration as a matrix: each data segment
  (UK, International, ETF/ETP, AIM, ...) at Level 1 and Level 2, crossed with
  Principal / Client Facilitation / Trading Platforms. An Order Form must be
  executed for the relevant Licensable Activity "irrespective of whether a
  Licensable Activity has Charges associated with it or not."
* **Nasdaq** states that Non-Display fees and reporting requirements "vary
  depending upon the Nasdaq data product", and prices depth non-display
  separately -- "U.S. NASDAQ DEPTH [TOTALVIEW/ LEVEL 2] NON-DISPLAY PROFESSIONAL
  [INTERNAL]" is its own entitlement. Nasdaq Basic is display-only: "Non-Display
  Usage is NOT included."

So the entitlement key is (venue, depth, non-display activity category), and this
module models it that way.

Why the gate fails closed
-------------------------
Licensing breaches are found late and priced retroactively. The CTA Nonprofessional
Subscriber Policy is explicit: "If NYSE finds that the vendor has incorrectly
qualified a professional subscriber as nonprofessional, the vendor will be liable
for retroactive fees billed by NYSE for the subscriber at the professional rate."
A wrong "approve" therefore surfaces as a back-fee assessment months or years
later, not as an error on the day. Every check here denies on missing, stale or
unrecognised information rather than assuming permission -- in particular an
unrecognised ``usage_type`` is a denial, never a fall-through to DISPLAY.

Determinism
-----------
``audit_stream_entitlement`` accepts ``as_of_date``. It defaults to today only as a
convenience; pass it explicitly for reproducible, auditable output.

Scope limitations (read before relying on this)
-----------------------------------------------
* ``L1``/``L2``/``L3`` is a repo-internal depth ladder (top-of-book / aggregated
  depth / order-by-order). Venues name their tiers differently; map your venue's
  product names onto this ladder when you populate ``max_data_level``.
* The engine enforces what a compliance owner encodes. It cannot read your Order
  Forms, your ILA schedules or your exchange reporting codes for you.
* It counts nothing. Reportable units are derived from your infrastructure
  inventory, not from stream requests -- see ``references/standards.md``.

References: see ``references/standards.md``.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Dict, FrozenSet, Iterable, Optional, Sequence, Set, Tuple

logger = logging.getLogger(__name__)

# --- Usage vocabulary --------------------------------------------------------
USAGE_DISPLAY = "DISPLAY"
USAGE_NON_DISPLAY_ALGO = "NON_DISPLAY_ALGO"

#: The only usage types this engine knows how to reason about. Anything else is
#: denied rather than treated as DISPLAY: a typo must not buy non-display access.
RECOGNISED_USAGE_TYPES: FrozenSet[str] = frozenset({USAGE_DISPLAY, USAGE_NON_DISPLAY_ALGO})

# --- Non-display activity categories -----------------------------------------
# Named after the categories the venues actually declare against: LSE's
# non-display declaration uses these three labels verbatim, and CME's Category A
# splits along the same lines (A1 principal, A2 client facilitation,
# A3 alternative venue).
ND_CATEGORY_PRINCIPAL = "PRINCIPAL"
ND_CATEGORY_CLIENT_FACILITATION = "CLIENT_FACILITATION"
ND_CATEGORY_TRADING_PLATFORM = "TRADING_PLATFORM"

# --- Subscriber classification -----------------------------------------------
CLASSIFICATION_PROFESSIONAL = "PROFESSIONAL"
CLASSIFICATION_NON_PROFESSIONAL = "NON_PROFESSIONAL"
RECOGNISED_CLASSIFICATIONS: FrozenSet[str] = frozenset(
    {CLASSIFICATION_PROFESSIONAL, CLASSIFICATION_NON_PROFESSIONAL}
)

ACCOUNT_NATURAL_PERSON = "NATURAL_PERSON"
ACCOUNT_ORGANISATION = "ORGANISATION"
RECOGNISED_ACCOUNT_HOLDER_TYPES: FrozenSet[str] = frozenset(
    {ACCOUNT_NATURAL_PERSON, ACCOUNT_ORGANISATION}
)

# --- Depth ladder ------------------------------------------------------------
#: Repo-internal depth ladder, ascending. A venue entitlement at L2 covers L1.
DATA_LEVEL_ORDER: Tuple[str, ...] = ("L1", "L2", "L3")
_DATA_LEVEL_RANK: Dict[str, int] = {level: i for i, level in enumerate(DATA_LEVEL_ORDER)}

# --- Audit statuses ----------------------------------------------------------
STATUS_APPROVED = "ENTITLEMENT_APPROVED"
STATUS_SUBSCRIBER_MISMATCH = "ENTITLEMENT_DENIED_SUBSCRIBER_MISMATCH"
STATUS_UNRECOGNISED_USAGE_TYPE = "ENTITLEMENT_DENIED_UNRECOGNISED_USAGE_TYPE"
STATUS_MISCLASSIFIED_SUBSCRIBER = "ENTITLEMENT_DENIED_MISCLASSIFIED_SUBSCRIBER"
STATUS_STALE_CLASSIFICATION = "ENTITLEMENT_DENIED_STALE_CLASSIFICATION"
STATUS_UNLICENSED_VENUE = "ENTITLEMENT_DENIED_UNLICENSED_VENUE"
STATUS_EXPIRED_LICENSE = "ENTITLEMENT_DENIED_EXPIRED_LICENSE"
STATUS_UNLICENSED_DATA_LEVEL = "ENTITLEMENT_DENIED_UNLICENSED_DATA_LEVEL"
STATUS_MISSING_NON_DISPLAY_LICENSE = "ENTITLEMENT_DENIED_MISSING_NON_DISPLAY_LICENSE"

#: Every status this engine can return. Callers routing on ``status`` must treat
#: an unrecognised value as a denial rather than falling through to approval.
ALL_STATUSES: Tuple[str, ...] = (
    STATUS_APPROVED,
    STATUS_SUBSCRIBER_MISMATCH,
    STATUS_UNRECOGNISED_USAGE_TYPE,
    STATUS_MISCLASSIFIED_SUBSCRIBER,
    STATUS_STALE_CLASSIFICATION,
    STATUS_UNLICENSED_VENUE,
    STATUS_EXPIRED_LICENSE,
    STATUS_UNLICENSED_DATA_LEVEL,
    STATUS_MISSING_NON_DISPLAY_LICENSE,
)

#: CTA requires retired and inactive professionals to re-verify Non-Professional
#: status semi-annually. 183 days is that cadence expressed in days; tighten it
#: if your venue agreements are stricter.
DEFAULT_MAX_ATTESTATION_AGE_DAYS = 183


class EntitlementConfigurationError(ValueError):
    """Raised when a profile, request, or engine call is structurally invalid.

    Subclasses ``ValueError`` so callers already guarding the audit call with
    ``except ValueError`` keep working.

    An entitlement gate must fail loudly on malformed configuration. A profile
    declaring a venue at depth ``"L4"``, or a request with an empty venue id, is
    a data-entry error; evaluating it anyway produces an authoritative-looking
    ENTITLEMENT_APPROVED backed by nothing.
    """


# --- validation helpers ------------------------------------------------------

def _require_non_empty(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EntitlementConfigurationError(f"{name} must be a non-empty string, got {value!r}")
    return value.strip()


def _require_bool(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise EntitlementConfigurationError(f"{name} must be a bool, got {value!r}")
    return value


def _normalise_token(value: object, name: str) -> str:
    return _require_non_empty(value, name).upper()


def _require_member(value: object, name: str, allowed: Iterable[str]) -> str:
    token = _normalise_token(value, name)
    allowed_set = set(allowed)
    if token not in allowed_set:
        raise EntitlementConfigurationError(
            f"{name} must be one of {sorted(allowed_set)}, got {token!r}")
    return token


def _parse_iso_date(value: Optional[str], name: str) -> Optional[date]:
    if value is None:
        return None
    text = _require_non_empty(value, name)
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise EntitlementConfigurationError(
            f"{name} must be an ISO-8601 date (YYYY-MM-DD), got {value!r}") from exc


# --- data model --------------------------------------------------------------

@dataclass
class VenueEntitlement:
    """What the firm has actually licensed at one venue.

    One instance per venue (or per venue segment / DCM, if that is how your Order
    Forms are cut -- ``CME``, ``CBOT`` and ``NYMEX`` are three entitlements, not
    one).
    """

    venue_id: str
    #: Deepest licensed level on the ``DATA_LEVEL_ORDER`` ladder. An entitlement
    #: at ``L2`` covers ``L1``; it does not cover ``L3``.
    max_data_level: str = "L1"
    #: Non-display activity categories licensed at this venue. Empty means
    #: display-only: the venue is licensed, but no automated consumption is.
    #: Never widen this list to make a request pass.
    non_display_categories: Sequence[str] = field(default_factory=tuple)
    #: ISO-8601 (YYYY-MM-DD) expiry of the licensed term at this venue. ``None``
    #: means "not tracked here" -- the engine will not gate on expiry and says so
    #: once per venue in the log. It deliberately does NOT default to a
    #: placeholder date: a hard-coded future date authorises everything until the
    #: day it passes, then denies everything at once.
    license_expiry_date: Optional[str] = None


@dataclass
class UserEntitlementProfile:
    """The subscriber's declared classification and per-venue entitlements.

    Every field is an assertion the compliance owner must be able to support from
    an executed agreement, Order Form or subscriber attestation. The engine
    enforces what it is told; it cannot read the paperwork for you.
    """

    user_id: str
    #: ``NATURAL_PERSON`` or ``ORGANISATION``. Under the CTA Nonprofessional
    #: Subscriber Policy only natural persons can qualify as Non-Professional, and
    #: an account held in an organisation's name is Professional regardless of who
    #: sits behind it.
    account_holder_type: str
    #: ``PROFESSIONAL`` or ``NON_PROFESSIONAL``. Professional is the default under
    #: both the Nasdaq and CTA definitions: all subscribers are deemed
    #: Professional unless positively qualified as Non-Professional.
    declared_classification: str
    #: True if the subscriber is a Securities Professional under the CTA test
    #: (registered/qualified with a regulator or exchange, engaged as an
    #: investment adviser, or performing such functions at an exempt bank).
    is_securities_professional: bool = False
    #: ISO-8601 date the Non-Professional declaration was last verified. Required
    #: for a NON_PROFESSIONAL declaration; ignored for PROFESSIONAL, which needs
    #: no attestation because it is the default state.
    classification_attested_on: Optional[str] = None
    venue_entitlements: Sequence[VenueEntitlement] = field(default_factory=tuple)


@dataclass
class DataStreamRequest:
    """One consumer's request to open a market data stream."""

    user_id: str
    venue_id: str
    #: Requested depth on the ``DATA_LEVEL_ORDER`` ladder.
    data_level: str
    #: ``DISPLAY`` or ``NON_DISPLAY_ALGO``. Anything else is denied.
    usage_type: str
    #: Required when ``usage_type == 'NON_DISPLAY_ALGO'``: which licensed activity
    #: the consumption falls under. Trading as principal and facilitating client
    #: business are separately licensed at CME (A1 vs A2) and LSE (Principal vs
    #: Client Facilitation), so the engine will not guess.
    non_display_category: Optional[str] = None


@dataclass
class EntitlementAuditReport:
    """The auditable record of one entitlement decision.

    ``audit_notes`` carries the human-readable reason and is intended to be
    persisted verbatim: it is the evidence an exchange auditor asks for. The
    engine keeps no durable record of its own -- persist every report, denials
    included, as it is returned.
    """

    user_id: str
    venue_id: str
    usage_type: str
    is_authorized: bool
    status: str                              # see ALL_STATUSES
    audit_notes: str
    #: Depth the decision was made against, after normalisation.
    data_level: str = ""
    #: Non-display activity category the decision was made against, if any.
    non_display_category: Optional[str] = None
    #: Classification the decision was made against, after normalisation.
    subscriber_classification: str = ""
    #: Date the decision was evaluated against, ISO-8601.
    evaluated_on: str = ""


class MarketDataEntitlementEngine:
    """Fail-closed pre-stream gate for per-venue market data entitlements.

    Decision order, broadest constraint first::

        subscriber identity -> usage type recognised -> classification integrity
        -> classification freshness -> venue licensed -> licence term
        -> depth licensed -> non-display activity licensed

    The order is part of the contract: it determines which ``status`` an auditor
    sees for a request that breaches more than one rule. Classification is checked
    before venue because a misclassified subscriber is a firm-wide error that
    every venue prices retroactively; venue and term precede depth and
    non-display because an unlicensed or lapsed venue makes the finer questions
    moot.

    The engine is stateless with respect to decisions -- it reserves nothing and
    counts nothing, so concurrent callers cannot race each other. The only shared
    state is a set of (subscriber, venue) pairs already warned about for untracked
    expiry, guarded by a lock so the warning is emitted once rather than once per
    thread.
    """

    def __init__(self, max_attestation_age_days: int = DEFAULT_MAX_ATTESTATION_AGE_DAYS) -> None:
        # bool is a subclass of int; True would silently become a 1-day window.
        if isinstance(max_attestation_age_days, bool) or not isinstance(
                max_attestation_age_days, int):
            raise EntitlementConfigurationError(
                f"max_attestation_age_days must be an int, got {max_attestation_age_days!r}")
        if max_attestation_age_days < 1:
            raise EntitlementConfigurationError(
                f"max_attestation_age_days must be >= 1, got {max_attestation_age_days}")
        self.max_attestation_age_days = max_attestation_age_days
        self._lock = threading.Lock()
        self._untracked_expiry_warned: Set[Tuple[str, str]] = set()

    # -- public API -----------------------------------------------------------

    def audit_stream_entitlement(
        self,
        profile: UserEntitlementProfile,
        request: DataStreamRequest,
        as_of_date: Optional[date] = None,
    ) -> EntitlementAuditReport:
        """Decide whether this subscriber may open this stream, and say why.

        Args:
            profile: the subscriber's declared classification and per-venue
                entitlements.
            request: the stream being requested.
            as_of_date: date to evaluate licence terms and attestation age
                against. Defaults to today; pass it explicitly for reproducible,
                auditable output (batch runs, replays, tests).

        Returns:
            An :class:`EntitlementAuditReport`. ``is_authorized`` is True only for
            ``ENTITLEMENT_APPROVED``.

        Raises:
            EntitlementConfigurationError: if the profile or request is
                structurally invalid. Malformed configuration is a defect, not a
                denial, and must not be mistaken for a compliance decision.
        """
        if as_of_date is None:
            as_of_date = date.today()
        elif isinstance(as_of_date, datetime):
            # datetime subclasses date, so an unguarded isinstance check lets a
            # datetime through and it then raises TypeError deep inside the
            # expiry comparison. Narrow it here instead: licence terms and
            # attestations are calendar-dated, and silently discarding a time
            # component would change which day a decision lands on.
            as_of_date = as_of_date.date()
        elif not isinstance(as_of_date, date):
            raise EntitlementConfigurationError(
                f"as_of_date must be a datetime.date, got {as_of_date!r}")

        profile_user = _require_non_empty(profile.user_id, "profile.user_id")
        request_user = _require_non_empty(request.user_id, "request.user_id")
        venue = _normalise_token(request.venue_id, "request.venue_id")
        level = _require_member(request.data_level, "request.data_level", DATA_LEVEL_ORDER)
        classification = _require_member(
            profile.declared_classification, "profile.declared_classification",
            RECOGNISED_CLASSIFICATIONS)
        account_type = _require_member(
            profile.account_holder_type, "profile.account_holder_type",
            RECOGNISED_ACCOUNT_HOLDER_TYPES)
        _require_bool(profile.is_securities_professional, "profile.is_securities_professional")
        attested_on = _parse_iso_date(
            profile.classification_attested_on, "profile.classification_attested_on")
        entitlements = self._index_entitlements(profile)

        # usage_type is normalised but NOT validated against a whitelist here: an
        # unrecognised value is a compliance denial (step 2), not a configuration
        # defect, because it typically arrives from a caller's config rather than
        # from the entitlement record.
        usage = _normalise_token(request.usage_type, "request.usage_type")
        category = (
            _normalise_token(request.non_display_category, "request.non_display_category")
            if request.non_display_category is not None else None
        )

        def deny(status: str, notes: str) -> EntitlementAuditReport:
            logger.warning(notes)
            return EntitlementAuditReport(
                user_id=profile_user, venue_id=venue, usage_type=usage,
                is_authorized=False, status=status, audit_notes=notes,
                data_level=level, non_display_category=category,
                subscriber_classification=classification,
                evaluated_on=as_of_date.isoformat(),
            )

        # 1. Subscriber identity. Auditing user A's request against user B's
        #    entitlements would silently grant B's licences to A and record the
        #    decision under B.
        if profile_user != request_user:
            return deny(
                STATUS_SUBSCRIBER_MISMATCH,
                f"ENTITLEMENT DENIED [{profile_user}]: request is for subscriber "
                f"'{request_user}' but was evaluated against the entitlement profile of "
                f"'{profile_user}'. Entitlements are not transferable between subscribers.",
            )

        # 2. Usage type must be recognised. A typo must fail closed, never fall
        #    through to DISPLAY and skip the non-display gate entirely.
        if usage not in RECOGNISED_USAGE_TYPES:
            return deny(
                STATUS_UNRECOGNISED_USAGE_TYPE,
                f"ENTITLEMENT DENIED [{profile_user}]: usage_type '{usage}' is not recognised "
                f"(expected one of {sorted(RECOGNISED_USAGE_TYPES)}). An unclassified usage "
                f"cannot be shown to be licensed, so it is refused.",
            )

        # 3. Subscriber classification integrity.
        misclassification = self._classification_defect(
            classification=classification,
            account_type=account_type,
            is_securities_professional=profile.is_securities_professional,
            usage=usage,
        )
        if misclassification is not None:
            return deny(
                STATUS_MISCLASSIFIED_SUBSCRIBER,
                f"ENTITLEMENT DENIED [{profile_user}]: {misclassification} Incorrectly "
                f"qualifying a professional subscriber as non-professional makes the "
                f"distributor liable for retroactive fees at the professional rate "
                f"(CTA Nonprofessional Subscriber Policy).",
            )

        # 4. Non-Professional declarations go stale. Professional needs no
        #    attestation: it is the default classification.
        if classification == CLASSIFICATION_NON_PROFESSIONAL:
            if attested_on is None:
                return deny(
                    STATUS_STALE_CLASSIFICATION,
                    f"ENTITLEMENT DENIED [{profile_user}]: NON_PROFESSIONAL declared with no "
                    f"classification_attested_on date. Non-Professional status must be "
                    f"positively verified by the distributor; an unverified declaration is "
                    f"treated as Professional-rate exposure and refused.",
                )
            age_days = (as_of_date - attested_on).days
            if age_days < 0:
                return deny(
                    STATUS_STALE_CLASSIFICATION,
                    f"ENTITLEMENT DENIED [{profile_user}]: classification_attested_on "
                    f"{attested_on.isoformat()} is in the future relative to the evaluation "
                    f"date {as_of_date.isoformat()}; the attestation cannot be relied on.",
                )
            if age_days > self.max_attestation_age_days:
                return deny(
                    STATUS_STALE_CLASSIFICATION,
                    f"ENTITLEMENT DENIED [{profile_user}]: NON_PROFESSIONAL status last "
                    f"verified {attested_on.isoformat()} ({age_days} days ago), exceeding the "
                    f"{self.max_attestation_age_days}-day re-verification window. Re-verify "
                    f"before re-opening the stream.",
                )

        # 5. Venue entitlement.
        entitlement = entitlements.get(venue)
        if entitlement is None:
            return deny(
                STATUS_UNLICENSED_VENUE,
                f"ENTITLEMENT DENIED [{profile_user}]: venue '{venue}' is not in the "
                f"subscriber's licensed venue set {sorted(entitlements)}. Venues licensed as "
                f"separate DCMs or segments (CME/CBOT/NYMEX/COMEX) each need their own "
                f"entitlement.",
            )

        # 6. Licence term.
        expiry = _parse_iso_date(
            entitlement.license_expiry_date, "VenueEntitlement.license_expiry_date")
        if expiry is None:
            self._warn_untracked_expiry(profile_user, venue)
        elif as_of_date > expiry:
            return deny(
                STATUS_EXPIRED_LICENSE,
                f"ENTITLEMENT DENIED [{profile_user}]: market data licence for venue "
                f"'{venue}' expired on {expiry.isoformat()} (evaluated "
                f"{as_of_date.isoformat()}).",
            )

        # 7. Depth. Depth-of-book is licensed separately from top-of-book at every
        #    venue this skill covers.
        licensed_level = _require_member(
            entitlement.max_data_level, "VenueEntitlement.max_data_level", DATA_LEVEL_ORDER)
        if _DATA_LEVEL_RANK[level] > _DATA_LEVEL_RANK[licensed_level]:
            return deny(
                STATUS_UNLICENSED_DATA_LEVEL,
                f"ENTITLEMENT DENIED [{profile_user}]: {level} depth requested for venue "
                f"'{venue}' but the entitlement covers only up to {licensed_level}. "
                f"Depth-of-book is a separate licensed product from top-of-book.",
            )

        # 8. Non-display activity.
        if usage == USAGE_NON_DISPLAY_ALGO:
            licensed_categories = self._normalise_categories(entitlement)
            if not licensed_categories:
                return deny(
                    STATUS_MISSING_NON_DISPLAY_LICENSE,
                    f"ENTITLEMENT DENIED [{profile_user}]: non-display consumption requested "
                    f"for venue '{venue}' but the entitlement is display-only. Non-display "
                    f"use is licensed and declared per venue; a display entitlement never "
                    f"confers it.",
                )
            if category is None:
                return deny(
                    STATUS_MISSING_NON_DISPLAY_LICENSE,
                    f"ENTITLEMENT DENIED [{profile_user}]: non-display consumption requested "
                    f"for venue '{venue}' without a non_display_category. Trading as "
                    f"principal, facilitating client business and operating a trading "
                    f"platform are separately licensed activities; the engine will not guess "
                    f"which one applies.",
                )
            if category not in licensed_categories:
                return deny(
                    STATUS_MISSING_NON_DISPLAY_LICENSE,
                    f"ENTITLEMENT DENIED [{profile_user}]: non-display category '{category}' "
                    f"is not licensed at venue '{venue}'; licensed categories are "
                    f"{sorted(licensed_categories)}. Each category requires its own declared "
                    f"and approved licence.",
                )

        notes = (
            f"ENTITLEMENT APPROVED [{profile_user}]: granted {level} {usage} stream access "
            f"for venue '{venue}'"
            + (f" under non-display category '{category}'"
               if usage == USAGE_NON_DISPLAY_ALGO and category else "")
            + f", subscriber classified {classification}, evaluated {as_of_date.isoformat()}."
        )
        logger.info(notes)
        return EntitlementAuditReport(
            user_id=profile_user, venue_id=venue, usage_type=usage,
            is_authorized=True, status=STATUS_APPROVED, audit_notes=notes,
            data_level=level, non_display_category=category,
            subscriber_classification=classification,
            evaluated_on=as_of_date.isoformat(),
        )

    # -- internals ------------------------------------------------------------

    @staticmethod
    def _index_entitlements(profile: UserEntitlementProfile) -> Dict[str, VenueEntitlement]:
        """Key the profile's entitlements by normalised venue id.

        Normalising both sides matters: the request venue is upper-cased, so an
        entitlement recorded as ``'cme'`` would otherwise be invisible and the
        request denied, while ``'CME '`` would match the venue but miss its
        expiry -- failing *open* on the term check.
        """
        items = profile.venue_entitlements
        if isinstance(items, (str, bytes)) or not isinstance(items, Iterable):
            raise EntitlementConfigurationError(
                f"venue_entitlements must be a sequence of VenueEntitlement, got {items!r}")
        indexed: Dict[str, VenueEntitlement] = {}
        for item in items:
            if not isinstance(item, VenueEntitlement):
                raise EntitlementConfigurationError(
                    f"venue_entitlements entries must be VenueEntitlement, got "
                    f"{type(item).__name__}")
            key = _normalise_token(item.venue_id, "VenueEntitlement.venue_id")
            if key in indexed:
                # Two entitlements for one venue resolve arbitrarily, and the one
                # that loses is as likely to be the narrower -- i.e. it can fail
                # open. Refuse the ambiguity instead.
                raise EntitlementConfigurationError(
                    f"duplicate VenueEntitlement for venue '{key}' in profile "
                    f"'{profile.user_id}'; merge them into one record")
            indexed[key] = item
        return indexed

    @staticmethod
    def _normalise_categories(entitlement: VenueEntitlement) -> FrozenSet[str]:
        items = entitlement.non_display_categories
        if isinstance(items, (str, bytes)) or not isinstance(items, Iterable):
            raise EntitlementConfigurationError(
                f"non_display_categories must be a sequence of category strings, got {items!r}")
        return frozenset(_normalise_token(c, "non_display_categories entry") for c in items)

    @staticmethod
    def _classification_defect(
        *,
        classification: str,
        account_type: str,
        is_securities_professional: bool,
        usage: str,
    ) -> Optional[str]:
        """Return a reason string if the declared classification cannot stand.

        Each rule traces to a primary source; see ``references/standards.md``.
        Professional is never a defect: it is the default classification, and
        over-declaring Professional costs the firm money rather than exposing it
        to a back-fee assessment.
        """
        if classification != CLASSIFICATION_NON_PROFESSIONAL:
            return None
        if account_type == ACCOUNT_ORGANISATION:
            return (
                "account holder is an ORGANISATION but is declared NON_PROFESSIONAL. "
                "Only natural persons can qualify as Non-Professional; data received "
                "through an account not registered to a natural person is Professional.")
        if is_securities_professional:
            return (
                "subscriber is a Securities Professional but is declared NON_PROFESSIONAL. "
                "Registration or qualification with a regulator, exchange or association, "
                "or engagement as an investment adviser, forecloses Non-Professional "
                "status.")
        if usage == USAGE_NON_DISPLAY_ALGO:
            return (
                "automated non-display consumption requested under a NON_PROFESSIONAL "
                "declaration. Non-Professional data is licensed for personal use only, and "
                "venues price non-display entitlements at professional rates.")
        return None

    def _warn_untracked_expiry(self, user_id: str, venue: str) -> None:
        """Log once per (subscriber, venue) that expiry is not being gated.

        An untracked expiry is a deliberate, visible omission rather than a silent
        one: the alternative -- a placeholder date -- authorises everything until
        it passes and then denies everything at once.
        """
        key = (user_id, venue)
        with self._lock:
            if key in self._untracked_expiry_warned:
                return
            self._untracked_expiry_warned.add(key)
        logger.warning(
            "Venue '%s' for subscriber '%s' has no license_expiry_date; the licence term "
            "is NOT being enforced for this venue.", venue, user_id)
