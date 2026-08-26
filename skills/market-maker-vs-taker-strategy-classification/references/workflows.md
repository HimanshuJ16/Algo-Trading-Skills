# Workflows — market-maker-vs-taker-strategy-classification

## 0. Scope the audit before loading anything

- One venue per run. Per-share and percentage-of-notional schedules do not share a
  pricing unit, so an effective bps figure blended across them is meaningless.
- One reporting period per run, aligned to whatever period the venue bills on.
- Decide up front what the answer will be used for. "Which side of the book is my flow
  on, and what did it cost" is in scope. "Am I a market maker" and "is my passive
  strategy working" are not — see `references/standards.md`.

## 1. Choose the classification basis

| Venue prices... | Basis | Why |
|---|---|---|
| per share / per unit (US equities) | `ClassificationBasis.QUANTITY` | The bill is proportional to units, so the ratio should be too. |
| as a percentage of trade value (crypto) | `ClassificationBasis.NOTIONAL` | The bill is proportional to value. |
| per contract by membership (CME futures) | neither — out of scope | There is no liquidity flag to attribute. |

There is no default. The engine requires the basis because the two can classify the same
log differently: a desk that posts passively in a cheap name and crosses the spread in an
expensive one is maker-heavy by share count and taker-heavy by value.

`QUANTITY` is rejected for a multi-symbol log — share counts are not additive across
instruments. Either classify one symbol at a time or use `NOTIONAL`.

## 2. Normalise the fill log

For each fill, establish:

1. **The liquidity category, not just a boolean.** Map the venue's liquidity indicator to
   `LiquidityCategory`: FIX tag 851 `1 -> ADDED`, `2 -> REMOVED`, `3 -> ROUTED_OUT`,
   `4 -> AUCTION`. Omit the field only when you know every fill was a continuous-book
   add or remove; it then derives from `is_maker`.
2. **A real boolean `is_maker`.** JSON `"false"` is truthy in Python. Parse it, do not
   pass it through. The engine rejects a non-bool rather than mis-booking the fill.
3. **Positive price and quantity.** Encode side separately; a negative quantity is a
   corrupt record, not a sell. Non-finite values are rejected rather than propagated as
   NaN through every downstream figure.
4. **A unique `trade_id` per fill.** Overlapping paginated fetches are the normal way
   the same fill arrives twice, and a double-counted fill corrupts every figure in the
   report. The engine rejects a repeated id; if the venue reuses one id across partial
   fills, key on the per-fill execution id instead.
5. **The signed fee actually billed.** Positive = charged, negative = credited. Use the
   venue's billed amount, not a rate card reconstruction — reconstruction belongs in
   `exchange-fee-tier-and-rebate-structure-analysis`.

If the venue reports a liquidity code this module has no category for (midpoint, hidden,
retail price improvement, and similar venue-specific codes), decide deliberately whether
it is an add, a remove, or neither, and record that mapping decision alongside the audit.
The engine rejects unknown category strings rather than guessing.

## 3. Decompose volume and classify

1. Bucket every fill into maker (`ADDED`), taker (`REMOVED`), or excluded
   (`ROUTED_OUT`, `AUCTION`).
2. $R_{\text{maker}} = W_{\text{maker}} / (W_{\text{maker}} + W_{\text{taker}})$ on the
   selected basis. Excluded fills are in neither term.
3. Classify: $\ge$ pure-maker threshold, $\le$ pure-taker threshold, else hybrid; both
   bounds inclusive, compared at full precision.
4. If there is no maker or taker volume at all, the result is
   `UNCLASSIFIED_NO_MAKER_TAKER_VOLUME` with a `None` ratio — not `0.0`.

## 4. Attribute fees and rebates

- Net: $\text{Fee}_{\text{effective\_bps}} = F_{\text{net}} / N_{\text{gross}} \times 10{,}000$
  over **all** fills, excluded ones included — they were still billed.
- Per side: `maker_fees_paid_usd`, `taker_fees_paid_usd`, `excluded_fees_paid_usd`, plus
  each side's effective bps against its own notional.
- The per-side split is the diagnostic that matters. A `PURE_MAKER_STRATEGY` label with a
  positive `maker_fees_paid_usd` means the strategy took on passive execution risk and was
  charged for it anyway.

## 5. Read the report critically

Before quoting any figure from it:

- [ ] Read `warnings` in full. Each entry describes a condition that makes some number
      less than it appears.
- [ ] Check `classification_ratio` against the thresholds yourself if it is near one — a
      label produced 0.001 from a cut-off is an artefact.
- [ ] Check `excluded_trades_count`. Large excluded notional means the continuous-book
      ratio describes only part of the day's activity.
- [ ] Compare `maker_volume_ratio` and `maker_notional_ratio`. A wide gap means the answer
      depends on the basis, and the basis needs to match how the venue bills you.

## 6. Act on it

| Finding | Action |
|---|---|
| Maker-dominant, maker fees positive | The passive posture is not being paid for. Check the venue's maker rate at your tier before assuming the rebate exists (`exchange-fee-tier-and-rebate-structure-analysis`). |
| Taker-dominant, high effective bps | Quantify the fee drag against realized alpha before changing anything; then evaluate passive alternatives with `post-only-and-maker-taker-fee-optimization` and `queue-position-modeling-for-passive-orders`. |
| Unexpected taker fills in a passive algo | Look for limit orders submitted without a post-only flag that crossed on arrival. |
| Wide gap between the two ratios | Re-run on the basis that matches the venue's pricing unit; report both. |
| Maker ratio high but P&L poor | Adverse selection, not fees. See `adverse-selection-measurement-for-passive-orders`. |
