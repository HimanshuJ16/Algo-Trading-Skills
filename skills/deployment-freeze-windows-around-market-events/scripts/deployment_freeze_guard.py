import datetime
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class MacroEventFreezeWindow:
    event_id: str
    event_name: str
    event_start_epoch_sec: float
    pre_event_buffer_minutes: float = 60.0
    post_event_buffer_minutes: float = 60.0

@dataclass
class DeploymentRequest:
    deployment_id: str
    service_name: str
    target_environment: str             # 'PRODUCTION', 'STAGING', 'RESEARCH'
    requested_epoch_sec: float
    is_emergency_hotfix: bool = False
    risk_officer_approval: bool = False
    head_of_trading_approval: bool = False

@dataclass
class DeploymentFreezeAuditReport:
    deployment_id: str
    service_name: str
    target_environment: str
    is_approved: bool
    status: str                         # 'APPROVED', 'DEPLOYMENT_BLOCKED_FREEZE_ACTIVE', 'BREAK_GLASS_HOTFIX_APPROVED', 'MISSING_DUAL_AUTHORIZATION'
    active_freeze_event_name: Optional[str]
    applied_policy: str

class DeploymentFreezeGuardEngine:
    """
    Quantitative DevOps risk guard for enforcing automated deployment freeze windows around high-volatility
    macro events (FOMC, CPI, NFP) and market open/close windows, with dual sign-off break-glass protocols.
    """
    def __init__(self):
        self.freeze_events: List[MacroEventFreezeWindow] = []

    def register_freeze_event(self, event: MacroEventFreezeWindow):
        self.freeze_events.append(event)

    def evaluate_deployment_request(self, req: DeploymentRequest) -> DeploymentFreezeAuditReport:
        """
        Audits deployment request timestamp against registered macro freeze windows.
        """
        # Staging & Research environments are exempt from deployment freezes
        if req.target_environment.upper() != "PRODUCTION":
            return DeploymentFreezeAuditReport(
                deployment_id=req.deployment_id,
                service_name=req.service_name,
                target_environment=req.target_environment,
                is_approved=True,
                status="APPROVED",
                active_freeze_event_name=None,
                applied_policy=f"Non-production environment ({req.target_environment}) exempt from deployment freeze."
            )

        req_time = req.requested_epoch_sec

        # Check if request falls inside any active freeze window
        active_event = None
        for evt in self.freeze_events:
            freeze_start = evt.event_start_epoch_sec - (evt.pre_event_buffer_minutes * 60.0)
            freeze_end = evt.event_start_epoch_sec + (evt.post_event_buffer_minutes * 60.0)

            if freeze_start <= req_time <= freeze_end:
                active_event = evt
                break

        if active_event is None:
            return DeploymentFreezeAuditReport(
                deployment_id=req.deployment_id,
                service_name=req.service_name,
                target_environment=req.target_environment,
                is_approved=True,
                status="APPROVED",
                active_freeze_event_name=None,
                applied_policy="No active market event deployment freeze window."
            )

        # Active Freeze Detected! Check for Break-Glass Emergency Hotfix Dual Authorization
        if req.is_emergency_hotfix:
            if req.risk_officer_approval and req.head_of_trading_approval:
                msg = f"BREAK-GLASS EMERGENCY OVERRIDE APPROVED during {active_event.event_name} (Dual sign-off confirmed)."
                logger.warning(f"DEPLOYMENT BREAK-GLASS [{req.deployment_id}]: {msg}")
                return DeploymentFreezeAuditReport(
                    deployment_id=req.deployment_id,
                    service_name=req.service_name,
                    target_environment=req.target_environment,
                    is_approved=True,
                    status="BREAK_GLASS_HOTFIX_APPROVED",
                    active_freeze_event_name=active_event.event_name,
                    applied_policy=msg
                )
            else:
                msg = f"EMERGENCY HOTFIX REJECTED: Missing dual sign-off (Risk Officer={req.risk_officer_approval}, Head of Trading={req.head_of_trading_approval})."
                logger.error(f"DEPLOYMENT REJECTED [{req.deployment_id}]: {msg}")
                return DeploymentFreezeAuditReport(
                    deployment_id=req.deployment_id,
                    service_name=req.service_name,
                    target_environment=req.target_environment,
                    is_approved=False,
                    status="MISSING_DUAL_AUTHORIZATION",
                    active_freeze_event_name=active_event.event_name,
                    applied_policy=msg
                )

        # Routine Production Deployment BLOCKED!
        msg = f"PRODUCTION DEPLOYMENT BLOCKED: Active freeze window for '{active_event.event_name}'."
        logger.critical(f"DEPLOYMENT BLOCKED [{req.deployment_id}]: {msg}")
        return DeploymentFreezeAuditReport(
            deployment_id=req.deployment_id,
            service_name=req.service_name,
            target_environment=req.target_environment,
            is_approved=False,
            status="DEPLOYMENT_BLOCKED_FREEZE_ACTIVE",
            active_freeze_event_name=active_event.event_name,
            applied_policy=msg
        )
