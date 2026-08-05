import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

@dataclass
class ComplianceResult:
    """Legacy ComplianceResult for backward compatibility."""
    is_compliant: bool
    reason: str

class SanctionsListType(str, Enum):
    OFAC_SDN = "OFAC_SDN"
    EU_CONSOLIDATED = "EU_CONSOLIDATED"
    UN_SANCTIONS = "UN_SANCTIONS"
    UK_HMT = "UK_HMT"

class ScreeningEntityKind(str, Enum):
    COUNTERPARTY = "COUNTERPARTY"
    INSTRUMENT_ISSUER = "INSTRUMENT_ISSUER"

@dataclass
class SanctionedEntry:
    entity_id: str                       # LEI, ISIN, or Tax ID
    name: str
    country_iso: str
    list_type: SanctionsListType
    is_50_percent_owned_subsidiary: bool = False

DEFAULT_SANCTIONED_DATABASE: List[SanctionedEntry] = [
    SanctionedEntry("LEI_SANC_001", "VTB BANK PJSC", "RU", SanctionsListType.OFAC_SDN),
    SanctionedEntry("LEI_SANC_002", "SBERBANK OF RUSSIA", "RU", SanctionsListType.EU_CONSOLIDATED),
    SanctionedEntry("ISIN_RU000A0JX0J2", "RUSSIAN FEDERAL BOND", "RU", SanctionsListType.OFAC_SDN),
    SanctionedEntry("LEI_SANC_003", "IRAN NATIONAL OIL CO", "IR", SanctionsListType.UN_SANCTIONS),
    SanctionedEntry("LEI_SANC_004", "KOREA MINING DEVELOPMENT", "KP", SanctionsListType.UN_SANCTIONS),
]

EMBARGOED_COUNTRIES = {"IR", "KP", "CU", "SY", "RU_CRIMEA"}

@dataclass
class ScreeningSubject:
    subject_id: str                      # LEI or ISIN or Internal ID
    name: str
    country_iso: str
    entity_kind: ScreeningEntityKind
    ownership_pct_by_sanctioned: float = 0.0

@dataclass
class SanctionsHit:
    subject_id: str
    matched_sanctioned_name: str
    list_type: SanctionsListType
    match_score_pct: float
    reason: str

@dataclass
class SanctionsScreeningReport:
    subject_id: str
    name: str
    is_cleared: bool
    has_sanctions_hit: bool
    has_embargo_violation: bool
    hits: List[SanctionsHit]
    status: str                          # 'CLEARED', 'BLOCKED_SANCTIONS_HIT', 'BLOCKED_EMBARGO'
    audit_notes: str

class SanctionsScreeningForCounterpartiesAndInstrumentsEngine:
    """
    Production-grade sanctions screening engine auditing counterparties and financial instrument issuers
    against OFAC SDN, EU, UN lists using fuzzy string matching, OFAC 50% Rule, and country embargoes.
    """
    def __init__(
        self,
        sanctions_database: Optional[List[SanctionedEntry]] = None,
        fuzzy_match_threshold_pct: float = 85.0
    ):
        self.database = sanctions_database or DEFAULT_SANCTIONED_DATABASE
        self.fuzzy_threshold = fuzzy_match_threshold_pct

    def check(self, data: dict) -> ComplianceResult:
        """Legacy check method retained for 100% backward compatibility."""
        if data.get("valid"):
            return ComplianceResult(True, "Valid")
        return ComplianceResult(False, "Invalid")

    def _levenshtein_similarity(self, s1: str, s2: str) -> float:
        """Computes normalized Levenshtein similarity percentage between two strings."""
        str1 = s1.upper().strip()
        str2 = s2.upper().strip()

        if str1 == str2:
            return 100.0
        if not str1 or not str2:
            return 0.0

        len1, len2 = len(str1), len(str2)
        dp = [[0] * (len2 + 1) for _ in range(len1 + 1)]

        for i in range(len1 + 1):
            dp[i][0] = i
        for j in range(len2 + 1):
            dp[0][j] = j

        for i in range(1, len1 + 1):
            for j in range(1, len2 + 1):
                cost = 0 if str1[i - 1] == str2[j - 1] else 1
                dp[i][j] = min(
                    dp[i - 1][j] + 1,       # Deletion
                    dp[i][j - 1] + 1,       # Insertion
                    dp[i - 1][j - 1] + cost # Substitution
                )

        dist = dp[len1][len2]
        max_len = max(len1, len2)
        sim = (1.0 - dist / max_len) * 100.0
        return round(sim, 2)

    def screen_subject(self, subject: ScreeningSubject) -> SanctionsScreeningReport:
        """
        Screens a counterparty or instrument issuer against sanctions database and embargo rules.
        """
        hits: List[SanctionsHit] = []
        has_embargo = subject.country_iso.upper() in EMBARGOED_COUNTRIES

        # 1. Check OFAC 50% Rule
        if subject.ownership_pct_by_sanctioned >= 50.0:
            hits.append(SanctionsHit(
                subject_id=subject.subject_id,
                matched_sanctioned_name="OFAC 50% Rule Ownership",
                list_type=SanctionsListType.OFAC_SDN,
                match_score_pct=100.0,
                reason=f"OFAC 50% Rule Violation: {subject.ownership_pct_by_sanctioned}% owned by sanctioned entity."
            ))

        # 2. Database Name & ID Matching
        for entry in self.database:
            # Exact ID Match
            if subject.subject_id.upper() == entry.entity_id.upper():
                hits.append(SanctionsHit(
                    subject_id=subject.subject_id,
                    matched_sanctioned_name=entry.name,
                    list_type=entry.list_type,
                    match_score_pct=100.0,
                    reason=f"Exact ID match with sanctioned entity ({entry.entity_id})"
                ))

            # Fuzzy Name Matching
            sim_score = self._levenshtein_similarity(subject.name, entry.name)
            if sim_score >= self.fuzzy_threshold:
                hits.append(SanctionsHit(
                    subject_id=subject.subject_id,
                    matched_sanctioned_name=entry.name,
                    list_type=entry.list_type,
                    match_score_pct=sim_score,
                    reason=f"Fuzzy name match ({sim_score}%) with sanctioned entity '{entry.name}'"
                ))

        has_sanction_hit = len(hits) > 0
        is_cleared = not (has_sanction_hit or has_embargo)

        if has_sanction_hit:
            status = "BLOCKED_SANCTIONS_HIT"
        elif has_embargo:
            status = "BLOCKED_EMBARGO"
        else:
            status = "CLEARED"

        notes = (
            f"SANCTIONS SCREENING [{status}] ({subject.name}): ID = {subject.subject_id}, "
            f"Country = {subject.country_iso}, Hits = {len(hits)}, Embargoed = {has_embargo}."
        )

        if not is_cleared:
            logger.warning(notes)
        else:
            logger.info(notes)

        return SanctionsScreeningReport(
            subject_id=subject.subject_id,
            name=subject.name,
            is_cleared=is_cleared,
            has_sanctions_hit=has_sanction_hit,
            has_embargo_violation=has_embargo,
            hits=hits,
            status=status,
            audit_notes=notes
        )
