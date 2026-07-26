import math
import logging
from dataclasses import dataclass
from typing import List, Dict

logger = logging.getLogger(__name__)

@dataclass
class VaultShardLocation:
    shard_id: int
    vault_name: str
    country_code: str       # ISO 2-letter country code (e.g. 'CH', 'SG', 'US')
    provider_name: str      # Custodian entity (e.g. 'CustodianA', 'CustodianB')
    is_iso_27001: bool = True

@dataclass
class AuditReport:
    is_compliant: bool
    threshold_m: int
    total_shards_n: int
    max_shards_in_single_country: int
    max_shards_with_single_provider: int
    jurisdictional_entropy: float
    violations: List[str]

class ColdStorageGeographicDistributor:
    """
    Analyzes and audits M-of-N Shamir Secret Sharing key shard geographic
    and jurisdictional distribution to prevent Single Point of Failure (SPOF) risks.
    """
    def __init__(self, threshold_m: int, total_shards_n: int):
        if threshold_m <= 1 or threshold_m > total_shards_n:
            raise ValueError(f"Invalid threshold: M ({threshold_m}) must be in range [2, N ({total_shards_n})].")
        self.threshold_m = threshold_m
        self.total_shards_n = total_shards_n

    def calculate_entropy(self, country_counts: Dict[str, int]) -> float:
        """
        Calculates Shannon Entropy of country distribution: H = -sum(p * log2(p)).
        Higher entropy represents more uniform geographic distribution.
        """
        total = sum(country_counts.values())
        if total == 0:
            return 0.0
        entropy = 0.0
        for count in country_counts.values():
            p = count / total
            if p > 0:
                entropy -= p * math.log2(p)
        return round(entropy, 3)

    def audit_distribution(self, shard_locations: List[VaultShardLocation]) -> AuditReport:
        """
        Audits a proposed shard placement matrix against SPOF risk criteria.
        """
        if len(shard_locations) != self.total_shards_n:
            raise ValueError(f"Expected {self.total_shards_n} shard locations, got {len(shard_locations)}.")

        violations = []
        country_counts: Dict[str, int] = {}
        provider_counts: Dict[str, int] = {}

        for loc in shard_locations:
            country_counts[loc.country_code] = country_counts.get(loc.country_code, 0) + 1
            provider_counts[loc.provider_name] = provider_counts.get(loc.provider_name, 0) + 1
            
            if not loc.is_iso_27001:
                violations.append(f"Shard {loc.shard_id} at {loc.vault_name} lacks ISO 27001 compliance.")

        max_country_shards = max(country_counts.values()) if country_counts else 0
        max_provider_shards = max(provider_counts.values()) if provider_counts else 0

        # SPOF Check 1: Single Jurisdiction Key Reconstruction Risk
        if max_country_shards >= self.threshold_m:
            violations.append(
                f"Jurisdiction SPOF Violation: {max_country_shards} shards reside in a single country "
                f"(Threshold M={self.threshold_m}). Key could be reconstructed within a single jurisdiction."
            )

        # SPOF Check 2: Single Provider Collusion Risk
        if max_provider_shards >= self.threshold_m:
            violations.append(
                f"Provider SPOF Violation: {max_provider_shards} shards held by single provider "
                f"(Threshold M={self.threshold_m}). Key could be reconstructed by a single entity."
            )

        entropy = self.calculate_entropy(country_counts)
        is_compliant = len(violations) == 0

        if not is_compliant:
            logger.warning(f"Cold Storage Audit FAILED with {len(violations)} violation(s).")
        else:
            logger.info(f"Cold Storage Audit PASSED. Jurisdictional Entropy: {entropy}")

        return AuditReport(
            is_compliant=is_compliant,
            threshold_m=self.threshold_m,
            total_shards_n=self.total_shards_n,
            max_shards_in_single_country=max_country_shards,
            max_shards_with_single_provider=max_provider_shards,
            jurisdictional_entropy=entropy,
            violations=violations
        )
