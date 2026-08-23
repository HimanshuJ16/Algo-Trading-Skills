# Pre-Flight Checklist

- [ ] Is `toxicity_score_bps` measured from **your own** post-trade markouts, never from a venue-published toxicity or "liquidity profiling" score?
- [ ] Are venue statistics validated at ingest (fill rate in $[0,1]$, finite toxicity, non-negative minimum quantity) rather than trusted from the upstream job?
- [ ] Is the as-of timestamp of every venue statistic recorded, and is the refresh cadence understood (FINRA ATS volume is 2–4 weeks delayed by rule)?
- [ ] Are toxic venues ($\text{Toxicity} > \text{MaxToxicity}$) excluded, with the exclusion reason captured in the audit report?
- [ ] Is negative (favourable) toxicity clamped to 0 before the discount, so a broken markout cannot hand one venue the whole parent?
- [ ] Does every child order satisfy $\text{MinQty} \le \text{ChildQty}$ — i.e. no child order that can never trade?
- [ ] Is the venue eligibility gate the *same* minimum used to compute MinQty ($\max$ of venue minimum and engine floor), not the venue minimum alone?
- [ ] Are weights renormalised after dropping under-funded venues, and is the truncation residual redistributed, so $\text{allocated} + \text{unallocated} = \text{parent}$?
- [ ] Is a non-empty `unallocated_reason` (`NO_ELIGIBLE_VENUE`, `PARENT_TOO_SMALL_FOR_VENUE_MIN_QTY`, `VENUE_CONCENTRATION_CAP`) treated as an event the caller must handle, not as noise?
- [ ] Is `max_venue_allocation_pct` set explicitly if concentration in a single pool is a concern (it is `None`/uncapped by default)?
- [ ] Are the ceiling (5.0 bps), fill-rate floor (0.05), toxicity decay (50.0 bps) and MinQty fraction (20%) calibrated on your own realised distributions rather than left at the shipped defaults?
- [ ] Is dark participation capped upstream — does the caller decide how much of the parent goes dark, given this engine places everything it is handed?
- [ ] Are child orders priced at the midpoint by the caller (`ExecInst(18)='M'` / `PegPriceType(1094)=2`), since this module emits size and MinQty only?
- [ ] **EU**: has the single volume cap (7%, reference-price waiver, MiFIR as amended by Reg (EU) 2024/791) suspension status been checked before routing dark under that waiver?
- [ ] **US**: has the venue's Form ATS-N filing been read for its actual MinQty support, order types and counterparty segmentation?
- [ ] Is self-match prevention configured, and is affiliation between the pool operator and unwanted counterparties understood?
- [ ] Do realised markouts feed back into `toxicity_score_bps` on a defined cadence, closing the loop?
