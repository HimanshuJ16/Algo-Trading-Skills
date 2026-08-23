---
name: data-localization-requirements-for-trade-records
description: Storage-residency gate for trade records — audits destination cloud
  regions against origin-jurisdiction localization regimes (China CSL/PIPL/DSL, India
  RBI payment-data circular, GDPR Chapter V, SEC Rule 17a-4) and fails closed on
  unresolved routes.
domain: Data Management Global
subdomain: Regulatory Compliance & Sovereignty
tags:
- data-localization
- trade-record-sovereignty
- pipl
- rbi-data-localization
- mifid-ii
- sec-17a-4
- cross-border-egress
brokers_frameworks:
- China CSL Art. 37 / PIPL Art. 40 / DSL
- India RBI Payment System Data Circular
- EU GDPR Chapter V / MiFID II
- SEC Rule 17a-4
- Python Dataclasses
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in multi-region quantitative trading systems, broker adapters, and cloud storage pipelines to decide whether a given cloud region is an acceptable *resting place* for a class of trade record. Some jurisdictions do impose genuine in-country storage mandates (China CSL Art. 37 for CII operators and PIPL Art. 40 above CAC volume thresholds; India's RBI payment-system-data circular). Others — contrary to widespread belief — impose none: **GDPR contains no data-localization requirement**, and **SEC Rule 17a-4 imposes no residency mandate**. This module encodes that distinction so trade-record infrastructure is neither illegally offshore nor needlessly fragmented across regions.

## When NOT to Use

- **As a lawful-transfer decision.** Whether a cross-border transfer is permitted turns on a legal instrument this engine does not hold: a GDPR Chapter V mechanism (Art. 45 adequacy, Art. 46 SCCs, Art. 49 derogation) or a PIPL Art. 38 mechanism (CAC security assessment, CAC standard contract, certification). The engine returns `TRANSFER_MECHANISM_REQUIRED` rather than approving. Use `cross-border-data-transfer-restrictions-for-trade-data` for the egress/PII-minimization layer.
- **As legal advice or a jurisdiction-complete rulebook.** Five regimes are encoded. Any other origin jurisdiction resolves to `REVIEW_REQUIRED` by design — that is a prompt for counsel, not a gap to route around.
- **As a retention scheduler.** `verify_sec_17a4_retention` checks a *declared* posture against Rule 17a-4; it does not enforce lifecycle policy. Use `record-retention-periods-by-jurisdiction` and `data-retention-policy-and-storage-tiering`.

## Prerequisites

- Trade record metadata (`record_id`, `origin_jurisdiction`, `destination_cloud_region`, `record_type`, `is_primary_store`).
- `record_type` classified as one of `TRADE_EXECUTION`, `CLIENT_PII`, `PAYMENT_LEDGER`, `MARKET_TICK` — the regime that attaches depends on it.
- A region→jurisdiction map giving each cloud region's **physical** location (defaults provided; override for non-AWS providers).

## Workflow

1. **Resolve the destination's physical jurisdiction** from `destination_cloud_region`, not from its name. A region prefix is not a jurisdiction: `eu-west-2` is London (UK) and `eu-central-2` is Zurich (CH) — both third countries under GDPR. An **unmapped region resolves to `REVIEW_REQUIRED`**, never to an approval.
2. **Classify the record.** `MARKET_TICK` (exchange-disseminated price/size with no counterparty identity) is not personal data, so PIPL Art. 40 and GDPR do not attach to it; `CLIENT_PII`, `TRADE_EXECUTION`, and `PAYMENT_LEDGER` do carry personal data.
3. **Apply the origin regime:**
   - `CN`: in-country → `COMPLIANT`. Personal data leaving China → `LOCALIZATION_VIOLATION_BLOCKED` (default-deny: export is lawful only via a PIPL Art. 38 mechanism the engine cannot see). Market ticks leaving China → `REVIEW_REQUIRED`, because the DSL "important data" catalogue can still capture non-personal data.
   - `IN`: in-country → `COMPLIANT`. `PAYMENT_LEDGER` offshore → `BLOCKED` under the RBI circular. Other Indian trade records offshore → `REVIEW_REQUIRED`, **not** blocked: SEBI CSCRF's data-localization standard PR.DS.S2 was kept in abeyance on 31 Dec 2024, so no in-force securities-market mandate applies.
   - `EU`: in EEA → `COMPLIANT`. Personal data to a third country → `TRANSFER_MECHANISM_REQUIRED`, **not** a violation — GDPR mandates no localization. Non-personal market data → `COMPLIANT`.
   - `US`/`UK`: no residency mandate; offshore storage is `COMPLIANT` *except* into a jurisdiction whose export controls can defeat prompt production (SEC Rule 17a-4(j)), which yields `REVIEW_REQUIRED`.
   - Anything else → `REVIEW_REQUIRED`. The absence of a rule is never read as permission.
4. **Verify retention separately** with `verify_sec_17a4_retention`: 6 years for 17a-4(a) records (first two easily accessible), 3 years for 17a-4(b), and **either** WORM **or** the audit-trail alternative added by the 2022 amendments (effective 3 Jan 2023).
5. **Read the audit trail.** Every decision is appended to `engine.audit_trail`, which returns per-entry copies so a recorded `BLOCKED` decision cannot be edited into an approval.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Treating GDPR as a localization law.** GDPR has no in-country storage requirement; it regulates the *transfer*, which adequacy (the EU–US Data Privacy Framework, adopted 10 Jul 2023 and upheld by the General Court on 3 Sep 2025), SCCs, or a derogation can satisfy. Blocking every EU→US route as a "GDPR violation" fragments infrastructure for no legal reason while teaching an agent the wrong rule.
- **Reading `eu-` as "in the EU".** `eu-west-2` (London) and `eu-central-2` (Zurich) sit outside the EEA. A residency check keyed on the region-name prefix silently approves a third-country transfer.
- **Applying India's payment-data rule to trade records.** The RBI circular DPSS.CO.OD No.2785/06.08.005/2017-2018 (6 Apr 2018) covers **payment system data**. SEBI CSCRF PR.DS.S2 — the securities-market localization standard — has been in abeyance since 31 Dec 2024. Asserting a hard SEBI mandate is regulatory misinformation in the other direction.
- **Believing WORM is still mandatory under 17a-4.** The 2022 amendments retained WORM as an *option* and added an audit-trail alternative that permits recreation of a modified or deleted record. Rejecting a compliant audit-trail system forces an unnecessary storage migration.
- **Applying one retention number to every record.** 17a-4(a) records run 6 years; 17a-4(b) records run 3. A flat "6-year WORM" rule over-retains order memoranda and under-documents the accessibility qualifier.
- **Fail-open defaults for unlisted jurisdictions.** A residency gate whose `else` branch returns compliant approves Russian records (Federal Law 242-FZ requires personal data of Russian citizens to be held in databases in Russia) simply because no rule was written. Unknown must mean unresolved.
- **Conflating anonymized ticks with PII.** Applying strict localization to public price quotes while missing raw trader PII and FIX order messages gets the cost/risk trade-off exactly backwards.
- **Exempting backups and DR replicas.** `is_primary_store` is carried for audit context but deliberately does **not** relax any rule: a cross-region S3 replica, a nightly snapshot, or a DR copy is still a resting copy abroad. The RBI circular permits foreign *processing* only where the data is brought back and deleted; China's export rules attach to the transfer, not to the copy's label.

## Verification

- Instantiate `DataLocalizationComplianceEngine`. Submit a Chinese trade record (`origin="CN"`, `destination_cloud_region="us-east-1"`) and verify `status="LOCALIZATION_VIOLATION_BLOCKED"`. Submit an Indian payment ledger to `ap-south-1` and verify `status="COMPLIANT"`.
- Submit an EU `CLIENT_PII` record to `us-east-1` and verify `status="TRANSFER_MECHANISM_REQUIRED"` with `is_compliant=False` — not a flat violation.
- Submit records with `origin_jurisdiction="RU"` and with an unmapped region (`"xx-nowhere-9"`) and verify both return `REVIEW_REQUIRED` with `is_compliant=False` (no fail-open).
- Submit an EU record to `eu-west-2` and verify `destination_jurisdiction="UK"`, not `"EU"`.
- Call `verify_sec_17a4_retention` with `storage_mode="AUDIT_TRAIL"` and verify it passes; with `retention_years=3` against `17a-4(a)` and verify it fails.
- Mutate an entry returned by `engine.audit_trail` and verify the engine's own record is unchanged.
- Run `python -m unittest discover -s skills/data-localization-requirements-for-trade-records/scripts`.

## Related Skills

- `cross-border-data-transfer-restrictions-for-trade-data`
- `record-retention-periods-by-jurisdiction`
- `record-keeping-requirements-for-tax-audit-defense`
