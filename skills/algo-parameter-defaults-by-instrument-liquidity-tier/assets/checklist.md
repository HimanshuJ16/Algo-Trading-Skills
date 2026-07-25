# Checklist for Algo Parameter Defaults

- [ ] Ensure 30-day ADV is accurately passed to the manager (not intraday volume).
- [ ] Verify that LOW liquidity tiers explicitly disable `cross_spread_allowed`.
- [ ] Verify that HIGH liquidity tiers enforce a strict, low `max_participation_rate` to avoid signaling.
- [ ] Tests pass: `python scripts/test_algo_parameter_defaults_by_instrument_liquidity_tier.py`

## Sign-off
- Execution Quant: ___________________________
- Date: ___________________________