"""
Regional broker deployment-constraint engine.

Scope note (important): this module answers "where may my trading process run,
and what must its network egress look like, for broker X to accept its order
flow". It separates three things that are routinely conflated:

  1. Broker/exchange *access controls* that are real, in force, and enforced by
     the broker at the API boundary -- principally the SEBI/NSE static-IP
     whitelisting requirement for API order placement with Indian brokers.
  2. Legal *data-residency* mandates, which bind the entity the rule is written
     for. For a client running its own algo against a broker API there is no
     in-force SEBI, GDPR, or SEC mandate to host in any particular region; for a
     regulated entity or its outsourcing vendor the analysis is real but
     document-dependent, so the engine returns REVIEW_REQUIRED rather than
     inventing a verdict.
  3. Latency/operational *preference* (e.g. AWS Mumbai for Indian brokers),
     which is advisory and must never be reported as a compliance violation.

For the storage-residency question -- which region may hold trade records at
rest -- use `data-localization-requirements-for-trade-records`. For egress and
PII minimization use `cross-border-data-transfer-restrictions-for-trade-data`.
"""
import logging
import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# --- Decision statuses ----------------------------------------------------
# COMPLIANT is the only approval. Both other statuses carry is_deployable=False
# so a caller gating on that flag fails closed.
STATUS_COMPLIANT = "COMPLIANT"
STATUS_BLOCKED = "BLOCKED"
STATUS_REVIEW_REQUIRED = "REVIEW_REQUIRED"

# --- Finding severities ---------------------------------------------------
SEVERITY_BLOCKING = "BLOCKING"   # a verified, in-force constraint is breached
SEVERITY_REVIEW = "REVIEW"       # this engine cannot resolve it; escalate
SEVERITY_ADVISORY = "ADVISORY"   # operational preference, not a rule

# --- Deployer roles -------------------------------------------------------
# The role decides whether a residency rule binds *you*. A retail or proprietary
# client trading its own account through a broker API is not the addressee of
# SEBI CSCRF, the RBI payment-data circular, or SEC Rule 17a-4 / 17a-7.
ROLE_CLIENT = "CLIENT"
ROLE_REGULATED_ENTITY = "REGULATED_ENTITY"
ROLE_RE_VENDOR = "RE_VENDOR"
VALID_ROLES = frozenset({ROLE_CLIENT, ROLE_REGULATED_ENTITY, ROLE_RE_VENDOR})

# --- Egress IP posture ----------------------------------------------------
# SEBI/NSE require order API traffic to originate from a static IP registered
# with the broker. A cloud NAT gateway address shared with unrelated parties is
# static but not exclusively yours; Zerodha restricts sharing a registered IP to
# immediate family, so a shared address is an escalation, not an approval.
EGRESS_STATIC_DEDICATED = "STATIC_DEDICATED"
EGRESS_STATIC_SHARED = "STATIC_SHARED"
EGRESS_DYNAMIC = "DYNAMIC"
EGRESS_UNKNOWN = "UNKNOWN"
VALID_EGRESS_TYPES = frozenset({
    EGRESS_STATIC_DEDICATED,
    EGRESS_STATIC_SHARED,
    EGRESS_DYNAMIC,
    EGRESS_UNKNOWN,
})

# Cloud region -> jurisdiction of the region's physical location. A region name
# prefix is not a jurisdiction: eu-west-2 is London (UK) and eu-central-2 is
# Zurich (CH), neither of which is in the EEA. Used for advisory latency checks
# and for reporting; an unmapped region resolves to None, never to an approval.
REGION_TO_JURISDICTION: Dict[str, str] = {
    # AWS
    "ap-south-1": "IN",      # Mumbai
    "ap-south-2": "IN",      # Hyderabad
    "ap-southeast-1": "SG",
    "eu-west-1": "EU",       # Ireland
    "eu-west-3": "EU",       # Paris
    "eu-central-1": "EU",    # Frankfurt
    "eu-north-1": "EU",      # Stockholm
    "eu-south-1": "EU",      # Milan
    "eu-west-2": "UK",       # London -- third country post-Brexit
    "eu-central-2": "CH",    # Zurich -- never was EU/EEA
    "us-east-1": "US",
    "us-east-2": "US",
    "us-west-1": "US",
    "us-west-2": "US",
    # GCP
    "asia-south1": "IN",     # Mumbai
    "asia-south2": "IN",     # Delhi
    "asia-southeast1": "SG",
    "europe-west1": "EU",    # Belgium
    "europe-west3": "EU",    # Frankfurt
    "europe-west4": "EU",    # Netherlands
    "europe-west2": "UK",    # London
    "europe-west6": "CH",    # Zurich
    "us-central1": "US",
    "us-east1": "US",
    "us-west1": "US",
}


