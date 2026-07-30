import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class VendorContractSpec:
    vendor_id: str
    vendor_name: str
    license_tier: str                   # e.g. 'ENTERPRISE_BPIPE', 'RESEARCH_DESKTOP', 'REFINITIV_DACS'
    allowed_use_cases: List[str]        # e.g. ['INTERNAL_RESEARCH', 'NON_DISPLAY_TRADING', 'RISK_MANAGEMENT']
    is_non_display_allowed: bool
    is_redistribution_allowed: bool
    max_concurrent_entitlements: int
    current_active_entitlements: int = 0
    contract_expiration_date: str = "2027-12-31"

@dataclass
class DataAccessRequest:
    request_id: str
    vendor_id: str
    requested_by_system: str
    use_case_type: str                  # 'INTERNAL_RESEARCH', 'NON_DISPLAY_TRADING', 'EXTERNAL_REDISTRIBUTION'
    is_external_redistribution: bool
    requested_seats: int = 1

@dataclass
class VendorUsageAuditReport:
    request_id: str
    vendor_id: str
    is_approved: bool
    status: str                         # 'APPROVED', 'REDISTRIBUTION_LICENSING_VIOLATION', 'NON_DISPLAY_LICENSING_VIOLATION', 'CONCURRENCY_CAP_EXCEEDED'
    applied_policy: str
    active_entitlements_remaining: int

class VendorUsageRestrictionEngine:
    """
    Quantitative market data compliance engine for tracking vendor license restrictions
    (Bloomberg, Refinitiv, ICE), non-display trading entitlements, and blocking illegal external data redistribution.
    """
    def __init__(self):
        self.contracts: Dict[str, VendorContractSpec] = {}

    def register_contract(self, contract: VendorContractSpec):
        self.contracts[contract.vendor_id] = contract

    def evaluate_access_request(self, req: DataAccessRequest) -> VendorUsageAuditReport:
        """
        Audits data access request against vendor contract specifications.
        """
        if req.vendor_id not in self.contracts:
            raise ValueError(f"Vendor contract for {req.vendor_id} not registered.")

        contract = self.contracts[req.vendor_id]
        use_case = req.use_case_type.upper()

        # 1. External Redistribution Check
        if req.is_external_redistribution and not contract.is_redistribution_allowed:
            msg = f"REDISTRIBUTION VIOLATION [{contract.vendor_name}]: External redistribution NOT permitted under license {contract.license_tier}."
            logger.critical(msg)
            return VendorUsageAuditReport(
                request_id=req.request_id, vendor_id=req.vendor_id, is_approved=False,
                status="REDISTRIBUTION_LICENSING_VIOLATION", applied_policy=msg,
                active_entitlements_remaining=contract.max_concurrent_entitlements - contract.current_active_entitlements
            )

        # 2. Non-Display Trading Check
        if use_case == "NON_DISPLAY_TRADING" and not contract.is_non_display_allowed:
            msg = f"NON-DISPLAY VIOLATION [{contract.vendor_name}]: Algorithmic non-display trading NOT permitted."
            logger.error(msg)
            return VendorUsageAuditReport(
                request_id=req.request_id, vendor_id=req.vendor_id, is_approved=False,
                status="NON_DISPLAY_LICENSING_VIOLATION", applied_policy=msg,
                active_entitlements_remaining=contract.max_concurrent_entitlements - contract.current_active_entitlements
            )

        # 3. Allowed Use Case Scope Check
        if use_case not in [u.upper() for u in contract.allowed_use_cases]:
            msg = f"UNAUTHORIZED USE CASE [{contract.vendor_name}]: Use case {use_case} not in allowed list."
            logger.error(msg)
            return VendorUsageAuditReport(
                request_id=req.request_id, vendor_id=req.vendor_id, is_approved=False,
                status="UNAUTHORIZED_USE_CASE_VIOLATION", applied_policy=msg,
                active_entitlements_remaining=contract.max_concurrent_entitlements - contract.current_active_entitlements
            )

        # 4. Concurrency Cap Check
        if contract.current_active_entitlements + req.requested_seats > contract.max_concurrent_entitlements:
            msg = f"CONCURRENCY EXCEEDED [{contract.vendor_name}]: {contract.current_active_entitlements + req.requested_seats} > Max {contract.max_concurrent_entitlements}."
            logger.warning(msg)
            return VendorUsageAuditReport(
                request_id=req.request_id, vendor_id=req.vendor_id, is_approved=False,
                status="CONCURRENCY_CAP_EXCEEDED", applied_policy=msg,
                active_entitlements_remaining=contract.max_concurrent_entitlements - contract.current_active_entitlements
            )

        # Approved -> Increment entitlement count
        contract.current_active_entitlements += req.requested_seats
        remaining = contract.max_concurrent_entitlements - contract.current_active_entitlements
        logger.info(f"DATA ACCESS APPROVED [{contract.vendor_name}]: System={req.requested_by_system}, UseCase={use_case}. Remaining seats={remaining}.")

        return VendorUsageAuditReport(
            request_id=req.request_id,
            vendor_id=req.vendor_id,
            is_approved=True,
            status="APPROVED",
            applied_policy="Compliant with vendor contractual usage scope and concurrency limits.",
            active_entitlements_remaining=remaining
        )
