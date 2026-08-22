---
name: capital-efficiency-across-cross-margined-strategies
description: Use when deciding how much collateral a portfolio-margined account frees up
  versus margining each strategy standalone — estimating spread credits between opposing
  legs the way SPAN-style inter-commodity credits work, netting the same instrument across
  strategy sleeves first, and keeping the estimate conservative enough that capital is
  never sized against an offset the broker will not actually grant.
domain: algorithmic-trading
subdomain: risk-management
tags:
- risk-management
- cross-margin
- portfolio-margin
- capital-efficiency
- margin-offset
- collateral
- institutional
brokers_frameworks:
- CME SPAN
- OCC TIMS / Customer Portfolio Margin
- Interactive Brokers
- Bybit Unified Trading Account
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when several strategies share one margined account and capital, not
signal quality, is the binding constraint. Margining every sleeve standalone charges for
risks the account does not actually carry: a long in one sleeve and a short in a related
instrument in another partly cancel, and a portfolio-margin engine prices the residual
rather than the sum.

Use it to answer two questions with a number rather than a hope: how much collateral does
the offset plausibly release, and how much of that release survives a conservative
assumption about the credit the broker will grant. The module reports the isolated total,
the estimated cross-margined total, the Capital Efficiency Ratio between them, and a
per-spread audit trail showing which credit produced which dollar.

## When NOT to Use

- **As a substitute for the broker's margin number.** This is an estimator with a
  published-parameter input, not a margin engine. OCC TIMS and CME SPAN revalue the book
  across a scenario grid; Bybit's Unified Trading Account uses stress-test results. Size
  positions against the broker's own calculator or margin API, and use this to plan.
- **To justify deploying the freed capital.** The output is a *capacity* figure, not a
  recommendation. Freed collateral redeployed into correlated risk is the mechanism by
  which cross-margined books cascade when the correlation breaks.
- **Across venues.** Positions at two exchanges do not offset — separate accounts at
  separate clearing organisations. Inter-clearing-house netting exists only inside formal
  cross-margin programmes; see `cross-margining-across-asset-classes`.
- **For a single-name equity book under US portfolio margin.** OCC's Customer Portfolio
  Margin grants non-index single-stock class groups no offset at all, however correlated
  the names are. A correlation-derived estimate will be pure fiction there; pass an
  explicit credit rate of `0.0`.
- **For per-contract accuracy on options.** Real requirements are floored per contract
  ($0.375 x multiplier under TIMS, the short option minimum under SPAN). This model has no
  contract concept; use `min_cross_margin_fraction` to approximate the floor.

## Prerequisites

- Positions as `(symbol, signed delta, standalone margin)`, **one row per instrument**.
  Rows from different strategy sleeves must be netted first — see step 1.
- Ideally, the broker's or exchange's **published offset percentages** for the pairs you
  hold (SPAN inter-commodity credit tables, OCC product-group offsets, or the broker's
  margin documentation). A correlation matrix is the fallback when those are unavailable.
- An account actually enabled for portfolio margining, and eligible for it: FINRA Rule
  4210(g) sets minimum equity of $100,000 to $500,000 depending on the member's intraday
  monitoring capability, and IBKR requires $110,000 to open a portfolio-margin account and
  $100,000 to keep one.
- If using correlations: a dated, documented estimation window. Correlation is an input
  you chose, not a fact about the future.

## Workflow

1. **Net each instrument across sleeves before margining anything.** Two sleeves holding
   BTC — one long, one short — are one net position to the broker, and the account is
   margined on the net. `net_positions_by_symbol` collapses them, scaling margin by the
   net delta at the group's highest margin rate and capping the result at the summed
   standalone margin so netting can never raise the requirement. `calculate_margin`
   **raises** on duplicate symbols rather than silently margining them as two spreadable
   legs.
2. **Prefer a published credit rate over a correlation.** Pass
   `credit_rate_overrides={'CORN': {'SOYB': 0.65}}` where the exchange publishes the
   number; it is used as given and the haircut is not applied on top, because a published
   rate is already the post-haircut figure. Correlation is consulted only for pairs with
   no override.
3. **Treat the haircut as your conservatism, not a standard.** `correlation_haircut`
   defaults to 0.80. No regulator or clearing house prescribes that figure — it exists
   because a raw historical correlation is the most optimistic number in the calculation.
