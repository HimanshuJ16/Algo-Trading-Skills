"""
cross-strategy-signal-reuse-and-licensing: alpha marketplace governance —
signal catalog registration, AUM capacity entitlement gating, internal
licence fee attribution, and audit reporting.

Fail-closed design: capacity is the only control standing between a signal
and self-cannibalisation, so every input that could silently disable it
(NaN, infinity, negative AUM, a duplicate subscription id that would
overwrite a live entitlement) is REJECTED loudly rather than absorbed.

Scope boundary — this module computes and records a fee under a licence
schedule that a human has already negotiated and benchmarked. It does NOT
determine, test, or certify that the schedule is arm's length. That is a
transfer pricing analysis (OECD TPG 2022 Chapters I, VI and VII) requiring
a comparability study; see ``PRICING_BASIS_NOTE`` and
``references/standards.md``.
"""
from dataclasses import dataclass, field, replace
import logging
import math
import threading
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Relative tolerance applied to the capacity comparison so that a projection
# landing exactly on the cap after float accumulation is treated as within
# capacity. The documented rule is sum(AUM) <= capacity.
CAPACITY_REL_TOLERANCE = 1e-9

# Why a proprietary alpha signal cannot use the OECD simplified services
# approach. Verified against OECD TPG 2022 Chapter VII: para 7.45 defines
# low value-adding intra-group services as supportive, NOT part of the core
# business, and neither using nor creating unique and valuable intangibles;
# para 7.47 explicitly excludes services constituting the core business of
# the MNE group and research and development services; para 7.61 is the 5%
# mark-up that applies ONLY inside that simplified approach.
PRICING_BASIS_NOTE = (
    "Negotiated intra-group licence of a unique and valuable intangible. "
    "NOT eligible for the OECD TPG 2022 Chapter VII simplified approach for "
    "low value-adding intra-group services (excluded by paras 7.45 and 7.47 "
    "as core-business / R&D / unique-intangible), so the 5% mark-up of para "
    "7.61 does not apply. Price must be supported by a comparability "
    "analysis under Chapters I and VI (including DEMPE)."
)


class SignalLicensingError(Exception):
    """Base class for licensing-engine failures."""


class UnknownSignalError(SignalLicensingError, KeyError):
    """Raised when a signal id is not present in the catalog."""


class UnknownSubscriptionError(SignalLicensingError, KeyError):
    """Raised when a subscription id is not present in the register."""


class DuplicateRegistrationError(SignalLicensingError):
    """Raised when registering over an existing signal or subscription id
    without explicitly opting in. Silently overwriting a signal would
    retroactively re-price every live subscription; silently overwriting a
    subscription id would erase another pod's entitlement record while its
    AUM is still being counted against capacity."""


