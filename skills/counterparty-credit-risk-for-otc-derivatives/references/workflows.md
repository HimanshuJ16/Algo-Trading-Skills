# Workflows for OTC Counterparty Credit Risk

All formula references are to BCBS 279, "The standardised approach for
measuring counterparty credit risk exposures" (March 2014, rev. April 2014),
https://www.bis.org/publ/bcbs279.pdf (consolidated as Basel CRE52).

1. **Netting Set Aggregation**:
   - Sum MTM values for all contracts in the netting set: $V_{net} = \sum V_{mtm, i}$.
   - Decision point: if no verified ISDA Master Agreement (or equivalent
     legally enforceable netting opinion) covers the set, do NOT net —
     compute exposure trade-by-trade and gross up.

2. **Replacement Cost (margined netting set, para 144)**:
   - $RC = \max(V_{net} - C,\ TH + MTA - NICA,\ 0)$ where $C$ is collateral
     held and $NICA$ is net independent collateral received.
   - The $TH + MTA - NICA$ term floors exposure at the contractually
     uncollateralised band; it binds whenever $V_{net} - C$ drops below it.
   - Unmargined representation: set $TH = MTA = NICA = 0$ to recover
     para 136: $RC = \max(V_{net} - C, 0)$.

3. **PFE Add-On & Multiplier (paras 149, 183)**:
   - $\text{AddOn} = \sum (\text{Notional}_i \times SF_i)$ with supervisory
     factors from `SA_CCR_SUPERVISORY_FACTORS` (BCBS 279 Table 2:
     interest rate 0.5%, FX 4%, equity single-name 32%, equity index 20%,
     commodity 18% / electricity 40%).
   - $m = \min(1,\ 0.05 + 0.95 \cdot e^{(V_{net} - C) / (1.9 \cdot \text{AddOn})})$;
     $PFE = m \times \text{AddOn}$.
   - Decision point: skip the multiplier only when $V_{net} - C \ge 0$
     (it is exactly 1 there).

4. **Exposure at Default (para 128)**:
   - $EAD = 1.4 \times (RC + PFE)$.

5. **CVA Pricing Adjustment (single-period proxy)**:
   - $CVA = (1 - R) \times EAD \times PD$.
   - Undiscounted, single-horizon approximation of the canonical
     $(1-R) \sum_t EE(t) \cdot PD(t) \cdot DF(t)$; use for limit
     monitoring, not pricing.

6. **CSA Margin Call Audit (para 140, footnotes 8-9)**:
   - Delivery Amount $= \max(0,\ V_{net} - C - TH)$.
   - If Delivery Amount $\ge MTA \implies$ trigger margin call for the full
     delivery amount (inclusive boundary); below MTA, exposure drifts
     uncollateralised by design of the CSA.
