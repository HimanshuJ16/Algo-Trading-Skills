"""
api-key-least-privilege-audit-tool: Automated permission scope auditor enforcing least privilege
and withdrawal restriction security policies on broker API keys.
"""
from dataclasses import dataclass, field
import logging
from enum import Enum
from typing import Dict, List, Optional, Set

logger = logging.getLogger(__name__)


class BotRole(Enum):
    MARKET_DATA_ONLY = "MARKET_DATA_ONLY"
    EXECUTION_BOT = "EXECUTION_BOT"
    PORTFOLIO_MONITOR = "PORTFOLIO_MONITOR"
    ADMIN_SUPERVISOR = "ADMIN_SUPERVISOR"


# Globally forbidden permissions for any non-admin trading process
CRITICAL_FORBIDDEN_PERMISSIONS: Set[str] = {
    "withdraw", "withdraw_funds", "transfer", "crypto_transfer",
    "account_admin", "sub_account_create", "api_key_manage"
}


@dataclass
class RoleSecurityPolicy:
    role: BotRole
    required_permissions: Set[str]
    allowed_permissions: Set[str]
    forbidden_permissions: Set[str]


@dataclass
class KeyAuditReport:
    key_alias: str
    broker_name: str
    role: BotRole
    is_compliant: bool
    granted_permissions: Set[str]
    missing_required: List[str]
    excess_violations: List[str]
    security_warning: Optional[str] = None


# Role policy matrix definition
ROLE_POLICIES: Dict[BotRole, RoleSecurityPolicy] = {
    BotRole.MARKET_DATA_ONLY: RoleSecurityPolicy(
        role=BotRole.MARKET_DATA_ONLY,
        required_permissions={"read_market_data"},
        allowed_permissions={"read_market_data", "read_account_info"},
        forbidden_permissions=CRITICAL_FORBIDDEN_PERMISSIONS.union({"place_orders", "cancel_orders"}),
    ),
    BotRole.EXECUTION_BOT: RoleSecurityPolicy(
        role=BotRole.EXECUTION_BOT,
        required_permissions={"read_market_data", "place_orders", "cancel_orders"},
        allowed_permissions={"read_market_data", "read_account_info", "place_orders", "cancel_orders", "read_positions"},
        forbidden_permissions=CRITICAL_FORBIDDEN_PERMISSIONS,
    ),
    BotRole.PORTFOLIO_MONITOR: RoleSecurityPolicy(
        role=BotRole.PORTFOLIO_MONITOR,
        required_permissions={"read_account_info", "read_positions"},
        allowed_permissions={"read_market_data", "read_account_info", "read_positions", "read_orders"},
        forbidden_permissions=CRITICAL_FORBIDDEN_PERMISSIONS.union({"place_orders", "cancel_orders"}),
    ),
}


class APIKeyLeastPrivilegeAuditor:
    """
    Audits broker API key permission scopes against role-based security policies
    to prevent over-privileged keys from being used in live trading.
    """

    def __init__(self, policies: Optional[Dict[BotRole, RoleSecurityPolicy]] = None):
        self.policies = policies or ROLE_POLICIES

    def audit_key(
        self,
        key_alias: str,
        broker_name: str,
        role: BotRole,
        granted_permissions: Set[str],
    ) -> KeyAuditReport:
        """
        Audits granted permissions against the role policy and returns a KeyAuditReport.
        """
        if role not in self.policies:
            raise ValueError(f"No security policy defined for role {role.value}")

        policy = self.policies[role]
        granted_clean = {p.lower() for p in granted_permissions}

        # Check missing required permissions
        missing = [p for p in policy.required_permissions if p.lower() not in granted_clean]

        # Check excess / forbidden permissions
        excess = []
        for p in granted_clean:
            if p in {f.lower() for f in policy.forbidden_permissions}:
                excess.append(p)
            elif p not in {a.lower() for a in policy.allowed_permissions}:
                excess.append(p)

        is_compliant = (len(missing) == 0) and (len(excess) == 0)

        warning = None
        if excess:
            warning = (
                f"CRITICAL SECURITY VIOLATION: API Key '{key_alias}' ({broker_name}) "
                f"possesses excess/forbidden permissions for role {role.value}: {excess}. "
                f"IMMEDIATE REVOCATION REQUIRED."
            )
            logger.critical(warning)
        elif missing:
            warning = f"INSUFFICIENT PERMISSIONS: Key '{key_alias}' missing required permissions: {missing}."
            logger.warning(warning)

        return KeyAuditReport(
            key_alias=key_alias,
            broker_name=broker_name,
            role=role,
            is_compliant=is_compliant,
            granted_permissions=granted_permissions,
            missing_required=missing,
            excess_violations=excess,
            security_warning=warning,
        )
