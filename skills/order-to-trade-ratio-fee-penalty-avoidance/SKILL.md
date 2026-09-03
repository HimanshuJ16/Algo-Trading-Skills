---
name: order-to-trade-ratio-fee-penalty-avoidance
description: >-
  Use when an algorithm's unexecuted order-message traffic is subject to a venue order-to-trade ratio limit or surcharge — computes the RTS 9 count and volume ratios per instrument using the RTS 9 Annex message weights, estimates the venue penalty, and throttles or freezes order modifications before the limit binds.
domain: Market Microstructure & Regulatory Compliance
subdomain: Exchange Fee Optimization & OTR Throttling
tags: ["otr", "order-to-trade-ratio", "exchange-fees", "fee-penalty", "quote-stuffing", "hft-compliance", "microstructure", "rts-9"]
brokers_frameworks: ["MiFID II RTS 9 (Commission Delegated Regulation (EU) 2017/566)", "Eurex Excessive System Usage Fee", "NSE / SEBI Algo OTR Penalty Framework", "ICE Futures Europe & ICE Endex OTR Guidance", "Python Dataclasses"]
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when a strategy sends order messages that the venue counts against an order-to-trade ratio: market making, passive repricing, iceberg replenishment, or any algorithm that cancel-replaces faster than it fills. Two distinct regimes bite, and they are not the same thing:

- **A regulatory limit.** Under MiFID II, RTS 9 (Commission Delegated Regulation (EU) 2017/566) requires EU trading venues to calculate, per member and **per financial instrument**, a ratio of *unexecuted* orders to transactions in both volume and number terms, and to set a maximum. Exceeding it is a venue-rule breach, not a fee line.
- **A fee or penalty schedule.** Separately, venues charge for excessive messaging: Eurex's Excessive System Usage fee, NSE's per-algo-order slabs on the daily member-level OTR, ICE Futures Europe's flat GBP 2,000 / EUR 2,000 charge per Red-Threshold session.

Use the engine to estimate both from your own message counts *during* the session, so throttling happens before the venue's end-of-session report confirms the breach.

## When NOT to Use

- **As the system of record.** The venue computes the official ratio and issues the charge. This is a client-side estimate that cannot see venue-side effects it does not receive messages for — stop and peg orders triggered by the venue, implied orders, market-operations cancellations. Reconcile against the venue's daily OTR report (ICE, for example, delivers per-member CSVs by 06:00 GMT the following day); where they disagree, the venue is right.
- **On venue-aggregated totals.** RTS 9 Art. 3(2) determines breach from activity "in one specific instrument". A roll-up across a book of instruments can read compliant while a single ISIN breaches — the exact case the rule exists to catch. Audit per instrument and fold with `aggregate_worst_instrument`.
- **Before applying the venue's own order exclusions.** SEBI/NSE excludes algo orders priced within 0.75% of the LTP, orders from Designated Market Makers, and orders in auction/pre-open/block/post-close sessions. RTS 9 Art. 1(a) excludes cancels sent after auction uncrossing, connectivity loss, or kill-switch use. Those tests need the order book and session state; this engine takes counts that are already filtered.
- **As a market-abuse control.** A low OTR does not mean the order flow is not layering or spoofing, and a high one is not evidence that it is — see `wash-trade-and-spoofing-self-detection`.
- **With a guessed limit.** There is deliberately no default `max_count_otr`. Published limits span from NSE's daily ratio of 50 to ICE Futures Europe's Red Threshold of 2,500,000 in number terms. A plausible-looking default is wrong nearly everywhere.

## Prerequisites

