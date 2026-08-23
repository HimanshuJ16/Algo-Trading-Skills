"""
data-vendor-contractual-usage-restriction-tracking: pre-access compliance gate for
market data vendor contract scope.

What this module is and is not
------------------------------
It is a **fail-closed policy gate**. Given a documented vendor contract scope and
an inbound data access request, it decides whether that request stays inside the
scope the firm actually licensed, and records an auditable reason either way.

It is **not** a fee calculator and **not** a substitute for the vendor's own
entitlement system (Bloomberg EMRS, LSEG DACS). Those systems enforce
permissioning at the feed; this module enforces the *contractual* boundary
upstream of them, at the point where an internal system asks for data, so that an
undeclared use case is refused before it ever reaches a fee-liable feed.

Why a pre-access gate matters
-----------------------------
Under the Nasdaq Global Data Agreement (v4.87, s.4(c)) any use of the Information
not already provided for in the Nasdaq Requirements -- expressly including
derivative information, retransmission, redistribution and index calculation --
requires *prior written approval* and payment of the applicable fees. Nasdaq may
audit a Distributor's records, reports and systems (s.7(a)), normally no more than
once per twelve months. Where a Final Audit finds underreporting, the amounts plus
interest fall due within sixty days, and for a good-faith error the Distributor's
liability reaches back **three years** (s.7(e)); underreporting of 10% or more of
reported Reportable Units additionally makes the Distributor liable for Nasdaq's
audit, legal and administrative costs (s.7(f)).

The practical consequence for this module: a wrong "approve" is not discovered on
the day it happens, it is discovered years later with a multi-year back-fee
attached. Every check therefore denies on missing or ambiguous information rather
than assuming permission.

Scope limitations (read before relying on seat counts)
------------------------------------------------------
``requested_seats`` models *contractual* entitlement units as negotiated with the
vendor. It is deliberately NOT the exchange unit of count. Nasdaq's Non-Display
unit of count is the greater of (a) the number of Subscribers that can modify the
application in real time, or (b) the number of Devices (usually servers) that
receive and benefit from the Information -- see the US Equities and Options Data
Policies, s.7. Deriving reportable units from a per-request seat counter will
understate them. Feed that calculation from your infrastructure inventory, not
from this engine.

Determinism
-----------
``evaluate_access_request`` accepts ``as_of_date``. It defaults to today only as a
convenience; pass it explicitly for reproducible, auditable output.

References: see ``references/standards.md``.
"""
from __future__ import annotations

import logging
import threading
from collections import deque
from dataclasses import dataclass
from datetime import date
from typing import Deque, Dict, FrozenSet, Iterable, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# --- Use case vocabulary -----------------------------------------------------
USE_CASE_INTERNAL_RESEARCH = "INTERNAL_RESEARCH"
USE_CASE_NON_DISPLAY_TRADING = "NON_DISPLAY_TRADING"
USE_CASE_EXTERNAL_REDISTRIBUTION = "EXTERNAL_REDISTRIBUTION"
USE_CASE_RISK_MANAGEMENT = "RISK_MANAGEMENT"

# --- Audit statuses ----------------------------------------------------------
STATUS_APPROVED = "APPROVED"
STATUS_CONTRACT_EXPIRED = "CONTRACT_EXPIRED"
STATUS_REDISTRIBUTION_VIOLATION = "REDISTRIBUTION_LICENSING_VIOLATION"
STATUS_NON_DISPLAY_VIOLATION = "NON_DISPLAY_LICENSING_VIOLATION"
STATUS_UNAUTHORIZED_USE_CASE = "UNAUTHORIZED_USE_CASE_VIOLATION"
STATUS_CONCURRENCY_CAP_EXCEEDED = "CONCURRENCY_CAP_EXCEEDED"

#: Statuses this engine can return. Callers routing on ``status`` should treat an
#: unrecognised value as a denial rather than falling through to approval.
ALL_STATUSES: Tuple[str, ...] = (
    STATUS_APPROVED,
    STATUS_CONTRACT_EXPIRED,
    STATUS_REDISTRIBUTION_VIOLATION,
    STATUS_NON_DISPLAY_VIOLATION,
    STATUS_UNAUTHORIZED_USE_CASE,
    STATUS_CONCURRENCY_CAP_EXCEEDED,
)

