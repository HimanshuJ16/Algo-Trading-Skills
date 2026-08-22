"""Audit M-of-N key-shard placement across vaults, countries and legal jurisdictions.

The audit covers both failure directions of a threshold scheme:

* Confidentiality SPOF - a single group holding >= M shards can reconstruct the
  key on its own (a domestic seizure order, a raid, or provider collusion).
* Availability SPOF - a single group holding > (N - M) shards can, by becoming
  unavailable, leave fewer than M shards reachable, which permanently destroys
  access to the assets.

Auditing only the first direction produces false passes: a 4-of-6 scheme with
3 shards in one country is confidentiality-safe (3 < 4) yet one country-level
outage locks the funds forever (6 - 3 = 3 < 4).

Thresholds here are internal engineering policy, not regulatory mandates; no
regulator surveyed prescribes a shard geography (see references/standards.md).
"""

import logging
import math
import re
from dataclasses import dataclass
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

_COUNTRY_CODE_RE = re.compile(r"^[A-Z]{2}$")

# references/standards.md: keep at least this many spare shards above the
# reconstruction threshold so ordinary shard loss is survivable.
DEFAULT_MIN_REDUNDANCY_GAP = 2


def _normalize(value: str, label: str) -> str:
    """Uppercase and strip a grouping key so 'ch', ' CH ' and 'CH' group together."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string, got {value!r}.")
    return value.strip().upper()


@dataclass
class VaultShardLocation:
    """One SSS shard and the vault, country, jurisdiction and provider holding it.

    `jurisdiction` is the legal regime that can compel the shard, which is not
    always the country of the vault: sub-custodians, parent entities and
    cross-border legal process can place vaults in different countries under a
    single effective regime. It defaults to `country_code` when not supplied.
    """

    shard_id: int
    vault_name: str
    country_code: str       # ISO 3166-1 alpha-2 style, 2 letters (e.g. 'CH', 'SG', 'US')
    provider_name: str      # Custodian entity (e.g. 'CustodianA', 'CustodianB')
    is_iso_27001: bool = True
    jurisdiction: Optional[str] = None

    def __post_init__(self) -> None:
        if isinstance(self.shard_id, bool) or not isinstance(self.shard_id, int) or self.shard_id < 1:
            raise ValueError(f"shard_id must be a positive integer, got {self.shard_id!r}.")
        if not isinstance(self.vault_name, str) or not self.vault_name.strip():
            raise ValueError("vault_name must be a non-empty string.")
        self.country_code = _normalize(self.country_code, "country_code")
        if not _COUNTRY_CODE_RE.match(self.country_code):
            raise ValueError(
                f"country_code must be a 2-letter code (e.g. 'CH'), got {self.country_code!r}. "
                "Free-text country names group incorrectly in the audit."
            )
        self.provider_name = _normalize(self.provider_name, "provider_name")
        self.jurisdiction = (
            self.country_code if self.jurisdiction is None
            else _normalize(self.jurisdiction, "jurisdiction")
        )


@dataclass
class AuditReport:
    """Result of a placement audit. `violations` is empty if and only if `is_compliant`."""

    is_compliant: bool
    threshold_m: int
    total_shards_n: int
    max_shards_in_single_country: int
    max_shards_with_single_provider: int
    jurisdictional_entropy: float   # Shannon entropy over country_code, descriptive only
    violations: List[str]
    max_shards_in_single_jurisdiction: int = 0
    redundancy_gap: int = 0         # N - M: shards that may be lost before assets are unrecoverable


class ColdStorageGeographicDistributor:
    """
    Audits M-of-N Shamir Secret Sharing shard placement for both single-point-of-
    failure directions: single-group key reconstruction and single-group key loss.
    """

    def __init__(
        self,
        threshold_m: int,
        total_shards_n: int,
        min_redundancy_gap: int = DEFAULT_MIN_REDUNDANCY_GAP,
    ) -> None:
        if threshold_m <= 1 or threshold_m > total_shards_n:
            raise ValueError(
                f"Invalid threshold: M ({threshold_m}) must be in range [2, N ({total_shards_n})]."
            )
        if min_redundancy_gap < 0:
            raise ValueError(f"min_redundancy_gap must be >= 0, got {min_redundancy_gap}.")
        self.threshold_m = threshold_m
        self.total_shards_n = total_shards_n
        self.min_redundancy_gap = min_redundancy_gap

    def calculate_entropy(self, country_counts: Dict[str, int]) -> float:
        """
        Calculates Shannon Entropy of country distribution: H = -sum(p * log2(p)).
        Higher entropy represents more uniform geographic distribution.

        Descriptive only: entropy never gates compliance, because it is not a
        safety criterion. A 3-of-5 scheme with 2/2/1 shards over three countries
        scores lower than 1/1/1/1/1 over five, but two placements with identical
        entropy can differ in whether any single group reaches the threshold.
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

    def _group_counts(self, shard_locations: List[VaultShardLocation], attr: str) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for loc in shard_locations:
            key = getattr(loc, attr)
            counts[key] = counts.get(key, 0) + 1
        return counts

    def audit_distribution(self, shard_locations: List[VaultShardLocation]) -> AuditReport:
        """
        Audits a proposed shard placement matrix against SPOF risk criteria.

        Raises ValueError if the placement is not a well-formed M-of-N matrix
        (wrong shard count, or duplicated / out-of-range shard ids). A duplicated
        shard id means fewer distinct shards exist than the audit would assume,
        so every downstream count would overstate the real safety margin.
        """
        if len(shard_locations) != self.total_shards_n:
            raise ValueError(
                f"Expected {self.total_shards_n} shard locations, got {len(shard_locations)}."
            )

        shard_ids = [loc.shard_id for loc in shard_locations]
        duplicates = sorted({sid for sid in shard_ids if shard_ids.count(sid) > 1})
        if duplicates:
            raise ValueError(
                f"Duplicate shard_id(s) {duplicates}: only {len(set(shard_ids))} distinct shards "
                f"supplied for an N={self.total_shards_n} scheme. Copies of one shard do not add "
                "threshold coverage."
            )
        out_of_range = sorted(sid for sid in shard_ids if sid > self.total_shards_n)
        if out_of_range:
            raise ValueError(
                f"shard_id(s) {out_of_range} exceed N={self.total_shards_n}; "
                "shard ids must lie in [1, N]."
            )

        violations: List[str] = []
        country_counts = self._group_counts(shard_locations, "country_code")
        provider_counts = self._group_counts(shard_locations, "provider_name")
        jurisdiction_counts = self._group_counts(shard_locations, "jurisdiction")

        for loc in shard_locations:
            if not loc.is_iso_27001:
                violations.append(
                    f"Shard {loc.shard_id} at {loc.vault_name} lacks ISO 27001 compliance."
                )

        max_country_shards = max(country_counts.values()) if country_counts else 0
        max_provider_shards = max(provider_counts.values()) if provider_counts else 0
        max_jurisdiction_shards = max(jurisdiction_counts.values()) if jurisdiction_counts else 0

        redundancy_gap = self.total_shards_n - self.threshold_m

        groups = [("Country", country_counts), ("Provider", provider_counts)]
        if jurisdiction_counts != country_counts:
            # Identical groupings would report the same concentration twice; the
            # jurisdiction view only adds information once it has been mapped.
            groups.insert(1, ("Jurisdiction", jurisdiction_counts))

        for label, counts in groups:
            for key, count in sorted(counts.items()):
                if count >= self.threshold_m:
                    # Confidentiality SPOF: this group alone can reconstruct the key.
                    violations.append(
                        f"{label} Confidentiality SPOF Violation: {count} shard(s) concentrated in "
                        f"{label.lower()} '{key}' (Threshold M={self.threshold_m}). "
                        "Key could be reconstructed by that single group."
                    )
                elif count > redundancy_gap:
                    # Availability SPOF: losing this group alone leaves fewer than M shards.
                    violations.append(
                        f"{label} Availability SPOF Violation: {count} shard(s) concentrated in "
                        f"{label.lower()} '{key}'; losing it leaves {self.total_shards_n - count} of "
                        f"M={self.threshold_m} required shards. Assets would be permanently "
                        "unrecoverable."
                    )

        if redundancy_gap < self.min_redundancy_gap:
            violations.append(
                f"Redundancy Reserve Violation: threshold gap (N-M) is {redundancy_gap}, "
                f"below the configured minimum of {self.min_redundancy_gap}. "
                f"Losing {redundancy_gap + 1} shard(s) would permanently destroy access."
            )

        entropy = self.calculate_entropy(country_counts)
        is_compliant = len(violations) == 0

        if not is_compliant:
            logger.warning("Cold Storage Audit FAILED with %d violation(s).", len(violations))
        else:
            logger.info("Cold Storage Audit PASSED. Jurisdictional Entropy: %s", entropy)

        return AuditReport(
            is_compliant=is_compliant,
            threshold_m=self.threshold_m,
            total_shards_n=self.total_shards_n,
            max_shards_in_single_country=max_country_shards,
            max_shards_with_single_provider=max_provider_shards,
            jurisdictional_entropy=entropy,
            violations=violations,
            max_shards_in_single_jurisdiction=max_jurisdiction_shards,
            redundancy_gap=redundancy_gap,
        )