- Per-instrument, per-session message counts, split by RTS 9 Annex type: `limit_submits`, `limit_modifies`, `limit_cancels`, `quote_submits`, `quote_modifies`, `quote_cancels`, plus `exempt_cancels` for the Art. 1(a) exclusions.
- `transactions` — totally **or partially** executed orders (RTS 9 Art. 1(b)); a partial fill is a transaction.
- `ordered_volume` / `traded_volume` in the RTS 9 Art. 1(c) unit for the asset class: instrument count for shares/ETFs/depositary receipts, nominal value for bonds and structured finance products, lots or contracts for derivatives, tonnes of CO2 for emission allowances. Both in the same unit.
- The venue's published `max_count_otr` and `max_volume_otr`, and the `OTRConvention` they are expressed in.
- The venue's penalty schedule as `PenaltyTier`s, if you want a fee estimate.

## Workflow

1. **Pick the convention before anything else.** The two published definitions differ by exactly one:
   $$\text{OTR}_{\text{RTS 9}} = \frac{\text{Orders}}{\text{Transactions}} - 1 \qquad \text{OTR}_{\text{gross}} = \frac{\text{Orders}}{\text{Transactions}}$$
   RTS 9 Art. 3(1) measures *unexecuted* orders, so a member whose every order fills scores $0.0$, not $1.0$. Applying a gross-convention limit to an RTS 9 ratio throttles a full transaction's worth of messages too late.
   - **Decision point:** if the venue's document says "unexecuted orders to transactions", use `RTS9_UNEXECUTED`. If it publishes a plain "N orders per trade" slab (NSE), use `GROSS_MESSAGES_PER_TRADE`.

2. **Count messages by RTS 9 Annex weight, not one-per-message.**
   - Limit submit / add / delete: **1**. Limit **modify: 2** — the Annex states a modification "entails a cancellation and a new insertion".
   - Quote: **2** (one per side). Quote delete: **2**. Quote **modify: 4**.
   - **Decision point:** subtract Art. 1(a) exempt cancels — those sent after auction uncrossing, loss of venue connectivity, or use of a kill functionality. Do not let a risk control that fired correctly manufacture a breach.

3. **Evaluate both ratios; breach on either.** RTS 9 Art. 3(2): the limit is exceeded if activity in one instrument exceeds "either or both" of the two ratios. A count ratio inside its limit proves nothing if the volume ratio is outside its own. A volume-only breach reports `binding_ratio="VOLUME"` and `excess_messages=0` — do not read that zero as compliance.

4. **Handle zero transactions as undefined, never as one.**
   - **Decision point:** if the member has no transactions in the instrument, the ratio is *not calculable* — the treatment ICE applies ("No OTR ratios will be calculated in case the member has not traded for the applicable trading session"). Substituting `max(1, transactions)` silently grants a limit's worth of free messages and reports a fabricated number as a measurement. The engine returns `OTR_NOT_CALCULABLE_NO_TRANSACTIONS` with `None` ratios, and freezes: messages with no fills are the worst case the regime targets, not the safest.

5. **Estimate the penalty against the venue's actual structure.**
   - Flat excess-times-fee (Eurex ESU form, `(messages − limit) × fee`): one `PenaltyTier` from the limit, unbounded above.
   - Progressive slabs (NSE, "on incremental basis"): `NSE_ALGO_OTR_PENALTY_TIERS_2018`.
   - Flat per-breach charge (ICE): not a per-message schedule; derive it from `status == OTR_BREACH_PENALTY_ACTIVE` and count breach sessions yourself.

