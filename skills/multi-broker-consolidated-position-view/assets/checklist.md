# Pre-Flight / Sign-off Checklist — multi-broker-consolidated-position-view

Use this before considering the skill's implementation complete.

## Inputs

- [ ] **Symbol Translation Mapping:** Every traded broker symbol maps to a canonical
      symbol — not just the ones that currently collide.
- [ ] **Strict Mapping Enabled:** `strict_symbol_mapping=True` in production. If not,
      `unmapped_broker_symbols()` is checked and treated as blocking before the view
      is consumed.
- [ ] **Signed Quantities:** Adapters emit negative quantities for shorts (IBKR
      `position`, Alpaca `qty`, Binance `positionAmt`) rather than a separate side flag.
- [ ] **Contract Multipliers:** Every derivative leg carries an explicit
      `contract_multiplier` read from the contract definition, not assumed from the
      instrument type or ticker (adjusted option contracts break the "always 100" rule).
- [ ] **Cost-Basis Convention:** For each adapter, it is determined and encoded whether
      the reported cost field already embeds the multiplier
      (`average_cost_includes_multiplier`). IBKR `avgCost` does for derivatives.

## FX and valuation

- [ ] **No Hardcoded Rates:** `fx_rates` is injected from a live source; no rate
      literals are committed to code or config as a fallback.
- [ ] **Rate Direction:** Rates are base-currency units per one unit of the quoted
      currency, verified against a known pair (e.g. a EUR position valued *up* into USD
      when EUR/USD > 1, not down).
- [ ] **Base Currency Anchored:** `fx_rates[base_currency] == 1.0`.
- [ ] **Unknown Currency Fails Closed:** A position in a currency absent from the table
      raises `MissingFxRateError` rather than converting at 1:1.

## Snapshot integrity

- [ ] **Timestamps Present:** Every leg carries a timezone-aware `as_of` from the broker.
- [ ] **Max Age Enforced:** `max_snapshot_age` is configured and `valuation_time` is
      passed, so a stale leg raises instead of being blended with live ones.
- [ ] **FX Table Timestamped:** `fx_rates_as_of` is set and age-checked.
- [ ] **Skew Monitored:** `snapshot_skew_seconds` is surfaced to operators and
      alert-thresholded.

## Accounting outputs

- [ ] **Net & Gross Quantity:** Both computed and distinguished.
- [ ] **Gross Market Value Consumed Downstream:** Any gross-exposure or GMV cap reads
      `gross_market_value_base`, not the signed `total_market_value_base`.
- [ ] **Internal Offsetting Surfaced:** `is_internally_offset` is routed to whoever
      decides whether to collapse the offsetting legs.
- [ ] **Flat Position Cost Handled:** Consumers treat `weighted_avg_cost_base is None`
      as "undefined", not as zero.
- [ ] **Cost Basis Not Used for Accounting:** The blended average cost is not fed into
      tax, statement, or realized-P&L reporting.

## Reconciliation

- [ ] **Tolerance Per Instrument:** `symbol_tolerances` set for anything quoted to more
      than a few decimals; the 1e-5 default is not silently applied to crypto.
- [ ] **Tolerance Has Headroom:** Tolerances are not set at the exact expected quantity
      increment (binary float makes a "round" decimal tolerance non-exact).
- [ ] **Break Kinds Routed:** `QUANTITY_MISMATCH`, `MISSING_AT_BROKER`, and
      `UNEXPECTED_AT_BROKER` have distinct operational responses, and
      `UNEXPECTED_AT_BROKER` triggers a symbol-mapping check before a rogue-fill escalation.
- [ ] **Broker Treated As Authoritative:** No process overwrites broker state from this
      derived view.

## Validation

- [ ] **Automated Testing:** Run `python scripts/test_consolidated_ledger.py` — 100%
      pass rate.
- [ ] **Reconciled Against a Broker Statement:** At least one live consolidated view
      has been tied out to the brokers' own native-currency position reports.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
