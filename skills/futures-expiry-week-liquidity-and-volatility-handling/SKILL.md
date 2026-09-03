---
name: futures-expiry-week-liquidity-and-volatility-handling
description: >-
  Use when still trading a futures contract inside its final weeks, where liquidity
  leaves before expiry and quad-witching adds volatility; applies position-size haircuts
  and raises a roll mandate. The roll itself is futures-contract-roll-automation.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: multi-asset-derivatives
  tags: futures-expiry, liquidity-fragmentation, quad-witching, order-book-depth, bid-ask-spread, position-haircut, microstructure-risk
  brokers_frameworks: "CME Group; ICE Futures; Python Dataclasses"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill in algorithmic execution engines, market-making algorithms, and futures risk managers that still hold or trade a contract inside its final weeks. Liquidity leaves an expiring futures contract *before* it expires: CME designates the Equity Index roll date as **the Monday preceding the third Friday** of the expiration month, and from that date the second-nearest expiration is identified as the lead month. For practically the whole of expiry week the contract the strategy is still in is no longer where the depth is — the resting size thins and the quoted spread widens while the position is open.

The engine reads one microstructure snapshot of the expiring contract (`symbol`, `days_to_expiration`, `bid_ask_spread_ticks`, `top_of_book_depth_qty`, `baseline_average_depth_qty`, `is_quadruple_witching_week`) and returns the execution constraints that follow from it: market orders blocked on a wide spread, a size haircut on a thinned book or a quarterly witching week, entries blocked and a roll mandated at the expiration cutoff, and escalation once the contract has stopped trading.

## When NOT to Use

- **As the roll mechanism.** This engine says *that* a roll is due; it does not evaluate roll triggers, build the calendar spread, or route it. See `futures-contract-roll-automation`.
- **As a market-impact model.** `top_of_book_depth_qty` is a single level. A halved order can still sweep several levels of a drained book, and the haircut will not tell you so. Size against impact with `liquidity-adjusted-position-sizing` and validate the fill assumption with `execution-realistic-simulation`.
- **On a contract that has already stopped trading.** Past Last Trading Day the engine returns `EXPIRED_ESCALATE` and refuses to describe any executable size. There is no automated remedy — the leg cannot be lifted and the position is heading to settlement.
- **As a general volatility overlay.** The trigger here is the calendar, not the tape. For a volatility-shock overlay that applies away from expiry, see `adaptive-execution-under-volatility-spikes`.
- **For options pin risk.** Short options near the strike on expiration Friday are a different exposure with a different deadline; see `options-pin-risk-management-at-expiry`.
- **Without calibrating the defaults.** No regulator or exchange mandates a spread ceiling, a depth haircut, or a roll cutoff. Every threshold in this engine is a library default — see `references/standards.md`.

## Prerequisites

- A microstructure snapshot of the **expiring** contract, with units the engine cannot infer and will not guess:
  - `days_to_expiration` in **business** days to Last Trading Day. `0` = today is the final session; negative = the contract has stopped trading.
  - `bid_ask_spread_ticks` = $(\text{ask} - \text{bid}) / \text{tick size}$ for *this* product. A tick is product-specific — E-mini S&P 500 futures trade in 0.25 index-point ticks, CME Single Stock futures in 0.01 points ($\$1.00$ per tick) — so a threshold in ticks is a different currency amount on every contract.
  - `top_of_book_depth_qty` and `baseline_average_depth_qty` measured with the **same** depth convention (both near-side resting size, or both bid+ask). Mixing conventions silently rescales the ratio.
- A normal-market depth baseline for the same contract, strictly positive. There is no fallback: an absent baseline raises rather than being clamped.
- A separately owned roll path and a human escalation route — the engine reports, it does not act.

## Workflow

1. **Validate before comparing.** Every threshold in this engine is a `>` or `<` test, and `NaN` loses all of them. An unvalidated engine given a `NaN` spread answers "market orders are fine", and given a zero baseline answers "the book is deep" — the *least* restrictive report from the data it understands least. `FuturesOrderBookState.validate()` raises on non-finite, negative, crossed, or absent inputs.
2. **Expiration audit — runs first, because it can void the rest.**
   - `days_to_expiration < 0` $\implies$ `EXPIRED_ESCALATE`: quantity capped at $0$, market orders and entries blocked, **no roll mandated**, logged at CRITICAL. *Decision point:* do not report a roll as the remedy here — telling an agent to roll a leg that no longer trades produces an order the venue will reject and hides a position that needs a human.
   - `days_to_expiration <= mandatory_roll_dbe_cutoff` (**inclusive**) $\implies$ `MANDATORY_ROLL_REQUIRED`: entries blocked, roll mandated, contract still tradable. `DBE = 0` is the final session, so it is a roll, not an escalation.
   - *Decision point:* the default cutoff of 2 business days is later than the market's own roll. CME's designated Equity Index roll date sits roughly four business days before expiration, so a cutoff of 2 fires after liquidity has already migrated. Calibrate per product rather than inheriting the default.
