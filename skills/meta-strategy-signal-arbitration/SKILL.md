---
name: meta-strategy-signal-arbitration
description: >-
  Multi-strategy signal arbitration and internal order netting engine, resolving conflicting sub-strategy signals, enforcing risk-off vetoes, suppressing rebalancing churn, and preventing opposing internal orders from reaching the venue as self-matches.
domain: Portfolio Multi Strategy
subdomain: Signal Arbitration & Internal Order Netting
tags: ["meta-strategy", "signal-arbitration", "internal-order-netting", "conflict-resolution", "risk-veto", "deadband-filter", "self-match-avoidance"]
brokers_frameworks: ["Multi-Strategy Arbitrator", "Python Dataclasses"]
version: "1.1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when managing multi-strategy portfolios running concurrent independent algorithms (e.g. Trend Following, Mean Reversion, Statistical Arbitrage, Sentiment/NLP) on shared asset universes. Sub-strategies frequently generate opposing trading signals on identical symbols (e.g. Strategy A BUY $+\$100{,}000$ vs Strategy B SELL $-\$60{,}000$). Routing both orders to market crosses the spread twice, pays two sets of fees, and — the larger exposure — risks the two orders matching each other at the venue. This module implements **Meta-Strategy Signal Arbitration** and **Internal Order Netting**: it evaluates priority risk-off vetoes, calculates a weighted consensus signal, and emits only the net difference ($+\$40{,}000$) as an executable order.

## When NOT to Use

Do **not** use it as a substitute for venue-level self-match prevention configuration, for broker/market-access pre-trade risk controls, or for a portfolio kill switch — it sits upstream of all three and enforces none of them. It also has no view of current positions, no cross-symbol netting, and no per-strategy fill allocation; those belong to the skills cross-linked below.

## Prerequisites

- Sub-strategy signal payload (`strategy_id`, `symbol`, `raw_signal`: $[-1.0, +1.0]$, `conviction_score`: $[0.0, 1.0]$, `target_notional_usd`, `is_risk_veto`: bool).
- Strategy allocation weights (`strategy_id`, `weight` $> 0$, `priority_rank`) — **one entry per signalling strategy**; there is no default weight.
- An agreed convention that `target_notional_usd` carries the exposure **change** each strategy requests, not an absolute position target (see Workflow step 3).

## Workflow

1. **Fail-Closed Input Validation**:
   - Reject an empty batch, any signal whose `symbol` differs from the arbitrated symbol, duplicate `strategy_id`s, any `strategy_id` with no configured weight, non-finite values, and `raw_signal`/`conviction_score` outside their documented ranges.
   - A `ValueError` here means *do not trade this symbol on this pass*. Never catch it and fall through to the sub-strategies' raw orders — that is precisely the un-netted routing this module exists to prevent.
2. **Priority Risk-Off Veto Audit**:
   - If any strategy emits `is_risk_veto == True` $\implies$ enforce absolute risk-off override (`ARBITRATION_VETO_RISK_OFF`), regardless of that strategy's weight or the strength of opposing alpha.
   - The report returns `consensus_signal = 0.0` (flat), **not** $-1.0$: a veto means "hold no risk here", while $-1.0$ would instruct a downstream sizer to open a maximum-conviction short.
3. **Weighted Consensus Signal Calculation**:
   - $$S_{\text{consensus}} = \frac{\sum_k w_k \times S_{k, i} \times C_{k, i}}{\sum_k w_k}$$
   - Compute Gross Notional $= \sum_k |N_k|$ and Net Notional $= \sum_k N_k$. Gross equals traded notional only if $N_k$ are exposure *changes*; absolute position targets from a non-flat book overstate both gross and savings.
4. **Internal Order Netting & Transaction Savings**:
   - $$\text{Savings}_{\text{usd}} = (\text{Gross Notional} - |\text{Net Notional}|) \times \frac{\text{cost\_bps}}{10{,}000.0}$$
   - `cost_bps` is a **one-way, all-in** cost per unit of notional. If deriving it from a quoted spread, use *half* the quoted spread — SEC Reg NMS defines effective spread as double the distance from the midpoint, so the cost of crossing measured against mid is half the quote.
