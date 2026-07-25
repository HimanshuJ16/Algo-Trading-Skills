# Deep Workflow Reference — broker-margin-interest-accrual-tracking

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

1. **Margin Rate Tier Schedule Setup**:
   - Register rate tiers (e.g. $0–$100k @ 6.50% APR; $100k–$1M @ 5.80% APR).

2. **Daily Margin Debit Balance Calculation**:
   - Compute daily debit balance $B_t = \max(0, -\text{CashBalance}_t) + \text{ShortMarketValue}_t$.

3. **Accrual Simulation & Weekend Compounding**:
   - Calculate daily interest $I_t = B_t \times (\text{APR} / 365)$.
   - Charge 3 days of interest on Friday night holdings (Fri, Sat, Sun).

4. **Net P&L Adjustment**:
   - Deduct total accrued interest from Gross P&L: $\text{NetPnL}_{\text{adjusted}} = \text{GrossPnL} - \sum I_t$.

## Production Implementation Reference

- Reference code: `scripts/margin_interest.py` (`MarginInterestTracker`, `MarginRateTier`, `MarginInterestSummary`).
- Automated unit tests: `scripts/test_margin_interest.py`.
