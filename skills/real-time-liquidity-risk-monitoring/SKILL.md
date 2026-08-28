---
name: real-time-liquidity-risk-monitoring
description: >-
  Use when monitoring the market liquidity of a live portfolio — Days to Liquidate (DTL)
  at a participation cap, bid-ask spread spikes, L2 depth collapse, and a
  Liquidity-Adjusted VaR (L-VaR) add-on over a mid-price VaR.
domain: algorithmic-trading
subdomain: risk-management
tags: ["risk-management", "liquidity-risk", "days-to-liquidate", "spread-spike", "order-book-depth", "l-var", "market-impact"]
brokers_frameworks: ["Bangia-Diebold-Schuermann-Stroughair L-VaR", "Python Dataclasses"]
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when a portfolio's risk report is computed at mid-price and therefore says nothing about what it would cost to actually get out. It measures **market (asset) liquidity** — the cost and horizon of unwinding — across four dimensions: Days to Liquidate at a participation cap, bid-ask spread spikes against each symbol's own baseline, top-of-book depth collapse, and a cost-of-liquidity add-on that converts a mid-price VaR into a Liquidity-Adjusted VaR.

The engine is a **monitor**, not a control: it produces a `RealTimeLiquidityReport` for a governance or alerting path. Enforcement (resizing, halting, unwinding) belongs in a separate component — see `kill-switch-and-drawdown-circuit-breakers`.

## When NOT to Use

- **For funding liquidity.** Cash, collateral, margin calls and settlement obligations are a different risk. Basel III's LCR and NSFR govern that for banks; nothing here implements them. See `margin-utilization-circuit-breaker` and `broker-account-margin-call-handling`.
- **As a market-risk model.** The `baseline_var_usd` argument is *your* VaR, computed elsewhere. This skill only adds the liquidation-cost layer on top of it; see `value-at-risk-var-live-monitoring`.
- **With a calm-market ADV during a dislocation.** DTL scales inversely with ADV, so a trailing 30-day ADV computed before a shock understates the liquidation horizon precisely when it matters. The engine takes ADV as an input and cannot tell a stressed estimate from a stale one — you must supply the stressed figure.
- **With the default market-impact coefficient unexamined.** `market_impact_coeff_per_day = 0.10` is a placeholder, not a calibrated or published value (see Workflow step 3).
- **For pre-trade sizing.** This audits an existing book. To size a *new* position against available liquidity, use `liquidity-adjusted-position-sizing`.
- **For scenario liquidity stress.** For "what if ADV halves and spreads triple", use `portfolio-stress-test-including-liquidity-crunch-scenarios`; this engine reads one snapshot.

## Prerequisites

- One observation per symbol, deduplicated (net the lots first): `symbol`, `position_size` (signed; magnitude is used), `current_price` (> 0), `adv` (> 0, shares/contracts), `bid_ask_spread` and `normal_spread` (>= 0 and > 0, **price units** not basis points), `l2_depth_top3` and `normal_l2_depth` (>= 0 and > 0, share/contract units).
- `baseline_var_usd` — your own mid-price VaR for the same portfolio, at the same confidence level, horizon and currency. It is a **required** argument.
- Config (`Config`): `max_dtl_threshold_days` (2.0), `max_participation_pct` (0.10), `spread_spike_threshold_ratio` (2.0), `depth_drop_threshold_pct` (0.50), `market_impact_coeff_per_day` (0.10). All are library defaults requiring calibration — see `references/standards.md`.

## Workflow

1. **Days to Liquidate (DTL)**:
   - $\text{DTL}_i = \frac{|\text{Position}_i|}{\text{MaxParticipationPct} \cdot \text{ADV}_i}$. Breach when $\text{DTL}_i \ge \text{MaxDTLThreshold}$.
   - **Decision point — which ADV.** DTL is only as honest as the ADV fed to it. Feed the *stressed* ADV you would actually expect in the regime you are monitoring for; the engine cannot detect a stale one and will report a comfortable horizon computed from volume that no longer exists.
   - Shorts count at magnitude: buying back a short consumes liquidity exactly as selling a long does, so portfolio notional here is **gross**, not net.

2. **Spread Spike & Depth Collapse**:
   - $\text{SpreadRatio}_i = \frac{\text{Spread}_i}{\text{NormalSpread}_i}$; spike when $\ge 2.0$.
   - $\text{DepthDrop}_i = 1 - \frac{\text{Depth}_i}{\text{NormalDepth}_i}$; collapse when $\ge 50\%$.
   - **Decision point — all three thresholds are inclusive.** A metric landing exactly on its limit is reported as a breach. A monitor that stays silent precisely at its configured limit is a foot-gun, and a real-time feed cannot be relied on to avoid exact equality.
   - **Decision point — reject the observation, do not smooth it.** A `NaN`, a zero ADV, a zero price, or a crossed (negative) spread is a data fault. The engine raises rather than clamping, because every NaN comparison is `False`: a clamped or NaN metric raises no flag and the book reads as healthy.

