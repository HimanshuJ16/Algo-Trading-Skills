---
name: multi-account-same-strategy-fan-out
description: Use when one quantitative strategy signal must be split across multiple
  client accounts or sub-accounts (fund management / prop trading) and the per-account
  quantities must sum exactly to the master order, with deterministic collision-free
  client order IDs and an auditable pro-rata allocation record.
domain: algorithmic-trading
subdomain: broker-integration
tags:
- broker-integration
- multi-account
- fan-out
- pro-rata
- largest-remainder-apportionment
- fund-management
brokers_frameworks:
- IBKR FA Allocation Groups
- CME Average Price System (Rule 553)
- Multi-Account Fan-Out Allocator
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when a single master signal — "Buy 1,000 shares of AAPL" — has to be turned into per-account order quantities across $N$ sub-accounts, and each account must genuinely require its own order: separate broker connections, separate venues, separate legal entities, or a broker with no bunched-order facility.

The core problem is not latency, it is **apportionment**. Sizing each account independently and rounding — `round(Q_master × w_i)` per account — does not sum back to the master quantity. Three equal-NAV accounts splitting 10 shares round to 3 each and never trade the tenth share; four equal accounts splitting 6 shares each round 1.5 up to 2 and dispatch 8 shares for a 6-share signal. This skill apportions by the **largest-remainder (Hamilton) method** so the per-account quantities sum to $Q_{\text{master}}$ exactly, assigns deterministic client order IDs that a retry can reproduce, and records the basis behind every quantity so the split is independently verifiable.

## When NOT to Use

- **As a substitute for average pricing.** N separate per-account orders receive N different fills, so one client is filled better than another by luck of the matching engine. Dispatching them concurrently narrows the *latency* skew; it cannot remove the *price* dispersion. The mechanism that does is the opposite pattern — place one bunched order, allocate the fills post-execution at a single average price (17 CFR 1.35(b)(5) for futures; CME Rule 553 Average Price System; IBKR FA allocation groups, where you send one order and the broker allocates it). If a bunched-order facility is available, prefer it and use this skill only for the accounts it cannot reach.
- **On the broker's own allocation surface.** With an IBKR Financial Advisor account you place a *single* order carrying `faGroup` and `faMethod` (`NetLiq`, `AvailableEquity`, `EqualQuantity`, `PctChange`) and IBKR performs the allocation server-side. Re-implementing the split client-side and sending N orders instead gives up the broker's average pricing and doubles the reconciliation surface.
- **To close positions sized by NAV.** NAV sizes an *entry*. On an exit, an account holding less than its NAV share gets over-sold into a short and the rest get under-sold. Allocate the unwind against each account's **held quantity** via `ALLOCATION_METHOD_WEIGHT`, not against NAV.
- **As a pre-trade risk control.** The allocator does not know buying power, margin, borrow availability, or restricted lists. It computes a fair split of an already-approved quantity; the limits belong upstream — see `multi-strategy-capital-allocation-limits` and `cross-account-aggregate-risk-view`.

## Prerequisites

- A registry of sub-account IDs with a current NAV snapshot per account, or an explicit per-account weight.
- An allocation basis decision: pro-rata by NAV (`PRO_RATA_NAV`) or explicit weight (`EXPLICIT_WEIGHT`).
- A `min_order_qty` policy — the quantity below which an account is *excluded* from the batch rather than given a token order.
- Multi-account broker API or per-sub-account credentials, plus a dispatch layer of your own: this skill produces order instructions, it does not submit them.
- For US futures: confirmation that the manager is an eligible account manager under 17 CFR 1.35(b)(5) if allocations are made post-execution.

## Workflow

1. **Register Sub-Accounts and Snapshot the Basis**:
   - Record account IDs, NAVs, and any explicit weights. Duplicate registration raises rather than overwriting — a silently replaced NAV is a wrong allocation nothing reports.
   - **Decision point — snapshot the NAVs once per batch, before apportioning.** NAVs that move mid-batch produce weights that do not sum to 1 and an allocation no auditor can reproduce.

2. **Choose the Allocation Basis**:
   - Entering a position: basis = NAV. Exiting one: basis = held quantity, via `EXPLICIT_WEIGHT`.
   - **Decision point — `EXPLICIT_WEIGHT` with a missing weight must raise, never fall back to NAV.** A silent fallback unwinds the wrong quantity from every account at once.

3. **Apportion by Largest Remainder**:
   - Exact entitlement $e_i = Q_{\text{master}} \times \dfrac{b_i}{\sum_j b_j}$; allocate $\lfloor e_i \rfloor$ to each account.
   - Distribute the $Q_{\text{master}} - \sum_i \lfloor e_i \rfloor$ stranded shares to the accounts with the largest fractional parts $e_i - \lfloor e_i \rfloor$, ties broken by `account_id` ascending.
   - **Never round per account.** Python's `round()` is round-half-to-even: `round(2.5) == 2` but `round(3.5) == 4`, so two accounts with near-identical entitlements receive different quantities and the total drifts off the master signal in either direction.

