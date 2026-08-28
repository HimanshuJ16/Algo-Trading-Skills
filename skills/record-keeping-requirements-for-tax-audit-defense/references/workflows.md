# Workflows for Record-Keeping Requirements for Tax Audit Defense

Scope: US federal income tax. Evaluate every run against an explicit `as_of`
date so results are reproducible.

1. **Mandatory Field Completeness Audit**
   - Flag any record missing a configured mandatory field (`trade_id`, `symbol`,
     `side`, `quantity`, `price`, `trade_date`, `cost_basis_usd` by default).
   - Treat a missing `cost_basis_usd` as the highest-priority defect: absent
     basis evidence the IRS may assert a zero basis and tax gross proceeds.

2. **Structural Validation**
   - Reject unparseable ISO dates, sides outside `BUY`/`SELL`, non-positive or
     non-finite quantities, negative prices, unknown lot methods, and disposals
     dated before acquisition.
   - Report defects as data rather than raising — a single malformed row must
     not abort an audit over a large record set.
   - Flag duplicate `trade_id`s once per duplicated key.

3. **Holding Period Classification (IRC § 1222)**
   - Prefer `acquisition_date` + `disposal_date`. Long-term requires disposal
     strictly after the one-year anniversary; a 29 February acquisition takes a
     28 February anniversary, because the holding period starts 1 March.
   - Fall back to `holding_period_days` only when dates are unavailable:
     $\le 365$ short-term, $\ge 367$ long-term, $366$ ambiguous.
   - Resolve every ambiguous record by obtaining the dates. Do not default it.

4. **Wash Sale Determination (IRC § 1091)**
   - For each capital-account sell, confirm a recorded `wash_sale_flag`,
     including a negative determination.
   - If `as_of` falls within 30 days of the sale, mark the determination
     provisional (advisory) rather than defective — the replacement window is
     still open and a later purchase can change the answer.
   - Re-run the determination once the window closes, then persist the final
     result.

5. **Lot Identification Substantiation (Treas. Reg. § 1.1012-1(c))**
   - For `SPECIFIC_ID` sells, require a `lot_identification_date` no later than
     the settlement deadline (T+1 for most US securities since 2024-05-28).
   - Where the identification is missing or late, treat the position as FIFO and
     re-verify any gain/loss computed on the specific-lot assumption.

6. **§ 475(f) Segregation Evidence**
   - Where a mark-to-market election is in force, skip steps 3 and 4 for trading
     securities — neither the capital short/long distinction nor § 1091 applies.
   - Require an `investment_identification_date` equal to the acquisition date
     for every security marked `held_for_investment`; those remain capital assets
     and are still subject to steps 3 and 4.

7. **Retention Policy Enforcement**
   - Anchor the clock to `disposal_date` (defaulting to `trade_date` for a sell).
   - Where no disposal date is known, report no purge date: the lot may still be
     open and its basis record must survive until the limitations period for the
     disposal year expires.
   - Compute `earliest_purge_date = disposal_date + retention_years` and flag any
     pending purge that is not yet eligible.
   - Never mark a record under `legal_hold` purge-eligible.

8. **Audit Report Generation**
   - Emit a `TaxAuditComplianceReport` separating `DEFECT` from `ADVISORY`
     counts, with a per-record `RetentionAssessment` rationale for each entry.
   - Remediate defects before filing; track advisories for re-evaluation once
     their windows close.
