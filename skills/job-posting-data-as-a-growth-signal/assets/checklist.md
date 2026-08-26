# Pre-Flight / Sign-off Checklist — job-posting-data-as-a-growth-signal

Use this before treating a Corporate Expansion Score as a usable factor.

## Data Provenance

- [ ] Do both posting counts come from the **same vendor panel** under the **same** point-in-time convention?
- [ ] Has the vendor's **publication lag** been applied upstream, so the snapshot reflects what was knowable on the decision date?
- [ ] Has the panel been checked for a **coverage change** between the two periods (career sites added/removed, company renamed, M&A)?
- [ ] Is the count source known — direct-from-career-site or aggregator? Aggregator feeds carry cross-posted duplicates and are not comparable to direct feeds.
- [ ] Have licensing, scraping-terms and MNPI questions been cleared separately? This engine is not a compliance control.

## Inputs

- [ ] Are role shares fractions in `[0.0, 1.0]` (0.5 == 50%), not whole numbers?
- [ ] Do `engineering_postings_pct + sales_postings_pct` sum to at most 1.0?
- [ ] Is `avg_posting_duration_days` a real, finite number — no NaN placeholder for "unknown"?
- [ ] For a **seasonal hirer**, is `previous_active_postings_count` the same quarter one year prior rather than the immediately preceding quarter?
- [ ] Is one base convention (YoY or QoQ) used consistently across the whole cross-section?

## Calibration — None of These Defaults Are Standards

- [ ] Is `min_previous_postings` set at or above the vendor's coverage floor for the smallest names in the universe?
- [ ] Is `ghost_job_stale_days_threshold` calibrated on this panel's duration distribution, not inherited at 120 days by default?
- [ ] Has the **cliff** at the threshold been considered — firms at 119 vs 121 days score 2× apart?
- [ ] Does an **average** duration actually detect ghosting on this panel, or does it mask the stale tail? Where per-posting creation/deletion dates exist, is a stale *share* used instead?
- [ ] For engineering-heavy books, has `stale_haircut_factor` been reconsidered? Longer vacancy duration for high-skill roles is associated with *higher* future profitability (Chen & Li, 2023).

## Reading the Output

- [ ] Is cross-sectional ranking done on `raw_growth_score` or `qoq_postings_growth_pct` rather than the **saturated** `corporate_growth_score`?
- [ ] Is `has_ghost_postings_penalty` checked before trusting a `STABLE_NEUTRAL`? The haircut shrinks a contraction toward neutral just as much as an expansion.
- [ ] Are `INSUFFICIENT_DATA` rows excluded from the factor rather than treated as zeros in a ranking?
- [ ] Is `EXPANSION_BULLISH` being used as a **fundamental** input combined with valuation and risk factors — not as a standalone buy signal? The realized-hiring-rate factor predicts *lower* returns (Belo, Lin & Bazdresch, 2014).
- [ ] Are `audit_notes` persisted alongside the engine configuration that produced them?

## Testing

- [ ] **Automated Testing:** Run `python -m unittest discover -s skills/job-posting-data-as-a-growth-signal/scripts` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Vendor, panel, base convention (QoQ/YoY), and the four calibrated parameters: ___________________________
