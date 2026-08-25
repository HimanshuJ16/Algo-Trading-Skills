# Workflows for Futures Expiry Week Handling

## 1. Snapshot assembly

Build one `FuturesOrderBookState` for the **expiring** contract. The engine cannot
infer any of these units and will not guess at them:

| Field | Unit / convention | Failure if supplied wrongly |
|---|---|---|
| `days_to_expiration` | **Business** days to Last Trading Day. `0` = final session, negative = no longer trading. | Calendar days halve the effective margin across a weekend. |
| `bid_ask_spread_ticks` | $(\text{ask} - \text{bid}) / \text{tick size}$ for this product. | Using the raw price difference makes a 2.0 "tick" threshold meaningless — ES quotes 0.25-point ticks. |
| `top_of_book_depth_qty` | Resting quantity at the top of the book, contracts. | — |
| `baseline_average_depth_qty` | The **same** measure, averaged over a normal-market window **for the same contract**. Must be > 0. | A bid+ask baseline against a near-side snapshot halves the ratio and haircuts permanently; the deferred contract's depth measures the roll, not the degradation. |
| `is_quadruple_witching_week` | Quarterly (Mar/Jun/Sep/Dec) third-Friday expiration week. | — |

`FuturesOrderBookState.validate()` runs before any comparison and raises on
non-finite, negative, crossed, or absent values. It is not defensive boilerplate:
`NaN` loses every `>` and `<` test in this engine, so an unvalidated snapshot
produces the *least* restrictive report from the data the engine understands least.

## 2. Expiration audit

Evaluated first, because it can void the rest of the decision.

| Condition | `status` | Engine behaviour |
|---|---|---|
| `days_to_expiration < 0` | `EXPIRED_ESCALATE` | `adjusted_max_order_qty = 0`, market orders and new entries blocked, **`is_mandatory_roll_required = False`**, `requires_manual_escalation = True`, logged at CRITICAL. |
| `days_to_expiration <= mandatory_roll_dbe_cutoff` | `MANDATORY_ROLL_REQUIRED` | New entries blocked, roll mandated, logged at WARNING. |
| Otherwise | continue to the microstructure audit | — |

`days_to_expiration == 0` means today is the final trading session: still tradable,
so it is a roll, not an escalation. Past Last Trading Day the engine deliberately
does **not** mandate a roll — the leg that would be lifted no longer trades, and
emitting a roll instruction there produces an order the venue rejects while hiding
a position that needs a human.

## 3. Microstructure audit

Run in **every** branch, including the mandatory-roll and escalation branches, so a
roll report still carries the state of the book the roll has to be executed into.

- **Spread**: `bid_ask_spread_ticks > max_spread_ticks_threshold` (strict) → market
  orders blocked, limit-only execution. Exactly at the threshold is not wide.
- **Depth**: `depth_ratio = top_of_book_depth_qty / baseline_average_depth_qty`;
  `depth_ratio < min_depth_ratio_threshold` (strict) → size haircut. The ratio is
  reported unclamped so an audit can reconstruct the decision.
- **Quad-witching week** → size haircut. This is a *policy override, not a
  measurement*: it fires on a tight, deep book too. `is_depth_thinned` is the field
  that says the book was actually thin.

A thin book does **not** block market orders. The spread is the immediate cost of
crossing; one depth level does not bound market-order cost, so gating the block on
depth would imply protection this engine cannot deliver. Thin books are answered
with size, and full-book impact belongs to `liquidity-adjusted-position-sizing`.

Every condition that fired is returned in `restriction_reasons`, ordered
`PAST_LAST_TRADING_DAY` / `EXPIRATION_CUTOFF`, `WIDE_SPREAD`,
`THIN_TOP_OF_BOOK_DEPTH`, `QUAD_WITCHING_WEEK`.

## 4. Size adjustment

$$\text{adjusted\_max\_order\_qty} = \lfloor \text{base\_order\_qty} \times \text{haircut factor} \rfloor$$

- Floored, never rounded: the cap can never exceed the risk budget.
- If the floor lands on `0` while the base quantity was positive,
  `is_order_size_suppressed` is set and the note says so. That means **do not send
  the order** — not "send quantity 0", which most venues reject and some
  interpret as a different instruction.
- `adjusted_max_order_qty` is a **cap on an order the caller is otherwise permitted
  to send**. When `is_new_entry_allowed` is `False` the only permitted orders are
  risk-reducing (close or roll); the cap does not re-authorise an entry.

## 5. Execution and audit logging

- Persist the whole `FuturesExpiryRiskReport`, not just `audit_notes` — the report
  carries the snapshot state, the unclamped depth ratio, and every reason code,
  which is what a later reconstruction needs.
- Alert on `EXPIRED_ESCALATE` (logged at CRITICAL) and on
  `is_order_size_suppressed`; neither has an automated remedy.
- Wire the report into the order path. An advisory report that nothing reads blocks
  nothing — the engine enforces no constraint by itself.
- Hand the roll itself to `futures-contract-roll-automation`; this engine states
  that a roll is due, not how to construct or route it.