def _validate_amount(name: str, value: float, allow_zero: bool = True) -> float:
    """Reject non-numeric, NaN, infinite and negative monetary amounts.

    NaN is the dangerous case: ``nan > cap`` is False, so an unchecked NaN
    AUM would be GRANTED and would then poison the capacity sum forever,
    permanently disabling the cap for every later request.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a real number, got {value!r}")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite, got {value!r}")
    if value < 0 or (value == 0 and not allow_zero):
        raise ValueError(
            f"{name} must be {'non-negative' if allow_zero else 'positive'}, got {value!r}"
        )
    return value


def _validate_identifier(name: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string, got {value!r}")
    return value


@dataclass
class SignalProfile:
    """Licensing terms and capacity cap for one reusable alpha signal."""

    signal_id: str
    signal_name: str
    owner_entity: str                  # e.g. 'US_Quant_Research_Lab'
    base_license_fee_annual_usd: float
    pnl_share_pct: float               # e.g. 0.05 (5% share of strategy PnL)
    max_aum_capacity_usd: float        # Maximum total AUM permitted across all consumers

    def __post_init__(self) -> None:
        _validate_identifier("signal_id", self.signal_id)
        _validate_identifier("signal_name", self.signal_name)
        _validate_identifier("owner_entity", self.owner_entity)
        self.base_license_fee_annual_usd = _validate_amount(
            "base_license_fee_annual_usd", self.base_license_fee_annual_usd
        )
        self.max_aum_capacity_usd = _validate_amount(
            "max_aum_capacity_usd", self.max_aum_capacity_usd, allow_zero=False
        )
        if (
            isinstance(self.pnl_share_pct, bool)
            or not isinstance(self.pnl_share_pct, (int, float))
            or not math.isfinite(self.pnl_share_pct)
            or not 0.0 <= self.pnl_share_pct <= 1.0
        ):
            raise ValueError(
                "pnl_share_pct must be a fraction in [0, 1] (0.05 == 5%), "
                f"got {self.pnl_share_pct!r}"
            )
        self.pnl_share_pct = float(self.pnl_share_pct)


@dataclass
class StrategySubscription:
    """A consumer pod's request to trade capital against a licensed signal."""

    subscription_id: str
    strategy_id: str
    signal_id: str
    consumer_entity: str               # e.g. 'UK_StatArb_Desk'
    allocated_aum_usd: float
    is_active: bool = True

    def __post_init__(self) -> None:
        _validate_identifier("subscription_id", self.subscription_id)
        _validate_identifier("strategy_id", self.strategy_id)
        _validate_identifier("signal_id", self.signal_id)
        _validate_identifier("consumer_entity", self.consumer_entity)
        self.allocated_aum_usd = _validate_amount(
            "allocated_aum_usd", self.allocated_aum_usd
        )
        if not isinstance(self.is_active, bool):
            raise ValueError(f"is_active must be a bool, got {self.is_active!r}")


@dataclass
class FeeAttributionReport:
    """Fee computed under an already-negotiated licence schedule.

    ``arm_length_documented`` records only whether a benchmarking reference
    was supplied alongside the calculation. It is a documentation-presence
    flag, NOT an assertion that the price is arm's length — no code in this
    module performs a comparability analysis.
    """

    subscription_id: str
    strategy_id: str
    signal_id: str
    owner_entity: str
    consumer_entity: str
    is_cross_entity: bool
    base_fee_usd: float
    pnl_share_pct: float
    gross_pnl_usd: float
    loss_carryforward_applied_usd: float
    shareable_pnl_usd: float
    pnl_share_fee_usd: float
    total_fee_usd: float
    remaining_loss_carryforward_usd: float
    pricing_basis: str
    benchmarking_evidence_ref: Optional[str]
    arm_length_documented: bool


@dataclass
class EntitlementCheckResult:
    signal_id: str
    strategy_id: str
    is_entitled: bool
    current_total_subscribed_aum_usd: float
    max_aum_capacity_usd: float
    reason: str


@dataclass
class SignalLicensingAuditReport:
    """Point-in-time entitlement and capacity state for one signal."""

    signal_id: str
    signal_name: str
    owner_entity: str
    max_aum_capacity_usd: float
    total_subscribed_aum_usd: float
    capacity_utilisation_pct: float
    remaining_capacity_usd: float
    active_subscription_ids: List[str] = field(default_factory=list)
    revoked_subscription_ids: List[str] = field(default_factory=list)
    consumer_entities: List[str] = field(default_factory=list)
    pricing_basis: str = PRICING_BASIS_NOTE