4. **Read the spread audit trail, not just the total.** Each `OffsetCredit` names the two
   legs, the rate, the amount spread and whether the rate was `published` or
   `correlation`. A total driven by `correlation`-sourced credits on pairs the broker
   groups separately is a total that will not survive contact with the margin engine.
5. **Floor the estimate before you plan against it.** Set `min_cross_margin_fraction` to
   the smallest fraction of standalone margin you believe the broker could ever charge.
   `floor_applied` on the report tells you the floor, not the model, produced the answer.
6. **Compare CER against reality, then re-anchor.** Reconcile the estimate against the
   broker's actual requirement on a live book. Persistent optimism means the credit rates
   are wrong; correct the rates rather than raising the haircut to compensate.

> Full procedure: see `references/workflows.md`.
> Sourced methodology parameters: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Passing the same symbol twice.** Two sleeves' BTC rows margined as two legs get a
  *spread credit* against each other instead of netting to flat: a genuinely flat book
  reported $12,000 of requirement under the previous implementation. This now raises
  `MarginInputError` and points at `net_positions_by_symbol`.
- **Believing a correlation is a credit rate.** Exchanges publish offset percentages as
  fixed parameters — SPAN's inter-commodity credit table, OCC's product-group offsets —
  set from their own analysis and revised on their schedule, not from your EWMA window.
- **Assuming correlation holds in the tail.** A 0.95 correlation is a statement about the
  sample you measured. Margin engines apply their own conservatism precisely because the
  hedge that is supposed to fund the position is the one that fails in a dislocation.
- **Redeploying the freed collateral into more of the same risk.** The offset assumed the
  legs cancel; doubling the book on the strength of it removes the cancellation that
  justified the credit, and liquidation cascades from there.
- **Expecting a 5x or 10x capital saving.** Structurally impossible in this model: each
  spread consumes its credited amount from **both** legs, so the credit never exceeds half
  the isolated requirement and the ratio is bounded above by **2.0**.
- **Netting across exchanges.** A Binance long does not offset a Bybit short. Two accounts,
  two clearing organisations, two margin calls.
- **Crediting a negative correlation.** A long and a short in negatively correlated
  instruments are risk-*additive*. No credit is granted; the inverse case — two same-side
  legs that genuinely hedge — is also uncredited, so the estimate is low on such books.
- **Letting bad data through.** A correlation of 1.4 from a broken feed produced a
  requirement below the model's own floor and a CER of 3.33x. Out-of-range correlations,
  credit rates, haircuts, negative margins, NaN and infinity now raise `MarginInputError`.

## Verification

- Run the unit suite and confirm every test passes:
  `python -m unittest discover -s skills/capital-efficiency-across-cross-margined-strategies/scripts`
- Long $100k of A against short $100k of B, each carrying $10,000 standalone margin, at a
  0.90 correlation and a 0.80 haircut: the credit rate is 0.72, the credit is
  `min(10000, 10000) * 0.72 = $7,200`, cross margin is **$12,800** and CER is **1.5625**.
  A materially lower requirement than that is a bug, not a saving.
- Shuffle the position list and confirm the requirement is unchanged. Spreads form
  highest-credit-first, so the number must not depend on arrival order.
- Give one long two competing shorts and confirm the higher-credit spread forms first and
  consumes the long.
- Net a long and an equal short of the same symbol and confirm the netted position carries
  zero delta and zero margin — then confirm passing both rows unnetted raises.
- Pass a correlation of 1.4, a haircut of 1.5, a negative margin and a NaN delta, and
  confirm each raises `MarginInputError`. A number coming back is a fail-open bug.
- Set a 100% credit rate on a matched pair and confirm CER is exactly 2.0, never infinity.
- Reconcile against the broker's own margin figure on a live book before allocating
  against the freed capital.

## Related Skills

- `cross-margining-across-asset-classes`
- `options-margin-span-calculation-global`
- `multi-leg-strategy-margin-optimization`
- `margin-utilization-circuit-breaker`
- `broker-account-margin-call-handling`
- `broker-margin-interest-accrual-tracking`
- `correlation-aware-exposure-limits`
