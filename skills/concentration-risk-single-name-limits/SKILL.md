---
name: concentration-risk-single-name-limits
description: >-
  Use when a pre-trade gate must cap single-name exposure as a share of NAV and against
  average daily volume, downsizing or rejecting the order and reporting portfolio
  Herfindahl-Hirschman concentration.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: risk-management
  tags: concentration-risk, single-name-limit, hhi, adv-limit, pre-trade-risk, position-sizing
  brokers_frameworks: "NumPy; Generic Risk Engine"
  version: "1.1.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill to enforce pre-trade concentration risk limits across equity, futures, or crypto portfolios. Concentrating too much capital in a single security or issuer creates extreme idiosyncratic risk (e.g. unexpected earnings crash, regulatory action) and market impact risk during liquidation. This module validates proposed orders against maximum % NAV limits and % ADV (Average Daily Volume) constraints, automatically downsizing or rejecting non-compliant orders.

The NAV cap is applied to the **absolute** resulting exposure, so shorts are capped symmetrically with longs.

## When NOT to Use

- **As the only pre-trade control.** A single-name cap does not bound leverage, aggregate portfolio exposure, correlated-cluster exposure, or drawdown. Compose it with the risk skills listed under Related Skills.
- **For issuer-level or group-level limits.** This module keys on a single tradable symbol. Aggregating multiple share classes, ADRs, or a parent and its subsidiaries into one issuer limit requires an issuer-mapping layer this skill does not provide.
- **As a compliance attestation.** The default 5%/10% thresholds are illustrative risk-policy defaults, not a certified implementation of any fund-diversification rule. See `references/standards.md` for what the real rules do and do not say.
- **For a deliberately concentrated mandate** (activist, single-name, or pair strategies), unless the limits are raised to the values the mandate actually authorises.

## Prerequisites

- Portfolio Net Asset Value (NAV) and current **signed** position market values (negative for shorts).
- Average Daily Volume over the firm's chosen lookback window, and market price for each security.
- The signed notional of any orders already sent to a venue and not yet filled or cancelled.

## Workflow

1. **Pre-Trade Evaluation**: Submit the proposed order (`symbol`, `side`, `quantity`, `price`) to `SingleNameConcentrationLimiter.evaluate_order`. `side` must be exactly `BUY` or `SELL` — an unrecognised side raises rather than defaulting, because a side-parsing typo must never fail open into an unlimited branch.
2. **Establish effective exposure**: $E = \text{Current Signed Value} + \text{Pending Order Notional}$. Pending (unfilled) orders **must** be included, or several concurrent orders will each pass individually and breach collectively.
3. **NAV Concentration Check** against the absolute cap $L = \text{Max NAV Pct} \times \text{NAV}$:
   - If the order **increases** absolute exposure (same direction as $E$, or $E = 0$): headroom $= L - |E|$. If the position is already at or beyond $L$, headroom is zero and no further increase is approved.
   - If the order **reduces** absolute exposure: headroom $= |E| + L$ — the full unwind is always permitted, plus a compliant position on the far side. A de-risking trade is never blocked, even when the position is already non-compliant.
   - $N_{nav} = \lfloor \text{headroom} / \text{Price} \rfloor$.
4. **ADV Liquidity Check**: $N_{adv} = \lfloor \text{Max ADV Pct} \times \text{ADV} \rfloor$, applied to both sides — market impact is side-agnostic. A missing or non-positive ADV **rejects** the order; it is never read as "no liquidity constraint".
5. **Order Downsizing**: If the order exceeds $\min(N_{nav}, N_{adv})$, downsize to that quantity (or hard-reject when `allow_downsizing=False`). Share counts are floored, never rounded up past a limit.
6. **Portfolio HHI Calculation**: Compute the Herfindahl-Hirschman Index ($HHI = \sum w_i^2$) and Effective Assets ($N_{eff} = 1 / HHI$) over **gross** exposure. Both return `NaN` when gross exposure is zero, since concentration is undefined for an empty portfolio.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Applying the NAV cap only to buys.** Treating "SELL reduces exposure" as universally true leaves short selling unbounded: a sell from a flat position opens a brand-new single-name short with no cap. Bound the **absolute** resulting exposure, and separate "this trade reduces |exposure|" from "this side is a sell".
- **Ignoring unfilled orders.** Checking only filled positions lets ten concurrent 4%-of-NAV orders each pass a 5% limit and settle into a 40% position. MiFID II RTS 6 Art. 15(2) makes including all orders sent to a venue an explicit requirement for firms in scope.
- **Blocking de-risking trades.** A limiter that rejects any order on an already-over-limit position traps the portfolio in the breach. Reducing trades must always pass.
- **Ignoring Offsetting Futures/Derivatives**: Calculating single-name equity concentration without factoring in single-stock futures or options delta. Fold delta notional into `current_position_value` if it shares the limit.
- **Static Share Limits in Volatile Markets**: Hardcoding maximum share counts instead of dynamic % ADV limits. As market volume fluctuates, static share limits can cause severe market impact.
- **Evaluating Post-Trade Only**: Checking concentration after order execution when the position is already over-allocated. Concentration limits MUST be enforced pre-trade.
- **Percent/fraction unit errors.** Passing `5` to mean "5%" would install a 500%-of-NAV cap and silently disable the control. The constructor rejects any limit outside `(0, 1]`.
- **Treating a downsized quantity as tradable as-is.** The limiter returns a raw share count with no lot-size or minimum-fill rounding; see `minimum-fill-size-and-lot-rounding-logic` before routing.

## Verification

- Instantiate `SingleNameConcentrationLimiter` with a 5% NAV limit and 10% ADV limit. Submit an order for AAPL that would push NAV weight to 8% and consume 15% ADV. Verify that the limiter downsizes the order to satisfy both the 5% NAV and 10% ADV bounds.
- Submit a `SELL` of 5,000 shares from a flat position with ample ADV and verify it is downsized to the same 5%-of-NAV share count as the equivalent `BUY` — the short side must not be unbounded.
- Submit a `SELL` against an already-over-limit long and verify it passes untouched.
- Calculate portfolio HHI across 10 equal-weighted positions and verify $HHI = 0.10$ ($N_{eff} = 10.0$).
- Run `python -m unittest discover -s skills/concentration-risk-single-name-limits/scripts`.

## Related Skills

- `portfolio-level-stop-loss-independent-of-strategy-stops`
- `strategy-capacity-estimation-before-scaling-capital`
- `leverage-limit-enforcement-across-instruments`
- `correlation-aware-exposure-limits`
- `minimum-fill-size-and-lot-rounding-logic`
- `sec-rule-15c3-5-risk-controls-us`
- `mifid-ii-algo-trading-compliance-eu`
