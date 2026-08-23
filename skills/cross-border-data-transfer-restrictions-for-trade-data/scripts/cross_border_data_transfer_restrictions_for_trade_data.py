import hashlib
import hmac
import logging
import secrets
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Recognized route policy outcomes. Anything else is rejected at registration
# time so a typo can never fail open into an unrestricted raw-PII transfer.
POLICY_ALLOWED_UNRESTRICTED = "ALLOWED_UNRESTRICTED"
POLICY_REQUIRES_ANONYMIZATION = "REQUIRES_ANONYMIZATION"
POLICY_BLOCKED = "BLOCKED"
VALID_POLICY_STATUSES = frozenset({
    POLICY_ALLOWED_UNRESTRICTED,
    POLICY_REQUIRES_ANONYMIZATION,
    POLICY_BLOCKED,
})

# Default applied when no explicit policy exists for a route: unknown routes
# are treated as restriction-bearing (default-deny), never as unrestricted.
DEFAULT_STRICT_FRAMEWORK = "DEFAULT_STRICT_PRIVACY"

# 64 bits of digest prefix: collision-safe well past millions of trader IDs
# (birthday bound 2^32) while keeping tokens short for telemetry pipelines.
_TOKEN_HEX_LENGTH = 16


@dataclass
class JurisdictionTransferPolicy:
    origin_country: str                # e.g. 'CN', 'CH', 'DE', 'US'
    destination_country: str           # e.g. 'US', 'UK', 'SG'
    policy_status: str                 # ALLOWED_UNRESTRICTED | REQUIRES_ANONYMIZATION | BLOCKED
    regulatory_framework: str          # e.g. 'PIPL', 'GDPR_Ch_V', 'Swiss_Art47_BankA'

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
    tax_id: Optional[str] = None       # PII; fully dropped on anonymized routes

@dataclass
class DataTransferAuditReport:
    trade_id: str
    origin_country: str
    destination_country: str
    transfer_approved: bool
    applied_anonymization: bool
    sanitized_payload: Optional[TradeDataPayload]
    audit_message: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

