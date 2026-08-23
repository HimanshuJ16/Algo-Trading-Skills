# Pre-Flight Checklist

- [ ] Are signal metadata, base fees, PnL share fractions, and AUM capacity limits registered?
- [ ] Is the capacity cap derived from a capacity study, not fitted to the current book?
- [ ] Is total ACTIVE subscribed AUM verified against maximum signal capacity, with denials left unrecorded?
- [ ] Are NaN, infinite, and negative AUM values rejected at the boundary rather than compared against the cap?
- [ ] Are duplicate `subscription_id` values and unintended `signal_id` re-registrations rejected instead of overwritten?
- [ ] Is there a revocation path that releases capacity and stops billing, with the record retained for audit?
- [ ] Is a loss carryforward (high-water mark) applied before the PnL share?
- [ ] Is a `benchmarking_evidence_ref` attached to every fee calculation, and is the absence of one surfaced rather than suppressed?
- [ ] Has it been confirmed that the OECD TPG 2022 Chapter VII 5% low value-adding mark-up is NOT being applied to a signal licence (excluded by paras 7.45 and 7.47)?
- [ ] Is there an intercompany agreement plus DEMPE analysis for cross-entity signal transfers, retained for the local file?
- [ ] Are external vendor/venue redistribution, derived-data, and non-display permissions tracked separately from internal entitlement?
- [ ] Are entitlement access tokens validated prior to signal stream delivery?
