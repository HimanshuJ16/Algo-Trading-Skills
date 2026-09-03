# Pre-Flight / Sign-off Checklist — multi-asset-backtest-currency-normalization

Use this before considering the skill's implementation complete.

## Rate convention

- [ ] **Direction pinned:** every stored rate is units of `to` per **one** unit of
      `from`, so conversion is a multiplication.
- [ ] **Vendor direction verified:** confirmed against a known anchor (1 USD is ~150 JPY,
      not ~0.0067 JPY). ECB rows quote the euro as base (`EUR -> USD`).
- [ ] **Inverses derived, not stored:** an explicitly quoted opposite direction cannot
      overwrite an already-registered rate.
- [ ] **Conflicting quotes rejected:** re-registering the same pair/date with a different
      rate raises unless overwrite is explicitly requested.

## Data validation

- [ ] **Finiteness enforced with `math.isfinite`,** not with `rate <= 0` — `nan` passes a
      comparison check and produces a NaN NAV.
- [ ] **Currency codes normalized and validated** as three-letter ISO 4217 alphabetic
      codes; `"USD "` cannot open a second ledger.
- [ ] **Date type enforced:** `datetime.datetime` rejected, so it cannot hash to a key no
      lookup will ever match.
- [ ] **Non-finite cash, quantity and price rejected** at the ledger boundary.

## Point-in-time integrity

- [ ] **Rates queried by valuation date $T$,** never a single current-day rate applied
      across history.
- [ ] **Missing-rate policy chosen deliberately:** exact-match, or a bounded
      `max_staleness_days` fallback.
- [ ] **Fallback searches backwards only** — a rate dated after the valuation date is
      never used (look-ahead guard).
- [ ] **Staleness recorded and logged** (`fx_rate_dates_used`, `stale_fx_currencies`), so
      a NAV marked on an old rate is auditable.

## Valuation & NAV

- [ ] **Per-currency cash balances held natively;** negative balances accepted as
      foreign-currency margin loans.
- [ ] **Local and reporting figures separately tagged** and never summed together.
- [ ] **Per-symbol breakdown accumulates** repeated lots instead of overwriting them.
- [ ] **A held currency with no usable rate aborts the valuation** rather than being
      silently omitted from NAV.
- [ ] **Reporting currency recorded on the snapshot** and used in every log line (no
      hard-coded `$`).

## Attribution & cost realism

- [ ] **NAV change decomposed into local + FX translation + interaction,** with the three
      components reconstructing the total exactly.
- [ ] **External cash flows netted out** before reading the local effect as P&L.
- [ ] **FX conversion costs modelled in the execution layer** (spread + broker fee on
      trades that actually convert), and *not* charged against mid-rate translation.

## Testing

- [ ] Run `python -m unittest discover -s skills/multi-asset-backtest-currency-normalization/scripts` and confirm 100% pass rate.
- [ ] Confirm the negative cases raise: `nan`/`inf`/zero/negative rates, malformed
      currency codes, `datetime` keys, missing rates, reversed attribution dates.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
