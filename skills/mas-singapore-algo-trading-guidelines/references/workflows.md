# Workflows for Singapore SGX Pre-Trade Compliance

Every step runs on every order. Nothing short-circuits: an order can breach
several requirements at once, and remediation needs the full list. The headline
`status` reports the most serious breach; `breaches` carries them all.

## 0. Reject structurally invalid input

Raise before auditing — these are caller bugs, not compliance outcomes, and must
never be reported as a clean audit.

- Non-finite or non-positive price, reference price, bid size or opposite best price.
  `float('nan') > limit` is `False`, so a NaN price would otherwise pass every ceiling.
- Non-positive quantity. A negative quantity produces a negative order value that
  passes every value ceiling.
- Unknown `side`, blank `symbol`, blank `session`, negative rate counter.
- `order.currency` different from `config.limit_currency`. Convert first;
  comparing across currencies understates risk.

## 1. Entity and Approved Trader governance

- Capital Markets Services licence or documented exemption under the SFA
  $\implies$ else `REJECTED_UNLICENSED_ENTITY`.
- Current SGX Approved Trader / Registered Representative registration, with a
  non-blank identifier (SGX FTR 2.13.2, 2.13.4)
  $\implies$ else `REJECTED_UNREGISTERED_APPROVED_TRADER`.
- `order.algo_id` matches `config.algo_id`. A mismatch means the order is being
  audited against another algorithm's limits
  $\implies$ else `REJECTED_ALGO_ID_MISMATCH`.

There is deliberately **no** check for a MAS algorithm registration number. MAS
issues none.

## 2. Automated trading controls

Per SGX RegCo's Algorithmic Trading Regulatory Guide, as formalised into the SGX
rulebooks:

- Pre-deployment testing signed off $\implies$ else `REJECTED_ALGO_NOT_TESTED`.
- Kill switch armed and reachable $\implies$ else `REJECTED_NO_KILL_SWITCH`.

## 3. Pre-execution value limit (SGX FTR 3.9.1(3))

- Value a limit order at its limit price; value a market order at the opposite
  best price, recording a warning that the executed value can exceed this if the
  order walks the book.
- An order priceable by neither **fails closed** $\implies$ `REJECTED_UNPRICEABLE_ORDER`.
- Over the firm's ceiling $\implies$ `REJECTED_PRE_EXECUTION_LIMIT`.

The ceiling is a **firm and Clearing Member** figure. SGX publishes none.

## 4. Circuit breaker band (SGX-ST Rule 8.14)

Scope gates first — skip the check entirely, reporting `None`, if either fails:

- `session` is not a continuous-trading phase. The mechanism does not run during
  the opening and closing routines.
- The instrument is not circuit-breaker eligible today. Eligibility is assessed
  daily against a start-of-Market-Day reference price of at least 0.50 in the
  underlying currency (JPY 500 for yen-denominated instruments). Unknown
  eligibility resolves **conservatively** to eligible, with a warning.

Then:

1. **Establish marketability.** A market order always matches. A limit order
   matches only if it crosses `opposite_best_price`. An unknown book resolves to
   "may be marketable", with a warning — a missing field must never make a
   breaching order look safe.
2. **Pick the price under test** — the worst *knowable* potential trade price.
   For a limit order that is the limit price; for a market order it is the
   opposite best price, with a warning that the order can still walk past the
   band deeper in the book.
3. **Compare unrounded** against $\pm$`circuit_breaker_band_pct` of the reference
   price — the last traded price **at least five minutes earlier**. The band is
   inclusive, so a breach requires the price to be strictly outside it.
4. **Resolve:**
   - Marketable and outside the band $\implies$ `REJECTED_CIRCUIT_BREAKER_BAND`.
   - Non-marketable and outside the band $\implies$ **not** rejected. The order
     rests. Record a warning: it is a latent Cooling-Off trigger for whoever
     aggresses it later.

For SGX-DT, override `circuit_breaker_band_pct` with the contract's own price
limit (FTR 4.1.15 and the contract specifications). The 10% default is the
SGX-ST securities figure only.

## 5. Forced Order Range (SGX-ST Practice Note 8.6)

- Skip with a warning if `min_bid_size` or `forced_order_range_ref_price` is
  absent; report `None`, never `0.0`.
- Distance in bids $= |P_{\text{limit}} - P_{\text{FOR ref}}| / \text{min bid size}$.
- Within the range (inclusive) $\implies$ pass.
- Outside without a Force Key confirmation $\implies$ `REJECTED_FORCED_ORDER_RANGE`.
- Outside **with** a Force Key confirmation $\implies$ the order proceeds; record
  the deliberate override as a warning. Regulatory Notice 11.4.2(g) permits the
  order once confirmed — the Force Key is a fat-finger control, not a prohibition.

## 6. Message rate ceiling

`current_order_rate_per_sec` counts messages already sent in the current
one-second window, including this one. The caller owns the counter; the engine is
stateless and cannot enforce a rate on its own
$\implies$ over the firm ceiling gives `REJECTED_ORDER_RATE_LIMIT`.

## 7. Audit report generation

Emit `SgxPreTradeComplianceReport` with the complete `breaches` and `warnings`
tuples, the most serious breach as `status`, and `None` — never `0.0` — for every
check that did not run.
