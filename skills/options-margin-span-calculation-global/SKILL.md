---
name: options-margin-span-calculation-global
description: >-
  Use when an options or futures strategy needs a margin figure before it can be
  sized or placed — approximates the legacy CME-SPAN scanning-risk core (the 16
  standard risk-array scenarios, net option value, short option minimum) for
  multi-leg listed option positions, caps the requirement at the position's
  exact worst-case loss at expiry, and flags where SPAN, CME SPAN 2, OCC TIMS
  portfolio margin and NSE SPAN + extreme loss margin diverge. A pre-screen, not
  the clearing house's number.
domain: algorithmic-trading
subdomain: multi-asset-derivatives
tags:
- multi-asset-derivatives
- span-(standard-portfolio-analysis-of-risk)
- broker-specific-portfolio-margin-models
brokers_frameworks:
- CME SPAN (legacy scenario-based methodology)
- CME SPAN 2 (filtered historical simulation VaR)
- OCC TIMS / FINRA Rule 4210(g) portfolio margin
- NSE Clearing SPAN + Extreme Loss Margin (India)
- broker-specific portfolio margin models
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this for any options or futures strategy where the margin requirement, not the notional exposure, determines whether a position is placeable and how much capital it ties up. A backtest or strategy design that tracks only notional or a naive per-contract margin estimate can produce a strategy that looks well inside its risk limits on paper but cannot be placed live, because the broker's real methodology requires materially more (or less) capital than assumed. Margin on a multi-leg position is not the sum of the legs: scenario-based methodologies revalue the whole portfolio under a matrix of price and volatility shocks, so offsetting legs net against each other and a defined-risk spread is charged closer to its maximum loss than to the sum of its legs' naked requirements.

Use it also for the reverse question — sizing available capital. A margin figure that is *understated* is the more dangerous error: it lets a position be opened that the account cannot carry through the next recalculation.

## When NOT to Use

- **As the authoritative pre-trade gate.** Real SPAN consumes a daily exchange parameter file (price scan range, volatility scan range, short option minimum, extreme-move multiplier and cover fraction, inter- and intra-commodity spread parameters). None of that ships with this skill. Where the broker exposes a margin-calculator API, that figure is authoritative and this one is only a fast pre-screen.
- **For CME products already migrated to SPAN 2.** SPAN 2 is a filtered-historical-simulation VaR model, not a re-parameterised scenario grid, and its numbers are not reproducible from any scan matrix. CME has been moving products across in phases since 2023 (energy and equity first). Check the product before assuming a scenario scan applies.
- **For a US portfolio-margin account.** Those are margined under the OCC's TIMS per FINRA Rule 4210(g), with *asymmetric* stress ranges by product group. Use the TIMS ranges (below), and treat the result as a different model, not tuned SPAN.
- **For a US Reg-T strategy-based account.** That is `multi-leg-strategy-margin-optimization` — a different rule set (FINRA Rule 4210(f)(2)) producing materially different numbers.
- **For calendar or diagonal structures, or portfolios spanning underlyings.** Intra- and inter-commodity spread charges and credits are the part of SPAN this skill does not model.

## Prerequisites

- The broker's own margin calculator, ideally as an API. Methodology differs by broker even when both nominally use SPAN, because brokers layer house requirements on top.
- Knowledge of which regime the account is on — exchange SPAN, CME SPAN 2, OCC TIMS portfolio margin, or Reg-T strategy-based — since the same position produces materially different capital requirements under each.
- Where a local approximation is used: the exchange's current SPAN parameter file, or an explicit written record that placeholder parameters are in use and the output is therefore indicative only.
- Time to expiry and current marks per leg. Without time to expiry an option can only be valued at intrinsic, which strips out all time value and **understates** the requirement on short legs.

## Workflow

