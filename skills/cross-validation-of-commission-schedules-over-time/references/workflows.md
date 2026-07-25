# Deep Workflow Reference — cross-validation-of-commission-schedules-over-time

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

1. **Construct Time-Varying Fee Schedule**: Define commission schedule rules indexed by effective date ranges.
2. **Lookup Date-Specific Fee Rate**: For each trade, resolve the applicable broker commission rate based on trade timestamp $T_{\text{trade}}$.
3. **Compute Trade Commission**: Calculate exact fee considering per-share rates, ticket minimums, and volume tiering.
4. **Audit Fee Schedule Impact**: Compare backtest return using historical fee schedules vs fixed modern fee schedules.

## Production Implementation Reference

- Reference code: `scripts/commission_schedule_modeler.py` (`HistoricalCommissionModeler`, `CommissionTier`, `TradeCommissionResult`).
- Automated unit tests: `scripts/test_commission_schedule_modeler.py`.
