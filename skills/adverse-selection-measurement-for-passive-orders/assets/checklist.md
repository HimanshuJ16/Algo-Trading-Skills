# Checklist for Adverse Selection Measurement

- [ ] Ensure only PASSIVE (limit) orders are passed to the evaluator.
- [ ] Verify that time horizons match the holding period of the strategy (e.g., HFT needs 10ms horizons, Swing trading needs 60s horizons).
- [ ] Confirm Sell side markout math is inverted relative to Buy side.
- [ ] Check that `test_adverse_selection_measurement_for_passive_orders.py` passes.
- [ ] Review the `AdverseSelectionReport` for toxicity warnings before scaling capital.

## Sign-off
- Execution Quant: ___________________________
- Date: ___________________________
