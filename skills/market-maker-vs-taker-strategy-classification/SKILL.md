---
name: market-maker-vs-taker-strategy-classification
description: >-
  Use when auditing a strategy's own fill log to see what share of its flow added rather
  than removed liquidity, and attributing the exchange fees and rebates each side
  earned. Not a determination of regulated market-maker status.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: market-microstructure-latency
  tags: market-microstructure, maker-vs-taker, strategy-classification, exchange-fees, maker-rebates, effective-fee-bps, fix-lastliquidityind, post-only
  brokers_frameworks: "FIX LastLiquidityInd (tag 851); Binance VIP Fee Tiers; Kraken Fee Schedule; MiFID II RTS 8 (EU 2017/578); Python Dataclasses"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when auditing what a running strategy *actually did* at the venue: what share of its executed flow added liquidity versus removed it, and what that posture cost or earned in exchange fees. Venues price the two sides asymmetrically — passive fills may be charged a lower fee or credited a rebate, aggressive fills are charged the taker rate — so the maker/taker split is the first-order driver of a high-turnover strategy's fee bill. The engine decomposes a fill log, computes the maker ratio on an explicit weighting basis, classifies the strategy as `PURE_MAKER_STRATEGY` / `PURE_TAKER_STRATEGY` / `HYBRID_MAKER_TAKER_STRATEGY`, and attributes fees and rebates to each side separately.

## When NOT to Use

