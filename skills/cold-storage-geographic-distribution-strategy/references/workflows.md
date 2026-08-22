# Workflows for Cold Storage Geographic Distribution

1. **Threshold Configuration**:
   - Define $M$ (reconstruction threshold) and $N$ (total shards). Example: 3-of-5.
   - $M \ge 2$; $M = 1$ is rejected because a single shard would reconstruct the key.
   - Set `min_redundancy_gap` (default 2). It is an internal policy dial, not a published requirement.
2. **Jurisdiction Mapping**:
   - Assign each shard $i \in \{1 \dots N\}$ to a vault with `country_code` (2-letter),
     `provider_name`, `jurisdiction` and `has ISO 27001` (`is_iso_27001` in code).
   - `jurisdiction` defaults to `country_code`. Override it when one legal regime reaches
     vaults in several countries (shared bloc, parent entity, sub-custodian's home supervisor).
   - Country codes and provider names are normalised to upper case with surrounding
     whitespace stripped, so spelling variants cannot split one group into several.
3. **Well-Formedness Check**:
   - Exactly $N$ placements, shard ids distinct and within $[1, N]$. Violations raise
     `ValueError` rather than producing a report, because a malformed matrix overstates
     every safety margin computed from it.
4. **Automated Audit Check** - for each grouping $g \in \{$country, jurisdiction, provider$\}$:
   - Confidentiality: $\max_g(\text{shards}) < M$, else that group can reconstruct the key alone.
   - Availability: $\max_g(\text{shards}) \le N - M$, else losing that group leaves fewer
     than $M$ shards and the assets are unrecoverable.
   - Certification: every shard's facility carries the recorded ISO 27001 evidence.
   - Redundancy reserve: $N - M \ge$ `min_redundancy_gap`.
   - When no `jurisdiction` has been mapped, the jurisdiction grouping is identical to
     the country grouping and is skipped, so one concentration is reported once.
5. **Metric Computation**:
   - Jurisdictional Entropy over country codes: $H = -\sum p_i \log_2(p_i)$, reported for
     context only. It is not a compliance gate; the violation list is authoritative.
   - Emit the audit report, remediate every violation, and re-audit the corrected placement.
