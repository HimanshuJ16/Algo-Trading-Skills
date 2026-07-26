import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

@dataclass
class JurisdictionRules:
    jurisdiction_code: str             # e.g. 'US_SEC', 'EU_MIFID_II', 'UK_FCA', 'HK_SFC'
    is_pfof_allowed: bool              # Payment For Order Flow allowed?
    is_lei_mandatory: bool             # Legal Entity Identifier tagging mandatory?
    short_selling_restriction_level: int # 0 = Standard, 1 = Uptick Rule, 2 = Net Short Reporting, 3 = Total Ban

@dataclass
class TradeOrderRequest:
    order_id: str
    entity_jurisdiction: str
    venue_jurisdiction: str
    symbol: str
    quantity: float
    price: float
    is_short: bool
    routed_via_pfof: bool
    lei_tag: Optional[str]

@dataclass
class RegulatoryComplianceDecision:
    order_id: str
    is_approved: bool
    resolved_pfof_allowed: bool
    resolved_lei_mandatory: bool
    resolved_max_short_restriction_level: int
    violations: List[str]
    applied_rules_summary: str

class CrossJurisdictionRegulatoryConflictEngine:
    """
    Compliance resolution engine for resolving conflicting regulatory rules (SEC vs MiFID II vs FCA)
    using Strictest Rule Primacy and auditing pre-trade order routing.
    """
    def __init__(self, rules: List[JurisdictionRules] = None):
        self.rules: Dict[str, JurisdictionRules] = {r.jurisdiction_code.upper(): r for r in (rules or [])}

    def register_jurisdiction_rules(self, rule: JurisdictionRules):
        self.rules[rule.jurisdiction_code.upper()] = rule

    def resolve_strictest_rules(self, jurisdictions: Set[str]) -> Tuple[bool, bool, int]:
        """
        Applies Strictest Rule Primacy across a set of overlapping jurisdictions:
        - PFOF Allowed: True ONLY IF allowed in ALL jurisdictions (AND logic)
        - LEI Mandatory: True IF mandatory in ANY jurisdiction (OR logic)
        - Short Restriction: MAX restriction level across jurisdictions
        """
        pfof_allowed = True
        lei_mandatory = False
        max_short_level = 0

        for j_code in jurisdictions:
            code = j_code.upper()
            if code not in self.rules:
                # Default fallback: Strict assumption for unknown jurisdiction
                pfof_allowed = False
                lei_mandatory = True
                max_short_level = max(max_short_level, 2)
                continue

            j_rule = self.rules[code]
            if not j_rule.is_pfof_allowed:
                pfof_allowed = False
            if j_rule.is_lei_mandatory:
                lei_mandatory = True
            max_short_level = max(max_short_level, j_rule.short_selling_restriction_level)

        return pfof_allowed, lei_mandatory, max_short_level

    def evaluate_order(self, request: TradeOrderRequest) -> RegulatoryComplianceDecision:
        """
        Audits a proposed trade order request against resolved strictest regulatory rules.
        """
        jurisdictions = {request.entity_jurisdiction, request.venue_jurisdiction}
        pfof_allowed, lei_mandatory, max_short_level = self.resolve_strictest_rules(jurisdictions)

        violations = []

        # 1. Audit PFOF Compliance
        if request.routed_via_pfof and not pfof_allowed:
            violations.append(
                f"PFOF VIOLATION: Order routed via Payment for Order Flow, but PFOF is banned in applicable regimes ({', '.join(jurisdictions)})."
            )

        # 2. Audit LEI Mandatory Tagging
        if lei_mandatory and (not request.lei_tag or len(request.lei_tag.strip()) != 20):
            violations.append(
                f"LEI VIOLATION: Missing or invalid 20-character Legal Entity Identifier (LEI) tag under ({', '.join(jurisdictions)})."
            )

        # 3. Audit Short Selling Restrictions
        if request.is_short and max_short_level == 3:
            violations.append(
                f"SHORT SELLING VIOLATION: Total short selling ban active in target jurisdiction."
            )

        is_approved = len(violations) == 0

        summary = (
            f"Strictest Rules [{', '.join(jurisdictions)}]: PFOF Allowed={pfof_allowed}, "
            f"LEI Mandatory={lei_mandatory}, Max Short Level={max_short_level}"
        )

        if not is_approved:
            logger.error(f"Order {request.order_id} REJECTED: {'; '.join(violations)}")
        else:
            logger.info(f"Order {request.order_id} APPROVED under cross-border compliance.")

        return RegulatoryComplianceDecision(
            order_id=request.order_id,
            is_approved=is_approved,
            resolved_pfof_allowed=pfof_allowed,
            resolved_lei_mandatory=lei_mandatory,
            resolved_max_short_restriction_level=max_short_level,
            violations=violations,
            applied_rules_summary=summary
        )
