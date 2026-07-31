import time
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

logger = logging.getLogger(__name__)

@dataclass
class UserEntitlementProfile:
    user_id: str
    is_corporate_or_algo_entity: bool   # If True, MUST be Professional subscriber
    is_professional: bool               # Professional vs Non-Professional subscriber
    has_non_display_license: bool       # True if licensed for automated algo consumption
    licensed_venues: Set[str]           # e.g. {'NASDAQ', 'NYSE', 'CME'}
    venue_license_expiries: Dict[str, float] = field(default_factory=dict) # venue_id -> epoch expiry

@dataclass
class DataStreamRequest:
    user_id: str
    venue_id: str                       # e.g. 'CME', 'NASDAQ', 'LSE'
    data_level: str                     # 'L1', 'L2', 'L3'
    usage_type: str                     # 'DISPLAY' or 'NON_DISPLAY_ALGO'
    request_timestamp_epoch: float = field(default_factory=time.time)

@dataclass
class EntitlementAuditReport:
    user_id: str
    venue_id: str
    usage_type: str
    is_authorized: bool
    status: str                         # 'ENTITLEMENT_APPROVED', 'ENTITLEMENT_DENIED_MISCLASSIFIED_SUBSCRIBER', 'ENTITLEMENT_DENIED_MISSING_NON_DISPLAY_LICENSE', 'ENTITLEMENT_DENIED_UNLICENSED_VENUE', 'ENTITLEMENT_DENIED_EXPIRED_LICENSE'
    audit_notes: str

class MarketDataEntitlementEngine:
    """
    Pre-stream market data entitlement and compliance engine,
    verifying exchange venue licenses (NASDAQ, NYSE, CME, LSE), professional vs non-professional subscriber classification, and non-display algorithmic licensing.
    """
    def __init__(self):
        pass

    def audit_stream_entitlement(
        self,
        profile: UserEntitlementProfile,
        request: DataStreamRequest
    ) -> EntitlementAuditReport:
        """
        Audits user subscriber classification, non-display licensing, and venue entitlement status.
        """
        user_id = profile.user_id
        venue_clean = request.venue_id.strip().upper()
        usage_clean = request.usage_type.strip().upper()

        # 1. Subscriber Classification Audit (Corporate/Algo entity MUST be Professional)
        if profile.is_corporate_or_algo_entity and not profile.is_professional:
            status = "ENTITLEMENT_DENIED_MISCLASSIFIED_SUBSCRIBER"
            notes = (
                f"ENTITLEMENT DENIED [{user_id}]: Corporate/Algo entity is misclassified as Non-Professional. "
                f"Exchange compliance mandates Professional Subscriber classification!"
            )
            logger.error(notes)
            return EntitlementAuditReport(
                user_id=user_id, venue_id=venue_clean, usage_type=usage_clean,
                is_authorized=False, status=status, audit_notes=notes
            )

        # 2. Non-Display Algorithmic License Audit
        if usage_clean == "NON_DISPLAY_ALGO" and not profile.has_non_display_license:
            status = "ENTITLEMENT_DENIED_MISSING_NON_DISPLAY_LICENSE"
            notes = (
                f"ENTITLEMENT DENIED [{user_id}]: Algorithmic trading feed requested for venue '{venue_clean}', "
                f"but profile lacks Non-Display Application Licensing."
            )
            logger.error(notes)
            return EntitlementAuditReport(
                user_id=user_id, venue_id=venue_clean, usage_type=usage_clean,
                is_authorized=False, status=status, audit_notes=notes
            )

        # 3. Venue Entitlement Audit
        if venue_clean not in profile.licensed_venues:
            status = "ENTITLEMENT_DENIED_UNLICENSED_VENUE"
            notes = (
                f"ENTITLEMENT DENIED [{user_id}]: Venue '{venue_clean}' is not included in user's active "
                f"licensed venues set: {sorted(list(profile.licensed_venues))}."
            )
            logger.error(notes)
            return EntitlementAuditReport(
                user_id=user_id, venue_id=venue_clean, usage_type=usage_clean,
                is_authorized=False, status=status, audit_notes=notes
            )

        # 4. License Expiration Audit
        expiry = profile.venue_license_expiries.get(venue_clean)
        if expiry and request.request_timestamp_epoch > expiry:
            status = "ENTITLEMENT_DENIED_EXPIRED_LICENSE"
            notes = (
                f"ENTITLEMENT DENIED [{user_id}]: Market data license for venue '{venue_clean}' expired "
                f"at epoch timestamp {expiry}."
            )
            logger.error(notes)
            return EntitlementAuditReport(
                user_id=user_id, venue_id=venue_clean, usage_type=usage_clean,
                is_authorized=False, status=status, audit_notes=notes
            )

        # 5. Entitlement Approved!
        status = "ENTITLEMENT_APPROVED"
        notes = (
            f"ENTITLEMENT APPROVED [{user_id}]: Granted {request.data_level} {usage_clean} stream "
            f"access for venue '{venue_clean}'."
        )
        logger.info(notes)
        return EntitlementAuditReport(
            user_id=user_id, venue_id=venue_clean, usage_type=usage_clean,
            is_authorized=True, status=status, audit_notes=notes
        )
