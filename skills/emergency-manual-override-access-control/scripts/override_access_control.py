import hashlib
import time
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Set

logger = logging.getLogger(__name__)

AUTHORIZED_ROLES = {"RISK_OFFICER", "HEAD_TRADER", "CTO", "MANAGING_DIRECTOR"}

@dataclass
class OverrideRequest:
    request_id: str
    target_system_id: str               # e.g. 'STRATEGY_STAT_ARB_01' or 'ALL_ALGOS'
    action_type: str                    # 'HALT_STRATEGY', 'KILL_SWITCH_ALL_ALGOS', 'PAUSE_ORDERS'
    primary_operator_id: str
    primary_operator_role: str          # e.g. 'RISK_OFFICER'
    justification_reason: str           # Mandatory justification text
    secondary_operator_id: Optional[str] = None
    secondary_operator_role: Optional[str] = None
    break_glass_token: Optional[str] = None
    ttl_minutes: int = 60

@dataclass
class OverrideControlReport:
    request_id: str
    target_system_id: str
    action_type: str
    severity_level: str                 # 'SEVERITY_HIGH' or 'SEVERITY_CRITICAL'
    is_approved: bool
    audit_hash_sha256: str
    ttl_minutes: int
    rejection_reason: Optional[str]
    audit_summary: str

class EmergencyOverrideAccessEngine:
    """
    Quantitative infrastructure security engine for managing break-glass emergency manual overrides
    (kill switches, algo halts), dual-signature authorization, role-based access control (RBAC), and immutable audit logging.
    """
    def __init__(self):
        self.active_overrides: Dict[str, OverrideRequest] = {}

    def compute_audit_hash(self, req: OverrideRequest, timestamp_epoch: float) -> str:
        """
        Computes tamper-evident SHA-256 hash over override payload.
        """
        payload = f"{req.request_id}|{req.target_system_id}|{req.action_type}|{req.primary_operator_id}|{req.justification_reason}|{timestamp_epoch}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def process_override_request(self, req: OverrideRequest) -> OverrideControlReport:
        """
        Audits RBAC roles, dual-signatures for critical actions, and generates immutable audit trail.
        """
        now = time.time()
        
        # 1. Validate mandatory justification
        if not req.justification_reason or len(req.justification_reason.strip()) < 10:
            msg = "REJECTED: Override requires detailed justification (minimum 10 characters)."
            logger.error(msg)
            return OverrideControlReport(
                request_id=req.request_id, target_system_id=req.target_system_id, action_type=req.action_type,
                severity_level="UNKNOWN", is_approved=False, audit_hash_sha256="", ttl_minutes=0,
                rejection_reason=msg, audit_summary=msg
            )

        # 2. Validate primary operator RBAC role
        if req.primary_operator_role.upper() not in AUTHORIZED_ROLES:
            msg = f"UNAUTHORIZED ROLE: Operator '{req.primary_operator_id}' role '{req.primary_operator_role}' is not in authorized list."
            logger.error(msg)
            return OverrideControlReport(
                request_id=req.request_id, target_system_id=req.target_system_id, action_type=req.action_type,
                severity_level="UNKNOWN", is_approved=False, audit_hash_sha256="", ttl_minutes=0,
                rejection_reason=msg, audit_summary=msg
            )

        # 3. Determine severity level
        is_critical = (req.action_type.upper() == "KILL_SWITCH_ALL_ALGOS")
        severity = "SEVERITY_CRITICAL" if is_critical else "SEVERITY_HIGH"

        # 4. Critical actions require Dual Sign-Off or Break-Glass token
        if is_critical:
            has_secondary = (
                req.secondary_operator_id is not None
                and req.secondary_operator_role is not None
                and req.secondary_operator_role.upper() in AUTHORIZED_ROLES
                and req.secondary_operator_id != req.primary_operator_id
            )
            has_break_glass = (req.break_glass_token is not None and len(req.break_glass_token) >= 8)

            if not (has_secondary or has_break_glass):
                msg = f"DUAL SIGN-OFF REQUIRED: Critical action '{req.action_type}' requires secondary authorized sign-off or Break-Glass token."
                logger.error(msg)
                return OverrideControlReport(
                    request_id=req.request_id, target_system_id=req.target_system_id, action_type=req.action_type,
                    severity_level=severity, is_approved=False, audit_hash_sha256="", ttl_minutes=0,
                    rejection_reason=msg, audit_summary=msg
                )

        audit_hash = self.compute_audit_hash(req, now)
        self.active_overrides[req.request_id] = req

        summary = (
            f"EMERGENCY OVERRIDE APPROVED [{req.request_id}]: Action '{req.action_type}' on '{req.target_system_id}' "
            f"by {req.primary_operator_id} ({req.primary_operator_role}). AuditHash={audit_hash[:12]}... TTL={req.ttl_minutes}m."
        )
        logger.critical(summary)

        return OverrideControlReport(
            request_id=req.request_id,
            target_system_id=req.target_system_id,
            action_type=req.action_type,
            severity_level=severity,
            is_approved=True,
            audit_hash_sha256=audit_hash,
            ttl_minutes=req.ttl_minutes,
            rejection_reason=None,
            audit_summary=summary
        )