3. **Microstructure audit — evaluated in every branch, not only the restricted one.**
   - Spread: `bid_ask_spread_ticks > max_spread_ticks_threshold` (**strict**; exactly at the threshold is not wide) $\implies$ market orders blocked, limit-only execution.
   - Depth: $\text{ratio} = \text{top\_of\_book\_depth\_qty} / \text{baseline\_average\_depth\_qty}$; `ratio < min_depth_ratio_threshold` (**strict**) $\implies$ size haircut.
   - Quad-witching week $\implies$ size haircut. *Decision point:* this flag is a **policy override, not a measurement**. It halves size on a tight, deep book too. Set it deliberately, and do not read a haircut caused by it as evidence that the book was thin — `is_depth_thinned` is the field that says that.
   - *Decision point:* a thin book does **not** block market orders. The spread is the immediate cost of crossing; one depth level is not a bound on market-order cost, so overloading the block with a depth signal would imply protection the engine cannot give. Thin books are answered with size.
4. **Execution size adjustment**: $\text{AdjustedQty} = \lfloor \text{BaseOrderQty} \times \text{HaircutFactor} \rfloor$ — floored, never rounded, so the cap can never exceed the budget. *Decision point:* when the floor lands on $0$, `is_order_size_suppressed` is set. That means **do not send the order**, not "send quantity 0" — most venues reject a zero-quantity order and some reuse the quantity field.
5. **Read the whole report, not the status.** `restriction_reasons` carries every condition that fired, `is_spread_wide` / `is_depth_thinned` / `depth_ratio` carry the book state, and `adjusted_max_order_qty` is a **cap on a permitted order** — when `is_new_entry_allowed` is `False` the only permitted orders are risk-reducing, and the cap does not re-authorise an entry.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **A safeguard that fails open on missing data**: `float('nan') > 2.0` is `False`, so a `NaN` spread reads as "not wide" and *permits* market orders; a `NaN` or zero-clamped depth baseline reads as "deep" and *cancels* the haircut. The controls switch themselves off exactly when the feed breaks. Validate every field before the first comparison.
- **Using market orders in a thinning expiry book**: sending market orders once the spread has widened, and paying the widened spread on every child order.
- **Reading `adjusted_max_order_qty` as permission to trade**: it is a size cap on an order the caller is *otherwise* permitted to send. At `MANDATORY_ROLL_REQUIRED` and `EXPIRED_ESCALATE` new entries are blocked, whatever the cap says.
- **Treating quarterly witching as one uniform event**: on the third Friday the expiring E-mini S&P 500 future stops trading at **9:30 a.m. ET** and settles to the S&P 500 Special Opening Quotation, while CME Single Stock futures trade until **3:00 p.m. CT** and settle to the cash close. A "close out before the bell" rule protects the single stock future and misses the index future by an entire session.
- **Assuming "quad witching" still means the same four products**: US single stock futures ceased trading when OneChicago closed in September 2020, and the day was more accurately called triple witching until CME relisted single stock futures on 27 July 2026. Treat the flag as "a quarterly third-Friday expiration week", not as a count of instrument classes.
- **Rolling on the engine's default cutoff**: 2 business days is *this library's* default and sits after CME's own designated roll date. Nothing about it is mandated, and inheriting it means rolling into the liquidity that has already left.
- **Failing to enforce entry bans near expiry**: opening a new position in a contract that is inside the roll cutoff, then discovering the exit has to be executed in the thinnest book of the cycle.
- **Comparing depth against a baseline measured differently**: a top-of-book baseline against a bid+ask snapshot halves the ratio and triggers a permanent haircut; the reverse suppresses it permanently.
- **Sizing on top-of-book depth alone**: the haircut is a discipline, not an impact model. A halved order can still clear several levels of a drained book.

## Verification

- Instantiate `FuturesExpiryRiskHandlerEngine()` (defaults: spread 2.0 ticks, depth ratio 0.30, DBE cutoff 2, haircut 0.50). Input a liquid contract (`DBE=15`, spread 1.0, depth 1000 against baseline 1000): verify `NORMAL_EXECUTION`, `size_haircut_factor == 1.0`, `adjusted_max_order_qty == 100` on a base of 100, and `restriction_reasons == []`.
- Input `DBE=4`, spread 3.5, depth 200 against baseline 1000, quad-witching `True`: verify `EXPIRY_WEEK_RESTRICTED`, `is_market_orders_allowed is False`, `depth_ratio == 0.20`, `adjusted_max_order_qty == 50`, and that `restriction_reasons` contains all three of `WIDE_SPREAD`, `THIN_TOP_OF_BOOK_DEPTH`, `QUAD_WITCHING_WEEK`.
- Boundary checks: a spread of exactly 2.0 is **not** wide; a depth ratio of exactly 0.30 (300/1000) is **not** thinned; `DBE == 2` mandates a roll while `DBE == 3` does not.
- Escalation regression: `DBE = -1` must return `EXPIRED_ESCALATE` with `adjusted_max_order_qty == 0`, `is_mandatory_roll_required is False`, and `requires_manual_escalation is True`.
- Fail-open regressions: a `NaN`/`inf` spread, a `NaN` depth, a zero or negative depth baseline, a crossed (negative) spread, a negative depth, a non-integer `days_to_expiration`, and a non-positive or non-integer `base_order_qty` must each raise `ValueError` rather than producing a permissive report. Out-of-range constructor thresholds must also raise.
- Sizing: `floor(7 × 0.50) == 3` (not 4), and a base quantity of 1 under a 50% haircut sets `is_order_size_suppressed`.
- Run `python -m unittest discover -s skills/futures-expiry-week-liquidity-and-volatility-handling/scripts` and confirm a 100% pass rate.

## Related Skills

- `futures-contract-roll-automation`
- `order-book-microstructure-signal-research`
- `liquidity-adjusted-position-sizing`
- `options-pin-risk-management-at-expiry`
- `adaptive-execution-under-volatility-spikes`
