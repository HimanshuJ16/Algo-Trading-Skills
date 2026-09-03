"""api-key-least-privilege-audit-tool: Automated permission scope auditor enforcing least privilege
and withdrawal restriction security policies on broker API keys.

What this module is and is not
------------------------------
It is a **client-side, deny-by-default gate** that compares a set of API key
permission scopes against a role policy and refuses to let an over-privileged key
reach a live trading process. Anything not present in the role's
``allowed_permissions`` is reported as a violation, so an unrecognised
broker-native scope name (``enableWithdrawals``, ``can_transfer``,
``Withdraw Funds``) fails the audit rather than passing unnoticed.

It is **not** an enforcement mechanism. It cannot revoke, downgrade or constrain a
key. An attacker holding a withdrawal-capable key calls the broker directly and
never runs this code. The controls that actually constrain a key live at the
broker — scope selection at key-creation time, and IP access restriction — and in
your secret storage. This auditor's job is to catch a misconfigured key *before*
deployment and leave an auditable record of why it was rejected.

It also **trusts the scope set it is handed**, so the audit is only as good as the
provenance of that set. Some venues expose a key-permission introspection endpoint
and some do not; see ``references/standards.md`` for which is which and where the
scopes must instead come from an operator-maintained record.

Fail-closed properties
----------------------
* An empty granted-scope set never reports compliant, and its warning states
  explicitly that an empty set is more often a failed probe than a genuinely
  unprivileged key.
* A bare ``str``/``bytes`` argument is rejected rather than iterated character by
  character, which would otherwise turn a plausible caller mistake into a
  meaningless report.
* ``missing_required`` and ``excess_violations`` are sorted, so repeat audits of
  the same key produce identical records across processes.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Dict, FrozenSet, Iterable, List, Optional

logger = logging.getLogger(__name__)

__all__ = [
    "WILDCARD_PERMISSION",
    "SEVERITY_COMPLIANT",
    "SEVERITY_INSUFFICIENT",
    "SEVERITY_CRITICAL",
    "CRITICAL_FORBIDDEN_PERMISSIONS",
    "ROLE_POLICIES",
    "BotRole",
    "RoleSecurityPolicy",
    "KeyAuditReport",
    "APIKeyLeastPrivilegeAuditor",
]

# Wildcard permission that grants unrestricted access — always flagged as a violation
WILDCARD_PERMISSION = "*"

# Machine-readable audit outcomes. The two failure modes call for opposite operator
# action, and a deployment gate must be able to tell them apart without
# string-matching the human-readable warning:
#   SEVERITY_CRITICAL     -> the key holds scopes it must not have. Revoke it.
#   SEVERITY_INSUFFICIENT -> the key lacks scopes the role needs. Re-issue it.
SEVERITY_COMPLIANT = "COMPLIANT"
SEVERITY_INSUFFICIENT = "INSUFFICIENT_PERMISSIONS"
SEVERITY_CRITICAL = "CRITICAL_VIOLATION"


class BotRole(Enum):
    MARKET_DATA_ONLY = "MARKET_DATA_ONLY"
    EXECUTION_BOT = "EXECUTION_BOT"
    PORTFOLIO_MONITOR = "PORTFOLIO_MONITOR"
    ADMIN_SUPERVISOR = "ADMIN_SUPERVISOR"


# Globally forbidden permissions for any non-admin trading process
CRITICAL_FORBIDDEN_PERMISSIONS: FrozenSet[str] = frozenset({
    "withdraw", "withdraw_funds", "transfer", "crypto_transfer",
    "account_admin", "sub_account_create", "api_key_manage",
})


@dataclass(frozen=True)
class RoleSecurityPolicy:
    role: BotRole
    required_permissions: FrozenSet[str]
    allowed_permissions: FrozenSet[str]
    forbidden_permissions: FrozenSet[str]

    @property
    def required_lower(self) -> FrozenSet[str]:
        return frozenset(p.lower() for p in self.required_permissions)

    @property
    def allowed_lower(self) -> FrozenSet[str]:
        return frozenset(p.lower() for p in self.allowed_permissions)

    @property
    def forbidden_lower(self) -> FrozenSet[str]:
        return frozenset(p.lower() for p in self.forbidden_permissions)

    def __post_init__(self) -> None:
        """Reject a policy that no key can satisfy.

        If a required scope is not allowed, or is simultaneously forbidden, then a key
        granted exactly what the policy asks for is reported as a CRITICAL_VIOLATION —
        an order to revoke a correctly-configured key. Fail loudly at policy-definition
        time instead, where the mistake is visible.
        """
        unallowed = self.required_lower - self.allowed_lower
        if unallowed:
            raise ValueError(
                f"Policy for {self.role.value} is unsatisfiable: required permissions "
                f"{sorted(unallowed)} are not in allowed_permissions."
            )
        contradictory = self.required_lower & self.forbidden_lower
        if contradictory:
            raise ValueError(
                f"Policy for {self.role.value} is contradictory: permissions "
                f"{sorted(contradictory)} are both required and forbidden."
            )


@dataclass(frozen=True)
class KeyAuditReport:
    """Immutable record of one key audit.

    ``missing_required`` and ``excess_violations`` are sorted so that the record is
    reproducible and diffable across processes. ``severity`` is the machine-readable
    outcome; ``security_warning`` is its human-readable form and must not be parsed.
    """

    key_alias: str
    broker_name: str
    role: BotRole
    is_compliant: bool
    granted_permissions: FrozenSet[str]
    missing_required: List[str]
    excess_violations: List[str]
    security_warning: Optional[str] = None
    severity: str = SEVERITY_COMPLIANT

    @property
    def has_critical_violation(self) -> bool:
        """True when the key holds scopes it must not have — revoke it, do not redeploy."""
        return self.severity == SEVERITY_CRITICAL


# Role policy matrix definition
ROLE_POLICIES: Dict[BotRole, RoleSecurityPolicy] = {
    BotRole.MARKET_DATA_ONLY: RoleSecurityPolicy(
        role=BotRole.MARKET_DATA_ONLY,
        required_permissions=frozenset({"read_market_data"}),
        allowed_permissions=frozenset({"read_market_data", "read_account_info"}),
        forbidden_permissions=CRITICAL_FORBIDDEN_PERMISSIONS | frozenset({"place_orders", "cancel_orders"}),
    ),
    BotRole.EXECUTION_BOT: RoleSecurityPolicy(
        role=BotRole.EXECUTION_BOT,
        required_permissions=frozenset({"read_market_data", "place_orders", "cancel_orders"}),
        allowed_permissions=frozenset({
            "read_market_data", "read_account_info", "place_orders", "cancel_orders", "read_positions",
        }),
        forbidden_permissions=CRITICAL_FORBIDDEN_PERMISSIONS,
    ),
    BotRole.PORTFOLIO_MONITOR: RoleSecurityPolicy(
        role=BotRole.PORTFOLIO_MONITOR,
        required_permissions=frozenset({"read_account_info", "read_positions"}),
        allowed_permissions=frozenset({
            "read_market_data", "read_account_info", "read_positions", "read_orders",
        }),
        forbidden_permissions=CRITICAL_FORBIDDEN_PERMISSIONS | frozenset({"place_orders", "cancel_orders"}),
    ),
    BotRole.ADMIN_SUPERVISOR: RoleSecurityPolicy(
        role=BotRole.ADMIN_SUPERVISOR,
        required_permissions=frozenset({"read_account_info", "read_positions"}),
        allowed_permissions=frozenset({
            "read_market_data", "read_account_info", "read_positions", "read_orders",
            "place_orders", "cancel_orders", "account_admin", "api_key_manage",
            "sub_account_create",
        }),
        forbidden_permissions=frozenset({"withdraw", "withdraw_funds", "transfer", "crypto_transfer"}),
    ),
}


class APIKeyLeastPrivilegeAuditor:
    """
    Audits broker API key permission scopes against role-based security policies
    to prevent over-privileged keys from being used in live trading.
    """

    def __init__(self, policies: Optional[Dict[BotRole, RoleSecurityPolicy]] = None):
        self.policies = policies if policies is not None else ROLE_POLICIES

    @staticmethod
    def _normalise_granted(granted_permissions: Iterable[str]) -> FrozenSet[str]:
        """Strip, lower-case and de-duplicate the granted scope set.

        Rejects ``str``/``bytes`` explicitly. Both are iterable, so a caller passing
        a single scope name instead of a collection would otherwise have it split
        into characters and audited as a set of one-letter scopes.
        """
        if granted_permissions is None:
            raise TypeError("granted_permissions must be a collection of scope strings, not None.")
        if isinstance(granted_permissions, (str, bytes)):
            raise TypeError(
                "granted_permissions must be a collection of scope strings, not a bare "
                f"{type(granted_permissions).__name__}. Pass {{'read_market_data'}}, "
                "not 'read_market_data'."
            )
        try:
            items = list(granted_permissions)
        except TypeError as exc:
            raise TypeError(
                "granted_permissions must be an iterable collection of scope strings."
            ) from exc
        for item in items:
            if not isinstance(item, str):
                raise TypeError(
                    f"granted_permissions contains a non-string scope: {item!r} "
                    f"({type(item).__name__})."
                )
        return frozenset(p.strip().lower() for p in items if p.strip())

    def audit_key(
        self,
        key_alias: str,
        broker_name: str,
        role: BotRole,
        granted_permissions: Iterable[str],
    ) -> KeyAuditReport:
        """
        Audits granted permissions against the role policy and returns a KeyAuditReport.

        All permission comparisons are case-insensitive. A wildcard ``*`` in the
        granted set is always flagged as a critical violation regardless of role.
        The check is deny-by-default: any scope absent from the role's
        ``allowed_permissions`` is a violation even when it is not explicitly
        forbidden, so unrecognised broker-native scope names fail closed.

        Raises:
            ValueError: no security policy is defined for ``role``.
            TypeError: ``granted_permissions`` is ``None``, a bare string, not
                iterable, or contains a non-string element.
        """
        if role not in self.policies:
            raise ValueError(f"No security policy defined for role {role.value}")

        policy = self.policies[role]
        granted_clean = self._normalise_granted(granted_permissions)

        # Wildcard detection — unrestricted access is always a violation
        wildcard_detected = WILDCARD_PERMISSION in granted_clean

        # Missing required permissions. Sorted: set iteration order varies between
        # processes, and an audit record has to be reproducible and diffable.
        missing = sorted(p for p in policy.required_lower if p not in granted_clean)

        # Excess / forbidden permissions, deny-by-default.
        excess = sorted(
            p for p in granted_clean
            if p == WILDCARD_PERMISSION
            or p in policy.forbidden_lower
            or p not in policy.allowed_lower
        )

        is_compliant = (not missing) and (not excess)

        warning: Optional[str] = None
        if excess:
            severity = SEVERITY_CRITICAL
            warning = (
                f"CRITICAL SECURITY VIOLATION: API Key '{key_alias}' ({broker_name}) "
                f"possesses excess/forbidden permissions for role {role.value}: {excess}. "
                f"IMMEDIATE REVOCATION REQUIRED."
            )
            if wildcard_detected:
                warning += " WILDCARD PERMISSION DETECTED — key has unrestricted access."
            if missing:
                # Report both conditions. The key is simultaneously over-privileged
                # for what it may do and under-privileged for what it must do;
                # reporting only the first hides half of the remediation.
                warning += f" Key is also missing required permissions: {missing}."
            logger.critical(warning)
        elif missing:
            severity = SEVERITY_INSUFFICIENT
            warning = (
                f"INSUFFICIENT PERMISSIONS: Key '{key_alias}' ({broker_name}) missing "
                f"required permissions for role {role.value}: {missing}."
            )
            if not granted_clean:
                warning += (
                    " Granted scope set was EMPTY — confirm the key-permission probe "
                    "actually returned data before treating this as an unprivileged key."
                )
            logger.warning(warning)
        else:
            severity = SEVERITY_COMPLIANT

        return KeyAuditReport(
            key_alias=key_alias,
            broker_name=broker_name,
            role=role,
            is_compliant=is_compliant,
            granted_permissions=granted_clean,
            missing_required=missing,
            excess_violations=excess,
            security_warning=warning,
            severity=severity,
        )
