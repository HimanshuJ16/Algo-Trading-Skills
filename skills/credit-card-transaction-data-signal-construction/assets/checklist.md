# Pre-Flight Checklist

- [ ] Is panel scaling factor ($\gamma_{\text{panel}}$) calibrated against historical reported 10-Q revenues, and recalibrated after any panel composition change?
- [ ] Is Year-over-Year (YoY) revenue growth calculated on seasonality-aligned quarters (t vs t-4) under the company's fiscal calendar (NRF 4-5-4 for US retailers, with 53-week restatements)? Labels in `YYYY-Qn` form are alignment-checked by the engine; other schemes are not.
- [ ] Has the surprise threshold been calibrated to the panel's measured prediction error rather than left at the ±2.5% default?
- [ ] Is the vendor's actual documented delivery lag (12h–7d depending on vendor/tier) incorporated in backtests, with as-delivered (point-in-time) snapshots rather than final restated values?
- [ ] Is the Wall Street consensus point-in-time (as-of estimate date), not restated?
- [ ] Has the ticket-size/volume decomposition been checked for panel-shift artifacts (volume collapsing with stable ticket size)?
- [ ] Are prior-year panel bases validated (zero/negative bases raise errors rather than emitting 0% growth)?
- [ ] Is vendor due diligence documented (aggregation/de-identification representations) with MNPI procedures per Advisers Act Section 204A and Rule 204A-1 (or local analogue)?
- [ ] Is `confidence_score` used only for ranking signals, never for position sizing?
