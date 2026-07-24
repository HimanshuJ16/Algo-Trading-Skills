# Deep Workflow Reference — benchmark-relative-performance-attribution

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Full Procedure

1. **Synchronize Portfolio & Benchmark Return Series:**
   - Align daily return vectors $R_p$ and $R_b$.

2. **Compute Alpha ($\alpha$) and Beta ($\beta$):**
   - Calculate Beta: $\beta = \frac{\text{Cov}(R_p, R_b)}{\text{Var}(R_b)}$.
   - Calculate Annualized Alpha: $\alpha = (R_p - R_f) - \beta (R_b - R_f)$.

3. **Compute Active Return & Information Ratio ($IR$):**
   - Calculate Tracking Error: $TE = \text{Std}(R_p - R_b) \cdot \sqrt{252}$.
   - Calculate Information Ratio: $IR = \frac{\text{Active Return}}{TE}$.

4. **Execute Brinson-Fachler Sector Attribution:**
   - Allocation Effect: $A_i = (w_{p,i} - w_{b,i}) \cdot (R_{b,i} - R_b)$.
   - Selection Effect: $S_i = w_{b,i} \cdot (R_{p,i} - R_{b,i})$.
   - Interaction Effect: $I_i = (w_{p,i} - w_{b,i}) \cdot (R_{p,i} - R_{b,i})$.

## Failure Modes Observed in Production

- **Beta Mistaken for Alpha:** Attributing gains from market bull runs to strategy alpha without Beta adjustment.
- **Un-Synchronized Dates:** Mismatching strategy and benchmark returns by date.

## Production Implementation Reference

- Reference code: `scripts/attribution_engine.py` (`PerformanceAttributionEngine`, `AttributionSummary`, `BrinsonSectorResult`).
- Automated unit tests: `scripts/test_attribution_engine.py`.
