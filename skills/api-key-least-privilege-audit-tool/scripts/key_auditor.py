"""api-key-least-privilege-audit-tool: Automated permission scope auditor enforcing least privilege
and withdrawal restriction security policies on broker API keys.
"""
from dataclasses import dataclass, field
import logging
from enum import Enum
from typing import Dict, FrozenSet, List, Optional, Set

logger = logging.getLogger(__name__)

# Wildcard permission that grants unrestricted access — always flagged as a violation
WILDCARD_PERMISSION = "*"


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


@dataclass(frozen=True)
class KeyAuditReport:
    key_alias: str
    broker_name: str
    role: BotRole
    is_compliant: bool
    granted_permissions: FrozenSet[str]
    missing_required: List[str]
    excess_violations: List[str]
    security_warning: Optional[str] = None


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

    def audit_key(
        self,
        key_alias: str,
        broker_name: str,
        role: BotRole,
        granted_permissions: Set[str],
    ) -> KeyAuditReport:
        """
        Audits granted permissions against the role policy and returns a KeyAuditReport.

        All permission comparisons are case-insensitive. A wildcard ``*`` in the
        granted set is always flagged as a critical violation regardless of role.
        """
        if role not in self.policies:
            raise ValueError(f"No security policy defined for role {role.value}")

        policy = self.policies[role]
        granted_clean: Set[str] = {p.strip().lower() for p in granted_permissions if p and p.strip()}

        # Wildcard detection — unrestricted access is always a violation
        wildcard_detected = WILDCARD_PERMISSION in granted_clean

        # Check missing required permissions
        missing = [p for p in policy.required_lower if p not in granted_clean]

        # Check excess / forbidden permissions
        excess = []
        for p in granted_clean:
            if p == WILDCARD_PERMISSION:
                excess.append(p)
            elif p in policy.forbidden_lower:
                excess.append(p)
            elif p not in policy.allowed_lower:
                excess.append(p)

        is_compliant = (len(missing) == 0) and (len(excess) == 0)

        warning = None
        if excess:
            warning = (
                f"CRITICAL SECURITY VIOLATION: API Key '{key_alias}' ({broker_name}) "
                f"possesses excess/forbidden permissions for role {role.value}: {excess}. "
                f"IMMEDIATE REVOCATION REQUIRED."
            )
            if wildcard_detected:
                warning += " WILDCARD PERMISSION DETECTED — key has unrestricted access."
            logger.critical(warning)
        elif missing:
            warning = f"INSUFFICIENT PERMISSIONS: Key '{key_alias}' missing required permissions: {missing}."
            logger.warning(warning)

        return KeyAuditReport(
            key_alias=key_alias,
            broker_name=broker_name,
            role=role,
            is_compliant=is_compliant,
            granted_permissions=frozenset(granted_clean),
            missing_required=missing,
            excess_violations=excess,
            security_warning=warning,
        )
