import hashlib
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class JurisdictionTransferPolicy:
    origin_country: str                # e.g. 'CN', 'CH', 'DE', 'US'
    destination_country: str           # e.g. 'US', 'UK', 'SG'
    policy_status: str                 # 'ALLOWED_UNRESTRICTED', 'REQUIRES_ANONYMIZATION', 'BLOCKED'
    regulatory_framework: str          # e.g. 'GDPR', 'PIPL', 'SwissBankingSecrecy'

@dataclass
class TradeDataPayload:
    trade_id: str
    origin_country: str
    trader_id: str                     # PII
    client_name: str                   # PII
    account_number: str                # PII
    symbol: str
    quantity: float
    price: float

@dataclass
class DataTransferAuditReport:
    trade_id: str
    origin_country: str
    destination_country: str
    transfer_approved: bool
    applied_anonymization: bool
    sanitized_payload: Optional[TradeDataPayload]
    audit_message: str

class CrossBorderTradeDataGovernanceEngine:
    """
    Quantitative compliance & data governance engine enforcing cross-border trade data transfer restrictions,
    anonymizing PII fields, and blocking non-compliant egress.
    """
    def __init__(self, policies: List[JurisdictionTransferPolicy] = None):
        self.policies: Dict[Tuple[str, str], JurisdictionTransferPolicy] = {}
        for p in (policies or []):
            self.register_policy(p)

    def register_policy(self, policy: JurisdictionTransferPolicy):
        key = (policy.origin_country.upper(), policy.destination_country.upper())
        self.policies[key] = policy

    def anonymize_trader_id(self, trader_id: str) -> str:
        """
        Hashes trader ID using SHA-256 to eliminate PII tracking across borders.
        """
        return "TRD_HASH_" + hashlib.sha256(trader_id.encode('utf-8')).hexdigest()[:12]

    def redact_account_number(self, account_number: str) -> str:
        """
        Redacts account numbers, leaving only last 4 digits.
        """
        clean = str(account_number)
        if len(clean) <= 4:
            return "****"
        return "XXXX-XXXX-" + clean[-4:]

    def process_data_transfer(
        self,
        payload: TradeDataPayload,
        destination_country: str
    ) -> DataTransferAuditReport:
        """
        Audits proposed cross-border data transfer against jurisdiction policies,
        applying PII masking/tokenization or blocking non-compliant transfers.
        """
        dest = destination_country.upper()
        orig = payload.origin_country.upper()

        if orig == dest:
            # Intra-jurisdiction transfer -> Unrestricted
            return DataTransferAuditReport(
                trade_id=payload.trade_id, origin_country=orig, destination_country=dest,
                transfer_approved=True, applied_anonymization=False, sanitized_payload=payload,
                audit_message="Domestic transfer within same jurisdiction approved without masking."
            )

        policy_key = (orig, dest)
        if policy_key not in self.policies:
            # Default strict policy: Require anonymization for unknown cross-border routes
            policy_status = "REQUIRES_ANONYMIZATION"
            framework = "DEFAULT_STRICT_PRIVACY"
        else:
            pol = self.policies[policy_key]
            policy_status = pol.policy_status
            framework = pol.regulatory_framework

        if policy_status == "BLOCKED":
            msg = f"CROSS-BORDER TRANSFER BLOCKED [{orig} -> {dest}]: Prohibited by {framework}."
            logger.error(msg)
            return DataTransferAuditReport(
                trade_id=payload.trade_id, origin_country=orig, destination_country=dest,
                transfer_approved=False, applied_anonymization=False, sanitized_payload=None,
                audit_message=msg
            )

        elif policy_status == "REQUIRES_ANONYMIZATION":
            sanitized = TradeDataPayload(
                trade_id=payload.trade_id,
                origin_country=orig,
                trader_id=self.anonymize_trader_id(payload.trader_id),
                client_name="ANONYMOUS_CLIENT",
                account_number=self.redact_account_number(payload.account_number),
                symbol=payload.symbol,
                quantity=payload.quantity,
                price=payload.price
            )
            msg = f"CROSS-BORDER TRANSFER APPROVED [{orig} -> {dest}]: PII anonymized under {framework}."
            logger.info(msg)
            return DataTransferAuditReport(
                trade_id=payload.trade_id, origin_country=orig, destination_country=dest,
                transfer_approved=True, applied_anonymization=True, sanitized_payload=sanitized,
                audit_message=msg
            )

        else: # ALLOWED_UNRESTRICTED
            return DataTransferAuditReport(
                trade_id=payload.trade_id, origin_country=orig, destination_country=dest,
                transfer_approved=True, applied_anonymization=False, sanitized_payload=payload,
                audit_message=f"Cross-border transfer [{orig} -> {dest}] approved unrestricted under {framework}."
            )
