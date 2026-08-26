# Deep Workflow Reference — multi-account-same-strategy-fan-out

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

### 1. Sub-Account Ingestion and Basis Snapshot

- Register each sub-account with its ID and current NAV; optionally attach an explicit
  `allocation_weight`.
- Duplicate registration raises. Refresh a NAV with `update_account_nav()`, suspend or
  resume an account with `set_account_active()`.
- A suspended account leaves the denominator entirely, so the master quantity is
  redistributed across the survivors rather than the batch under-filling.
- **Snapshot the basis once per batch.** The engine takes the snapshot under a lock at
  the start of `calculate_fanout_orders`, so a registration landing mid-batch cannot
  produce a half-updated allocation. If NAVs are sourced live from the broker, fetch
  them *before* the call, not during it.

### 2. Choosing the Allocation Basis

| Situation | Basis | Method |
|---|---|---|
| Entering a new position | NAV per account | `PRO_RATA_NAV` |
| Scaling an existing position pro-rata to capital | NAV per account | `PRO_RATA_NAV` |
| Closing or reducing a position | **held quantity** per account | `EXPLICIT_WEIGHT` |
| Honouring a client mandate weight that differs from raw NAV | mandate weight | `EXPLICIT_WEIGHT` |

`EXPLICIT_WEIGHT` raises if any active account lacks a weight. A silent fallback to
NAV would unwind the wrong quantity from every account in the batch simultaneously.

### 3. Largest-Remainder (Hamilton) Apportionment

For master quantity $Q$ and bases $b_i$:

1. Exact entitlement: $e_i = Q \times \dfrac{b_i}{\sum_j b_j}$
2. Initial allocation: $Q_i = \lfloor e_i \rfloor$
3. Stranded shares: $r = Q - \sum_i \lfloor e_i \rfloor$, always in $[0, n)$ for $n$ accounts
4. Rank accounts by fractional part $e_i - \lfloor e_i \rfloor$ descending, ties broken
   by `account_id` ascending; the top $r$ each receive one extra share.

The result satisfies $\sum_i Q_i = Q$ exactly. Independent per-account rounding does
not — see the comparison table in `references/standards.md`.

Steps 1-3 are performed in **exact rational arithmetic** (`fractions.Fraction`)
over the true values of the float bases, not in floating point. Flooring in
floating point misfloors entitlements that are mathematically integers — bases
1 / 20 / 29 over 100 shares entitle the third account to exactly 58, but
`100 * (29.0 / 50.0)` evaluates to `57.99999999999999` and floors to 57. The
remainder pass usually hands that share straight back, but not reliably once
several accounts are affected, and the recovered share is then misreported as a
remainder award. `n` is the number of sub-accounts, so the cost is irrelevant.

### 4. Minimum-Quantity Floor by Exclusion

```
eligible = all active accounts
loop:
    allocate eligible by largest remainder
    below = accounts allocated a non-zero quantity < min_order_qty
    if below is empty: done
    drop `below` from eligible and re-apportion
    if eligible is empty: allocate nothing
```

The loop terminates because `eligible` strictly shrinks each pass. Dropping an account
frees its shares to the survivors, which can lift a previously-borderline account above
the floor — hence the loop rather than a single pass.

Two outcomes must be surfaced, never swallowed:

- Accounts entitled to **zero** are reported in `excluded_accounts` with reason
  `ZERO_ENTITLEMENT`. Their entitlement was genuinely below one share.
- Accounts dropped by the floor are reported with reason `BELOW_MIN_ORDER_QTY`.
- If every account falls below the floor, `account_orders` is empty,
  `is_fully_allocated` is false, and a warning is logged. **The shortfall was not
  traded.** The caller decides whether to re-batch at a larger size.

The floor is never applied as `max(min_order_qty, share)`. That form mints quantity the
master signal did not authorise — 50 accounts with a 1-share floor turn a 10-share
signal into 50 shares — and on a SELL it opens a short in an account whose fair share
of the exit was zero.

### 5. Deterministic Client Order ID Generation

Format: `{prefix}_{batch_id}_{account_id}` (default prefix `CLORD`).

- Supplying `batch_id` makes the whole batch reproducible: a replay after an ambiguous
  timeout emits byte-identical IDs, so the broker's own de-duplication can reject the
  duplicate instead of the fan-out double-filling across every client account.
- Omitting it generates `{epoch_ms}-{8 hex}`. The random component matters: a bare
  millisecond timestamp plus an in-process counter collides after a restart, because
  the counter resets to zero.
- `batch_id` may not contain `_`, which keeps the three fields unambiguously
  separable when parsing an ID back apart.
- Uniqueness within a batch follows from account IDs being unique; uniqueness across
  batches follows from `batch_id`.
- Venue `ClOrdID` length limits vary and are **not** enforced here. Verify against
  your broker before choosing a prefix and account-ID scheme.

### 6. Dispatch and Reconciliation

Dispatch is deliberately outside this module. When implementing it:

- Concurrency reduces the *latency* skew between the first and last account's order.
  It does not equalize fill prices — see **When NOT to Use** in `SKILL.md`.
- Classify rejections before retrying, and retry with the original `batch_id`. A
  timeout is not a rejection.
- Retain the per-order audit fields (`allocation_basis`, `allocation_weight`,
  `exact_quantity`, `received_remainder_share`) alongside the fills. 17 CFR
  1.35(b)(5)(v) requires records reconstructing the order from placement through to
  per-account allocation, and (b)(5)(iv)(C) requires the method be objective enough to
  permit independent verification.
- Track `received_remainder_share` over time. Largest-remainder is fair within a batch,
  but with stable NAVs the same accounts keep winning the leftover share — a
  1.35(b)(5)(iv)(B) exposure the report is instrumented to expose.

## Worked Examples

| Accounts (basis) | Master qty | Result | Note |
|---|---|---|---|
| 500k / 300k / 200k | 1,000 | 500 / 300 / 200 | Exact division, no remainder |
| 100k / 100k / 100k | 10 | 4 / 3 / 3 | 1 stranded share to the lowest ID |
| 7 × 100k | 10 | 2,2,2,1,1,1,1 | 3 stranded shares |
| 999,900 / 100 | 10 | 10 / — | Small account entitled to 0.001 shares, excluded |
| 50 / 49 / 1, floor 5 | 100 | 51 / 49 / — | Third dropped by floor, shares redistributed |
| 2 × 100, floor 100 | 10 | — | All below floor; nothing allocated, flagged |

## Production Implementation Reference

- Reference code: `scripts/fanout_engine.py`
  (`MultiAccountStrategyFanOut`, `apportion_largest_remainder`, `AccountOrder`,
  `ExcludedAccount`, `FanOutReport`).
- Automated unit tests: `scripts/test_fanout_engine.py`.