DEFAULT_AUDIT_LOG_CAPACITY = 10_000


class VendorUsageConfigurationError(ValueError):
    """Raised when a contract, request, or engine call is structurally invalid.

    Subclasses ``ValueError`` so existing callers catching ``ValueError`` around
    ``evaluate_access_request`` keep working.

    A licensing gate must fail loudly on malformed configuration. A contract
    registered with a negative seat cap, or a request for -5 seats, is a
    data-entry error; evaluating it anyway produces an authoritative-looking
    APPROVED backed by nothing.
    """


def _require_non_empty(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise VendorUsageConfigurationError(f"{name} must be a non-empty string, got {value!r}")
    return value.strip()


def _require_int(value: object, name: str, *, minimum: int) -> int:
    # bool is a subclass of int; True would silently become 1 seat.
    if isinstance(value, bool) or not isinstance(value, int):
        raise VendorUsageConfigurationError(f"{name} must be an int, got {value!r}")
    if value < minimum:
        raise VendorUsageConfigurationError(f"{name} must be >= {minimum}, got {value}")
    return value


def _require_bool(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise VendorUsageConfigurationError(f"{name} must be a bool, got {value!r}")
    return value


def _normalise_use_case(value: object, name: str) -> str:
    return _require_non_empty(value, name).upper()


def _normalise_allowed_use_cases(value: object) -> FrozenSet[str]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
        raise VendorUsageConfigurationError(
            f"allowed_use_cases must be a list of use case strings, got {value!r}")
    return frozenset(_normalise_use_case(u, "allowed_use_cases entry") for u in value)


def _parse_expiration(value: Optional[str]) -> Optional[date]:
    if value is None:
        return None
    text = _require_non_empty(value, "contract_expiration_date")
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise VendorUsageConfigurationError(
            f"contract_expiration_date must be an ISO-8601 date (YYYY-MM-DD), got {value!r}"
        ) from exc


@dataclass
class VendorContractSpec:
    """The licensed scope of one vendor contract, as executed.

    Every field is an assertion the compliance owner must be able to support from
    the signed agreement and its schedules. The engine enforces what it is told;
    it cannot read the contract for you.
    """

    vendor_id: str
    vendor_name: str
    license_tier: str                        # e.g. 'ENTERPRISE_BPIPE', 'RESEARCH_DESKTOP', 'REFINITIV_DACS'
    allowed_use_cases: List[str]             # e.g. ['INTERNAL_RESEARCH', 'NON_DISPLAY_TRADING']
    is_non_display_allowed: bool
    is_redistribution_allowed: bool
    max_concurrent_entitlements: int
    current_active_entitlements: int = 0
    #: ISO-8601 (YYYY-MM-DD) expiry of the licensed term. ``None`` means "not
    #: tracked here" -- the engine will not gate on expiry and says so once per
    #: contract in the log. It deliberately does NOT default to a placeholder
    #: date: a hard-coded future date silently becomes a firm-wide outage on the
    #: day it passes, and silently authorises access until then.
    contract_expiration_date: Optional[str] = None


@dataclass
class DataAccessRequest:
    """One system's request to consume vendor data for a stated purpose."""

    request_id: str
    vendor_id: str
    requested_by_system: str
    use_case_type: str                       # see USE_CASE_* constants
    is_external_redistribution: bool
    requested_seats: int = 1


@dataclass
class VendorUsageAuditReport:
    """The auditable record of one access decision.

    ``applied_policy`` carries the human-readable reason and is intended to be
    persisted verbatim: it is the evidence a vendor or exchange auditor asks for.
    """

    request_id: str
    vendor_id: str
    is_approved: bool
    status: str                              # see ALL_STATUSES
    applied_policy: str
    active_entitlements_remaining: int
    #: Purpose the decision was actually made against, after normalisation.
    evaluated_use_case: str = ""
    #: Date the decision was evaluated against, ISO-8601.
    evaluated_on: str = ""
    #: True when the request was treated as external redistribution, whether that
    #: came from the boolean flag or from the use case itself.
    treated_as_external_redistribution: bool = False


class VendorUsageRestrictionEngine:
    """Fail-closed gate enforcing vendor contractual usage scope.

    Decision order, most fundamental constraint first::

        contract expired -> external redistribution -> non-display ->
        use case in licensed scope -> concurrency headroom

    Expiry is checked first because an expired term withdraws every other
    permission; redistribution is checked before use-case scope because it is the
    breach with the largest and longest-tailed financial exposure.

    Approval **reserves** ``requested_seats`` against the contract. Callers must
    return them with :meth:`release_entitlement` when the consuming system
    disconnects, or the contract will drift into false CONCURRENCY_CAP_EXCEEDED
    denials. Denials never mutate contract state.

    Thread safety: all contract state is guarded by a re-entrant lock, so
    concurrent request handlers cannot both pass the concurrency check and
    over-allocate the same last seat.
    """

    def __init__(self, audit_log_capacity: int = DEFAULT_AUDIT_LOG_CAPACITY) -> None:
        _require_int(audit_log_capacity, "audit_log_capacity", minimum=1)
        self._lock = threading.RLock()
        #: Registered contracts keyed by vendor_id. This is the single source of
        #: truth: expiry and licensed scope are re-derived from it on every
        #: request rather than cached at registration, so a contract mutated in
        #: place cannot silently diverge from what is actually enforced.
        self.contracts: Dict[str, VendorContractSpec] = {}
        self._untracked_expiry_warned: Set[str] = set()
        #: In-memory inspection buffer of recent decisions, newest last. This is a
        #: debugging and monitoring aid, NOT the system of record: a Nasdaq Final
        #: Audit can reach back three years (GDA s.7(e)), which no bounded
        #: in-process buffer can satisfy. Persist reports durably as they are
        #: returned.
        self._audit_log: Deque[VendorUsageAuditReport] = deque(maxlen=audit_log_capacity)

    # -- registration ---------------------------------------------------------

    def register_contract(self, contract: VendorContractSpec, *, replace: bool = False) -> None:
        """Validate and register a vendor contract scope.

        Re-registering an existing ``vendor_id`` is refused unless ``replace`` is
        True, because a silent overwrite resets ``current_active_entitlements``
        and loses the count of seats already handed out.
        """
        if not isinstance(contract, VendorContractSpec):
            raise VendorUsageConfigurationError(
                f"contract must be a VendorContractSpec, got {type(contract).__name__}")

        vendor_id = _require_non_empty(contract.vendor_id, "vendor_id")
        _require_non_empty(contract.vendor_name, "vendor_name")
        _require_non_empty(contract.license_tier, "license_tier")
        _require_bool(contract.is_non_display_allowed, "is_non_display_allowed")
        _require_bool(contract.is_redistribution_allowed, "is_redistribution_allowed")

        max_seats = _require_int(
            contract.max_concurrent_entitlements, "max_concurrent_entitlements", minimum=0)
        active = _require_int(
            contract.current_active_entitlements, "current_active_entitlements", minimum=0)
        if active > max_seats:
            raise VendorUsageConfigurationError(
                f"current_active_entitlements ({active}) exceeds "
                f"max_concurrent_entitlements ({max_seats}) for {vendor_id}")

        allowed = _normalise_allowed_use_cases(contract.allowed_use_cases)
        if not allowed:
            raise VendorUsageConfigurationError(
                f"allowed_use_cases must not be empty for {vendor_id}: a contract licensing "
                "no use case would deny every request")

        # A contract listing EXTERNAL_REDISTRIBUTION but not permitting it is
        # self-contradictory, and the contradiction resolves silently in favour
        # of denial. Surface it at registration instead.
        if USE_CASE_EXTERNAL_REDISTRIBUTION in allowed and not contract.is_redistribution_allowed:
            raise VendorUsageConfigurationError(
                f"{vendor_id}: allowed_use_cases lists EXTERNAL_REDISTRIBUTION but "
                "is_redistribution_allowed is False")
        if USE_CASE_NON_DISPLAY_TRADING in allowed and not contract.is_non_display_allowed:
            raise VendorUsageConfigurationError(
                f"{vendor_id}: allowed_use_cases lists NON_DISPLAY_TRADING but "
                "is_non_display_allowed is False")

        expiration = _parse_expiration(contract.contract_expiration_date)

        with self._lock:
            if vendor_id in self.contracts and not replace:
                raise VendorUsageConfigurationError(
                    f"Vendor contract {vendor_id} already registered; pass replace=True to "
                    "supersede it (this discards the current active entitlement count)")
            self.contracts[vendor_id] = contract
            self._untracked_expiry_warned.discard(vendor_id)

        logger.info(
            "Registered vendor contract %s (%s, tier=%s): seats=%d/%d, non_display=%s, "
            "redistribution=%s, expires=%s",
            vendor_id, contract.vendor_name, contract.license_tier, active, max_seats,
            contract.is_non_display_allowed, contract.is_redistribution_allowed,
            expiration.isoformat() if expiration else "UNTRACKED")

    # -- evaluation -----------------------------------------------------------

    def evaluate_access_request(
        self,
        req: DataAccessRequest,
        *,
        as_of_date: Optional[date] = None,
    ) -> VendorUsageAuditReport:
        """Audit one data access request against the registered contract scope.

        On approval the requested seats are reserved against the contract. On any
        denial contract state is left untouched.

        Args:
            req: the request to evaluate.
            as_of_date: date to test contract expiry against. Defaults to today;
                pass explicitly for reproducible audit output.

        Raises:
            VendorUsageConfigurationError: the vendor is not registered, or the
                request is structurally invalid. A ``ValueError`` subclass, so
                existing ``except ValueError`` handlers still catch it.
        """
        if not isinstance(req, DataAccessRequest):
            raise VendorUsageConfigurationError(
                f"req must be a DataAccessRequest, got {type(req).__name__}")

        request_id = _require_non_empty(req.request_id, "request_id")
        vendor_id = _require_non_empty(req.vendor_id, "vendor_id")
        _require_non_empty(req.requested_by_system, "requested_by_system")
        use_case = _normalise_use_case(req.use_case_type, "use_case_type")
        _require_bool(req.is_external_redistribution, "is_external_redistribution")
        # Zero-seat requests are rejected rather than silently approved: an
        # approval that reserves nothing still grants access, and a negative
        # value would *credit* seats back on approval and corrupt the count.
        seats = _require_int(req.requested_seats, "requested_seats", minimum=1)

        evaluated_on = as_of_date if as_of_date is not None else date.today()
        if not isinstance(evaluated_on, date):
            raise VendorUsageConfigurationError(
                f"as_of_date must be a datetime.date, got {as_of_date!r}")

        # A caller may name the use case without setting the flag, or vice versa.
        # Either signal alone is treated as external redistribution: this is the
        # breach with three-year back-fee exposure, so it fails closed.
        is_redistribution = req.is_external_redistribution or (
            use_case == USE_CASE_EXTERNAL_REDISTRIBUTION)

        with self._lock:
            if vendor_id not in self.contracts:
                raise VendorUsageConfigurationError(
                    f"Vendor contract for {vendor_id} not registered.")

            contract = self.contracts[vendor_id]
            # Re-derived per request, not cached: a contract edited in place after
            # registration must change what is enforced, or fail loudly if the
            # edit made it invalid -- never be silently ignored.
            expiration = _parse_expiration(contract.contract_expiration_date)
            allowed = _normalise_allowed_use_cases(contract.allowed_use_cases)

            def _deny(status: str, msg: str) -> VendorUsageAuditReport:
                return self._finalise(VendorUsageAuditReport(
                    request_id=request_id, vendor_id=vendor_id, is_approved=False,
                    status=status, applied_policy=msg,
                    active_entitlements_remaining=self._remaining(contract),
                    evaluated_use_case=use_case,
                    evaluated_on=evaluated_on.isoformat(),
                    treated_as_external_redistribution=is_redistribution,
                ))

            # 0. Contract term. An expired term withdraws every other permission.
            if expiration is None:
                if vendor_id not in self._untracked_expiry_warned:
                    self._untracked_expiry_warned.add(vendor_id)
                    logger.warning(
                        "Contract %s (%s) has no contract_expiration_date; expiry is NOT being "
                        "enforced for this vendor.", vendor_id, contract.vendor_name)
            elif evaluated_on > expiration:
                msg = (f"CONTRACT EXPIRED [{contract.vendor_name}]: licensed term ended "
                       f"{expiration.isoformat()}, evaluated {evaluated_on.isoformat()}.")
                logger.critical(msg)
                return _deny(STATUS_CONTRACT_EXPIRED, msg)

            # 1. External redistribution.
            if is_redistribution and not contract.is_redistribution_allowed:
                msg = (f"REDISTRIBUTION VIOLATION [{contract.vendor_name}]: external "
                       f"redistribution NOT permitted under license {contract.license_tier}.")
                logger.critical(msg)
                return _deny(STATUS_REDISTRIBUTION_VIOLATION, msg)

            # 2. Non-display (automated, machine-consumed) usage.
            if use_case == USE_CASE_NON_DISPLAY_TRADING and not contract.is_non_display_allowed:
                msg = (f"NON-DISPLAY VIOLATION [{contract.vendor_name}]: algorithmic "
                       f"non-display trading NOT permitted under license {contract.license_tier}.")
                logger.error(msg)
                return _deny(STATUS_NON_DISPLAY_VIOLATION, msg)

            # 3. Use case inside the licensed scope.
            if use_case not in allowed:
                msg = (f"UNAUTHORIZED USE CASE [{contract.vendor_name}]: use case {use_case} "
                       f"not in licensed scope {sorted(allowed)}.")
                logger.error(msg)
                return _deny(STATUS_UNAUTHORIZED_USE_CASE, msg)

            # 4. Concurrency headroom.
            projected = contract.current_active_entitlements + seats
            if projected > contract.max_concurrent_entitlements:
                msg = (f"CONCURRENCY EXCEEDED [{contract.vendor_name}]: {projected} seats "
                       f"requested in total > cap {contract.max_concurrent_entitlements}.")
                logger.warning(msg)
                return _deny(STATUS_CONCURRENCY_CAP_EXCEEDED, msg)

            contract.current_active_entitlements = projected
            remaining = self._remaining(contract)
            logger.info(
                "DATA ACCESS APPROVED [%s]: system=%s, use_case=%s, seats=%d, remaining=%d.",
                contract.vendor_name, req.requested_by_system, use_case, seats, remaining)

            return self._finalise(VendorUsageAuditReport(
                request_id=request_id,
                vendor_id=vendor_id,
                is_approved=True,
                status=STATUS_APPROVED,
                applied_policy=("Compliant with vendor contractual usage scope, licensed term "
                                "and concurrency limits."),
                active_entitlements_remaining=remaining,
                evaluated_use_case=use_case,
                evaluated_on=evaluated_on.isoformat(),
                treated_as_external_redistribution=is_redistribution,
            ))

    # -- entitlement lifecycle ------------------------------------------------

    def release_entitlement(self, vendor_id: str, seats: int = 1) -> int:
        """Return previously approved seats to the contract; returns seats remaining.

        Without this, every approval permanently consumes headroom and the
        contract drifts into denying compliant requests. Releasing more seats
        than are outstanding is refused rather than clamped, because it means the
        caller's accounting has diverged from the engine's.
        """
        vendor_id = _require_non_empty(vendor_id, "vendor_id")
        seats = _require_int(seats, "seats", minimum=1)

        with self._lock:
            if vendor_id not in self.contracts:
                raise VendorUsageConfigurationError(
                    f"Vendor contract for {vendor_id} not registered.")
            contract = self.contracts[vendor_id]
            if seats > contract.current_active_entitlements:
                raise VendorUsageConfigurationError(
                    f"Cannot release {seats} seats for {vendor_id}: only "
                    f"{contract.current_active_entitlements} are currently reserved")
            contract.current_active_entitlements -= seats
            remaining = self._remaining(contract)

        logger.info("Released %d seat(s) for %s; remaining=%d.", seats, vendor_id, remaining)
        return remaining

    def get_audit_trail(self) -> List[VendorUsageAuditReport]:
        """Snapshot of the in-memory decision buffer, oldest first.

        Bounded by ``audit_log_capacity``; see :class:`VendorUsageRestrictionEngine`
        for why this cannot serve as the retained audit record.
        """
        with self._lock:
            return list(self._audit_log)

    # -- internals ------------------------------------------------------------

    @staticmethod
    def _remaining(contract: VendorContractSpec) -> int:
        return max(0, contract.max_concurrent_entitlements - contract.current_active_entitlements)

    def _finalise(self, report: VendorUsageAuditReport) -> VendorUsageAuditReport:
        self._audit_log.append(report)
        return report
