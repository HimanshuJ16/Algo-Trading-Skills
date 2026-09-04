---
name: algo-wheel-broker-execution-quality-comparison
description: >-
  Use when comparing brokers on an algo wheel from captured arrival prices and fills,
  ranking them by notional-weighted implementation shortfall and producing
  canary-preserving flow allocations that keep sampling alive.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: execution-algorithms
  tags: execution, algo-wheel, tca, implementation-shortfall, broker-routing, best-execution
  brokers_frameworks: generic
  version: "1.3.0"
  author: algo-trading-skills-contributors
---

## Purpose and Scope

Use this skill to compare broker execution quality after fills and convert the
comparison into a controlled target allocation for a broker wheel. The
reference implementation calculates signed Implementation Shortfall (IS) in
basis points, includes explicit fees, ranks brokers using decision-notional
weights, preserves a minimum canary allocation for non-leading brokers, and
returns the sample count and notional coverage behind each rank.

**It measures the executed quantity only.** Perold's implementation shortfall
is the difference between a paper portfolio filled instantly at the decision
price and the real portfolio, and it includes the opportunity cost of quantity
that was never executed. `BrokerExecution` carries no unfilled residual, so the
score here is the execution-cost component alone. A broker that fills the easy
part of an order and cancels the rest scores *better* on this metric than one
that completed the order. Pair the score with a fill-rate or completion
measure before it drives allocations — see Common Pitfalls.

This is a TCA and allocation component, not a complete best-execution program.
It does not select the arrival-price methodology, prove statistical
significance, replace venue selection controls, or route live orders itself.

## When to Use

Use it when:

- the trading system captures an arrival or decision price before routing;
- fills, quantities, broker identity, and explicit fees are available in a
  consistent currency and time window;
- order assignment to brokers within a comparable segment is randomised, so
  the resulting scores are not confounded by which orders each broker received;
- fill rate and unfilled residual are measured separately alongside this score;
- broker performance should influence future flow without fully starving
  underperformers;
- the desk can review allocation changes and retain the supporting TCA data.

Use the same benchmark definition across brokers and compare like-for-like
orders by side, instrument, venue, urgency, size, and market regime whenever
possible. A single unsegmented average can hide systematic routing bias.

## When NOT to Use

Do not use this skill when decision prices are missing, stale, or generated
after routing, or when fees are not comparable across brokers. Do not use it as
the sole evidence for regulatory best execution, a live risk limit, a broker
credit check, or a market-impact model.

Do not use it as a full implementation-shortfall measure, or as the only input
to a FINRA Rule 5310 regular and rigorous review: that review must also weigh
likelihood of execution, speed, and size of execution, none of which this
score contains.

Do not rank brokers from a single fill or from an unbounded historical window,
and do not rank brokers whose orders were chosen by a trader rather than by the
wheel — discretionary assignment reintroduces exactly the selection bias the
wheel exists to remove. Choose a review window and minimum observation policy
appropriate to the desk and configure it through `min_observations` and
`min_notional`.

## Prerequisites

- Python 3.10+.
- A validated execution record containing broker, side, decision price, fill
  price, quantity, and explicit fees.
- Decision price and fee currency aligned with the notional calculation, or a
  documented FX conversion before evaluation.
- A randomised assignment mechanism that allocates comparable orders to brokers
  according to the published target weights.
- A defined measurement window, order universe, and handling policy for partial
  fills, cancels, rejects, and venue differences.
- A documented data-sufficiency policy (minimum executions and notional per
  broker) before any allocation change.
- An approval process for changing wheel allocations and a route-level rollback
  or kill-switch mechanism.
- Monitoring for sample count, notional coverage, fill rate, IS distribution,
  and allocation drift.

## Inputs and Outputs

`BrokerExecution` is immutable and represents one completed execution. Prices
and quantity must be finite and strictly positive; fees may be positive or
negative when rebates are real and documented. `rank_brokers` returns an
ordered list of `BrokerScore` records — score, execution count, decision
notional, and promotion eligibility — for the audit trail. `evaluate_brokers`
returns a mapping from broker ID to target flow share; shares sum to 1.0 within
`ALLOCATION_TOLERANCE` for a non-empty broker set.

The reference evaluator uses:

- Buy slippage: `(fill_price - decision_price) / decision_price * 10000`;
- Sell slippage: `(decision_price - fill_price) / decision_price * 10000`;
- Fee bps: `fees_usd / (decision_price * quantity) * 10000`;
- IS: slippage bps plus fee bps.

Positive IS is a cost; negative IS is price improvement. Broker averages are
weighted by decision notional rather than by execution count.

Allocation rules:

- the best-ranked broker that satisfies `min_observations` and `min_notional`
  leads and receives the residual flow;
