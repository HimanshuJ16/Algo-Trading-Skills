"""
Data localization / residency audit engine for trade records.

Scope note (important): this module is a *storage-residency* gate. It decides
whether a given cloud region is an acceptable resting place for a class of trade
record given the record's origin jurisdiction. It deliberately does NOT decide
whether a cross-border transfer is lawful -- that depends on a legal instrument
(GDPR Chapter V adequacy/SCCs, PIPL Art. 38 CAC assessment / standard contract /
certification) that lives outside this process. Where such a mechanism is the
deciding factor the engine returns TRANSFER_MECHANISM_REQUIRED rather than
inventing an approval. Pair with
`cross-border-data-transfer-restrictions-for-trade-data`.
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# --- Audit outcomes -------------------------------------------------------
# COMPLIANT and LOCALIZATION_VIOLATION_BLOCKED are terminal decisions. The two
# middle statuses are explicit "this engine cannot decide" outcomes: they are
# NOT approvals, and every non-COMPLIANT status carries is_compliant=False so a
# caller that gates on `is_compliant` fails closed.
STATUS_COMPLIANT = "COMPLIANT"
STATUS_BLOCKED = "LOCALIZATION_VIOLATION_BLOCKED"
STATUS_MECHANISM_REQUIRED = "TRANSFER_MECHANISM_REQUIRED"
STATUS_REVIEW_REQUIRED = "REVIEW_REQUIRED"

# --- Record classes -------------------------------------------------------
RECORD_TYPE_TRADE_EXECUTION = "TRADE_EXECUTION"
RECORD_TYPE_CLIENT_PII = "CLIENT_PII"
RECORD_TYPE_PAYMENT_LEDGER = "PAYMENT_LEDGER"
RECORD_TYPE_MARKET_TICK = "MARKET_TICK"
VALID_RECORD_TYPES = frozenset({
    RECORD_TYPE_TRADE_EXECUTION,
    RECORD_TYPE_CLIENT_PII,
    RECORD_TYPE_PAYMENT_LEDGER,
    RECORD_TYPE_MARKET_TICK,
})

# Record classes that carry personal data. Exchange-disseminated market ticks
# (price/size, no counterparty identity) are not personal data, so the
# personal-data localization regimes (PIPL, GDPR) do not attach to them. They
# may still be caught by non-personal-data regimes (China DSL "important data"),
# which is why CN tick egress resolves to REVIEW_REQUIRED, not COMPLIANT.
PERSONAL_DATA_RECORD_TYPES = frozenset({
    RECORD_TYPE_TRADE_EXECUTION,
    RECORD_TYPE_CLIENT_PII,
    RECORD_TYPE_PAYMENT_LEDGER,
})

# Region -> jurisdiction of the region's physical location. This is the
# authority for residency decisions: an `eu-` prefix does NOT imply EU/EEA
# territory (eu-west-2 is London/UK and eu-central-2 is Zurich/CH -- both are
# third countries for GDPR Chapter V purposes).
REGION_TO_JURISDICTION: Dict[str, str] = {
    "cn-north-1": "CN",
    "cn-northwest-1": "CN",
    "ap-south-1": "IN",
    "ap-south-2": "IN",
    "eu-west-1": "EU",       # Ireland
    "eu-west-3": "EU",       # Paris
    "eu-central-1": "EU",    # Frankfurt
    "eu-north-1": "EU",      # Stockholm
    "eu-south-1": "EU",      # Milan
    "eu-south-2": "EU",      # Spain
    "eu-west-2": "UK",       # London -- third country post-Brexit
    "eu-central-2": "CH",    # Zurich -- never was EU/EEA
    "us-east-1": "US",
    "us-east-2": "US",
    "us-west-1": "US",
    "us-west-2": "US",
}

# Jurisdictions whose export-control / state-access regimes can make records
# practically unretrievable by a foreign regulator, which is itself a problem
# for SEC Rule 17a-4(j) (records must be furnished promptly to representatives
# of the Commission as legible, true, complete and current copies).
RETRIEVAL_RISK_JURISDICTIONS = frozenset({"CN", "RU"})

# Allowed *primary* storage regions per origin jurisdiction. Public and
# overridable; values must stay consistent with REGION_TO_JURISDICTION.
JURISDICTION_ALLOWED_REGIONS: Dict[str, List[str]] = {
    "CN": ["cn-north-1", "cn-northwest-1"],
    "IN": ["ap-south-1", "ap-south-2"],
    "EU": ["eu-west-1", "eu-west-3", "eu-central-1",
           "eu-north-1", "eu-south-1", "eu-south-2"],
    "US": ["us-east-1", "us-east-2", "us-west-1", "us-west-2"],
    "UK": ["eu-west-2"],
}

# SEC Rule 17a-4 retention periods, in years, by rule paragraph.
#   17a-4(a): blotters, ledgers, securities records etc. -> 6 years, with the
#             first two years in an easily accessible place.
#   17a-4(b): order memoranda, communications, and other "business as such"
#             records -> 3 years.
SEC_17A4_RETENTION_YEARS: Dict[str, int] = {
    "17a-4(a)": 6,
    "17a-4(b)": 3,
}

# Electronic recordkeeping-system conditions permitted by Rule 17a-4(f) since
# the 2022 amendments (effective 3 January 2023): the original WORM
# (non-rewriteable, non-erasable) condition OR the audit-trail alternative,
# which must permit recreation of an original record that is modified or
# deleted. WORM was retained as an option, not as the only option.
STORAGE_MODE_WORM = "WORM"
STORAGE_MODE_AUDIT_TRAIL = "AUDIT_TRAIL"
STORAGE_MODE_MUTABLE = "MUTABLE"
SEC_17A4_PERMITTED_STORAGE_MODES = frozenset({STORAGE_MODE_WORM, STORAGE_MODE_AUDIT_TRAIL})


@dataclass
class TradeRecordPayload:
    record_id: str
    origin_jurisdiction: str            # 'CN', 'IN', 'EU', 'US', 'UK'
    destination_cloud_region: str       # e.g. 'cn-north-1', 'us-east-1', 'ap-south-1'
    record_type: str                    # see VALID_RECORD_TYPES
    is_primary_store: bool


@dataclass
class DataLocalizationAuditReport:
    record_id: str
    origin_jurisdiction: str
    destination_cloud_region: str
    is_compliant: bool
    status: str                         # see STATUS_* constants
    applied_policy: str
    remediation_instructions: List[str]
    destination_jurisdiction: str = "UNKNOWN"
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class RetentionConfiguration:
    """Declared retention posture of the system holding a US-regulated record."""
    record_id: str
    sec_record_class: str               # '17a-4(a)' or '17a-4(b)'
    retention_years: float
    storage_mode: str                   # WORM | AUDIT_TRAIL | MUTABLE
    first_two_years_readily_accessible: bool = True


@dataclass
class RetentionAuditReport:
    record_id: str
    is_compliant: bool
    required_retention_years: int
    findings: List[str]


class DataLocalizationComplianceEngine:
    """
    Storage-residency gate for trade records.

    Decides whether `destination_cloud_region` is an acceptable resting place for
    a record originating in `origin_jurisdiction`, and records every decision in
    an audit trail. Unknown origin jurisdictions and unmapped destination regions
    resolve to REVIEW_REQUIRED (is_compliant=False), never to a silent approval.
    """

    def __init__(
        self,
        allowed_regions_map: Optional[Dict[str, List[str]]] = None,
        region_jurisdiction_map: Optional[Dict[str, str]] = None,
    ):
        self.allowed_regions_map = allowed_regions_map or dict(JURISDICTION_ALLOWED_REGIONS)
        self.region_jurisdiction_map = region_jurisdiction_map or dict(REGION_TO_JURISDICTION)
        self._audit_trail: List[DataLocalizationAuditReport] = []

    @property
    def audit_trail(self) -> List[DataLocalizationAuditReport]:
        """
        Chronological copy of every localization decision made by this engine.
        Entries are copied, not aliased, so a caller that mutates a returned
        report cannot rewrite a recorded BLOCKED decision into an approval.
        """
        copies: List[DataLocalizationAuditReport] = []
        for entry in self._audit_trail:
            fields = dict(entry.__dict__)
            fields["remediation_instructions"] = list(entry.remediation_instructions)
            copies.append(DataLocalizationAuditReport(**fields))
        return copies

    # -- input handling ----------------------------------------------------

    @staticmethod
    def _require_text(value: object, field_name: str) -> str:
        if not isinstance(value, str):
            raise TypeError(f"{field_name} must be a string, got {type(value).__name__}")
        cleaned = value.strip()
        if not cleaned:
            raise ValueError(f"{field_name} must be a non-empty string")
        return cleaned

    # -- localization audit ------------------------------------------------

    def audit_trade_record_localization(
        self, record: TradeRecordPayload
    ) -> DataLocalizationAuditReport:
        """
        Audits a record's destination cloud region against the residency regime of
        its origin jurisdiction.

        Never raises on a non-compliant route -- the decision is carried in the
        returned report -- but raises TypeError/ValueError on malformed input,
        before any policy decision is reached.
        """
        if not isinstance(record, TradeRecordPayload):
            raise TypeError(f"record must be TradeRecordPayload, got {type(record).__name__}")
        record_id = self._require_text(record.record_id, "record.record_id")
        jur = self._require_text(record.origin_jurisdiction, "record.origin_jurisdiction").upper()
        dest_region = self._require_text(
            record.destination_cloud_region, "record.destination_cloud_region"
        ).lower()
        record_type = self._require_text(record.record_type, "record.record_type").upper()
        if record_type not in VALID_RECORD_TYPES:
            raise ValueError(
                f"record.record_type {record.record_type!r} is not one of "
                f"{sorted(VALID_RECORD_TYPES)}"
            )
        if not isinstance(record.is_primary_store, bool):
            raise TypeError(
                "record.is_primary_store must be bool, got "
                f"{type(record.is_primary_store).__name__}"
            )

        dest_jurisdiction = self.region_jurisdiction_map.get(dest_region, "UNKNOWN")
        allowed_regions = self.allowed_regions_map.get(jur, [])
        in_country = dest_region in allowed_regions

        if dest_jurisdiction == "UNKNOWN" and not in_country:
            # An unmapped region cannot be shown to be in-country and its
            # governing law is unknown. Fail closed rather than guess.
            status, policy, remediations = (
                STATUS_REVIEW_REQUIRED,
                f"Unmapped destination region '{dest_region}': physical jurisdiction unknown, "
                "residency cannot be established.",
                [f"Map '{dest_region}' to its physical jurisdiction in region_jurisdiction_map "
                 "before routing regulated records to it."],
            )
        elif jur == "CN":
            status, policy, remediations = self._evaluate_cn(
                record_type, allowed_regions, in_country
            )
        elif jur == "IN":
            status, policy, remediations = self._evaluate_in(
                record_type, allowed_regions, in_country
            )
        elif jur == "EU":
            status, policy, remediations = self._evaluate_eu(
                dest_jurisdiction, record_type, in_country
            )
        elif jur in ("US", "UK"):
            status, policy, remediations = self._evaluate_us_uk(
                jur, dest_jurisdiction, in_country
            )
        else:
            status, policy, remediations = (
                STATUS_REVIEW_REQUIRED,
                f"No residency policy registered for origin jurisdiction '{jur}'; no approval is "
                "inferred from the absence of a rule.",
                [f"Register an allowed-region policy for '{jur}' before routing its records "
                 "(e.g. Russia Federal Law 242-FZ requires personal data of Russian citizens to "
                 "be recorded in databases located in Russia)."],
            )

        is_compliant = status == STATUS_COMPLIANT
        if status == STATUS_BLOCKED:
            logger.critical(
                "DATA LOCALIZATION VIOLATION [%s]: origin=%s type=%s -> region=%s (%s). Egress BLOCKED.",
                record_id, jur, record_type, dest_region, dest_jurisdiction,
            )
        elif not is_compliant:
            logger.warning(
                "DATA LOCALIZATION UNRESOLVED [%s]: origin=%s type=%s -> region=%s (%s). Status=%s.",
                record_id, jur, record_type, dest_region, dest_jurisdiction, status,
            )

        report = DataLocalizationAuditReport(
            record_id=record_id,
            origin_jurisdiction=jur,
            destination_cloud_region=dest_region,
            destination_jurisdiction=dest_jurisdiction,
            is_compliant=is_compliant,
            status=status,
            applied_policy=policy,
            remediation_instructions=remediations,
        )
        self._audit_trail.append(report)
        return report

    # -- per-jurisdiction rules -------------------------------------------

    def _evaluate_cn(
        self, record_type: str, allowed_regions: List[str], in_country: bool
    ) -> Tuple[str, str, List[str]]:
        if in_country:
            return (
                STATUS_COMPLIANT,
                "China CSL Art. 37 / PIPL Art. 40: in-country storage confirmed.",
                [],
            )
        if record_type not in PERSONAL_DATA_RECORD_TYPES:
            return (
                STATUS_REVIEW_REQUIRED,
                "China DSL: non-personal market data falls outside PIPL Art. 40 localization, but "
                "may still be catalogued as 'important data', which requires a CAC security "
                "assessment before export.",
                ["Confirm the tick set is not catalogued as 'important data' before exporting."],
            )
        return (
            STATUS_BLOCKED,
            "China CSL Art. 37 / PIPL Art. 40: export of China-collected personal information is "
            "blocked by default. Export is lawful only via a PIPL Art. 38 mechanism (CAC security "
            "assessment, CAC standard contract, or certification), and this engine holds no "
            "evidence that one is in place.",
            [
                f"Route the record to a Chinese region ({allowed_regions}), or",
                "record a completed PIPL Art. 38 transfer mechanism and reconfigure the policy. "
                "Note the CAC Provisions on Promoting and Regulating Cross-Border Data Flows "
                "(22 Mar 2024) exempt some low-volume, non-sensitive, non-CIIO exports.",
            ],
        )

    def _evaluate_in(
        self, record_type: str, allowed_regions: List[str], in_country: bool
    ) -> Tuple[str, str, List[str]]:
        if in_country:
            return (
                STATUS_COMPLIANT,
                "India: in-country storage confirmed (RBI DPSS.CO.OD No.2785/06.08.005/2017-2018, "
                "6 Apr 2018, for payment system data).",
                [],
            )
        if record_type == RECORD_TYPE_PAYMENT_LEDGER:
            return (
                STATUS_BLOCKED,
                "India RBI circular DPSS.CO.OD No.2785/06.08.005/2017-2018 (6 Apr 2018): payment "
                "system data must be stored only in India. Foreign processing is permitted but the "
                "data must be brought back to India and deleted abroad; a foreign resting copy is "
                "not permitted.",
                [f"Store the payment ledger in an Indian region ({allowed_regions})."],
            )
        return (
            STATUS_REVIEW_REQUIRED,
            "India: no in-force securities-market localization mandate covers this record class. "
            "SEBI CSCRF data-localization standard PR.DS.S2 was kept in abeyance by SEBI circular "
            "dated 31 Dec 2024 pending further consultation, and the RBI mandate is scoped to "
            "payment system data. Treat in-country storage as the prudent default, not a settled "
            "legal requirement.",
            ["Re-check the SEBI CSCRF PR.DS.S2 abeyance status before relying on offshore storage "
             "for Indian trade records; DPDP Act s.16 transfer restrictions are also not yet "
             "notified."],
        )

    def _evaluate_eu(
        self, dest_jurisdiction: str, record_type: str, in_country: bool
    ) -> Tuple[str, str, List[str]]:
        if in_country:
            return (
                STATUS_COMPLIANT,
                "EU/EEA storage confirmed; no GDPR Chapter V transfer arises.",
                [],
            )
        if record_type not in PERSONAL_DATA_RECORD_TYPES:
            return (
                STATUS_COMPLIANT,
                "Non-personal market data: outside GDPR scope. MiFID II Art. 16(6) retention and "
                "regulator-accessibility obligations still follow the record.",
                [],
            )
        return (
            STATUS_MECHANISM_REQUIRED,
            "GDPR imposes no data-localization mandate: a transfer to "
            f"'{dest_jurisdiction}' is lawful under a Chapter V mechanism (Art. 45 adequacy -- "
            "e.g. the EU-US Data Privacy Framework adequacy decision of 10 Jul 2023, upheld by the "
            "General Court on 3 Sep 2025 -- Art. 46 SCCs, or an Art. 49 derogation). This engine "
            "holds no evidence of such a mechanism, so it does not approve the route.",
            [
                "Record the applicable Chapter V mechanism (adequacy finding, SCCs, or recipient "
                "DPF certification) for this destination, or",
                "keep the primary copy in an EU/EEA region.",
            ],
        )

    def _evaluate_us_uk(
        self, jur: str, dest_jurisdiction: str, in_country: bool
    ) -> Tuple[str, str, List[str]]:
        framework = "SEC Rule 17a-4" if jur == "US" else "FCA SYSC 9 / UK MiFID record-keeping"
        if in_country:
            return (
                STATUS_COMPLIANT,
                f"{jur}: in-country storage confirmed; {framework} retention obligations apply.",
                [],
            )
        if dest_jurisdiction in RETRIEVAL_RISK_JURISDICTIONS:
            return (
                STATUS_REVIEW_REQUIRED,
                f"{jur}: {framework} imposes no residency mandate, but storing records in "
                f"'{dest_jurisdiction}' threatens the prompt-production obligation (SEC Rule "
                "17a-4(j) requires legible, true, complete and current copies to be furnished "
                "promptly on request) where local export controls can block retrieval.",
                [f"Retain a retrievable copy outside '{dest_jurisdiction}' that independently "
                 "satisfies the retention and production obligations."],
            )
        return (
            STATUS_COMPLIANT,
            f"{jur}: {framework} imposes no data-residency mandate; offshore storage is permitted "
            "provided retention, immutability and prompt-production obligations travel with the "
            "record.",
            [],
        )

    # -- retention audit ---------------------------------------------------

    @staticmethod
    def verify_sec_17a4_retention(config: RetentionConfiguration) -> RetentionAuditReport:
        """
        Verifies a declared retention posture against SEC Rule 17a-4.

        Checks the retention period for the record's rule paragraph
        (17a-4(a) -> 6 years, 17a-4(b) -> 3 years), that the electronic
        recordkeeping system meets one of the two conditions permitted by
        17a-4(f) since the 2022 amendments (WORM, or the audit-trail alternative
        that permits recreation of an original record), and the "first two years
        in an easily accessible place" qualifier that attaches to 17a-4(a).

        WORM is NOT the only permitted mode: the audit-trail alternative has been
        available since the amendments took effect on 3 January 2023.
        """
        if not isinstance(config, RetentionConfiguration):
            raise TypeError(
                f"config must be RetentionConfiguration, got {type(config).__name__}"
            )
        record_class = str(config.sec_record_class).strip()
        if record_class not in SEC_17A4_RETENTION_YEARS:
            raise ValueError(
                f"sec_record_class {config.sec_record_class!r} must be one of "
                f"{sorted(SEC_17A4_RETENTION_YEARS)}"
            )
        if isinstance(config.retention_years, bool) or not isinstance(
            config.retention_years, (int, float)
        ):
            raise TypeError(
                f"retention_years must be a number, got {type(config.retention_years).__name__}"
            )

        storage_mode = str(config.storage_mode).strip().upper()
        required_years = SEC_17A4_RETENTION_YEARS[record_class]
        findings: List[str] = []

        if config.retention_years < required_years:
            findings.append(
                f"Retention of {config.retention_years} years is short of the {required_years} "
                f"years required for {record_class} records."
            )
        if storage_mode not in SEC_17A4_PERMITTED_STORAGE_MODES:
            findings.append(
                f"Storage mode '{storage_mode}' satisfies neither Rule 17a-4(f) condition: use "
                f"{STORAGE_MODE_WORM} (non-rewriteable, non-erasable) or "
                f"{STORAGE_MODE_AUDIT_TRAIL} (audit-trail alternative permitting recreation of an "
                "original record)."
            )
        if record_class == "17a-4(a)" and not config.first_two_years_readily_accessible:
            findings.append(
                "17a-4(a) records must be kept in an easily accessible place for the first two "
                "years."
            )

        return RetentionAuditReport(
            record_id=config.record_id,
            is_compliant=not findings,
            required_retention_years=required_years,
            findings=findings,
        )
