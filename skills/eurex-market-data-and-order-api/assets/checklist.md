# Pre-Flight Checklist

- [ ] Are T7 EMDI market data depth feeds parsed for mid-price and order book imbalance?
- [ ] Are contract-specific tick steps (1.0 for FESX, 0.01 for FGBL) validated?
- [ ] Is T7 ETI binary header (`template_id: 10100`, `session_id`, `sequence_no`) formatted correctly?
- [ ] Are price reasonability limits checked prior to order dispatch?
