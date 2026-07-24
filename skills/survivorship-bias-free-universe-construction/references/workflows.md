# Deep Workflow Reference — survivorship-bias-free-universe-construction

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Full Procedure

1. **Populate Constituent Master Database:**
   - Register all historical components with `listing_date`, `delisting_date`, `delisting_reason`, and `delisting_settlement_price`.

2. **Query Point-in-Time Active Universe:**
   - On each backtest bar date $T$, execute `get_active_universe(as_of_date=T)`.
   - Filter instruments where $\text{listing\_date} \le T \le \text{delisting\_date}$.

3. **Handle Delisting Event Settlement:**
   - When simulation reaches $T = \text{delisting\_date}$:
     - If `BANKRUPTCY`: Liquidate position at $0.00$ recovery value.
     - If `MERGER_ACQUISITION`: Liquidate position at buyout acquisition price.

4. **Execute Survivorship Bias Coverage Audit:**
   - Invoke `audit_survivorship_bias(start_date, end_date)`. Verify delisted ratio > 0.

## Failure Modes Observed in Production

- **Current Index Backtesting:** Applying current index constituents backwards in time, omitting failed companies.
- **Ignoring Bankruptcy Losses:** Removing delisted stocks from universe without realizing 100% loss on open long positions.

## Production Implementation Reference

- Reference code: `scripts/universe_builder.py` (`SurvivorshipFreeUniverseEngine`, `InstrumentMetadata`, `DelistingReason`).
- Automated unit tests: `scripts/test_universe_builder.py`.
