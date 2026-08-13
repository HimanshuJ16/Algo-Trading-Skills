---
name: algo-wheel-broker-execution-quality-comparison
description: >-
  Compares broker execution quality with validated, notional-weighted
  implementation shortfall and produces deterministic canary-preserving flow
  allocations for an algorithmic broker wheel.
domain: execution-algorithms
subdomain: smart-order-routing
tags:
- execution
- algo-wheel
- tca
- implementation-shortfall
- broker-routing
- best-execution
brokers_frameworks:
- generic
version: "1.2.0"
author: System
license: MIT
---

## Purpose and Scope

Use this skill to compare broker execution quality after fills and convert the
comparison into a controlled target allocation for a broker wheel. The
reference implementation calculates signed Implementation Shortfall (IS) in
basis points, includes explicit fees, ranks brokers using decision-notional
weights, and preserves a minimum canary allocation for non-leading brokers.

This is a TCA and allocation component, not a complete best-execution program.
It does not select the arrival-price methodology, prove statistical
significance, replace venue selection controls, or route live orders itself.

## When to Use

Use it when:

- the trading system captures an arrival or decision price before routing;
- fills, quantities, broker identity, and explicit fees are available in a
  consistent currency and time window;
- broker performance should influence future flow without fully starving
  underperformers;
- the desk can review allocation changes and retain the supporting TCA data.

Use the same benchmark definition across brokers and compare like-for-like
orders by side, instrument, venue, urgency, size, and market regime whenever
possible. A single unsegmented average can hide systematic routing bias.

## When Not to Use

Do not use this skill when decision prices are missing, stale, or generated
after routing, or when fees are not comparable across brokers. Do not use it as
the sole evidence for regulatory best execution, a live risk limit, a broker
credit check, or a market-impact model.

Do not rank brokers from a single fill or from an unbounded historical window.
Choose a review window and minimum observation policy appropriate to the desk;
the small reference implementation expects the caller to perform that data
sufficiency and segmentation review.

## Prerequisites

- Python 3.9+.
- A validated execution record containing broker, side, decision price, fill
  price, quantity, and explicit fees.
- Decision price and fee currency aligned with the notional calculation, or a
  documented FX conversion before evaluation.
- A defined measurement window, order universe, and handling policy for partial
  fills, cancels, rejects, and venue differences.
- An approval process for changing wheel allocations and a route-level rollback
  or kill-switch mechanism.
- Monitoring for sample count, notional coverage, IS distribution, and
  allocation drift.

## Inputs and Outputs

`BrokerExecution` is immutable and represents one completed execution. Prices
and quantity must be finite and strictly positive; fees may be positive or
negative when rebates are real and documented. The evaluator returns a mapping
from broker ID to target flow share. Shares sum to 1.0 for a non-empty broker
set.

The reference evaluator uses:

- Buy slippage: `(fill_price - decision_price) / decision_price * 10000`;
- Sell slippage: `(decision_price - fill_price) / decision_price * 10000`;
- Fee bps: `fees_usd / (decision_price * quantity) * 10000`;
- IS: slippage bps plus fee bps.

Positive IS is a cost; negative IS is price improvement. Broker averages are
weighted by decision notional rather than by execution count.

## Workflow

1. **Define the benchmark**: document the decision-price timestamp, price
   source, side convention, fee definition, currency conversion, and review
   window before collecting results.
2. **Validate execution records**: reject missing broker IDs, unsupported sides,
   non-finite values, non-positive prices, and non-positive quantities. Do not
   turn invalid records into zero-cost executions.
3. **Segment the universe**: compare orders with similar instrument, size,
   urgency, venue, and market conditions; record exclusions and partial-fill
   treatment.
4. **Calculate IS**: calculate signed slippage and explicit fee bps using the
   same decision-notional denominator for buys and sells.
5. **Aggregate and rank**: calculate notional-weighted average IS per broker;
   break exact ties deterministically by broker ID and retain the underlying
   sample and notional coverage for review.
6. **Assign target flow**: give the leading broker the residual allocation and
   each other observed broker the configured minimum canary share. Reject a
   configuration whose canary floors leave no allocation for the leader.
7. **Approve and deploy**: review the proposed change, version the allocation
   snapshot, deploy it atomically, and monitor post-deployment performance.
8. **Re-evaluate**: repeat on a bounded schedule and after material changes to
   broker algo, fee schedule, venue mix, or market regime.

## Common Pitfalls

- Using `decision_price / fill_price` for sell slippage, which changes the
  denominator and overstates a 100-to-99 sell shortfall as 101.01 bps instead
  of 100 bps before fees.
- Averaging rounded per-trade bps or weighting every fill equally when order
  sizes differ materially.
- Returning zero for a zero or invalid decision price and allowing bad data to
  win the ranking.
- Comparing brokers with different fee currencies, venue mix, order urgency,
  or client flow without segmentation or adjustment.
- Letting the canary floor exceed the available flow or allowing input order to
  decide ties and therefore change allocations nondeterministically.
- Changing allocations without retaining the TCA window, data snapshot,
  ranking, approval, and rollback reference.

## Verification

Run:

```text
python scripts/test_algo_wheel_broker_execution_quality_comparison.py
```

The tests cover buy and sell IS, price improvement, notional weighting,
canary allocation, deterministic ties, single-broker behavior, empty input,
invalid data, and allocation configuration errors. Add production integration
tests for the execution ledger, fee/FX normalization, allocation publication,
and rollback before connecting the result to live routing.

## Related Skills

- `implementation-shortfall-minimization`
- `post-trade-execution-quality-scorecard`
- `best-execution-record-keeping-global`
- `broker-failover-secondary-account-routing`