- **To decide whether you are a regulated market maker or dealer.** This is a fee/execution diagnostic computed from fills. Under MiFID II the test is one of *quoting presence*, not fills: Directive 2014/65/EU Article 17(4) defines pursuing a market making strategy as posting firm, simultaneous two-way quotes of comparable size at competitive prices, and RTS 8 (Regulation (EU) 2017/578) fixes the presence obligation in terms of daily trading hours quoted, not trades executed. In the US, the SEC's expanded dealer rules (3a5-4, 3a44-2) were vacated on 21 November 2024 and the SEC dismissed its appeal on 20 February 2025 — no fill ratio is a registration trigger. Use `mifid-ii-algo-trading-compliance-eu` for the actual obligation.
- **To judge whether passive execution is working.** A fill log contains the passive orders that filled and cannot contain the ones that did not, so the maker ratio says nothing about fill rate, queue position, or adverse selection. Pair with `adverse-selection-measurement-for-passive-orders` and `queue-position-modeling-for-passive-orders`.
- **On per-contract fee schedules.** Venues that bill per contract by membership/product/venue rather than by liquidity flag (CME Group's futures schedules work this way) have no maker/taker rate to attribute, and an effective rate in bps of notional does not describe their cost.
- **Across venues in one run.** A per-share venue and a percentage-of-notional venue do not share a pricing unit; blending them into one effective bps figure produces a number that is arithmetically valid and economically meaningless.
- **As a forward-looking fee estimate.** This audits realized fills. For "what would the next tier cost", use `exchange-fee-tier-and-rebate-structure-analysis`.

## Prerequisites

- Executed fill log with, per fill: `trade_id`, `symbol`, `is_maker` (a real boolean), `executed_price` (> 0), `quantity` (> 0, absolute — encode side separately), `fee_paid_usd`, and optionally `liquidity_category`.
- **Sign convention**: `fee_paid_usd` positive means the venue *charged* you; negative means it *credited* you a rebate. Every USD figure and the effective bps rate follow the same convention, so a negative net is rebate capture. This matches `exchange-fee-tier-and-rebate-structure-analysis`.
- **Classification basis** — `ClassificationBasis.QUANTITY` or `ClassificationBasis.NOTIONAL`. Required, no default; see step 1.
- Fills from a single venue. The engine does not normalise pricing units across venues.

## Workflow

1. **Choose the classification basis before computing anything — it decides what the ratio means.**
   - `QUANTITY`: maker quantity / (maker + taker quantity). Correct where the fee is levied *per unit* — US equity venues quote maker rebates and taker fees in dollars per share, so share counts are what the bill is proportional to.
   - `NOTIONAL`: maker notional / (maker + taker notional). Correct where the fee is a *percentage of trade value* — Binance and Kraken both quote maker/taker rates as a percentage of trade value, tiered on 30-day rolling volume.
   - **Decision point:** the two bases can return different labels on the same log. A desk that posts small passive orders in a cheap name and crosses the spread in an expensive one is maker-heavy by share count and taker-heavy by value. Pick the basis that matches how the venue bills you; the report carries both ratios so you can see the disagreement.
   - **Decision point:** the engine *refuses* a multi-symbol log on the `QUANTITY` basis. One BTC and 100 shares of AAPL are not 101 of anything — either classify one symbol at a time or switch to `NOTIONAL`.

2. **Classify each fill's liquidity category before bucketing it — the flag is not binary.**
   - FIX `LastLiquidityInd` (tag 851) enumerates `1 = Added Liquidity`, `2 = Removed Liquidity`, `3 = Liquidity Routed Out`, and (FIX 5.0 SP2) `4 = Auction`. Only the first two are the maker and taker sides of the continuous book.
   - **Decision point:** if the log carries routed-out or auction fills, pass `liquidity_category` explicitly. They go into a separate excluded bucket, out of the ratio's numerator *and* denominator, while their fees stay in the net. Collapsing them into `is_maker=False` inflates the taker share — a closing-auction print of any size can flip a genuinely passive desk from `PURE_MAKER_STRATEGY` to `HYBRID_MAKER_TAKER_STRATEGY`.
   - **Decision point:** `is_maker` must be a real `bool`. Broker REST payloads routinely carry the flag as the string `"false"`, which is truthy in Python; the engine rejects a non-bool rather than booking every taker fill as a maker fill.
   - **Decision point:** deduplicate the log before submitting it. Overlapping paginated fetches are the normal way a fill arrives twice, and a double-counted fill corrupts every figure in the report with nothing in the output to show it. The engine rejects a repeated `trade_id`; if the venue reuses one id across partial fills, key on the per-fill execution id instead.

3. **Compute the maker ratio and classify against the thresholds.**
   - $R_{\text{maker}} = \dfrac{W_{\text{maker}}}{W_{\text{maker}} + W_{\text{taker}}}$ where $W$ is quantity or notional per the selected basis.
   - $R_{\text{maker}} \ge 0.80 \implies$ `PURE_MAKER_STRATEGY`; $R_{\text{maker}} \le 0.20 \implies$ `PURE_TAKER_STRATEGY`; strictly between $\implies$ `HYBRID_MAKER_TAKER_STRATEGY`. Both bounds are inclusive.
   - **These thresholds are a reporting convention, not a standard.** No regulator or exchange defines a maker-ratio cut-off. Override them to whatever your desk means; the engine rejects a swapped or equal pair, which would otherwise make the taker branch unreachable.
   - **Decision point:** comparison is against the full-precision ratio, never a rounded one. A ratio of 0.79996 rounds to 0.8000 at four decimal places, and classifying the rounded value silently promotes it to `PURE_MAKER_STRATEGY`. The report also flags any ratio within 0.005 of a threshold as a cut-off artefact — re-read those before acting on the label.
   - If no fill added or removed continuous-book liquidity, the result is `UNCLASSIFIED_NO_MAKER_TAKER_VOLUME` with a `None` ratio, not `0.0` (which would read as "entirely taker").

4. **Attribute fees and rebates per side, not just in aggregate.**
   - $\text{Fee}_{\text{effective\_bps}} = \dfrac{F_{\text{net}}}{N_{\text{gross}}} \times 10{,}000$, over *all* fills including excluded ones — they were still billed.
   - The report also carries `maker_fees_paid_usd` / `taker_fees_paid_usd` and each side's effective bps against its own notional.
   - **Decision point:** a positive `maker_fees_paid_usd` means the passive side was charged, not credited. A maker-dominant posture only pays for itself where the venue's maker rate is negative at your tier — on standard crypto tier tables it is not (Binance's published spot schedule charges a positive maker rate at every VIP tier). The report warns when this happens.