class BrokerDeploymentConstraintError(Exception):
    """Raised by assert_deployable when a deployment is not approved."""


@dataclass(frozen=True)
class Finding:
    """One constraint observation. `citation` names the instrument or document."""
    code: str
    severity: str
    message: str
    citation: str


@dataclass(frozen=True)
class BrokerConstraintProfile:
    """
    What a specific broker actually enforces, and what actually binds a deployer
    connecting to it. `residency_note` is deliberately prose: the residency
    position is document-dependent and must not be reduced to a region allowlist.
    """
    broker: str
    jurisdiction: str
    requires_static_order_ip: bool
    static_order_ip_must_be_domestic: bool
    client_residency_mandate: bool
    recommended_regions: Tuple[str, ...]
    access_citation: str
    residency_note: str


@dataclass(frozen=True)
class DeploymentProfile:
    """The deployment being assessed."""
    broker: str
    cloud_region: Optional[str] = None
    egress_ip_type: str = EGRESS_UNKNOWN
    places_orders: bool = True
    deployer_role: str = ROLE_CLIENT


@dataclass(frozen=True)
class DeploymentDecision:
    broker: str
    cloud_region: Optional[str]
    region_jurisdiction: Optional[str]
    status: str
    is_deployable: bool
    findings: Tuple[Finding, ...]

    def findings_by_severity(self, severity: str) -> List[Finding]:
        return [f for f in self.findings if f.severity == severity]


# Verified broker constraint matrix. Every entry is sourced in
# references/standards.md; do not add a broker without a citation.
BROKER_CONSTRAINT_PROFILES: Dict[str, BrokerConstraintProfile] = {
    "zerodha": BrokerConstraintProfile(
        broker="zerodha",
        jurisdiction="IN",
        requires_static_order_ip=True,
        # Zerodha states the registered IP need not be India-based, and accepts
        # both IPv4 and IPv6. The requirement is identity, not location.
        static_order_ip_must_be_domestic=False,
        client_residency_mandate=False,
        recommended_regions=("ap-south-1", "asia-south1"),
        access_citation=(
            "SEBI/HO/MIRSD/MIRSD-PoD/P/CIR/2025/0000013 (4 Feb 2025); "
            "Kite Connect API FAQs (static IP required for order requests)"
        ),
        residency_note=(
            "No in-force securities-market localisation mandate binds a client's own "
            "infrastructure: SEBI CSCRF standard PR.DS.S2 has been in abeyance since "
            "31 Dec 2024, and the RBI localisation circular covers payment system "
            "data, not client-side algo hosting."
        ),
    ),
    "upstox": BrokerConstraintProfile(
        broker="upstox",
        jurisdiction="IN",
        requires_static_order_ip=True,
        static_order_ip_must_be_domestic=False,
        client_residency_mandate=False,
        recommended_regions=("ap-south-1", "asia-south1"),
        access_citation=(
            "SEBI/HO/MIRSD/MIRSD-PoD/P/CIR/2025/0000013 (4 Feb 2025); "
            "Upstox Developer API static-IP endpoints (one primary plus one "
            "secondary IP, changeable once per calendar week)"
        ),
        residency_note=(
            "Same position as other SEBI-regulated brokers: no in-force client-side "
            "hosting-location mandate; SEBI CSCRF PR.DS.S2 remains in abeyance."
        ),
    ),
    "degiro": BrokerConstraintProfile(
        broker="degiro",
        jurisdiction="EU",
        requires_static_order_ip=False,
        static_order_ip_must_be_domestic=False,
        client_residency_mandate=False,
        recommended_regions=("eu-west-1", "eu-central-1", "europe-west1"),
        access_citation="DEGIRO publishes no official public trading API.",
        residency_note=(
            "GDPR imposes no data-localisation requirement -- it regulates the "
            "transfer (Chapter V), not the hosting region. MiFID II Art. 16(6) "
            "requires records to be retained and made available to the competent "
            "authority, not stored in the EU. DORA obligations attach to financial "
            "entities and their ICT providers, not to a broker's clients."
        ),
    ),
    "alpaca": BrokerConstraintProfile(
        broker="alpaca",
        jurisdiction="US",
        requires_static_order_ip=False,
        static_order_ip_must_be_domestic=False,
        client_residency_mandate=False,
        recommended_regions=("us-east-1", "us-east-2", "us-central1"),
        access_citation=(
            "No static-IP whitelisting requirement is documented for the Alpaca "
            "trading API."
        ),
        residency_note=(
            "SEC Rule 17a-4 imposes retention and prompt-production duties on "
            "broker-dealers, not a hosting-region mandate. Rule 17a-7's US-location "
            "duty addresses non-resident registered broker-dealers, not their clients."
        ),
    ),
}


