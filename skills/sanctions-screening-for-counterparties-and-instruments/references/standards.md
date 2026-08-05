# Standards for Sanctions Screening for Counterparties and Instruments

| Screening Dimension | Standard Rule | Enforcement Mechanism |
|---|---|---|
| OFAC 50% Rule | Entities $\ge 50\%$ owned by sanctioned parties MUST be blocked. | Automatic ownership % audit |
| Fuzzy Match Threshold | Levenshtein name similarity MUST be calibrated to $\ge 85.0\%$. | Automated string distance |
| Embargoed Jurisdictions | IR, KP, CU, SY, RU_CRIMEA MUST be hard-blocked. | ISO Country Code Filter |