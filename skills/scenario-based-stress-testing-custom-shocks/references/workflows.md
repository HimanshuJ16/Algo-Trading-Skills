# Workflows for Scenario-Based Stress Testing with Custom Shocks

## 1. Map positions to factors and sensitivities

Build one `AssetPosition` per *factor exposure*, not per instrument. A convertible bond
occupies two rows — an `EQUITY_SPOT` row carrying its delta-equivalent value and a
`CREDIT_SPREAD` row carrying its spread duration — and both are shocked independently.

- `current_value_usd` is **signed**: a short is negative, and it gains when its factor
  falls.
- `beta_to_factor` means different things per shock type. For a relative shock it is a
  return elasticity (β = 1.1 → the position returns 1.1× the factor's return). For a
  yield/bps shock it is a duration in years, passed **positive**; the engine applies the
  minus sign.
- The factor name is a plain string join key. Nothing validates it against a factor
  universe, so a rename in reference data silently detaches a position from its shock.
  Step 4 is how you catch that.

## 2. Define or select scenarios

Three `HISTORICAL_CRISIS` replays run automatically; anything else is a
`StressScenarioDefinition` passed as `custom_scenarios`.

- Scenario ids must be unique across predefined and custom scenarios — reusing
  `SCEN_2008_LEHMAN` for your own scenario raises rather than producing two rows that any
  lookup by id would resolve arbitrarily.
- A factor may appear at most once per scenario. Two shocks on the same factor raise; in
  version 1.0.0 the later one silently won.
- Choose the shock type from the factor's nature, not from the scenario's:
  - price-like (equity, commodity, a proportional move in a vol level) → `RELATIVE_RETURN`,
    magnitude as a fraction, bounded at −1.0;
  - yield-like (rates, credit spreads) → `YIELD_BPS`, magnitude in basis points.
- `is_absolute_change=True` is the pre-2.0.0 spelling of `YIELD_BPS` and still works, but
  its arithmetic changed sign in 2.0.0. Migrate to `shock_type`.
- Historical replays are a floor, not a ceiling. BCBS d450 Principle 4 explicitly
  contemplates scenarios "not based on historical events and empirically observed
  relationships … if new or heightened vulnerabilities are identified, or if historical
  data do not contain a severe crisis episode."

## 3. Set the capital base

`capital_base_usd` is the denominator for every percentage in the report.

- Long-only book, base omitted → the sum of position values is used.
- Any short in the book → an explicit base is **required**. The net sum of a hedged book
  is not a drawdown denominator: a $1M long against a $999k short nets to $1,000, and a
  −$350 scenario loss over that reads as −35%.
- The base must be finite and strictly positive.

## 4. Run, then read the coverage fields before the P&L

```python
report = tester.run_multi_factor_stress_test(positions, custom_scenarios, capital_base_usd)
```

Read in this order:

1. `report.status` — `STRESS_TEST_COMPLETE` or `STRESS_TEST_INCOMPLETE_FACTOR_COVERAGE`.
2. `report.factors_never_shocked` and `report.value_never_shocked_usd` — factors held in
   the book that *no* scenario in this run touched. A run where that covers the whole book
   raises instead of returning an all-clear.
3. Per scenario, `unshocked_asset_ids` and `unshocked_value_usd` — what this particular
   scenario left alone. Partial coverage is expected and legitimate: a rates scenario
   properly leaves an equity book at zero.
4. Only then `simulated_pnl_usd`, `percentage_loss_pct` and
   `is_drawdown_limit_breached`.

A $0 scenario loss means either nothing moved or nothing matched. The coverage fields are
what tell the two apart.

## 5. Audit the limit breaches

`is_drawdown_limit_breached` is `loss_pct < -max_allowed_drawdown_pct`, evaluated on the
unrounded loss and strictly — a loss of exactly the limit passes. `breached_scenario_ids`
collects them; breaches and incomplete coverage are logged at WARNING through the module
logger, so configure a handler if this runs unattended.

`worst_case_scenario` and `max_loss_usd` name the least-favourable scenario even when
every scenario was a gain, in which case `max_loss_usd` is positive.

## 6. Archive the report

`results` carries the per-scenario breakdown — P&L, percentage, capital base, value
shocked and value not shocked — so every aggregate traces to its drivers. Store the
scenario definitions alongside the report: the numbers are meaningless without the shock
vector that produced them, and the defaults shipped here are library defaults rather than
calibrated house scenarios.

## Worked example — a mixed book under the 2008 replay

```python
positions = [
    AssetPosition("SPX", "EQUITY_SPOT", 600_000.0),                       # beta 1.0
    AssetPosition("HY",  "CREDIT_SPREAD", 400_000.0, beta_to_factor=5.0), # spread dur 5y
]
```

- Equity: $600{,}000 \times -0.35 = -\$210{,}000$
- Credit: $-5 \times 0.0300 \times \$400{,}000 = -\$60{,}000$
- Total: $-\$270{,}000$ on a $1,000,000 base $\Rightarrow$ −27.0%, breaching a 20% limit.

Under version 1.0.0 the credit leg returned $400{,}000 \times 3.00 \times 5 = +\$6{,}000{,}000$
— the "+300bps" comment was encoded as a +300% relative shock — and the scenario reported
a gain.