4. **Apply the Minimum-Quantity Floor by Exclusion, Not by Inflation**:
   - An account entitled to a non-zero quantity below `min_order_qty` is **dropped**, and its shares are re-apportioned across the survivors. Repeat until every remaining allocation clears the floor.
   - **Decision point — never use `max(min_order_qty, share)`.** Raising every account to the floor mints quantity the master signal never authorised: 50 accounts with a 1-share floor turn a 10-share signal into 50 shares, and on a SELL it opens a short in an account whose fair share was zero.
   - **Decision point — if every account falls below the floor, allocate nothing and say so.** `is_fully_allocated` is false and the shortfall was *not* traded; the caller must decide whether to re-batch, not discover the gap at reconciliation.

5. **Assign Deterministic Client Order IDs**:
   - `{prefix}_{batch_id}_{account_id}`, a pure function of the batch.
   - **Decision point — a retry after an ambiguous timeout must reuse the same `batch_id`.** Minting a fresh ID on retry is how a fan-out double-executes: the broker may already have accepted the first order and has no way to recognise the second as the same instruction. See `order-placement-idempotency`.

6. **Dispatch, Then Reconcile Against the Recorded Basis**:
   - Dispatch is the caller's job. Concurrency reduces latency skew across accounts; it does not equalize fills.
   - Retain `allocation_basis`, `allocation_weight`, `exact_quantity` and `received_remainder_share` per order. 17 CFR 1.35(b)(5)(iv)(C) requires a methodology objective enough to permit independent verification of the split, and (b)(5)(v) requires records reconstructing the order from placement through to allocation.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Per-account rounding that does not sum to the master quantity.** `round(Q × w_i)` per account under-executes (3 equal accounts × 10 shares → 9 dispatched) or over-executes (4 equal accounts × 6 shares → 8 dispatched) with no error raised. The shortfall is an un-traded position and the excess is unauthorised exposure; both surface days later in reconciliation, not at dispatch.
- **A minimum-quantity floor that inflates instead of excluding.** `max(min_order_qty, share)` guarantees every account a share regardless of entitlement. An account holding 0.01% of NAV receiving 1 share out of a 10-share signal has received 1,000× its pro-rata entitlement, taken from the other clients — the "consistently favorable treatment" 17 CFR 1.35(b)(5)(iv)(B) prohibits.
- **Believing concurrent dispatch equalizes fill prices.** It does not. Separate orders get separate fills; whoever's order reaches the book first in a moving market is advantaged. Only average pricing over a bunched order removes the dispersion.
- **Minting a fresh client order ID on retry.** A timeout is not a rejection — the broker may have accepted the order before the response was lost. A new ID on the retry makes the duplicate invisible to the broker's own de-duplication and fans a double-fill out across every client account at once.
- **A shared, unlocked sequence counter behind the IDs.** `self._seq += 1` is a read-modify-write. Under the concurrent dispatch this skill is used for, two threads can read the same value and emit the same "collision-free" ID, and a process restart resets the counter to zero.
- **Unwinding by NAV instead of by held quantity.** An account whose position drifted below its NAV weight is sold short by the difference, silently opening a position the strategy never asked for.
- **Remainder shares landing in the same accounts every batch.** Largest-remainder is fair within a batch, but with stable NAVs the tie-break sends the leftover share to the same accounts indefinitely. That is a fairness finding waiting to happen; track `received_remainder_share` and rotate if the distribution skews.
- **Negative quantities used to encode a sell.** A `-100` quantity fanned out pro-rata produces negative per-account quantities that a broker adapter may reinterpret arbitrarily. Direction belongs in `action`; the quantity must be a positive integer.

## Verification

- **Sum invariance (the case naive rounding fails):** register 3 accounts of equal NAV, fan out 10 shares, and verify quantities `{4, 3, 3}` summing to exactly 10 — not `{3, 3, 3}` summing to 9. Repeat with 4 equal accounts and 6 shares: expect `{2, 2, 1, 1}` summing to 6, not `{2, 2, 2, 2}` summing to 8.
- **Documented pro-rata case:** 1,000 shares across NAVs of \$500k / \$300k / \$200k must give exactly 500 / 300 / 200.
- **Floor by exclusion:** with accounts of \$999,900 and \$100 NAV and a 10-share master signal, the small account must receive **no order** (entitlement 0.001 shares) and the total allocated must be 10 — not 11 with a 1-share consolation order.
- **Floor with redistribution:** NAVs 50 / 49 / 1 with `min_order_qty=5` and 100 shares must drop the third account and re-apportion to `{51, 49}`, still summing to 100.
- **Idempotent IDs:** two calls with the same `batch_id` must emit byte-identical client order IDs; two calls without one must not collide.
- **Concurrency:** fan out from 32 threads simultaneously and verify every client order ID is unique and every batch sums to its master quantity.
- Run `python -m unittest discover -s skills/multi-account-same-strategy-fan-out/scripts` and confirm 100% pass rate.

## Related Skills

- `order-placement-idempotency`
- `minimum-fill-size-and-lot-rounding-logic`
- `cross-account-aggregate-risk-view`
- `multi-broker-consolidated-position-view`
- `multi-strategy-capital-allocation-limits`
- `broker-failover-secondary-account-routing`
- `best-execution-record-keeping-global`
