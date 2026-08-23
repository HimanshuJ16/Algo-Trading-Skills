# Pre-Flight Checklist

- [ ] Are ISDA Netting Set IDs assigned to all active bilateral OTC derivative contracts?
- [ ] Is close-out netting covered by a verified, legally enforceable ISDA Master Agreement (legal certainty conditions of BCBS 279 met)?
- [ ] Is the CSA direction confirmed (one-way counterparty-posts vs two-way)? This engine assumes one-way.
- [ ] Is Replacement Cost computed as max(V − C, TH + MTA − NICA, 0) — with the TH + MTA floor, not max(0, V − C − TH)?
- [ ] Are supervisory factors sourced from `SA_CCR_SUPERVISORY_FACTORS` / BCBS 279 Table 2 (equity single-name 32%, FX 4%, interest rate 0.5%) rather than memory?
- [ ] Is the PFE multiplier applied when the netting set is over-collateralised (V − C < 0)?
- [ ] Is EAD computed as 1.4 × (RC + PFE)?
- [ ] Is NICA (net independent collateral) included if the CSA has an independent amount?
- [ ] Are collateral haircuts considered before feeding posted collateral at value?
- [ ] Is Current Exposure (CE) calculated on a net MTM basis after deducting posted collateral?
- [ ] Does the margin call trigger fire only at delivery amount ≥ MTA (inclusive)?
- [ ] Is counterparty PD refreshed from market-implied (CDS-derived) levels rather than stale ratings?
- [ ] Is the recovery rate justified (40% ISDA convention for senior unsecured, or entity-specific)?
- [ ] Is Credit Valuation Adjustment (CVA) understood as a single-period proxy, not a pricing-quality CVA?
- [ ] Is the EAD compared against the max credit limit, with a block/top-up decision recorded on breach?
