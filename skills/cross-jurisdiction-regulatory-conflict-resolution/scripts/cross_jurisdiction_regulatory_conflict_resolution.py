import logging
import math
import re
from dataclasses import dataclass, field, replace
from enum import IntEnum
from typing import Dict, List, Optional, Sequence, Set, Tuple

logger = logging.getLogger(__name__)


class ShortSellingRestriction(IntEnum):
    """
    Ordinal severity of a jurisdiction's short-selling regime, ordered by how
    much it restricts the ability to execute a short sale. Strictest Rule
    Primacy takes the MAX across applicable jurisdictions, so the ordering is
    load-bearing and must not be reshuffled:

      0 NONE       - no short-sale-specific constraint.
      1 REPORTING  - disclosure obligation only; the trade may proceed
                     (e.g. EU SSR Reg. 236/2012 net short position
                     notification at 0.1% of issued share capital, permanent
                     since Commission Delegated Regulation (EU) 2022/27).
      2 PRICE_TEST - the trade may proceed only at permitted prices
                     (e.g. SEC Reg SHO Rule 201 alternative uptick rule, live
                     for the remainder of the day and the next day once a
                     security falls 10% from the prior close).
      3 BAN        - short selling prohibited outright (e.g. an NCA emergency
                     ban under SSR Arts. 20/23, or a national ban such as
                     Korea's Nov 2023 - 31 Mar 2025 prohibition).

    NOTE (breaking change in skill v2.0.0): levels 1 and 2 previously meant
    UPTICK and NET_SHORT_REPORTING respectively. That encoding ranked a
    disclosure-only regime ABOVE a price test, so a firm trading under a
    price-test jurisdiction and a reporting jurisdiction resolved to
    "reporting" and silently dropped the price-test obligation. Existing
    integer configurations for levels 1 and 2 must be re-checked; prefer the
    named enum members over bare integers.
    """

    NONE = 0
    REPORTING = 1
    PRICE_TEST = 2
    BAN = 3


# Obligation codes surfaced on an APPROVED decision. The engine resolves and
# reports these; enforcing them requires market data (price test) or a
# reporting pipeline (net short position) that lives outside this module.
OBLIGATION_LEI_TAGGING = "LEI_TAGGING_REQUIRED"
OBLIGATION_NATIONAL_CLIENT_ID = "NATIONAL_CLIENT_ID_REQUIRED"
OBLIGATION_SHORT_REPORTING = "SHORT_SELL_POSITION_REPORTING_REQUIRED"
OBLIGATION_SHORT_PRICE_TEST = "SHORT_SELL_PRICE_TEST_APPLIES"

# ISO 17442-1: 20 upper-case alphanumeric characters, where positions 19-20 are
# ISO/IEC 7064 MOD 97-10 check digits (numeric).
_LEI_PATTERN = re.compile(r"^[A-Z0-9]{18}[0-9]{2}$")
_LEI_LENGTH = 20


def is_valid_lei(candidate: Optional[str]) -> bool:
    """
    Structural validation of a Legal Entity Identifier per ISO 17442-1:2020.

    Checks the character set and length (20 upper-case alphanumerics, the last
    two numeric check digits) and the ISO/IEC 7064 MOD 97-10 checksum: with each
    letter mapped to its base-36 value (A=10 ... Z=35), the whole 20-character
    code read as one integer must satisfy ``value % 97 == 1``.

    Lower-case input is rejected rather than silently upper-cased: the tag held
    on the order record must be byte-identical to what is reported downstream.

    LIMITATION: this is an offline structural check only. It cannot tell whether
    the LEI is actually issued, is attached to the right entity, or carries an
    active registration status in the GLEIF database - all of which MiFIR Art. 26
    transaction reporting requires. A GLEIF lookup is still needed before the
    tag is relied on for reporting.
    """
    if not isinstance(candidate, str):
        return False
    lei = candidate.strip()
    if len(lei) != _LEI_LENGTH or not _LEI_PATTERN.match(lei):
        return False
    # int(c, 36) maps '0'-'9' -> 0-9 and 'A'-'Z' -> 10-35, which is exactly the
    # alphanumeric-to-numeric conversion ISO/IEC 7064 MOD 97-10 specifies.
    numeric = "".join(str(int(char, 36)) for char in lei)
    return int(numeric) % 97 == 1