1. **Establish the margin regime before computing anything.** Exchange SPAN, CME SPAN 2, OCC TIMS and Reg-T strategy-based margin are four different models. Picking the wrong one is not a calibration error — the numbers are not comparable.
2. **Call the broker's margin-calculator API with the actual proposed multi-leg order where one exists**, and treat the returned figure as authoritative. Use the local approximation to pre-screen candidate trades cheaply, never as the final gate before order placement.
3. **Where no API exists, run the scenario scan — with real parameters.** The reference implementation scans the 16 standard SPAN scenarios: 14 pairings of a price move of {0, ±1/3, ±2/3, ±1} × the price scan range against the volatility scan range up and down, plus 2 extreme-move scenarios at a multiple of the scan range whose loss is only fractionally covered. The scan range is the parameter that decides the answer: a ±6% grid is roughly the OCC's high-capitalisation broad-based index range, and applying it to a single-name equity option — where TIMS stresses ±15% — understates the requirement by more than a factor of two. Source the parameters from the exchange; do not accept the library defaults as production values.
4. **Compute the scan on value *changes* from the current mark, not profit and loss from the entry premium.** Margin is a forward-looking measure of what the position can lose from where it is now. A scan anchored to the fill price makes the requirement depend on the price the position was opened at, which is meaningless as a risk figure and diverges further from the broker's number the longer the position is held.
5. **Apply the short option minimum.** A deep out-of-the-money short option can scan to a near-zero requirement, which is exactly why exchanges publish a per-contract floor. Omitting it is the single easiest way to produce a dangerously optimistic figure for a far-OTM short book. If the floor is unavailable, treat the output as unusable for short positions rather than as a low number.
6. **Cap a defined-risk position at its exact worst-case loss at expiry, and test bounded-ness structurally, not by pattern.** Compute the expiry payoff at zero, at every strike, and in the upside limit; the loss is unbounded exactly when the multiplier-weighted net call quantity is negative. Do not infer "hedged" from the mere presence of an opposite-signed leg of the same type — ten short puts against one long put is nine naked puts, and a ratio call spread has a long leg and unbounded loss at the same time.
7. **Add exchange-specific overlays explicitly, and only where they apply.** NSE-style Extreme Loss Margin is a flat percentage of short-leg notional charged *on top of* SPAN, with no analogue in a CME performance bond; charging it on a US position inflates the figure, and omitting it on an Indian one understates the amount actually blocked. Long options are paid for in full and attract no exposure charge.
8. **Validate the approximation against reality before trusting it.** Reconcile a sample of estimates against the broker's actual figures and record the sign and size of the bias. An unvalidated internal approximation is a hypothesis, not a margin number.
9. **Treat margin as a live figure, not a number computed at entry.** Brokers recalculate at least daily and more often in volatile periods, so an unchanged position's requirement can rise on its own and eat the capital budgeted for new trades. Monitor it; see `margin-utilization-circuit-breaker`.
10. **Model margin utilisation in the backtest whenever the strategy could be capital-constrained.** A backtest that ignores margin implicitly assumes infinite capital and overstates how many concurrent positions were actually holdable.
11. **Never treat margin figures as fungible across venues.** A spread that is cheap under one methodology can be expensive under another; aggregate risk-limit checks (see `correlation-aware-exposure-limits`) must use the per-venue figure, not a blended assumption.

> Full step-by-step procedure with methodology detail: see `references/workflows.md`.
> Margin-standard coverage table for this skill: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- Summing each leg's naked requirement for a defined-risk spread, over-constraining capital by a multiple of what the position actually blocks.
- Inferring "defined risk" from leg shape rather than from the payoff. Ten short puts hedged by one long put, or a 1×2 ratio call spread, both look paired and neither is defined-risk; margining them as spreads produces a near-zero requirement on a position with very large or unbounded loss.
- Charging margin on a long option. A long option is paid for in full at entry; its worst case is losing premium already spent, so a correct calculation returns a zero requirement, not the premium.
- Running a scenario scan with a volatility axis that the valuation function ignores — valuing legs at intrinsic makes every vol-up scenario identical to its vol-down twin, and turns a 16-scenario matrix into a price grid with a misleading name.
- Anchoring the scan to the entry premium instead of the current mark, so the "margin" changes with the fill price and drifts from the broker's figure as the position ages.
- Using a price scan range borrowed from another product class — index-scale ranges (±6%) on single-name equity options (TIMS: ±15%) understate the requirement badly.
- Omitting the short option minimum, letting a far-OTM short book scan to nearly zero.
- Assuming SPAN is one fixed methodology. Legacy SPAN, CME SPAN 2, OCC TIMS and NSE SPAN + ELM are different models with different inputs, and CME's SPAN 2 migration means the correct model for a given CME product changes over time.
- Treating margin as fixed at entry, and discovering an increase only when a later order is rejected for insufficient margin.
- Ignoring margin constraints in the backtest, implicitly assuming unlimited capital.
- Presenting an internal approximation as equivalent to the broker's figure without ever reconciling the two.

## Verification

- For a sample of representative multi-leg positions, compare the internal estimate against the broker's margin-calculator output (or the margin actually charged on a paper/live position) and record the systematic bias, with its sign. A consistently low bias is a blocker, not a rounding note.
- Confirm a long-only option position returns a zero requirement, and that a defined-risk spread's requirement never exceeds its worst-case loss at expiry.
- Confirm a ratio or unbalanced structure is *not* granted spread relief: compare the requirement against the same position with the surplus short legs removed and check it scales with the naked exposure.
- Confirm the requirement moves when implied volatility moves. If it does not, the volatility axis of the scan is not reaching the valuation and the matrix is doing half the work it claims.
- Confirm the scan-range and short-option-minimum parameters in use came from the exchange parameter file, not from library defaults.
- Confirm the backtest's margin-utilisation tracking yields a concurrent-position ceiling consistent with actual account capital, rather than allowing unlimited positions.
- Confirm live monitoring surfaces a margin increase on an existing, unchanged position (for example after a volatility spike) as an event in its own right, not something discovered when the next order is rejected.

## Related Skills

- `multi-leg-strategy-margin-optimization`
- `margin-utilization-circuit-breaker`
- `early-exercise-assignment-risk-management`
- `correlation-aware-exposure-limits`
- `execution-realistic-simulation`
- `kill-switch-and-drawdown-circuit-breakers`
