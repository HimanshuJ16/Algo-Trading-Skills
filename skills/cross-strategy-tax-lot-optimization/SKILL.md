---
name: cross-strategy-tax-lot-optimization
description: >-
  Use when several strategies trade the same securities under one US tax entity and
  uncoordinated sells default to the earliest lot; applies specific-lot identification
  with long-term-gain preference and internal netting.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: portfolio-multi-strategy
  tags: tax-lot, hifo, tax-loss-harvesting, wash-sale, internal-netting, capital-gains, cross-strategy
  brokers_frameworks: "IRS Form 8949; Python Dataclasses"
  version: "1.1.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill in multi-strategy funds, wealth-management engines, and unified tax-managed accounts where several sub-strategies trade the same asset universe **under a single US tax entity**. A sell order placed without a specific-lot identification is charged against the earliest lot acquired — FIFO — by default under Treas. Reg. § 1.1012-1(c)(1)(i), which often realizes short-term gains that a higher-basis lot would have avoided.

This module:

- Ranks open tax lots by **HIFO** (highest cost basis first) or **LTCG-preferring** order, implemented as Specific Lot Identification.
- Nets offsetting sub-strategy orders internally so only the residual is routed externally and only the residual is treated as a disposition.
- Screens the resulting loss lots against the **IRC § 1091 61-day window** (30 days before the sale, the sale date, 30 days after) and reports a quantity-limited disallowance.

## When NOT to Use

- **§ 475(f) mark-to-market electors.** For securities in an elected trading business, gains and losses are ordinary, and the wash-sale rules and capital-loss limitations do not apply ([IRS Topic 429](https://www.irs.gov/taxtopics/tc429)). Applying this skill's wash-sale screen there produces a disallowance that does not exist. See `mark-to-market-election-for-active-traders-us`.
- **Non-US tax entities.** Every rule implemented here is US federal. Other jurisdictions use different matching rules (for example a same-day/30-day share-pooling regime rather than HIFO), and state treatment may differ from federal.
- **Pods that are separate tax entities.** This module assumes one taxpayer. If pods file separately, an internal cross is a real transaction between two taxpayers and one pod's buy does not create a wash sale for another.
- **Cent-exact tax filing.** Monetary values are `float`. Use this for lot *selection*, then re-derive reportable figures in a decimal ledger — see `automated-tax-lot-reporting-pipeline`.

## Prerequisites

- Open tax-lot inventory carrying `lot_id`, `strategy_id`, `symbol`, `acquisition_date` (YYYY-MM-DD), `days_held`, `cost_basis_per_share`, and `quantity`.
- Current market price for the target security.
- **Operational prerequisite for anything other than FIFO:** an adequate identification must reach the broker no later than the earlier of the settlement date or the settlement time required by Exchange Act Rule 15c6-1 — T+1 for most US equities since 28 May 2024 — or a standing instruction on the account (Treas. Reg. § 1.1012-1(c)(8)). Without it the IRS treats the sale as FIFO regardless of what this module selected.
- Known replacement purchases across **all** sub-strategies for the traded symbol, with signed day offsets relative to the loss sale.

## Workflow

1. **Net cross-strategy orders first.** Call `net_cross_strategy_orders(orders)` for the symbol. The internally crossed quantity never leaves the tax entity, so it realizes nothing; only `net_quantity` is a disposition. Running lot selection on the gross sell quantity double-counts realized losses and overstates harvested capital losses.
2. **Select the method deliberately.** `HIFO_MIN_TAX` maximizes near-term loss realization; `LTCG_OPTIMIZED` takes long-term lots first to pay the preferential rate, then the highest basis within each holding-period bucket; `FIFO` is the statutory default. An unrecognized method string raises — it is never silently downgraded to FIFO.
3. **Register replacement purchases with signed offsets.** `register_replacement_purchase(symbol, strategy_id, days_from_sale, quantity)` — negative for buys before the loss sale, positive for buys after, `0` for same-day. The post-sale side matters most: a pod that buys back the day after another pod harvests a loss triggers § 1091 just as surely as a pre-sale buy.
4. **Run `optimize_sell_order`.** Pass `sale_date` whenever you have it: without it the long-term test falls back to `days_held > 365`, which misclassifies any lot whose holding period spans a leap day. Use `dry_run=True` to score several methods against the same inventory before committing.
5. **Escalate the disallowance.** `total_disallowed_loss_usd` is this sale's § 1091 disallowance only. The offsetting basis increase on the replacement shares (§ 1091(d)) and holding-period tacking (§ 1223(3)) are **not** applied here — hand the executions to `wash-sale-rule-tracking-us` to close the loop.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Selecting HIFO without identifying the lot to the broker.** The selection is only respected if adequate identification is delivered by the earlier of settlement date or the Rule 15c6-1 settlement time (T+1). A HIFO decision made internally on T+2 is legally FIFO, and the realized gain on the 1099-B will not match the internal ledger.
- **Scanning the wash-sale window backwards only.** IRC § 1091 covers 30 days *after* the loss sale as well as before. Harvesting a loss in Pod A on Monday and letting Pod B rebalance into the same name on Tuesday disallows the loss.
- **Assuming internal netting launders the wash sale.** Crossing Pod B's buy against Pod A's sell removes the market impact, not the tax event. Where the pods are separate taxpayers the buy is still a replacement purchase.
- **Taxing the gross rather than the net.** Pod A sells 1,000 and Pod B buys 600 inside one entity; only 400 shares are disposed of. Feeding 1,000 into lot selection invents 600 shares of realized loss that no return can claim.
- **Treating the disallowance as all-or-nothing.** § 1091 disallows the loss only on shares actually replaced. Replacing 40 of 100 loss shares disallows 40%; the rest stays deductible.
- **Using `days_held > 365` as the long-term test.** The statutory test is "more than one year". A lot bought 1 Jan 2024 and sold 1 Jan 2025 is 366 calendar days old but is still short-term.
- **Flagging profitable sales as wash sales.** A wash sale requires a realized loss. Warning on a gain lot inside the window trains operators to ignore the alert.

## Verification

- Instantiate `CrossStrategyTaxLotOptimizer`. Add three AAPL lots: A (\$150, acquired 2025-01-01, 400 days), B (\$200, 2025-10-01, 100 days), C (\$100, 2025-12-01, 50 days). At \$180, `HIFO_MIN_TAX` for 100 shares must select Lot B and realize a \$2,000 short-term loss with no wash sale flagged.
- Register a replacement buy at `days_from_sale=25, quantity=40` and re-run: exactly \$800 of the \$2,000 loss must be disallowed and \$1,200 must remain deductible.
- Net `SELL 1000 / BUY 600` and confirm `net_quantity == 400` and `internally_crossed_quantity == 600`.

```bash
python -m unittest discover -s skills/cross-strategy-tax-lot-optimization/scripts
```

## Related Skills

- `wash-sale-rule-tracking-us`
- `fifo-vs-specific-lot-tax-accounting-methods`
- `mark-to-market-election-for-active-traders-us`
- `automated-tax-lot-reporting-pipeline`