class BrokerDeploymentConstraintEngine:
    """
    Evaluates a deployment against the constraints a broker actually enforces.

    The engine fails closed: an unknown broker, an unknown cloud region, or a
    deployer that is itself regulated resolves to REVIEW_REQUIRED. Only a fully
    resolved, constraint-satisfying deployment returns COMPLIANT.
    """

    def __init__(
        self,
        profiles: Optional[Dict[str, BrokerConstraintProfile]] = None,
        region_map: Optional[Dict[str, str]] = None,
    ) -> None:
        self.profiles = dict(profiles) if profiles is not None else dict(BROKER_CONSTRAINT_PROFILES)
        self.region_map = dict(region_map) if region_map is not None else dict(REGION_TO_JURISDICTION)
        self._audit_trail: List[DeploymentDecision] = []

    @property
    def audit_trail(self) -> List[DeploymentDecision]:
        """Copy of the decision log. Decisions are frozen and cannot be edited."""
        return list(self._audit_trail)

    @staticmethod
    def probe_current_region() -> Optional[Tuple[str, str]]:
        """
        Detects (provider, region) from the environment.

        Returns None when nothing is set. It deliberately does NOT fall back to a
        default region: guessing a region that happens to satisfy a constraint is
        how an unconfigured process silently passes a deployment gate.
        """
        for provider, variables in (
            ("AWS", ("AWS_REGION", "AWS_DEFAULT_REGION")),
            ("GCP", ("GCP_REGION", "CLOUD_RUN_REGION")),
            ("CUSTOM", ("TRADING_HOST_REGION",)),
        ):
            for variable in variables:
                value = (os.environ.get(variable) or "").strip()
                if value:
                    return (provider, value.lower())
        logger.warning("No cloud region environment variable set; region is unresolved.")
        return None

    def evaluate(self, profile: DeploymentProfile) -> DeploymentDecision:
        """Assesses `profile` and records the decision in the audit trail."""
        broker_key = self._normalize_broker(profile.broker)
        region = self._normalize_region(profile.cloud_region)
        egress = self._validate_egress(profile.egress_ip_type)
        role = self._validate_role(profile.deployer_role)

        findings: List[Finding] = []
        constraints = self.profiles.get(broker_key)

        if constraints is None:
            findings.append(Finding(
                code="BROKER_NOT_REGISTERED",
                severity=SEVERITY_REVIEW,
                message=(
                    f"No verified constraint profile for broker '{profile.broker}'. "
                    "Confirm the broker's API access controls and the deployer's "
                    "regulatory status before deploying."
                ),
                citation="Engine policy: absence of a rule is not permission.",
            ))
        else:
            findings.extend(
                self._assess_order_access(constraints, egress, profile.places_orders, region)
            )
            findings.extend(self._assess_residency(constraints, role))
            findings.extend(self._assess_region_preference(constraints, region))

        region_jurisdiction = self.region_map.get(region) if region else None
        if region is None:
            findings.append(Finding(
                code="REGION_UNRESOLVED",
                severity=SEVERITY_REVIEW,
                message=(
                    "Deployment cloud region is unknown; residency and latency posture "
                    "cannot be assessed."
                ),
                citation="Engine policy: an unresolved region is never treated as compliant.",
            ))
        elif region_jurisdiction is None:
            findings.append(Finding(
                code="REGION_UNMAPPED",
                severity=SEVERITY_REVIEW,
                message=(
                    f"Region '{region}' is not in the region-to-jurisdiction map, so its "
                    "physical location is unverified. Map it before relying on this decision."
                ),
                citation="Engine policy: region name prefixes do not identify jurisdictions.",
            ))

        decision = self._finalize(profile.broker, region, region_jurisdiction, findings)
        self._audit_trail.append(decision)
        return decision

    def assert_deployable(self, profile: DeploymentProfile) -> DeploymentDecision:
        """Evaluates `profile` and raises unless the decision is COMPLIANT."""
        decision = self.evaluate(profile)
        if not decision.is_deployable:
            detail = "; ".join(
                f"[{f.severity}] {f.code}: {f.message}"
                for f in decision.findings
                if f.severity != SEVERITY_ADVISORY
            )
            raise BrokerDeploymentConstraintError(
                f"Deployment not approved for broker '{profile.broker}' "
                f"(status={decision.status}): {detail}"
            )
        return decision

    # --- constraint assessments ------------------------------------------
    def _assess_order_access(
        self,
        constraints: BrokerConstraintProfile,
        egress: str,
        places_orders: bool,
        region: Optional[str],
    ) -> List[Finding]:
        if not constraints.requires_static_order_ip:
            return []
        if not places_orders:
            # Verified carve-out: the SEBI static-IP requirement attaches to order
            # requests. Market data, WebSocket, order book and position endpoints
            # remain reachable from any address.
            return [Finding(
                code="STATIC_IP_NOT_REQUIRED_FOR_DATA",
                severity=SEVERITY_ADVISORY,
                message=(
                    "Read-only deployment: the static-IP requirement applies to order "
                    "requests, not to market data or portfolio endpoints."
                ),
                citation=constraints.access_citation,
            )]

        if egress == EGRESS_STATIC_DEDICATED:
            if (
                constraints.static_order_ip_must_be_domestic
                and region is not None
                and self.region_map.get(region) != constraints.jurisdiction
            ):
                return [Finding(
                    code="STATIC_IP_JURISDICTION_MISMATCH",
                    severity=SEVERITY_BLOCKING,
                    message=(
                        f"Broker requires the registered order IP to be in "
                        f"{constraints.jurisdiction}; region '{region}' is not."
                    ),
                    citation=constraints.access_citation,
                )]
            return []
        if egress == EGRESS_DYNAMIC:
            return [Finding(
                code="DYNAMIC_EGRESS_IP",
                severity=SEVERITY_BLOCKING,
                message=(
                    "Order API access requires a static IP registered with the broker. "
                    "A dynamic or ephemeral egress address (the default on serverless "
                    "or autoscaled hosts) will have its order requests rejected."
                ),
                citation=constraints.access_citation,
            )]
        if egress == EGRESS_STATIC_SHARED:
            return [Finding(
                code="SHARED_STATIC_EGRESS_IP",
                severity=SEVERITY_REVIEW,
                message=(
                    "Egress IP is static but shared. A registered order IP may be shared "
                    "only within the limits the broker sets (Zerodha: immediate family); "
                    "sharing beyond that risks API-key suspension."
                ),
                citation=constraints.access_citation,
            )]
        return [Finding(
            code="EGRESS_IP_UNKNOWN",
            severity=SEVERITY_REVIEW,
            message=(
                "Egress IP posture is unknown and the broker enforces static-IP order access."
            ),
            citation=constraints.access_citation,
        )]

    def _assess_residency(
        self, constraints: BrokerConstraintProfile, role: str
    ) -> List[Finding]:
        if role == ROLE_CLIENT:
            if constraints.client_residency_mandate:
                return [Finding(
                    code="CLIENT_RESIDENCY_MANDATE",
                    severity=SEVERITY_REVIEW,
                    message=f"A client-side residency mandate is recorded: {constraints.residency_note}",
                    citation=constraints.access_citation,
                )]
            return [Finding(
                code="NO_CLIENT_RESIDENCY_MANDATE",
                severity=SEVERITY_ADVISORY,
                message=constraints.residency_note,
                citation="See references/standards.md for the per-regime sources.",
            )]
        return [Finding(
            code="REGULATED_DEPLOYER_REVIEW",
            severity=SEVERITY_REVIEW,
            message=(
                f"Deployer role '{role}' is subject to obligations this engine cannot "
                f"resolve (outsourcing / ICT third-party contract terms, recordkeeping "
                f"location duties, regulator notification). Jurisdiction: "
                f"{constraints.jurisdiction}. {constraints.residency_note}"
            ),
            citation="See references/standards.md for the per-regime sources.",
        )]

    def _assess_region_preference(
        self, constraints: BrokerConstraintProfile, region: Optional[str]
    ) -> List[Finding]:
        if region is None or not constraints.recommended_regions:
            return []
        if region in constraints.recommended_regions:
            return []
        return [Finding(
            code="REGION_NOT_LATENCY_PREFERRED",
            severity=SEVERITY_ADVISORY,
            message=(
                f"Region '{region}' is outside the latency-preferred set "
                f"{list(constraints.recommended_regions)} for this broker. This is an "
                "operational preference, not a compliance violation."
            ),
            citation="Operational guidance only.",
        )]

    # --- helpers ----------------------------------------------------------
    def _finalize(
        self,
        broker: str,
        region: Optional[str],
        region_jurisdiction: Optional[str],
        findings: List[Finding],
    ) -> DeploymentDecision:
        if any(f.severity == SEVERITY_BLOCKING for f in findings):
            status = STATUS_BLOCKED
        elif any(f.severity == SEVERITY_REVIEW for f in findings):
            status = STATUS_REVIEW_REQUIRED
        else:
            status = STATUS_COMPLIANT
        decision = DeploymentDecision(
            broker=broker,
            cloud_region=region,
            region_jurisdiction=region_jurisdiction,
            status=status,
            is_deployable=(status == STATUS_COMPLIANT),
            findings=tuple(findings),
        )
        logger.info(
            "Broker deployment decision: broker=%s region=%s status=%s findings=%d",
            broker, region, status, len(findings),
        )
        return decision

    @staticmethod
    def _normalize_broker(broker: str) -> str:
        if not isinstance(broker, str) or not broker.strip():
            raise ValueError("broker must be a non-empty string")
        return broker.strip().lower()

    @staticmethod
    def _normalize_region(region: Optional[str]) -> Optional[str]:
        if region is None:
            return None
        if not isinstance(region, str):
            raise ValueError("cloud_region must be a string or None")
        normalized = region.strip().lower()
        return normalized or None

    @staticmethod
    def _validate_egress(egress_ip_type: str) -> str:
        if egress_ip_type not in VALID_EGRESS_TYPES:
            raise ValueError(
                f"egress_ip_type must be one of {sorted(VALID_EGRESS_TYPES)}, "
                f"got {egress_ip_type!r}"
            )
        return egress_ip_type

    @staticmethod
    def _validate_role(deployer_role: str) -> str:
        if deployer_role not in VALID_ROLES:
            raise ValueError(
                f"deployer_role must be one of {sorted(VALID_ROLES)}, got {deployer_role!r}"
            )
        return deployer_role