5. **Deadband Filter Audit**:
   - If $|S_{\text{consensus}} - S_{\text{current}}| < \epsilon_{\text{deadband}} \implies$ suppress rebalancing (`DEADBAND_REBALANCING_SUPPRESSED`). The comparison is strict, so a delta exactly equal to the threshold still trades.
   - A suppressed pass reports $\$0.00$ netting savings: nothing was routed, so netting avoided nothing.
6. **Audit Report Generation**: Output structured `MetaStrategyArbitrationReport`. Branch on `status`, never on `net_executable_notional_usd` alone — $0.0$ means "route no order", which is not "flatten to zero exposure".

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Routing Opposing Internal Orders to the Venue**: Sending simultaneous BUY and SELL orders for the same instrument from different internal sub-strategies. Beyond the doubled spread and fees, the two orders can match each other. FINRA Rule 5210 Supplementary Material .02 requires members to have policies and procedures reasonably designed to review for and prevent *a pattern or practice* of self-trades from a single or related algorithms or desks; CME's Rule 534 advisory recommends self-match-minimising functionality where algorithms controlled by the same individual or team trade against each other on more than an incidental basis.
- **Assuming the "Independent Algorithms" Safe Harbour Still Applies**: The bona-fide reading in FINRA 5210.02 covers *unrelated* algorithms; CME's advisory covers *fully independent* trading groups with no knowledge of one another's orders. Once sub-strategies feed a common arbitrator they are related algorithms under shared control, so bypassing the arbitrator "to keep the strategies independent" removes the mitigation without restoring the independence.
- **Overriding Risk-Off Signals with Alpha Signals**: Letting a high-conviction momentum signal outvote a risk-off stop loss. The veto is not a weighted input; it short-circuits arbitration.
- **Defaulting an Unrecognised `strategy_id` to a Fallback Weight**: A single typo in a strategy identifier then carries a weight unrelated to its allocation and can invert the sign of the consensus signal. Require an explicit weight and fail closed.
- **Netting Across Symbols**: Passing a mixed-symbol batch nets one instrument's exposure into another's order, producing a size no strategy requested on either. Validate every signal's symbol against the arbitrated symbol.
- **Over-Rebalancing on Micro Signal Churn**: Rebalancing for tiny consensus fluctuations without deadband filtering. Conversely, remember the deadband gates on *signal*, not notional — a large change in requested notional at an unchanged consensus is suppressed.
- **Double-Counting Spread in the Savings Estimate**: Feeding a full quoted spread into `cost_bps` overstates savings by roughly $2\times$; the crossing cost against mid is the half-spread.
- **Losing Per-Strategy Attribution After Netting**: A netted order has no one-to-one link to the requests that produced it. Allocate fills back to sub-strategies explicitly, or per-strategy performance measurement silently decays.

## Verification

- Instantiate `MetaStrategySignalArbitratorEngine(deadband_threshold=0.05, estimated_transaction_cost_bps=10.0)`. Audit AAPL with 2 strategies (Strategy 1 BUY $+\$100{,}000$, Strategy 2 SELL $-\$60{,}000$, weights $0.50/0.50$). Verify Gross $= \$160{,}000$, net order $= +\$40{,}000$, netted volume $= \$120{,}000$, internal netting savings $= \$120.00$ at $10\text{ bps}$, and status `ARBITRATION_NETTED_ORDER_GENERATED`.
- Audit a Risk Veto $\implies$ verify `ARBITRATION_VETO_RISK_OFF`, `net_executable_notional_usd == 0.0`, and `consensus_signal == 0.0`.
- Audit a cross-symbol batch, an unknown `strategy_id`, and a NaN `raw_signal` $\implies$ verify each raises `ValueError` and produces no order.
- Run `python -m unittest discover -s skills/meta-strategy-signal-arbitration/scripts`.

## Related Skills

- `multi-order-netting-before-routing`
- `exchange-self-match-prevention-configuration`
- `wash-trade-and-spoofing-self-detection`
- `portfolio-level-stop-loss-independent-of-strategy-stops`
- `strategy-level-kill-switch-vs-portfolio-level-kill-switch`
- `risk-adjusted-performance-attribution-per-strategy`
