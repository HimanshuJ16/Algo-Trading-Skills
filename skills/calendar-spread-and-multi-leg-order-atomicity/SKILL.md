---
name: calendar-spread-and-multi-leg-order-atomicity
description: Use when executing multi-leg strategies (like calendar spreads) on exchanges
  that do not support native combo orders. Implements algorithmic atomicity and legging-risk
  management.
domain: algorithmic-trading
subdomain: execution-algorithms
tags:
- execution
- multi-leg
- atomicity
- legging-risk
- calendar-spread
brokers_frameworks:
- Generic Execution
version: "1.1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when executing multi-leg derivatives strategies (e.g., selling near-month, buying far-month options) across exchanges that lack native "combo" or "spread" order types. Executing these legs independently exposes the portfolio to **Legging Risk**—where one leg is filled but the market moves away before the second leg fills, leaving the portfolio with unintended directional Delta risk.

## When NOT to Use

- **A native combination instrument exists.** CME Globex lists calendar spreads as tradable instruments in their own right and its implied-order engine matches spread and outright books together "without the risk to the trader/broker of being double filled or filled on one leg and not on the other leg." Eurex, Deribit and most listed-derivatives venues offer equivalent combo/strategy books. Native combos give exchange-guaranteed atomicity; this skill only *approximates* it. Always check for a native instrument first — see `assets/checklist.md`.
- **The legs are not simultaneously tradable** (different sessions, one leg halted, one leg pre-open). Algorithmic legging cannot mitigate a leg that has no market at all; see `execution-algo-behavior-under-halted-instrument`.
- **You have no reliable order-terminal-state feed.** This engine's legging-risk detection depends on being told when the hedge order finished, not merely when it filled. Without that event, a zero-fill hedge is indistinguishable from a hedge still working.

## Prerequisites

- Two or more correlated instruments forming a spread strategy.
- A live connection to a broker API that provides order-status callbacks for **both** fills *and* terminal order states (cancelled / expired / rejected).
- A predefined maximum allowable "slippage" tolerance for the net spread price.

## Workflow

1. **Spread Definition**: Define the target net spread price and the limit prices for individual legs. Validate leg ratios and prices up front — a zero or negative ratio is a sizing bug that would otherwise surface as a division error mid-execution.
2. **Anchor Leg Execution**: The engine places a limit order for the most illiquid leg first (the "anchor" leg). Starting an already-started engine is refused, because a re-entry would duplicate a live order on the venue.
3. **Triggered Execution**: Upon a partial or full fill of the anchor leg, the engine immediately fires an IOC (Immediate or Cancel) order for the hedging leg, sized to *that specific fill* so the leg ratio is preserved tranche by tranche. The IOC is priced at the far edge of the configured slippage tolerance, so the tolerance is expressed once in the limit price rather than by chasing the market with repeated re-prices.
4. **Legging Risk Assessment**: Evaluate the hedge **only when the hedge order reaches a terminal state**, never on a fill report alone. An IOC may emit several execution reports before its remainder is cancelled, so an early shortfall is not yet a break; conversely a hedge that fills nothing emits *no* fill report at all, and a fill-driven check would stay silent on the worst possible outcome — a fully naked anchor position.
5. **Break Handling**: If the terminated hedge leaves an unhedged quantity, transition to `BROKEN`, raise a critical alert for the firm's emergency hedge protocol, **and cancel the resting anchor order** so naked exposure stops growing. If no cancel path is wired up, the engine escalates that fact explicitly rather than failing quietly.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Market Order Hedging**: Using Market Orders to complete the second leg guarantees execution but opens the strategy to catastrophic slippage during flash crashes.
- **Executing the Liquid Leg First**: Filling the highly liquid leg first, then finding out the illiquid leg has no bids. Always execute the illiquid leg first.
- **Ignoring Partial Fills**: Failing to proportionately size the second leg based on the *partial* fill quantity of the first leg.
- **Treating a partial hedge fill as a broken spread**: An IOC that trades against three resting orders produces three execution reports. Declaring `BROKEN` on the first one both fires a false emergency and — because `BROKEN` is terminal — permanently ignores the fills that complete the hedge moments later.
- **Detecting legging risk from fill events only**: The dangerous case is the hedge that fills *zero*. It sends no fill callback, so a fill-driven state machine leaves the position naked and the state machine reporting "hedging" forever. Subscribe to terminal order states.
- **Leaving the anchor order resting after a break**: A passive anchor limit order keeps working after the hedge fails. Every subsequent fill adds unhedged exposure to a position you have already declared broken.
- **Float equality on quantities**: `0.1 + 0.2 < 0.3` in binary floating point. Comparing accumulated fractional fill quantities exactly will manufacture phantom broken spreads on crypto and other fractional-size venues; compare with a tolerance.

## Verification

- Simulate an illiquid anchor leg filling 50%, followed by the hedging leg filling 50%. Ensure the remaining 50% anchor leg is managed correctly.
- Simulate a hedge IOC that reports two partial fills totalling the full quantity, and one that reports no fills at all. The first must complete; the second must break and cancel the anchor.
- Compare `realized_net_spread()` (anchor VWAP minus hedge VWAP) against the target net spread to confirm the executed spread landed inside tolerance.
- Run `python -m unittest discover -s skills/calendar-spread-and-multi-leg-order-atomicity/scripts` and confirm 100% pass rate.

## Related Skills

- `execution-algo-behavior-under-halted-instrument`
- `smart-order-routing-across-venues`
- `multi-leg-strategy-margin-optimization`
- `order-placement-idempotency`
