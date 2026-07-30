# Standards for Graduated Data Quality Response

| Metric | Engineering Standard |
|---|---|
| Tier 0 (Normal) | Quality Score $Q \ge 90\% \implies$ 100% position sizing allowed. |
| Tier 1 (Minor Degradation) | $70\% \le Q < 90\% \implies$ 50% position sizing haircut applied. |
| Tier 2 (Moderate Degradation) | $40\% \le Q < 70\% \implies$ New position entries BLOCKED. |
| Tier 3 (Severe Outage) | Quality Score $Q < 40\% \implies$ EMERGENCY HALT & FLATTEN triggered. |