# Pre-Flight Checklist

## Contract scope

- [ ] Is every vendor contract registered with the use cases the executed agreement actually grants — and no others?
- [ ] Is `contract_expiration_date` populated from the contract, rather than left as a placeholder or silently untracked?
- [ ] Have the non-display and redistribution booleans been confirmed against the signed schedules, not inferred from the product name?
- [ ] Is the seat cap the contracted cap, and not a number raised to clear a past denial?

## Request gating

- [ ] Does every internal consumer of vendor data pass through the gate *before* the feed is opened?
- [ ] Are automated consumers — risk engines and auto-hedgers included, not just alpha strategies — classified as non-display?
- [ ] Is `is_external_redistribution` set for client portals, published charts, and derived series from which the underlying quotes could be recovered?
- [ ] Is `as_of_date` passed explicitly in batch, backfill, and replay contexts so decisions are reproducible?

## Entitlement lifecycle

- [ ] Is `release_entitlement` wired into disconnect and shutdown paths for every approved consumer?
- [ ] Are reserved seats reconciled against live connections on a schedule, so a leak surfaces before it causes false denials?

## Audit evidence

- [ ] Is every `VendorUsageAuditReport` — denials included — persisted durably as it is produced?
- [ ] Does retention cover the vendor's audit look-back period (three years under the Nasdaq GDA)?
- [ ] Is there a quantifiable, auditable procedure for counting fee-liable units that draws on the infrastructure inventory rather than this engine's seat counter?
