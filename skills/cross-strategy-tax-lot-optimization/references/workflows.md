# Workflows for Cross-Strategy Tax Lot Optimization

Order matters: net first, select lots second, screen for wash sales third. Selecting
lots before netting taxes shares that never left the entity.

## 1. Ingest the tax-lot inventory

Register every open lot across all sub-strategies with `add_tax_lot`. Registration
validates `quantity > 0`, `cost_basis_per_share >= 0`, `days_held >= 0`, and an
ISO `YYYY-MM-DD` `acquisition_date`. A malformed date fails here rather than
silently corrupting FIFO ordering or the holding-period test downstream.

## 2. Net cross-strategy orders

```
netted = optimizer.net_cross_strategy_orders([
    StrategyOrder("PodA", "AAPL", "SELL", 1000),
    StrategyOrder("PodB", "AAPL", "BUY",   600),
])
# crossed = 600, net_side = "SELL", net_quantity = 400
```

Route only `net_quantity` externally, and pass only `net_quantity` to
`optimize_sell_order`. The 600 crossed shares are a book transfer inside one tax
entity and realize nothing.

`wash_sale_risk` is set whenever any quantity crossed. It is a prompt to confirm
the entity assumption, not a finding: if the pods file separately the cross is a
real sale between taxpayers *and* the buying pod's acquisition is a § 1091
replacement purchase.

## 3. Select the method

| Method | Ordering | Use when |
| :--- | :--- | :--- |
| `HIFO_MIN_TAX` | Highest cost basis first | Harvesting losses or minimizing realized gain this period. |
| `LTCG_OPTIMIZED` | Long-term lots first, then highest basis within each bucket | Realizing a gain and preferring the long-term rate over a marginally smaller short-term gain. |
| `FIFO` | Earliest `acquisition_date` first | Matching the statutory default, or reconciling against a broker that received no identification. |

Unrecognized method strings raise `ValueError`. There is no silent fallback: a
typo must not quietly change the tax treatment of a live sale.

## 4. Run lot selection

```
result = optimizer.optimize_sell_order(
    symbol="AAPL",
    sell_quantity=netted.net_quantity,
    current_market_price=180.0,
    method="HIFO_MIN_TAX",
    sale_date="2026-03-15",   # enables the calendar-accurate long-term test
)
```

- `sell_quantity` must be positive and must not exceed open inventory; an
  over-sized request raises rather than under-filling silently.
- Supply `sale_date` whenever it is known. Without it the long-term test degrades
  to `days_held > 365`, which classifies a lot bought 1 Jan 2024 and sold 1 Jan
  2025 (366 calendar days, exactly one year) as long-term when it is short-term.
- `dry_run=True` scores a method without depleting inventory, so several methods
  can be compared before one is committed.

## 5. Screen for wash sales

Register every in-window buy of the symbol across **all** sub-strategies:

```
optimizer.register_replacement_purchase("AAPL", "PodB", days_from_sale=+2, quantity=40)
```

`days_from_sale` is signed relative to the loss sale — negative before, positive
after, `0` same day — so the full 61-day window is expressible. The legacy
`register_recent_buy(symbol, strategy_id, days_ago)` maps to a negative offset and
covers the pre-sale side only.

Disallowance is quantity-limited under § 1091(b): the replacement pool is consumed
across loss lots in selection order, so 150 replacement shares against two
100-share loss lots fully match the first and half-match the second. Omitting
`quantity` marks the replacement size unknown; the module then assumes full
coverage — the conservative direction — and says so in `result.warnings`.

**Per-sale scope.** The replacement pool is re-evaluated on every call and is not
consumed across calls. Two successive loss sales will each match the same
registered replacement shares, jointly overstating the disallowance. That is
deliberate — this is a pre-trade screen, not a ledger. Where several loss sales
compete for one replacement block, run the executions through
`wash-sale-rule-tracking-us`, which consumes replacement shares once.

## 6. Hand off the disallowance

`total_disallowed_loss_usd` and `net_deductible_gain_loss_usd` describe this sale
only. This module does not apply the § 1091(d) basis increase to the replacement
lots or tack holding periods under § 1223(3). Pass the executions to
`wash-sale-rule-tracking-us` for the authoritative ledger and Form 1099-B Box 1g
figures, and re-derive filing amounts in a decimal ledger — the values here are
`float`.
