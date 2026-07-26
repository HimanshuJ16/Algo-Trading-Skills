# Workflows for Cold Storage Geographic Distribution

1. **Threshold Configuration**:
   - Define $M$ (reconstruction threshold) and $N$ (total shards). Example: 3-of-5.
2. **Jurisdiction Mapping**:
   - Assign each shard $i \in \{1 \dots N\}$ to a physical vault with attributes: `country_code`, `provider_name`, `has_iso_27001`.
3. **Automated Audit Check**:
   - Check condition 1: $\max_c(\text{Shards in Country } c) < M$.
   - Check condition 2: $\max_p(\text{Shards with Provider } p) < M$.
4. **Resilience Metric Computation**:
   - Calculate Jurisdictional Entropy: $H = -\sum p_i \log_2(p_i)$.
   - Output Security Audit Certificate.