class SignalReuseAndLicensingEngine:
    """
    Alpha marketplace governance engine for managing cross-strategy signal
    reuse, entitlement access control, internal licence fee attribution, and
    AUM capacity limits.

    Capacity convention: ``sum(allocated_aum_usd)`` over ACTIVE subscriptions
    must stay at or below ``max_aum_capacity_usd``. Revoking a subscription
    releases its AUM; revoked subscriptions are retained for audit but stop
    consuming capacity and stop accruing fees.

    Thread safety: catalog mutation, entitlement checks and fee calculation
    are serialised internally, so two concurrent subscription requests cannot
    each pass against the same headroom and jointly breach the cap. Caller
    state (the pod's own capital allocation records) is not protected.
    """

    def __init__(self) -> None:
        self.signals: Dict[str, SignalProfile] = {}
        self.subscriptions: Dict[str, StrategySubscription] = {}
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # Catalog
    # ------------------------------------------------------------------
    def register_signal(self, signal: SignalProfile, replace: bool = False) -> None:
        """Register licensing terms for a signal.

        Re-registering an existing ``signal_id`` requires ``replace=True``:
        an unnoticed overwrite silently re-prices and re-caps every live
        subscription against that signal.
        """
        if not isinstance(signal, SignalProfile):
            raise TypeError(f"signal must be a SignalProfile, got {type(signal).__name__}")
        with self._lock:
            if signal.signal_id in self.signals:
                if not replace:
                    raise DuplicateRegistrationError(
                        f"Signal {signal.signal_id} is already registered. Pass "
                        f"replace=True to re-price it; existing subscriptions will "
                        f"need re-evaluation against the new terms."
                    )
                existing_aum = self._active_aum(signal.signal_id)
                logger.warning(
                    f"Signal {signal.signal_id} re-registered: capacity now "
                    f"${signal.max_aum_capacity_usd:,.0f} against ${existing_aum:,.0f} "
                    f"already subscribed. Re-run entitlement review."
                )
            self.signals[signal.signal_id] = signal
            logger.info(f"Signal {signal.signal_id} registered by {signal.owner_entity}.")

    def _active_aum(self, signal_id: str) -> float:
        return sum(
            s.allocated_aum_usd
            for s in self.subscriptions.values()
            if s.signal_id == signal_id and s.is_active
        )

    def get_subscribed_aum(self, signal_id: str) -> float:
        """Total ACTIVE subscribed AUM currently consuming this signal's capacity."""
        with self._lock:
            if signal_id not in self.signals:
                raise UnknownSignalError(f"Unknown signal ID {signal_id}")
            return self._active_aum(signal_id)

    # ------------------------------------------------------------------
    # Entitlement
    # ------------------------------------------------------------------
    def request_subscription(self, subscription: StrategySubscription) -> EntitlementCheckResult:
        """
        Audit a strategy subscription request against signal AUM capacity.

        A denied request is NOT recorded, so a denial never consumes capacity.
        An inactive request is rejected outright: recording an entitlement that
        is already revoked produces an audit trail that disagrees with reality.
        """
        if not isinstance(subscription, StrategySubscription):
            raise TypeError(
                f"subscription must be a StrategySubscription, got {type(subscription).__name__}"
            )
        with self._lock:
            if subscription.signal_id not in self.signals:
                logger.error(
                    f"ENTITLEMENT DENIED for {subscription.strategy_id}: "
                    f"unknown signal {subscription.signal_id}."
                )
                return EntitlementCheckResult(
                    signal_id=subscription.signal_id,
                    strategy_id=subscription.strategy_id,
                    is_entitled=False,
                    current_total_subscribed_aum_usd=0.0,
                    max_aum_capacity_usd=0.0,
                    reason=f"Unknown signal ID {subscription.signal_id}",
                )

            if subscription.subscription_id in self.subscriptions:
                raise DuplicateRegistrationError(
                    f"Subscription ID {subscription.subscription_id} already exists "
                    f"(strategy {self.subscriptions[subscription.subscription_id].strategy_id}). "
                    f"Revoke it before re-subscribing; ids must be unique per grant."
                )

            signal = self.signals[subscription.signal_id]

            if not subscription.is_active:
                logger.error(
                    f"ENTITLEMENT DENIED for {subscription.strategy_id} on "
                    f"{subscription.signal_id}: request submitted with is_active=False."
                )
                return EntitlementCheckResult(
                    signal_id=subscription.signal_id,
                    strategy_id=subscription.strategy_id,
                    is_entitled=False,
                    current_total_subscribed_aum_usd=self._active_aum(subscription.signal_id),
                    max_aum_capacity_usd=signal.max_aum_capacity_usd,
                    reason="Subscription request submitted as inactive; not recorded.",
                )

            existing_aum = self._active_aum(subscription.signal_id)
            projected_aum = existing_aum + subscription.allocated_aum_usd
            capacity_limit = signal.max_aum_capacity_usd * (1.0 + CAPACITY_REL_TOLERANCE)

            if projected_aum > capacity_limit:
                logger.error(
                    f"ENTITLEMENT DENIED for {subscription.strategy_id} on {subscription.signal_id}: "
                    f"Projected AUM ${projected_aum:,.0f} exceeds max capacity "
                    f"${signal.max_aum_capacity_usd:,.0f}!"
                )
                return EntitlementCheckResult(
                    signal_id=subscription.signal_id,
                    strategy_id=subscription.strategy_id,
                    is_entitled=False,
                    current_total_subscribed_aum_usd=existing_aum,
                    max_aum_capacity_usd=signal.max_aum_capacity_usd,
                    reason=(
                        f"AUM capacity cap breached (${projected_aum:,.0f} > "
                        f"${signal.max_aum_capacity_usd:,.0f})."
                    ),
                )

            # Store a copy: if the caller mutated the request object after the
            # grant, the stored allocated_aum_usd would change underneath the
            # capacity sum without ever passing an entitlement check.
            self.subscriptions[subscription.subscription_id] = replace(subscription)
            logger.info(
                f"ENTITLEMENT GRANTED for {subscription.strategy_id} on {subscription.signal_id} "
                f"(${subscription.allocated_aum_usd:,.0f}; ${projected_aum:,.0f} of "
                f"${signal.max_aum_capacity_usd:,.0f} capacity used)."
            )
            return EntitlementCheckResult(
                signal_id=subscription.signal_id,
                strategy_id=subscription.strategy_id,
                is_entitled=True,
                current_total_subscribed_aum_usd=projected_aum,
                max_aum_capacity_usd=signal.max_aum_capacity_usd,
                reason="Subscription approved and active.",
            )

    def revoke_subscription(self, subscription_id: str) -> StrategySubscription:
        """Deactivate a subscription, releasing its AUM back to signal capacity.

        The record is retained (``is_active=False``) rather than deleted so the
        entitlement history stays auditable. Revoking twice is idempotent.
        """
        with self._lock:
            if subscription_id not in self.subscriptions:
                raise UnknownSubscriptionError(f"Unknown subscription ID {subscription_id}")
            sub = self.subscriptions[subscription_id]
            if sub.is_active:
                sub.is_active = False
                logger.info(
                    f"ENTITLEMENT REVOKED for {sub.strategy_id} on {sub.signal_id}; "
                    f"${sub.allocated_aum_usd:,.0f} released to capacity."
                )
            return sub

    # ------------------------------------------------------------------
    # Fee attribution
    # ------------------------------------------------------------------
    def calculate_transfer_pricing_fee(
        self,
        subscription_id: str,
        strategy_realized_pnl_usd: float,
        loss_carryforward_usd: float = 0.0,
        benchmarking_evidence_ref: Optional[str] = None,
    ) -> FeeAttributionReport:
        """
        Apply the negotiated licence schedule to a period's realized PnL.

            shareable = max(0, realized_pnl - loss_carryforward)
            fee       = base_fee + pnl_share_pct * shareable

        ``loss_carryforward_usd`` is the unrecouped loss brought forward from
        prior periods (a high-water mark expressed as a positive number).
        Without it, a pod that loses $10M and then makes $1M pays a share on
        the full $1M — a term no unrelated licensee would accept, which is
        precisely the kind of divergence a transfer pricing review targets.
        The report returns ``remaining_loss_carryforward_usd`` to roll forward.

        This computes a fee; it does not establish that the schedule is arm's
        length. Pass ``benchmarking_evidence_ref`` (a comparability study or
        intercompany agreement reference) to record the supporting evidence.

        Raises:
            ValueError: non-finite PnL, or a negative loss carryforward.
            UnknownSubscriptionError: subscription id not in the register.
            UnknownSignalError: the underlying signal is no longer cataloged.
            SignalLicensingError: the subscription has been revoked.
        """
        if (
            isinstance(strategy_realized_pnl_usd, bool)
            or not isinstance(strategy_realized_pnl_usd, (int, float))
            or not math.isfinite(strategy_realized_pnl_usd)
        ):
            raise ValueError(
                f"strategy_realized_pnl_usd must be a finite real number, "
                f"got {strategy_realized_pnl_usd!r}"
            )
        pnl = float(strategy_realized_pnl_usd)
        carryforward = _validate_amount("loss_carryforward_usd", loss_carryforward_usd)
        if benchmarking_evidence_ref is not None and (
            not isinstance(benchmarking_evidence_ref, str) or not benchmarking_evidence_ref.strip()
        ):
            raise ValueError(
                f"benchmarking_evidence_ref must be a non-empty string or None, "
                f"got {benchmarking_evidence_ref!r}"
            )

        with self._lock:
            if subscription_id not in self.subscriptions:
                raise UnknownSubscriptionError(f"Unknown subscription ID {subscription_id}")
            sub = self.subscriptions[subscription_id]
            if not sub.is_active:
                raise SignalLicensingError(
                    f"Subscription {subscription_id} is revoked; billing a revoked "
                    f"entitlement overstates the intercompany charge."
                )
            if sub.signal_id not in self.signals:
                raise UnknownSignalError(
                    f"Signal {sub.signal_id} for subscription {subscription_id} is no "
                    f"longer registered; fee terms are unknown."
                )
            signal = self.signals[sub.signal_id]

            base_fee = signal.base_license_fee_annual_usd
            net_pnl = pnl - carryforward
            shareable_pnl = max(0.0, net_pnl)
            applied_carryforward = min(carryforward, max(0.0, pnl))
            remaining_carryforward = max(0.0, -net_pnl)
            pnl_fee = signal.pnl_share_pct * shareable_pnl
            total_fee = base_fee + pnl_fee

            has_evidence = benchmarking_evidence_ref is not None
            if not has_evidence:
                logger.warning(
                    f"Fee attributed for {sub.strategy_id} on {sub.signal_id} with no "
                    f"benchmarking_evidence_ref; the arm's-length basis is undocumented."
                )

            return FeeAttributionReport(
                subscription_id=subscription_id,
                strategy_id=sub.strategy_id,
                signal_id=sub.signal_id,
                owner_entity=signal.owner_entity,
                consumer_entity=sub.consumer_entity,
                is_cross_entity=(signal.owner_entity != sub.consumer_entity),
                base_fee_usd=round(base_fee, 2),
                pnl_share_pct=signal.pnl_share_pct,
                gross_pnl_usd=round(pnl, 2),
                loss_carryforward_applied_usd=round(applied_carryforward, 2),
                shareable_pnl_usd=round(shareable_pnl, 2),
                pnl_share_fee_usd=round(pnl_fee, 2),
                total_fee_usd=round(total_fee, 2),
                remaining_loss_carryforward_usd=round(remaining_carryforward, 2),
                pricing_basis=PRICING_BASIS_NOTE,
                benchmarking_evidence_ref=benchmarking_evidence_ref,
                arm_length_documented=has_evidence,
            )

    # ------------------------------------------------------------------
    # Audit
    # ------------------------------------------------------------------
    def generate_audit_report(self, signal_id: str) -> SignalLicensingAuditReport:
        """Produce the point-in-time entitlement and capacity record for a signal."""
        with self._lock:
            if signal_id not in self.signals:
                raise UnknownSignalError(f"Unknown signal ID {signal_id}")
            signal = self.signals[signal_id]
            active = [
                s for s in self.subscriptions.values()
                if s.signal_id == signal_id and s.is_active
            ]
            revoked = [
                s.subscription_id for s in self.subscriptions.values()
                if s.signal_id == signal_id and not s.is_active
            ]
            total_aum = sum(s.allocated_aum_usd for s in active)
            return SignalLicensingAuditReport(
                signal_id=signal_id,
                signal_name=signal.signal_name,
                owner_entity=signal.owner_entity,
                max_aum_capacity_usd=signal.max_aum_capacity_usd,
                total_subscribed_aum_usd=round(total_aum, 2),
                capacity_utilisation_pct=round(
                    100.0 * total_aum / signal.max_aum_capacity_usd, 4
                ),
                remaining_capacity_usd=round(
                    max(0.0, signal.max_aum_capacity_usd - total_aum), 2
                ),
                active_subscription_ids=sorted(s.subscription_id for s in active),
                revoked_subscription_ids=sorted(revoked),
                consumer_entities=sorted({s.consumer_entity for s in active}),
            )