3. **Cost of Liquidity → L-VaR**:
   - $\text{COL}_i = \tfrac{1}{2} \cdot \text{Notional}_i \cdot \left(\text{RelativeSpread}_i + k \cdot \text{DTL}_i\right)$, where $k$ is `market_impact_coeff_per_day` and $\text{RelativeSpread}_i = \text{Spread}_i / \text{Price}_i$.
   - The half-spread term is Bangia–Diebold–Schuermann–Stroughair (1999) Eq. 4 with the tail scaler $a = 0$ — the snapshot spread, with **no** spread-volatility term. It therefore estimates the mean-condition cost, not the 99th-percentile cost BDSS target, and understates tail exogenous liquidity risk.
   - **Decision point — $k$ is uncalibrated.** No regulator, exchange or published study prescribes a value. At the default $k = 0.10/\text{day}$ a 5-day DTL charges $25\%$ of notional, implausible for a liquid equity. Calibrate against your own realized transaction costs, or set $k = 0$ and use the half-spread term alone. `spread_cost_usd` and `impact_cost_usd` are reported separately so the split is auditable.
   - $\text{L-VaR} = \text{BaselineVaR} + \sum_i \text{COL}_i$. Costs are summed with no diversification benefit, and added straight onto the mid-price VaR — BDSS assume extreme return moves and extreme spread moves coincide, which makes the sum conservative where they do not.

4. **Audit Report**: emit `RealTimeLiquidityReport` with `status` in {`LIQUIDITY_HEALTHY`, `LIQUIDITY_RISK_ALERT`, `NO_POSITIONS`}. `NO_POSITIONS` is **not** a pass — an empty book has not been assessed.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Treating a clamped input as a measurement**: substituting a floor for a bad input (`max(1.0, adv × pct)`, `max(0.01, price)`) does not make the reading safe, it makes it fabricated — an ADV of zero yields a 100,000-day DTL and a $50bn L-VaR on a $10m position, both of which look like real numbers downstream.
- **Silent NaN**: every comparison against `NaN` is `False`, so a `NaN` spread raises no spike, a `NaN` depth raises no collapse, and the portfolio reports healthy while its L-VaR is `NaN`. Reject non-finite inputs at the boundary.
- **Static ADV assumptions**: a 30-day historical ADV measured in calm markets is the wrong denominator during a panic, when the active liquidity that produced it has withdrawn.
- **Ignoring depth collapse**: spread can stay tight while the size behind it evaporates. Quoted price without quoted size is not liquidity.
- **Underestimating fire-sale slippage**: assuming linear, frictionless unwinding of a concentrated position. Measured equity impact is *concave* in trade rate — Almgren et al. (2005) estimate a 3/5 power law and explicitly reject the square-root exponent — so no single per-day coefficient is right across all sizes.
- **Double-counting a symbol**: passing two lots of the same symbol doubles its notional, its liquidation cost, and its L-VaR contribution. Net the lots first; the engine raises on duplicates.
- **Reading `NO_POSITIONS` as healthy**: an unassessed book and a liquid book are different states.
- **Comparing L-VaR against a VaR from a different model**: `baseline_var_usd` must share confidence level, horizon and currency with everything it is compared to, or the add-on is measured against a moving baseline.

## Verification

- Instantiate `RealTimeLiquidityMonitorEngine()`. Audit a single position of $100{,}000$ shares @ $\$100$ with $\text{ADV} = 200{,}000$, spread $0.25$ vs normal $0.05$, depth $2{,}000$ vs normal $10{,}000$, and `baseline_var_usd=100000.0`: verify $\text{DTL} = 5.0$ days, `spread_ratio` $= 5.0$, `depth_drop_pct` $= 80.0$, and `status == "LIQUIDITY_RISK_ALERT"`.
- Verify the cost decomposition by hand on the liquid case ($10{,}000$ shares @ $\$100$, $\text{ADV} = 500{,}000$, spread $0.05$): $\text{DTL} = 0.2$, `spread_cost_usd` $= 0.5 \times 10^6 \times 0.0005 = \$250$, `impact_cost_usd` $= 0.5 \times 10^6 \times 0.10 \times 0.2 = \$10{,}000$, `portfolio_l_var_usd` $= \$110{,}250$.
- Boundary check: $\text{DTL} = 2.0$, spread ratio $= 2.0$, depth drop $= 50\%$ exactly must flag all three.
- Negative checks: `NaN`/`inf` in any field, `adv <= 0`, `current_price <= 0`, `normal_spread <= 0`, `normal_l2_depth <= 0`, a negative spread or depth, a blank symbol, a duplicate symbol, a negative or missing `baseline_var_usd`, and `positions=None` must each raise.
- Run `python scripts/test_real_time_liquidity_monitor.py` and confirm a 100% pass rate.

## Related Skills

- `liquidity-adjusted-position-sizing`
- `portfolio-stress-test-including-liquidity-crunch-scenarios`
- `value-at-risk-var-live-monitoring`
- `kill-switch-and-drawdown-circuit-breakers`