- every other observed broker receives `min_allocation`, including a
  better-scoring broker that failed the sufficiency policy;
- the leader must receive at least `min_allocation`, otherwise the
  configuration is rejected rather than inverting the ranking;
- if no broker satisfies the sufficiency policy, the wheel promotes nobody and
  returns equal weights with a warning.

## Workflow

1. **Define the benchmark**: document the decision-price timestamp, price
   source, side convention, fee definition, currency conversion, and review
   window before collecting results.
2. **Randomise assignment**: route comparable orders to brokers by the
   published weights using a randomising engine, not trader discretion, and
   record the assignment. Without this the next window's comparison measures
   order selection rather than broker skill.
3. **Validate execution records**: reject missing broker IDs, unsupported sides,
   non-finite values, non-positive prices, and non-positive quantities. Do not
   turn invalid records into zero-cost executions.
4. **Segment the universe**: compare orders with similar instrument, size,
   urgency, venue, and market conditions; record exclusions and partial-fill
   treatment.
5. **Calculate IS**: calculate signed slippage and explicit fee bps using the
   same decision-notional denominator for buys and sells.
6. **Measure what the score omits**: compute fill rate, cancelled residual, and
   reject rate per broker over the same window. A broker whose IS improved
   while its fill rate fell has not improved.
7. **Aggregate and rank**: call `rank_brokers` for the notional-weighted average
   IS, execution count, and notional coverage per broker; ties break
   deterministically by broker ID.
8. **Apply the sufficiency policy**: set `min_observations` and `min_notional`
   so a broker cannot lead the wheel on a thin sample. If nothing qualifies,
   accept the equal-weight no-promotion result and keep gathering data.
9. **Assign target flow**: give the leading eligible broker the residual
   allocation and each other observed broker the configured minimum canary
   share. Reject a configuration whose canary floors leave the leader with less
   than the floor itself.
10. **Approve and deploy**: review the proposed change, version the allocation
    snapshot together with the `BrokerScore` evidence, deploy it atomically, and
    monitor post-deployment performance.
11. **Re-evaluate**: repeat on a bounded schedule and after material changes to
    broker algo, fee schedule, venue mix, or market regime. FINRA members
    conducting a regular and rigorous review under Rule 5310 must do so at least
    quarterly.

## Common Pitfalls

- Ranking on executed-fill cost alone, which rewards a broker for cancelling
  the hard residual. Two brokers with identical IS and 95% versus 60% fill
  rates are not equivalent, and this score cannot tell them apart.
- Letting traders choose which orders go to which broker and then comparing the
  results. The wheel's comparison is only valid over randomised assignment.
- Promoting a broker to the leading share on one lucky small fill. Configure
  `min_observations` and `min_notional` instead of trusting the point estimate.
- Using `decision_price / fill_price` for sell slippage, which changes the
  denominator and overstates a 100-to-99 sell shortfall as 101.01 bps instead
  of 100 bps before fees.
- Averaging rounded per-trade bps or weighting every fill equally when order
  sizes differ materially.
- Returning zero for a zero or invalid decision price and allowing bad data to
  win the ranking.
- Comparing brokers with different fee currencies, venue mix, order urgency,
  or client flow without segmentation or adjustment.
- Letting the canary floor exceed the available flow, or setting a floor so
  high that the leading broker is routed less flow than the brokers it beat.
- Allowing input order to decide ties and therefore change allocations
  nondeterministically.
- Assuming allocation shares are exactly representable in binary floating
  point. Four brokers at a 10% floor sum to 0.9999999999999999 unless the
  leader absorbs the residual; compare against `ALLOCATION_TOLERANCE`.
- Changing allocations without retaining the TCA window, data snapshot,
  ranking, approval, and rollback reference.
- Building RTS 27 or RTS 28 reports from this output. Both MiFID II reporting
  obligations have been deleted — see `references/standards.md`.

## Verification

Run:

```text
python -m unittest discover -s skills/algo-wheel-broker-execution-quality-comparison/scripts
```

The tests cover buy and sell IS, price improvement, notional weighting, canary
allocation, deterministic ties, single-broker behavior, empty input, invalid
data, notional overflow, allocation configuration errors, the leader-floor
invariant, float-exact allocation sums, `BrokerScore` evidence, and the
data-sufficiency gate including the equal-weight fallback. Add production
integration tests for the execution ledger, fee/FX normalization, fill-rate
measurement, allocation publication, and rollback before connecting the result
to live routing.

## Related Skills

- `implementation-shortfall-minimization`
- `post-trade-execution-quality-scorecard`
- `best-execution-record-keeping-global`
- `broker-failover-secondary-account-routing`