5. **Audit Report Generation**: output the structured `StrategyClassificationReport`, and read `warnings` before quoting any figure from it.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Counting auction and routed-out fills as taker volume.** They are billed under their own rate codes, not the continuous-book taker rate, and a single large closing-auction print can move the ratio far enough to change the label. Exclude them explicitly; do not let a boolean flag decide for you.
- **Trusting a truthy liquidity flag.** `is_maker="false"` parsed from JSON is `True` in Python. Every taker fill in the log then books as a maker fill, the ratio reads 1.00, and the fee attribution says the desk earned rebates it was actually charged.
- **Rounding the ratio before comparing it to the threshold.** Round-then-classify promotes 0.79996 to `PURE_MAKER_STRATEGY`. Classify on the exact value and round only for display.
- **Summing share counts across instruments.** A maker ratio built from 1 BTC plus 100 AAPL shares is dominated by whichever instrument happens to have the larger unit count. Use the notional basis for multi-symbol logs.
- **Reading a high maker ratio as regulatory market-making status, or as evidence the passive strategy is working.** It is neither — see *When NOT to Use*.
- **Assuming maker means rebate.** On most standard crypto fee tiers the maker rate is a positive fee. Netting a "rebate capture" figure that is actually a fee inverts the sign of the conclusion.
- **Submitting limit orders without a post-only flag in a maker algo.** An order priced through the touch executes aggressively and is billed at the taker rate; the strategy intended to be passive and pays for the privilege. See `post-only-and-maker-taker-fee-optimization`.
- **Reading the effective bps rate across venues.** Per-share and percentage-of-value schedules do not blend.

## Verification

- Instantiate `MarketMakerVsTakerClassifierEngine(ClassificationBasis.NOTIONAL)`. Audit 100 fills (90 maker at 100 units × $100 with $-2.00 each, 10 taker at the same size with $+8.00 each): verify $R_{\text{maker}} = 0.90$, `PURE_MAKER_STRATEGY`, `total_gross_notional_usd` $= \$1{,}000{,}000$, `net_fees_paid_usd` $= -\$100.00$, and `effective_fee_rate_bps` $= -1.0$ (a rebate), with `status` `STRATEGY_CLASSIFICATION_SUCCESS`.
- **Basis divergence**: one maker fill of 100 units at $10 and one taker fill of 100 units at $190 in the same symbol must classify `HYBRID_MAKER_TAKER_STRATEGY` on `QUANTITY` (ratio 0.50) and `PURE_TAKER_STRATEGY` on `NOTIONAL` (ratio 0.05).
- **Rounding regression**: 79,996 maker units against 20,004 taker units must stay `HYBRID_MAKER_TAKER_STRATEGY` — the exact ratio 0.79996 is below the 0.80 threshold even though it rounds to 0.8000.
- **Excluded liquidity**: 5 maker + 5 taker + 10 `AUCTION` fills of equal notional must give a ratio of 0.50 (not 0.25), report the auction notional and fees in the excluded fields, keep them in the net fee total, and emit a warning. A log of only `ROUTED_OUT` fills must return `UNCLASSIFIED_NO_MAKER_TAKER_VOLUME` with `None` ratios.
- **Negative checks**: an empty log, a raw `dict` in place of a fill, a duplicate `trade_id`, `is_maker` as a string or `int`, a non-positive or non-finite price or quantity, a non-finite fee, a `liquidity_category` contradicting `is_maker`, an unknown category or basis, swapped/equal/out-of-range thresholds, and a multi-symbol log on the `QUANTITY` basis must each raise `TradeLogError` (a `ValueError`). `ExecutedTradeLog` is frozen, so assigning to a validated field must raise `FrozenInstanceError`.
- Run `python -m unittest discover -s skills/market-maker-vs-taker-strategy-classification/scripts` and confirm 100% pass rate.

## Related Skills

- `exchange-fee-tier-and-rebate-structure-analysis`
- `post-only-and-maker-taker-fee-optimization`
- `adverse-selection-measurement-for-passive-orders`
- `queue-position-modeling-for-passive-orders`
- `order-to-trade-ratio-fee-penalty-avoidance`
- `post-trade-execution-quality-scorecard`
- `transaction-cost-analysis-tca-integration`
- `mifid-ii-algo-trading-compliance-eu`