@dataclass
class JurisdictionRules:
    """
    Rule profile for one regulatory regime, supplied by compliance.

    ``short_selling_restriction_level`` uses the ShortSellingRestriction
    ordering; values outside 0-3 are rejected so an out-of-range integer cannot
    silently dominate (or be dominated in) the MAX resolution.
    """

    jurisdiction_code: str               # e.g. 'US_SEC', 'EU_MIFID_II', 'UK_FCA', 'HK_SFC'
    is_pfof_allowed: bool                # Payment For Order Flow permitted for this regime?
    is_lei_mandatory: bool               # Legal Entity Identifier tagging mandatory?
    short_selling_restriction_level: int # See ShortSellingRestriction

    def __post_init__(self) -> None:
        self.jurisdiction_code = _normalize_jurisdiction(
            self.jurisdiction_code, "jurisdiction_code"
        )
        if not isinstance(self.is_pfof_allowed, bool) or not isinstance(self.is_lei_mandatory, bool):
            raise TypeError("is_pfof_allowed and is_lei_mandatory must be booleans")
        level = self.short_selling_restriction_level
        if isinstance(level, bool) or not isinstance(level, int):
            raise TypeError(
                f"short_selling_restriction_level must be an int in 0-3, got {level!r}"
            )
        if level not in {int(member) for member in ShortSellingRestriction}:
            raise ValueError(
                f"short_selling_restriction_level {level} is out of range for "
                f"{self.jurisdiction_code}; valid values are "
                f"{[int(m) for m in ShortSellingRestriction]} (see ShortSellingRestriction)"
            )


@dataclass
class TradeOrderRequest:
    """
    Proposed order presented to the pre-trade compliance gate.

    ``is_natural_person_client`` / ``national_client_id`` exist because an LEI
    identifies a LEGAL entity only. Under MiFIR Art. 26 / RTS 22 (Commission
    Delegated Regulation (EU) 2017/590) Art. 6 and Annex II, a natural-person
    client is identified by a national client identifier (or the CONCAT
    fallback), not an LEI. Without this distinction the gate would reject every
    retail natural-person order for a "missing LEI".
    """

    order_id: str
    entity_jurisdiction: str
    venue_jurisdiction: str
    symbol: str
    quantity: float
    price: float
    is_short: bool
    routed_via_pfof: bool
    lei_tag: Optional[str]
    is_natural_person_client: bool = False
    national_client_id: Optional[str] = None


@dataclass
class RegulatoryComplianceDecision:
    """
    Immutable-by-convention record of one pre-trade compliance decision.

    ``required_obligations`` carries constraints that do NOT block the order but
    must be honoured downstream (price test, net short position reporting, LEI
    tagging). They are actionable only when ``is_approved`` is True - a rejected
    order is rejected regardless of the obligations listed alongside it.
    ``unregistered_jurisdictions`` names any applicable jurisdiction
    that had no configured rule profile and was therefore evaluated under the
    fail-closed default - a configuration signal, not a market signal.
    """

    order_id: str
    is_approved: bool
    resolved_pfof_allowed: bool
    resolved_lei_mandatory: bool
    resolved_max_short_restriction_level: int
    violations: List[str]
    applied_rules_summary: str
    required_obligations: List[str] = field(default_factory=list)
    unregistered_jurisdictions: List[str] = field(default_factory=list)
    applicable_jurisdictions: List[str] = field(default_factory=list)


def _normalize_jurisdiction(code: object, field_name: str) -> str:
    """
    Normalizes a jurisdiction code to trimmed upper case, rejecting anything
    empty or non-string. A blank code cannot be resolved to a rule profile, and
    approving an order whose applicable regime is unknown is exactly the
    fail-open this engine exists to prevent.
    """
    if not isinstance(code, str) or not code.strip():
        raise ValueError(f"{field_name} must be a non-empty string, got {code!r}")
    return code.strip().upper()


