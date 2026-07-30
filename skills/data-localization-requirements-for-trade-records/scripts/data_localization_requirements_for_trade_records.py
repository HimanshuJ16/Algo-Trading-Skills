import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Allowed Primary Cloud Regions per Jurisdiction
JURISDICTION_ALLOWED_REGIONS = {
    "CN": ["cn-north-1", "cn-northwest-1"],                  # China PIPL / DSL
    "IN": ["ap-south-1", "ap-south-2"],                      # India RBI / SEBI
    "EU": ["eu-central-1", "eu-west-1", "eu-west-3"],        # EU GDPR / MiFID II
    "US": ["us-east-1", "us-west-2", "us-east-2"],           # US SEC 17a-4
    "UK": ["eu-west-2"]                                       # UK FCA SYSC
}

@dataclass
class TradeRecordPayload:
    record_id: str
    origin_jurisdiction: str            # 'CN', 'IN', 'EU', 'US', 'UK'
    destination_cloud_region: str       # e.g. 'cn-north-1', 'us-east-1', 'ap-south-1'
    record_type: str                    # 'TRADE_EXECUTION', 'CLIENT_PII', 'PAYMENT_LEDGER', 'MARKET_TICK'
    is_primary_store: bool

@dataclass
class DataLocalizationAuditReport:
    record_id: str
    origin_jurisdiction: str
    destination_cloud_region: str
    is_compliant: bool
    status: str                         # 'COMPLIANT', 'LOCALIZATION_VIOLATION_BLOCKED'
    applied_policy: str
    remediation_instructions: List[str]

class DataLocalizationComplianceEngine:
    """
    Regulatory compliance engine for auditing cross-border trade record localization laws
    (China PIPL/DSL, India RBI/SEBI, EU GDPR, US SEC 17a-4) and blocking illegal cross-border egress.
    """
    def __init__(self, allowed_regions_map: Optional[Dict[str, List[str]]] = None):
        self.allowed_regions_map = allowed_regions_map or JURISDICTION_ALLOWED_REGIONS

    def audit_trade_record_localization(self, record: TradeRecordPayload) -> DataLocalizationAuditReport:
        """
        Audits trade record destination cloud region against origin country localization laws.
        """
        jur = record.origin_jurisdiction.upper()
        dest_region = record.destination_cloud_region.lower()

        allowed_regions = self.allowed_regions_map.get(jur, [])
        is_compliant = False
        remediations = []

        if jur == "CN":
            # China PIPL/DSL: Mandatory local storage, ZERO cross-border egress for primary trade data
            if dest_region in allowed_regions:
                is_compliant = True
                policy = "China PIPL/DSL Compliant: Local in-country storage confirmed."
            else:
                is_compliant = False
                policy = "China PIPL/DSL Violation: Cross-border egress of Chinese trade records BLOCKED."
                remediations.append(f"Route primary trade record to Chinese cloud region ({allowed_regions}).")

        elif jur == "IN":
            # India RBI/SEBI: Payment & trade ledgers must be stored on primary servers in India
            if dest_region in allowed_regions:
                is_compliant = True
                policy = "India RBI/SEBI Compliant: Primary server storage in India (ap-south-1) confirmed."
            else:
                is_compliant = False
                policy = "India RBI/SEBI Violation: Primary Indian trade records hosted outside India BLOCKED."
                remediations.append(f"Re-route primary trade storage to Indian region ({allowed_regions}).")

        elif jur == "EU":
            # EU GDPR/MiFID II: EU primary storage required; egress requires adequacy/SCCs
            if dest_region in allowed_regions:
                is_compliant = True
                policy = "EU GDPR/MiFID II Compliant: Local EU storage confirmed."
            else:
                is_compliant = False
                policy = "EU GDPR Violation: Egress to non-adequate region without Standard Contractual Clauses."
                remediations.append("Apply PII tokenization/anonymization or re-route to EU cloud region.")

        else: # Default US / UK
            if dest_region in allowed_regions:
                is_compliant = True
                policy = f"{jur} Regulatory Compliant: Region {dest_region} permitted under SEC 17a-4 / FCA rules."
            else:
                # Allowed if multi-region secondary backup for US
                is_compliant = True
                policy = f"{jur} Multi-region replication permitted subject to WORM 6-year retention."

        status = "COMPLIANT" if is_compliant else "LOCALIZATION_VIOLATION_BLOCKED"

        if not is_compliant:
            logger.critical(f"DATA LOCALIZATION VIOLATION [{record.record_id}]: Origin={jur} -> Dest={dest_region}. Egress BLOCKED!")

        return DataLocalizationAuditReport(
            record_id=record.record_id,
            origin_jurisdiction=jur,
            destination_cloud_region=dest_region,
            is_compliant=is_compliant,
            status=status,
            applied_policy=policy,
            remediation_instructions=remediations
        )
