# Workflows — Alt-Data Insider Trading Controls

This gate runs **after** vendor onboarding. If no `DiligenceRecord` exists from
`alternative-data-vendor-due-diligence-checklist`, stop and run that skill first;
`has_vendor_diligence_signoff` is a carried outcome, not a judgment made here.

## 1. Build the spec from evidence

Populate `AltDataDatasetSpec` from artefacts the firm holds, not from vendor
marketing. Map every answer to a literal `bool` — the engine raises
`AltDataComplianceError` on a coerced value rather than scoring it, because
`'no'` is truthy and would otherwise pass a positive control.

Where an answer is genuinely unknown, set the **conservative** value
(`has_mnpi_risk=True`, positive controls `False`) and escalate. Do not
approximate in the permissive direction to get a clean report.

## 2. MNPI provenance triage

Ask about origin, not about predictive power:

- Was any part of the dataset obtained by hacking, leak, or unauthorised access?
- Did any upstream contributor owe a duty of confidentiality to an issuer, or
  supply the data under terms restricting its use?
- Is any source an insider or tippee of a covered issuer?
- Would the dataset reveal a specific, imminent, non-public corporate outcome
  before its disclosure — as distinct from a broad consumer trend?

Any yes, or an unresolved unknown → `has_mnpi_risk=True` → `REJECTED_MNPI_RISK`,
routed to legal. This is the fail-closed branch and it terminates in a human
decision, never in an automatic retry.

## 3. Vendor diligence and terms-of-service posture

Two independent inputs, deliberately not merged:

- `has_vendor_diligence_signoff` — a **current** record exists. Not "one exists
  somewhere"; a record that predates a change in the vendor's collection
  methodology is stale and the answer is `False`.
- `is_tos_compliant` — collection is consistent with the source's terms. For
  scraped sources, check that the vendor still re-reads those terms, respects
  `robots.txt`, does not simulate user accounts or bypass CAPTCHAs, and does not
  scrape behind a login without authorisation.

Either failing yields `REJECTED_MISSING_DILIGENCE`, but `failed_controls`
distinguishes `VENDOR_DILIGENCE_SIGNOFF` from `TERMS_OF_SERVICE` so the
remediation owner is unambiguous.

## 4. PII scrubbing and panel aggregation

Also two independent controls:

- `is_pii_scrubbed` — identifiers removed **and** removal verified against
  singling-out, linkability and inference risk. Identifier deletion alone is not
  anonymisation under GDPR Recital 26.
- `panel_aggregation_count` — distinct contributors behind each published
  observation, tested against the firm's `min_panel_aggregation_count`.

Both feed `REJECTED_UNAGGREGATED_PII`, and the audit note names whichever
actually failed. A large panel does not cure unscrubbed data, and scrubbing does
not cure a panel of five.

## 5. Earnings blackout

`hours_to_earnings_release` is signed: positive before the release, negative
after. The gate compares the absolute value, so a two-sided window is enforced
with a single threshold. Pass `None` when no release is scheduled in the
monitoring horizon.

The result is `BLACKOUT_WINDOW_RESTRICTED` — a **time-boxed pause on an otherwise
clean dataset**, not a rejection. Route it to a scheduler that re-runs the audit
once the window clears, not to the legal escalation path used for rejections.

## 6. Classify and persist

All four controls are evaluated before classification, so every `is_*_cleared`
flag on the report is a tested result. Severity order:

```
REJECTED_MNPI_RISK        (critical)   -> legal escalation, no trading
REJECTED_MISSING_DILIGENCE (error)     -> vendor/legal remediation
REJECTED_UNAGGREGATED_PII  (error)     -> data-engineering remediation
BLACKOUT_WINDOW_RESTRICTED (warning)   -> time-boxed pause, auto re-check
LOW_RISK_APPROVED          (info)      -> cleared to trade
```

`risk_classification` carries the most severe failure; `failed_controls` carries
all of them. Persist the whole `AltDataComplianceReport`
(`dataclasses.asdict`-serializable) with the reviewer, the timestamp, and the
threshold policy in force. The 2022 Risk Alert names failure to **record**
diligence consistently as a deficiency in its own right — an unpersisted decision
is, for examination purposes, a decision that did not happen.

## 7. Re-diligence cadence

Approval is not permanent. Re-run this gate:

- On the vendor's `next_review_date` from the diligence record.
- Whenever the vendor changes data-collection methodology, sources, or terms.
- Whenever a red flag surfaces about a source.
- On each earnings cycle for datasets covering earnings-sensitive issuers.
- Whenever the firm changes its threshold policy — a report produced under a
  superseded policy no longer evidences the current one.