6. **Act on the recommendation, then fold across instruments.** `THROTTLE_ORDER_MODIFICATIONS` at the configured warning margin; `FREEZE_ORDER_MODIFICATIONS_REQUIRE_TAKER_FILL` at or above the limit. Feed per-instrument reports to `aggregate_worst_instrument` for a venue-level kill decision.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Counting a modify as one message.** RTS 9 Annex counts a limit modify as **2** and a quote modify as **4**. A repricing market maker that counts one-per-message sees roughly half its true ratio and throttles only after the venue has already recorded the breach. This under-throttles, which is the dangerous direction.
- **Dropping the `− 1`.** Under RTS 9 the ratio is `orders/transactions − 1`. Comparing a gross ratio against an RTS 9 limit reports the strategy as one full transaction's worth of messages worse than it is; comparing an RTS 9 ratio against a gross limit reports it as better. Both are wrong; only one is unsafe.
- **Aggregating across instruments.** A venue-wide "average OTR" is not a regulated quantity. Twenty heavily-executing instruments will bury one instrument quoting at 500:1, and the venue charges on that one.
- **Counting kill-switch cancels.** A mass cancel on kill-switch activation is explicitly excluded by Art. 1(a). Counting it makes the risk control look like the violation and can trip a freeze at the moment the strategy most needs to flatten.
- **Treating zero trades as one trade.** `max(1, trades)` is the standard shortcut and it fabricates both the ratio and the allowance. Zero transactions means not calculable.
- **Reading `excess_messages == 0` as safe.** It is a count-ratio quantity. A volume-ratio breach, or a count ratio sitting exactly at the limit, both produce zero excess and are still breaches.
- **Assuming a flat per-message fee.** None of Eurex, NSE or ICE charges that way universally: Eurex applies a sliding scale and waives fewer than four exceedances per calendar month as accidental; NSE charges incremental paise slabs on the daily ratio; ICE charges a flat GBP 2,000 / EUR 2,000 per Red-Threshold session regardless of message count. Model the venue you trade on.
- **Ignoring the non-monetary consequences.** NSE's framework also imposes a 15-minute cooling-off at the next open at a daily ratio of 500 or more, and suspension of proprietary trading for the first hour after more than ten penalised days in the previous thirty rolling trading days. The engine models charges only.
- **Deliberately "resetting" the ratio with taker fills.** Crossing the spread to buy a denominator is a real technique and a real cost — the taker fee plus spread routinely exceeds the message surcharge avoided. Price it before automating it, and confirm the venue does not treat self-matching or wash-like fills as abusive; see `wash-trade-and-spoofing-self-detection`.

## Verification

- Build `OTRThresholdPolicy(max_count_otr=..., max_volume_otr=..., convention=...)` — construction with no limits raises, by design.
- **Annex weighting**: `weighted_order_message_count` on 100 submits, 50 modifies, 30 cancels returns $100 + 2{\times}50 + 30 = 230$, not the naive $180$.
- **Convention**: 400 messages over 20 transactions is gross $20.0$ and RTS 9 $19.0$. Against a limit of $19.5$ the same activity is a warning under `RTS9_UNEXECUTED` and a breach under `GROSS_MESSAGES_PER_TRADE`.
- **Volume-only breach**: 50 messages / 10 transactions (count $5.0$, limit $100$) with ordered $5{,}000$ / traded $100$ (volume $50.0$, limit $10$) yields `OTR_BREACH_PENALTY_ACTIVE`, `binding_ratio="VOLUME"`, `excess_messages=0`.
- **Zero transactions**: 1,000 messages, 0 transactions yields `OTR_NOT_CALCULABLE_NO_TRANSACTIONS`, `count_otr is None`, `penalty_fee_accrued is None`, action `FREEZE_ORDER_MODIFICATIONS_REQUIRE_TAKER_FILL`.
- **NSE slabs**: 30,000 algo messages over 100 trades (daily OTR 300) accrues $20{,}000 \times ₹0.02 + 5{,}000 \times ₹0.10 = ₹900.00$.
- **Negative checks**: negative counters, a `NaN` or infinite volume, `traded_volume > ordered_volume`, `exempt_cancels` exceeding cancels sent, overlapping penalty tiers, and a missing policy must each raise.
- Run `python -m unittest discover -s skills/order-to-trade-ratio-fee-penalty-avoidance/scripts` and confirm a 100% pass rate.

## Related Skills

- `exchange-fee-tier-and-rebate-structure-analysis`
- `execution-venue-fee-tier-optimization`
- `post-only-and-maker-taker-fee-optimization`
- `mifid-ii-algo-trading-compliance-eu`
- `india-sebi-algo-trading-tagging-requirements`
- `wash-trade-and-spoofing-self-detection`
- `message-rate-limit-vs-latency-tradeoff-tuning`