class CrossBorderTradeDataGovernanceEngine:
    """
    Compliance & data governance engine enforcing cross-border trade data transfer
    route policies, pseudonymizing PII fields, and blocking non-compliant egress.

    Scope note: masking/hashing here is PSEUDONYMIZATION, not anonymization.
    Pseudonymized data remains personal data under GDPR Art. 4(5) and de-identified
    (not anonymized) data under PIPL Art. 73, so a lawful Chapter V / PIPL Art. 38
    transfer mechanism is still required; this engine is the data-minimization and
    egress-control layer on top of that mechanism, not a substitute for it.
    """

    def __init__(
        self,
        policies: Optional[List[JurisdictionTransferPolicy]] = None,
        tokenization_key: Optional[str] = None,
    ):
        # Secret key for keyed pseudonymization (HMAC-SHA256, GDPR Art. 4(5)/Art. 32
        # technique per EDPB Guidelines 01/2025). If none is supplied, a random
        # per-engine salt is generated: tokens stay deterministic within this
        # engine's lifetime but cannot be joined across restarts or other engines.
        self._tokenization_key = tokenization_key.encode("utf-8") if tokenization_key else None
        self._salt = secrets.token_bytes(16)
        self.policies: Dict[Tuple[str, str], JurisdictionTransferPolicy] = {}
        self._audit_trail: List[Dict[str, object]] = []
        for p in (policies or []):
            self.register_policy(p)

    def register_policy(self, policy: JurisdictionTransferPolicy) -> None:
        """
        Registers a route policy. Unknown policy_status values raise ValueError
        (fail-closed) so a misconfigured status can never be treated as an
        unrestricted transfer.
        """
        if not isinstance(policy, JurisdictionTransferPolicy):
            raise TypeError(f"policy must be JurisdictionTransferPolicy, got {type(policy).__name__}")
        origin = self._normalize_country(policy.origin_country, "policy.origin_country")
        dest = self._normalize_country(policy.destination_country, "policy.destination_country")
        status = str(policy.policy_status).strip().upper()
        if status not in VALID_POLICY_STATUSES:
            raise ValueError(
                f"Invalid policy_status {policy.policy_status!r} for route "
                f"{origin}->{dest}; must be one of {sorted(VALID_POLICY_STATUSES)}"
            )
        normalized = JurisdictionTransferPolicy(
            origin_country=origin,
            destination_country=dest,
            policy_status=status,
            regulatory_framework=policy.regulatory_framework,
        )
        self.policies[(origin, dest)] = normalized

    @property
    def audit_trail(self) -> List[Dict[str, object]]:
        """
        Copy of the chronological compliance audit trail. One entry per
        process_data_transfer decision (approved, anonymized, or blocked).

        Each entry is itself copied, not shared: a caller that mutates a
        returned dict cannot rewrite the engine's own compliance record.
        A shallow ``list()`` copy left the entry dicts aliased, so an audit
        decision could be edited after the fact through this property.
        """
        return [dict(entry) for entry in self._audit_trail]

    def anonymize_trader_id(self, trader_id: str) -> str:
        """
        Pseudonymizes a trader ID via keyed HMAC-SHA256 (or salted SHA-256 when
        no tokenization key was configured). Unsalted/unkeyed hashing is NOT used:
        low-entropy trader IDs are recoverable via dictionary attacks (EDPB
        Guidelines 01/2025 on Pseudonymisation; AEPD-EDPS 2019 joint paper).
        The result is still linkable to the original by the key holder, i.e. it
        remains personal data under GDPR and de-identified data under PIPL.
        """
        if not trader_id:
            raise ValueError("trader_id must be a non-empty string")
        if self._tokenization_key is not None:
            digest = hmac.new(self._tokenization_key, trader_id.encode("utf-8"), hashlib.sha256).hexdigest()
        else:
            digest = hashlib.sha256(self._salt + trader_id.encode("utf-8")).hexdigest()
        return "TRD_HASH_" + digest[:_TOKEN_HEX_LENGTH]

    def redact_account_number(self, account_number: str) -> str:
        """
        Redacts account numbers, leaving only the last 4 digits. Short or empty
        values are fully masked so no complete identifier can survive redaction.
        """
        clean = str(account_number) if account_number else ""
        if len(clean) <= 4:
            return "****"
        return "XXXX-XXXX-" + clean[-4:]

    def process_data_transfer(
        self,
        payload: TradeDataPayload,
        destination_country: str
    ) -> DataTransferAuditReport:
        """
        Audits a proposed cross-border data transfer against the route policy,
        applying PII pseudonymization or blocking non-compliant transfers.

        Returns a DataTransferAuditReport in every case (including blocked
        transfers, where sanitized_payload is None and transfer_approved is
        False); nothing is raised for a blocked route.

        Policy precedence:
          1. An explicitly registered policy for (origin, destination) --
             honoured even when origin == destination.
          2. Otherwise origin == destination -> domestic, approved unmasked.
          3. Otherwise -> REQUIRES_ANONYMIZATION (default-deny).

        ``sanitized_payload`` is always a distinct object from the caller's
        input, so the report stays a faithful snapshot of what was decided
        even if the caller mutates the payload afterwards.
        """
        if not isinstance(payload, TradeDataPayload):
            raise TypeError(f"payload must be TradeDataPayload, got {type(payload).__name__}")
        dest = self._normalize_country(destination_country, "destination_country")
        orig = self._normalize_country(payload.origin_country, "payload.origin_country")
        for required in ("trade_id", "trader_id", "client_name", "account_number"):
            if not getattr(payload, required):
                raise ValueError(f"payload.{required} must be a non-empty string")

        # An explicitly registered policy always wins, including on a
        # same-country route. Checking `orig == dest` first let the domestic
        # shortcut silently override a registered same-country BLOCKED or
        # REQUIRES_ANONYMIZATION policy and release raw PII - a fail-open in
        # a fail-closed engine. Intra-country restrictions are real (e.g.
        # entity-to-entity disclosure limits, PIPL Art. 40 domestic storage),
        # so a configured policy is honoured rather than discarded.
        explicit_policy = self.policies.get((orig, dest))

        if explicit_policy is None and orig == dest:
            # Intra-jurisdiction transfer, no policy configured -> no border
            # crossed, no masking required.
            report = DataTransferAuditReport(
                trade_id=payload.trade_id, origin_country=orig, destination_country=dest,
                transfer_approved=True, applied_anonymization=False,
                sanitized_payload=replace(payload, origin_country=orig),
                audit_message="Domestic transfer within same jurisdiction approved without masking."
            )
            self._record_audit(report, "DOMESTIC_APPROVED", "IntraJurisdiction")
            return report

        if explicit_policy is None:
            # Default strict policy: Require anonymization for unknown cross-border routes
            policy_status = POLICY_REQUIRES_ANONYMIZATION
            framework = DEFAULT_STRICT_FRAMEWORK
        else:
            policy_status = explicit_policy.policy_status
            framework = explicit_policy.regulatory_framework

        if policy_status == POLICY_BLOCKED:
            msg = f"CROSS-BORDER TRANSFER BLOCKED [{orig} -> {dest}]: Prohibited by {framework}."
            logger.error(msg)
            report = DataTransferAuditReport(
                trade_id=payload.trade_id, origin_country=orig, destination_country=dest,
                transfer_approved=False, applied_anonymization=False, sanitized_payload=None,
                audit_message=msg
            )
            self._record_audit(report, "BLOCKED", framework)
            return report

        elif policy_status == POLICY_REQUIRES_ANONYMIZATION:
            sanitized = TradeDataPayload(
                trade_id=payload.trade_id,
                origin_country=orig,
                trader_id=self.anonymize_trader_id(payload.trader_id),
                client_name="ANONYMOUS_CLIENT",
                account_number=self.redact_account_number(payload.account_number),
                symbol=payload.symbol,
                quantity=payload.quantity,
                price=payload.price,
                # Tax IDs are dropped entirely: no partial tax identifier may
                # survive anonymization (last-4 masking is not acceptable here).
                tax_id=None,
            )
            msg = f"CROSS-BORDER TRANSFER APPROVED [{orig} -> {dest}]: PII pseudonymized under {framework}."
            logger.info(msg)
            report = DataTransferAuditReport(
                trade_id=payload.trade_id, origin_country=orig, destination_country=dest,
                transfer_approved=True, applied_anonymization=True, sanitized_payload=sanitized,
                audit_message=msg
            )
            self._record_audit(report, "APPROVED_PSEUDONYMIZED", framework)
            return report

        elif policy_status == POLICY_ALLOWED_UNRESTRICTED:
            report = DataTransferAuditReport(
                trade_id=payload.trade_id, origin_country=orig, destination_country=dest,
                transfer_approved=True, applied_anonymization=False,
                sanitized_payload=replace(payload, origin_country=orig),
                audit_message=f"Cross-border transfer [{orig} -> {dest}] approved unrestricted under {framework}."
            )
            self._record_audit(report, "APPROVED_UNRESTRICTED", framework)
            return report

        else:
            # Unreachable when policies are registered through register_policy;
            # guards against direct mutation of self.policies with invalid statuses.
            raise ValueError(
                f"Invalid policy_status {policy_status!r} for route {orig}->{dest}; "
                f"must be one of {sorted(VALID_POLICY_STATUSES)}"
            )

    @staticmethod
    def _normalize_country(code: str, field_name: str) -> str:
        if not isinstance(code, str) or not code.strip():
            raise ValueError(f"{field_name} must be a non-empty country code string")
        return code.strip().upper()

    def _record_audit(
        self, report: DataTransferAuditReport, decision: str, framework: str
    ) -> None:
        self._audit_trail.append({
            "timestamp": report.timestamp,
            "trade_id": report.trade_id,
            "origin_country": report.origin_country,
            "destination_country": report.destination_country,
            "decision": decision,
            "regulatory_framework": framework,
            "applied_anonymization": report.applied_anonymization,
            "audit_message": report.audit_message,
        })
