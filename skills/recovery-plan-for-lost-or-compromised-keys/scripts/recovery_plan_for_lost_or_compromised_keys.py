"""
recovery-plan-for-lost-or-compromised-keys: readiness auditor for crypto custody
key-loss and key-compromise recovery plans.

What this module is and is not
------------------------------
It is a **readiness auditor** over a documented recovery plan. It scores what it
is told; it cannot verify that a shard is actually readable, that a sweep wallet
address is actually controlled by you, or that a drill actually reconstructed a
key. Every boolean here is an assertion a reviewer must be able to support with
an artefact (drill report, shard custody log, signed test transaction).

Two distinct failure modes, one plan
------------------------------------
* **Key loss** -- the key material is gone but no adversary holds it. Recovery
  depends entirely on *backups*: enough verified Shamir shards, an HSM seed, or a
  mnemonic. A sweep wallet does nothing for you here.
* **Key compromise** -- an adversary holds, or may hold, the key material.
  Backups are worthless because the adversary can spend from the same key;
  recovery depends on a *pre-configured sweep* to a destination whose key
  material is independent of the compromised key. NIST SP 800-57 Part 1 Rev. 5
  section 5.5.2 calls this re-keying, and requires that a compromise-recovery
  plan "shall be documented and easily accessible".

A plan is only READY when it covers **both**, which is why this engine checks
backup integrity and sweep readiness for every plan rather than branching on an
assumed incident type. An incident does not announce which kind it is at the
moment you need the plan.

Determinism
-----------
``audit_recovery_plans`` takes an ``as_of_date``. It defaults to today only as a
convenience; pass it explicitly so an audit report is reproducible and the trail
shows what was known when.

See ``references/standards.md`` for sourcing, and for which thresholds are
engineering defaults rather than external requirements.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

BACKUP_SHAMIR = "SHAMIR_SSS"
BACKUP_HSM = "HSM_SEED"
BACKUP_MNEMONIC = "MNEMONIC_PHRASE"

#: Backup schemes this engine knows how to reason about. An unrecognised value is
#: reported as a CRITICAL finding rather than raising, because "we have no
#: recognised backup scheme" is a substantive audit result, not a data-entry slip.
VALID_BACKUP_METHODS = frozenset({BACKUP_SHAMIR, BACKUP_HSM, BACKUP_MNEMONIC})

WALLET_HOT = "HOT"
WALLET_WARM = "WARM"
WALLET_COLD = "COLD"
VALID_WALLET_TYPES = frozenset({WALLET_HOT, WALLET_WARM, WALLET_COLD})

SEVERITY_CRITICAL = "CRITICAL"
SEVERITY_HIGH = "HIGH"
SEVERITY_MEDIUM = "MEDIUM"

STATUS_READY = "RECOVERY_PLAN_READY"
STATUS_NOT_READY = "RECOVERY_PLAN_NOT_READY"


class KeyRecoveryPlanError(ValueError):
    """Raised when a plan specification or engine configuration is internally impossible.

    Readiness auditing must fail loudly. A plan claiming 6 verified shards out of a
    5-shard split, or a drill dated in the future, is a data-entry error; auditing
    it anyway produces an authoritative-looking ``RECOVERY_PLAN_READY`` built on
    garbage, which is materially worse than no audit at all.
    """


@dataclass
class RecoveryPlanForLostOrCompromisedKeysConfig:
    """Policy thresholds for the audit.

    None of these numbers is mandated by any regulator or standard. CCSS Level III
    (2.04.2.1) requires the Key Compromise Policy be tested *at least annually*;
    NIST SP 800-53 Rev. 5 CP-4 leaves contingency-plan test frequency
    organisation-defined. The 90-day default is therefore a deliberately stricter
    internal policy, not a compliance floor. Calibrate it and record the calibration.
    """

    enabled: bool = True
    max_days_since_drill: int = 90
    #: Verified shards required *above* the reconstruction threshold, so that
    #: losing one shard between drills does not make the key unrecoverable.
    min_shamir_surplus_shards: int = 1
    #: A 1-of-N split gives every individual shard holder unilateral control,
    #: which defeats the purpose of splitting.
    min_shamir_threshold: int = 2
    #: NIST SP 800-57 Part 1 Rev. 5 section 5.5.2 separates the personnel to
    #: notify (a), to perform recovery (b), and to support recovery (f). A single
    #: named contact is a single point of failure at the worst possible moment.
    min_incident_response_contacts: int = 2
    #: CCSS 1.03.3.2 requires key-material backups in geographically separate
    #: location(s) from the operational key material.
    min_distinct_backup_locations: int = 2

    def validate(self) -> None:
        """Raise :class:`KeyRecoveryPlanError` if the configuration is nonsensical."""
        if self.max_days_since_drill <= 0:
            raise KeyRecoveryPlanError("max_days_since_drill must be positive.")
        if self.min_shamir_surplus_shards < 0:
            raise KeyRecoveryPlanError("min_shamir_surplus_shards must be non-negative.")
        if self.min_shamir_threshold < 1:
            raise KeyRecoveryPlanError("min_shamir_threshold must be at least 1.")
        if self.min_incident_response_contacts < 1:
            raise KeyRecoveryPlanError("min_incident_response_contacts must be at least 1.")
        if self.min_distinct_backup_locations < 1:
            raise KeyRecoveryPlanError("min_distinct_backup_locations must be at least 1.")


@dataclass
class RecoveryPlanSpec:
    """A documented recovery plan for one wallet's key material.

    Counting fields default to ``0`` meaning *not recorded*, which the audit
    reports as a finding. An unrecorded control is not a passing control.
    """

    plan_id: str
    wallet_type: str                                  # HOT | WARM | COLD
    backup_method: str                                # SHAMIR_SSS | HSM_SEED | MNEMONIC_PHRASE
    shamir_threshold: int = 0                         # 't' in t-of-n; SHAMIR_SSS only
    shamir_total_shards: int = 0                      # 'n' in t-of-n; SHAMIR_SSS only
    #: Shards confirmed readable by a keyholder within the current drill cycle.
    #: Shards that exist but have never been read are not verified.
    verified_shards_available: int = 0
    #: Largest number of shards held at any single location or by any single
    #: custodian. If this reaches the threshold, that party can reconstruct the
    #: key alone and the split provides no protection against them.
    max_shards_at_single_location: int = 0
    #: Distinct geographic locations holding backup material (any method).
    distinct_backup_locations: int = 0
    sweep_wallet_configured: bool = False
    #: True only if the sweep destination's key material is derived independently
    #: of the key this plan protects (separate seed, separate device, separate
    #: keyholders). A sweep into a sibling address of the same compromised seed
    #: returns the funds to the adversary.
    sweep_wallet_independently_keyed: bool = False
    #: A signed, confirmed test transaction has reached the sweep destination.
    sweep_wallet_test_transaction_verified: bool = False
    #: NIST SP 800-57 Part 1 Rev. 5 section 5.5.2(d): an inventory of all
    #: cryptographic keys, so re-keying can be monitored across every affected
    #: key (item (h)).
    key_inventory_documented: bool = False
    incident_response_contacts: int = 0
    #: Date of the last full recovery drill. ``None`` means never drilled, which
    #: is reported distinctly from an overdue drill.
    last_drill_date: Optional[date] = None


@dataclass
class PlanIssue:
    plan_id: str
    issue_type: str
    detail: str
    severity: str = SEVERITY_HIGH                     # CRITICAL | HIGH | MEDIUM


@dataclass
class KeyRecoveryReadinessReport:
    total_plans: int
    ready_count: int
    not_ready_count: int
    issues: List[PlanIssue]
    status: str                                       # RECOVERY_PLAN_READY | RECOVERY_PLAN_NOT_READY
    audit_notes: str
    as_of_date: Optional[date] = None
    issues_by_severity: Dict[str, int] = field(default_factory=dict)

    @property
    def critical_issue_count(self) -> int:
        return self.issues_by_severity.get(SEVERITY_CRITICAL, 0)

    @property
    def high_issue_count(self) -> int:
        return self.issues_by_severity.get(SEVERITY_HIGH, 0)


class RecoveryPlanForLostOrCompromisedKeysEngine:
    """Audits crypto custody recovery plans for key-loss and key-compromise readiness.

    Checks, per plan: backup method recognition, Shamir shard sufficiency and
    quorum distribution, geographic separation of backups, emergency sweep
    readiness (configured, independently keyed, test-transacted), key inventory,
    incident-response contact depth, and drill recency.
    """

    def __init__(self, config: Optional[RecoveryPlanForLostOrCompromisedKeysConfig] = None):
        self.config = config or RecoveryPlanForLostOrCompromisedKeysConfig()
        self.config.validate()

    def execute(self) -> bool:
        """Legacy execute method retained for backward compatibility."""
        return True if self.config.enabled else False

    # ------------------------------------------------------------------ #
    # Validation
    # ------------------------------------------------------------------ #
    def _validate_plan(self, plan: RecoveryPlanSpec, as_of_date: date) -> None:
        """Raise on values that cannot describe a real plan.

        Deliberately *not* raised here: an unrecognised ``backup_method``, which is
        a substantive finding rather than an impossibility.
        """
        if not isinstance(plan.plan_id, str) or not plan.plan_id.strip():
            raise KeyRecoveryPlanError("plan_id must be a non-empty string.")
        pid = plan.plan_id

        if not isinstance(plan.backup_method, str) or not plan.backup_method.strip():
            raise KeyRecoveryPlanError(f"{pid}: backup_method must be a non-empty string.")

        if str(plan.wallet_type).upper() not in VALID_WALLET_TYPES:
            raise KeyRecoveryPlanError(
                f"{pid}: wallet_type '{plan.wallet_type}' is not one of "
                f"{sorted(VALID_WALLET_TYPES)}."
            )

        for name in (
            "shamir_threshold", "shamir_total_shards", "verified_shards_available",
            "max_shards_at_single_location", "distinct_backup_locations",
            "incident_response_contacts",
        ):
            value = getattr(plan, name)
            if not isinstance(value, int) or isinstance(value, bool):
                raise KeyRecoveryPlanError(f"{pid}: {name} must be an int, got {value!r}.")
            if value < 0:
                raise KeyRecoveryPlanError(f"{pid}: {name} must be non-negative, got {value}.")

        if plan.backup_method.upper() == BACKUP_SHAMIR:
            if plan.shamir_total_shards < 1:
                raise KeyRecoveryPlanError(
                    f"{pid}: SHAMIR_SSS plan must declare shamir_total_shards >= 1."
                )
            if plan.shamir_threshold < 1:
                raise KeyRecoveryPlanError(
                    f"{pid}: SHAMIR_SSS plan must declare shamir_threshold >= 1."
                )
            if plan.shamir_threshold > plan.shamir_total_shards:
                raise KeyRecoveryPlanError(
                    f"{pid}: shamir_threshold ({plan.shamir_threshold}) exceeds "
                    f"shamir_total_shards ({plan.shamir_total_shards}); the key could "
                    f"never be reconstructed."
                )
            if plan.verified_shards_available > plan.shamir_total_shards:
                raise KeyRecoveryPlanError(
                    f"{pid}: verified_shards_available ({plan.verified_shards_available}) "
                    f"exceeds shamir_total_shards ({plan.shamir_total_shards})."
                )
            if plan.max_shards_at_single_location > plan.shamir_total_shards:
                raise KeyRecoveryPlanError(
                    f"{pid}: max_shards_at_single_location "
                    f"({plan.max_shards_at_single_location}) exceeds shamir_total_shards "
                    f"({plan.shamir_total_shards})."
                )

        if plan.last_drill_date is not None:
            # datetime is a subclass of date, but subtracting one from a date
            # raises, so reject it here with a message that says what to pass.
            if isinstance(plan.last_drill_date, datetime) or not isinstance(
                plan.last_drill_date, date
            ):
                raise KeyRecoveryPlanError(
                    f"{pid}: last_drill_date must be a datetime.date or None, got "
                    f"{type(plan.last_drill_date).__name__}."
                )
            if plan.last_drill_date > as_of_date:
                raise KeyRecoveryPlanError(
                    f"{pid}: last_drill_date ({plan.last_drill_date}) is after the audit "
                    f"date ({as_of_date})."
                )

    # ------------------------------------------------------------------ #
    # Per-plan checks
    # ------------------------------------------------------------------ #
    def _check_backup_integrity(self, plan: RecoveryPlanSpec) -> List[PlanIssue]:
        """Backup-method recognition, Shamir sufficiency, and quorum distribution."""
        issues: List[PlanIssue] = []
        method = plan.backup_method.upper()
        cfg = self.config

        if method not in VALID_BACKUP_METHODS:
            issues.append(PlanIssue(
                plan.plan_id, "INVALID_BACKUP_METHOD",
                f"Backup method '{plan.backup_method}' is not one of "
                f"{sorted(VALID_BACKUP_METHODS)}; recovery from key loss is unproven.",
                SEVERITY_CRITICAL,
            ))

        if method == BACKUP_SHAMIR:
            # Below threshold the key is already unrecoverable; at or above the
            # threshold but short of the surplus it is recoverable today with no
            # tolerance for losing a single shard. These are not the same finding.
            if plan.verified_shards_available < plan.shamir_threshold:
                issues.append(PlanIssue(
                    plan.plan_id, "SHARDS_BELOW_THRESHOLD",
                    f"Only {plan.verified_shards_available} verified shards against a "
                    f"{plan.shamir_threshold}-of-{plan.shamir_total_shards} split; the key "
                    f"cannot currently be reconstructed.",
                    SEVERITY_CRITICAL,
                ))
            elif plan.verified_shards_available < (
                plan.shamir_threshold + cfg.min_shamir_surplus_shards
            ):
                issues.append(PlanIssue(
                    plan.plan_id, "NO_SHARD_SURPLUS",
                    f"{plan.verified_shards_available} verified shards meets the "
                    f"{plan.shamir_threshold}-shard threshold but leaves less than the "
                    f"required surplus of {cfg.min_shamir_surplus_shards}; losing one "
                    f"shard makes the key unrecoverable.",
                    SEVERITY_HIGH,
                ))

            if plan.shamir_threshold < cfg.min_shamir_threshold:
                issues.append(PlanIssue(
                    plan.plan_id, "WEAK_SHAMIR_THRESHOLD",
                    f"Threshold {plan.shamir_threshold} is below the policy minimum "
                    f"{cfg.min_shamir_threshold}; any single shard holder can reconstruct "
                    f"the key unilaterally.",
                    SEVERITY_HIGH,
                ))

            if plan.max_shards_at_single_location == 0:
                issues.append(PlanIssue(
                    plan.plan_id, "SHARD_DISTRIBUTION_UNRECORDED",
                    "max_shards_at_single_location is not recorded; shard co-location "
                    "cannot be ruled out.",
                    SEVERITY_MEDIUM,
                ))
            elif plan.max_shards_at_single_location >= plan.shamir_threshold:
                issues.append(PlanIssue(
                    plan.plan_id, "SHARD_QUORUM_CO_LOCATED",
                    f"A single location holds {plan.max_shards_at_single_location} shards, "
                    f"at or above the {plan.shamir_threshold}-shard threshold; that party "
                    f"can reconstruct the key alone, and losing that one site destroys the "
                    f"quorum.",
                    SEVERITY_CRITICAL,
                ))

        if plan.distinct_backup_locations < cfg.min_distinct_backup_locations:
            issues.append(PlanIssue(
                plan.plan_id, "BACKUP_NOT_GEOGRAPHICALLY_SEPARATED",
                f"{plan.distinct_backup_locations} distinct backup location(s) recorded; "
                f"policy requires {cfg.min_distinct_backup_locations} "
                f"(CCSS 1.03.3.2 geographic separation).",
                SEVERITY_HIGH,
            ))

        return issues

    def _check_sweep_readiness(self, plan: RecoveryPlanSpec) -> List[PlanIssue]:
        """Emergency evacuation path for a compromise event."""
        issues: List[PlanIssue] = []

        if not plan.sweep_wallet_configured:
            issues.append(PlanIssue(
                plan.plan_id, "NO_SWEEP_WALLET",
                "No emergency sweep wallet configured; funds cannot be evacuated during "
                "an active compromise without first provisioning a destination.",
                SEVERITY_CRITICAL,
            ))
            return issues

        if not plan.sweep_wallet_independently_keyed:
            issues.append(PlanIssue(
                plan.plan_id, "SWEEP_WALLET_NOT_INDEPENDENTLY_KEYED",
                "Sweep destination is not confirmed to use key material independent of "
                "the protected key; sweeping into the same seed hands the funds back to "
                "the adversary.",
                SEVERITY_CRITICAL,
            ))

        if not plan.sweep_wallet_test_transaction_verified:
            issues.append(PlanIssue(
                plan.plan_id, "SWEEP_WALLET_UNTESTED",
                "No confirmed test transaction to the sweep destination; an address typo "
                "or wrong-chain destination would only surface mid-incident.",
                SEVERITY_HIGH,
            ))

        return issues

    def _check_incident_response(
        self, plan: RecoveryPlanSpec, as_of_date: date
    ) -> List[PlanIssue]:
        """Documentation and rehearsal elements of the compromise-recovery plan."""
        issues: List[PlanIssue] = []
        cfg = self.config

        if not plan.key_inventory_documented:
            issues.append(PlanIssue(
                plan.plan_id, "KEY_INVENTORY_MISSING",
                "No documented inventory of cryptographic keys (NIST SP 800-57 Pt.1 R5 "
                "section 5.5.2(d)); re-keying cannot be monitored across all affected keys.",
                SEVERITY_HIGH,
            ))

        if plan.incident_response_contacts < cfg.min_incident_response_contacts:
            issues.append(PlanIssue(
                plan.plan_id, "INSUFFICIENT_IR_CONTACTS",
                f"{plan.incident_response_contacts} incident response contact(s) recorded; "
                f"policy requires {cfg.min_incident_response_contacts} so that notification "
                f"and recovery do not depend on one reachable person.",
                SEVERITY_HIGH,
            ))

        if plan.last_drill_date is None:
            issues.append(PlanIssue(
                plan.plan_id, "DRILL_NEVER_CONDUCTED",
                "No recovery drill has ever been recorded; the backup is untested and its "
                "recoverability is unknown.",
                SEVERITY_CRITICAL,
            ))
        else:
            days = (as_of_date - plan.last_drill_date).days
            if days > cfg.max_days_since_drill:
                issues.append(PlanIssue(
                    plan.plan_id, "DRILL_OVERDUE",
                    f"Last drill {plan.last_drill_date} was {days} days before "
                    f"{as_of_date} (policy maximum {cfg.max_days_since_drill} days).",
                    SEVERITY_HIGH,
                ))

        return issues

    # ------------------------------------------------------------------ #
    # Audit
    # ------------------------------------------------------------------ #
    def audit_recovery_plans(
        self,
        plans: Sequence[RecoveryPlanSpec],
        as_of_date: Optional[date] = None,
    ) -> KeyRecoveryReadinessReport:
        """Audit every plan for key-loss and key-compromise recovery readiness.

        Args:
            plans: Recovery plans to audit. Plan IDs must be unique within the batch.
            as_of_date: Audit date used for drill recency. Defaults to today; pass it
                explicitly for reproducible output.

        Returns:
            A :class:`KeyRecoveryReadinessReport`. ``RECOVERY_PLAN_READY`` requires
            at least one plan and zero issues of any severity.

        Raises:
            KeyRecoveryPlanError: if any plan is internally impossible (see
                :meth:`_validate_plan`) or a plan_id is duplicated in the batch.
        """
        if isinstance(plans, RecoveryPlanSpec):
            raise KeyRecoveryPlanError(
                "audit_recovery_plans expects a sequence of RecoveryPlanSpec; pass "
                "[plan] rather than a single plan."
            )

        as_of = as_of_date or date.today()
        if as_of_date is None:
            logger.debug(
                "audit_recovery_plans called without as_of_date; defaulting to %s. "
                "Pass it explicitly for a reproducible audit trail.", as_of
            )

        seen_ids = set()
        for plan in plans:
            self._validate_plan(plan, as_of)
            if plan.plan_id in seen_ids:
                raise KeyRecoveryPlanError(
                    f"Duplicate plan_id '{plan.plan_id}' in the audit batch; readiness "
                    f"counts would be ambiguous."
                )
            seen_ids.add(plan.plan_id)

        issues: List[PlanIssue] = []
        ready = 0
        not_ready = 0

        for plan in plans:
            plan_issues: List[PlanIssue] = []
            plan_issues.extend(self._check_backup_integrity(plan))
            plan_issues.extend(self._check_sweep_readiness(plan))
            plan_issues.extend(self._check_incident_response(plan, as_of))

            if plan_issues:
                not_ready += 1
                issues.extend(plan_issues)
            else:
                ready += 1

        total = len(plans)
        by_severity: Dict[str, int] = {}
        for issue in issues:
            by_severity[issue.severity] = by_severity.get(issue.severity, 0) + 1

        all_ready = not_ready == 0 and total > 0
        status = STATUS_READY if all_ready else STATUS_NOT_READY

        if total == 0:
            notes = (
                f"KEY RECOVERY AUDIT [{status}] as of {as_of}: no plans supplied. "
                f"An empty plan set is not evidence of readiness."
            )
        else:
            notes = (
                f"KEY RECOVERY AUDIT [{status}] as of {as_of}: "
                f"Total Plans = {total}, Ready = {ready}, Not Ready = {not_ready}, "
                f"Issues = {len(issues)} "
                f"(critical={by_severity.get(SEVERITY_CRITICAL, 0)}, "
                f"high={by_severity.get(SEVERITY_HIGH, 0)}, "
                f"medium={by_severity.get(SEVERITY_MEDIUM, 0)})."
            )

        if all_ready:
            logger.info(notes)
        else:
            logger.warning(notes)

        return KeyRecoveryReadinessReport(
            total_plans=total,
            ready_count=ready,
            not_ready_count=not_ready,
            issues=issues,
            status=status,
            audit_notes=notes,
            as_of_date=as_of,
            issues_by_severity=by_severity,
        )