class CrossJurisdictionRegulatoryConflictEngine:
    """
    Pre-trade compliance gate resolving conflicting regulatory rules across the
    regimes applicable to a cross-border order (e.g. SEC vs MiFIR vs FCA) under
    Strictest Rule Primacy, and recording an audit decision for every order.

    SCOPE (important): Strictest Rule Primacy is a conservative FIRM POLICY
    heuristic, not a conflict-of-laws determination. It works only for
    prohibition-style rules, where obeying the strictest regime also satisfies
    the others. It cannot resolve a mandate-vs-prohibition conflict (regime A
    requires disclosing what regime B forbids disclosing); such conflicts need
    legal advice, a blocking-statute analysis, or a regulatory waiver, and this
    engine has no way to detect them. It also models exactly three rule
    dimensions - PFOF, LEI tagging, and short-selling severity. Anything else
    (research payment arrangements, best-execution policy, position limits,
    market-abuse surveillance) is out of scope.
    """

    def __init__(self, rules: Optional[Sequence[JurisdictionRules]] = None) -> None:
        self.rules: Dict[str, JurisdictionRules] = {}
        self._audit_trail: List[RegulatoryComplianceDecision] = []
        for rule in (rules or []):
            self.register_jurisdiction_rules(rule)

    def register_jurisdiction_rules(self, rule: JurisdictionRules) -> None:
        """Registers (or replaces) the rule profile for one jurisdiction."""
        if not isinstance(rule, JurisdictionRules):
            raise TypeError(
                f"rule must be JurisdictionRules, got {type(rule).__name__}"
            )
        self.rules[_normalize_jurisdiction(rule.jurisdiction_code, "jurisdiction_code")] = rule

    @property
    def audit_trail(self) -> List[RegulatoryComplianceDecision]:
        """
        Chronological copy of every decision returned by ``evaluate_order``.

        Each entry is deep-copied at the list level too, so a caller mutating a
        returned decision (or its ``violations`` list) cannot rewrite a recorded
        REJECTED decision into an approval after the fact.
        """
        return [self._copy_decision(decision) for decision in self._audit_trail]

    @staticmethod
    def _copy_decision(
        decision: RegulatoryComplianceDecision,
    ) -> RegulatoryComplianceDecision:
        return replace(
            decision,
            violations=list(decision.violations),
            required_obligations=list(decision.required_obligations),
            unregistered_jurisdictions=list(decision.unregistered_jurisdictions),
            applicable_jurisdictions=list(decision.applicable_jurisdictions),
        )

    def resolve_strictest_rules(self, jurisdictions: Set[str]) -> Tuple[bool, bool, int]:
        """
        Applies Strictest Rule Primacy across a set of overlapping jurisdictions:
        - PFOF Allowed: True ONLY IF allowed in ALL jurisdictions (AND logic)
        - LEI Mandatory: True IF mandatory in ANY jurisdiction (OR logic)
        - Short Restriction: MAX severity across jurisdictions

        An unregistered jurisdiction resolves to the STRICTEST value on every
        dimension (PFOF banned, LEI mandatory, short selling banned) so a
        missing rule profile fails closed rather than waving an order through.
        Previously the short-selling fallback used level 2 of 3, which was the
        only fallback that was not maximally strict.

        Raises ValueError on an empty jurisdiction set: with nothing to resolve,
        the accumulators would return "PFOF allowed, no LEI, no restriction" -
        a fail-open in a fail-closed engine.
        """
        normalized = {
            _normalize_jurisdiction(code, "jurisdiction") for code in jurisdictions
        }
        if not normalized:
            raise ValueError(
                "resolve_strictest_rules requires at least one jurisdiction; "
                "an empty set would resolve to the most permissive rule set."
            )

        pfof_allowed = True
        lei_mandatory = False
        max_short_level = int(ShortSellingRestriction.NONE)

        for code in normalized:
            j_rule = self.rules.get(code)
            if j_rule is None:
                # Fail-closed default for an unconfigured jurisdiction.
                logger.warning(
                    "No rule profile registered for jurisdiction %s; applying "
                    "fail-closed defaults (PFOF blocked, LEI mandatory, short selling banned).",
                    code,
                )
                pfof_allowed = False
                lei_mandatory = True
                max_short_level = max(max_short_level, int(ShortSellingRestriction.BAN))
                continue

            if not j_rule.is_pfof_allowed:
                pfof_allowed = False
            if j_rule.is_lei_mandatory:
                lei_mandatory = True
            max_short_level = max(max_short_level, j_rule.short_selling_restriction_level)

        return pfof_allowed, lei_mandatory, max_short_level

    def evaluate_order(self, request: TradeOrderRequest) -> RegulatoryComplianceDecision:
        """
        Audits a proposed order against the resolved strictest rule set and
        records the decision on ``audit_trail``.

        Decision points:
          - PFOF routing is a VIOLATION when any applicable regime bans it.
          - LEI tagging is a VIOLATION when required and the tag is missing or
            structurally invalid (ISO 17442 checksum), EXCEPT for natural-person
            clients, who require a national client identifier instead.
          - Short selling is a VIOLATION only at severity BAN. PRICE_TEST and
            REPORTING do not block the order; they are returned as
            ``required_obligations`` because enforcing them needs market data or
            a reporting pipeline outside this module. Silently approving them
            with no output was the previous behaviour and dropped the obligation.

        Malformed input (blank jurisdiction/order id/symbol, non-finite or
        non-positive quantity, non-finite or negative price) raises ValueError
        before any decision is taken - a compliance gate must not emit an
        APPROVED decision for an order it could not parse.
        """
        if not isinstance(request, TradeOrderRequest):
            raise TypeError(
                f"request must be TradeOrderRequest, got {type(request).__name__}"
            )
        for required in ("order_id", "symbol"):
            value = getattr(request, required)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"request.{required} must be a non-empty string")
        entity = _normalize_jurisdiction(request.entity_jurisdiction, "entity_jurisdiction")
        venue = _normalize_jurisdiction(request.venue_jurisdiction, "venue_jurisdiction")
        self._validate_numeric(request.quantity, "quantity", allow_zero=False)
        self._validate_numeric(request.price, "price", allow_zero=True)

        # Sorted, de-duplicated: audit messages must be byte-reproducible.
        # Iterating a set produced a different jurisdiction order between runs,
        # so two identical orders could yield different audit records.
        jurisdictions: List[str] = sorted({entity, venue})
        jurisdiction_label = ", ".join(jurisdictions)
        unregistered = [code for code in jurisdictions if code not in self.rules]

        pfof_allowed, lei_mandatory, max_short_level = self.resolve_strictest_rules(
            set(jurisdictions)
        )

        violations: List[str] = []
        obligations: List[str] = []

        # 1. Audit PFOF Compliance
        if request.routed_via_pfof and not pfof_allowed:
            violations.append(
                f"PFOF VIOLATION: Order routed via Payment for Order Flow, but PFOF is "
                f"banned in applicable regimes ({jurisdiction_label})."
            )

        # 2. Audit client identification (LEI for legal entities, national ID for
        #    natural persons per MiFIR Art. 26 / RTS 22 Art. 6 and Annex II).
        if lei_mandatory:
            if request.is_natural_person_client:
                national_id = request.national_client_id
                if not isinstance(national_id, str) or not national_id.strip():
                    violations.append(
                        f"CLIENT ID VIOLATION: Natural-person client requires a national "
                        f"client identifier (RTS 22 Annex II / CONCAT) under ({jurisdiction_label})."
                    )
                else:
                    obligations.append(OBLIGATION_NATIONAL_CLIENT_ID)
            elif not is_valid_lei(request.lei_tag):
                violations.append(
                    f"LEI VIOLATION: Missing or structurally invalid Legal Entity Identifier "
                    f"(ISO 17442: 20 upper-case alphanumerics with valid MOD 97-10 check "
                    f"digits) under ({jurisdiction_label})."
                )
            else:
                obligations.append(OBLIGATION_LEI_TAGGING)

        # 3. Audit Short Selling Restrictions
        if request.is_short:
            if max_short_level >= int(ShortSellingRestriction.BAN):
                violations.append(
                    f"SHORT SELLING VIOLATION: Short selling banned in applicable regimes "
                    f"({jurisdiction_label})."
                )
            elif max_short_level == int(ShortSellingRestriction.PRICE_TEST):
                obligations.append(OBLIGATION_SHORT_PRICE_TEST)
            elif max_short_level == int(ShortSellingRestriction.REPORTING):
                obligations.append(OBLIGATION_SHORT_REPORTING)

        is_approved = len(violations) == 0

        summary = (
            f"Strictest Rules [{jurisdiction_label}]: PFOF Allowed={pfof_allowed}, "
            f"LEI Mandatory={lei_mandatory}, Max Short Level={max_short_level}"
            f" ({ShortSellingRestriction(max_short_level).name})"
        )
        if unregistered:
            summary += f"; Unregistered jurisdictions (fail-closed): {', '.join(unregistered)}"

        if not is_approved:
            logger.error(
                "Order %s REJECTED: %s", request.order_id, "; ".join(violations)
            )
        else:
            logger.info(
                "Order %s APPROVED under cross-border compliance. Obligations: %s",
                request.order_id,
                ", ".join(obligations) or "none",
            )

        decision = RegulatoryComplianceDecision(
            order_id=request.order_id,
            is_approved=is_approved,
            resolved_pfof_allowed=pfof_allowed,
            resolved_lei_mandatory=lei_mandatory,
            resolved_max_short_restriction_level=max_short_level,
            violations=violations,
            applied_rules_summary=summary,
            required_obligations=obligations,
            unregistered_jurisdictions=unregistered,
            applicable_jurisdictions=jurisdictions,
        )
        self._audit_trail.append(self._copy_decision(decision))
        return decision

    @staticmethod
    def _validate_numeric(value: object, field_name: str, allow_zero: bool) -> None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"request.{field_name} must be a real number, got {value!r}")
        numeric = float(value)
        if not math.isfinite(numeric):
            raise ValueError(f"request.{field_name} must be finite, got {value!r}")
        if numeric < 0 or (numeric == 0 and not allow_zero):
            raise ValueError(
                f"request.{field_name} must be "
                f"{'non-negative' if allow_zero else 'strictly positive'}, got {value!r}"
            )
