---
name: real-time-liquidity-risk-monitoring
description: >-
  Real-time portfolio liquidity risk monitor tracking Days to Liquidate (DTL), bid-ask spread spikes, order book depth collapse, and Liquidity-Adjusted VaR (L-VaR).
domain: Risk Governance & Real-Time Analytics
subdomain: Real-Time Liquidity Risk & Market Impact
tags: ["liquidity-risk", "days-to-liquidate", "dtl", "spread-spike", "order-book-depth", "l-var", "market-impact"]
brokers_frameworks: ["Basel III/IV Liquidity Risk Standards", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when managing institutional portfolios subject to market liquidity shocks and position concentration risks. Liquidity risk occurs when a firm cannot unwind positions without incurring prohibitive slippage. This engine monitors real-time Days to Liquidate ($\text{DTL} = \frac{\text{Position}}{\text{Cap} \cdot \text{ADV}}$), detects bid-ask spread spikes ($\text{Spread} > 2 \times \text{Normal}$), tracks L2 order book depth drops ($> 50\%$), and calculates Liquidity-Adjusted Value-at-Risk (L-VaR).

## Prerequisites

- Position liquidity data (`symbol`, `position_size`, `current_price`, `adv`, `bid_ask_spread`, `l2_depth_top3`, `normal_spread`, `normal_l2_depth`).
- Config options (`max_dtl_threshold_days`: default 2.0, `max_participation_pct`: default 0.10, `spread_spike_threshold_ratio`: default 2.0).

## Workflow

1. **Days to Liquidate (DTL) Calculation**:
   - Compute DTL per symbol: $\text{DTL}_i = \frac{\text{PositionSize}_i}{\text{MaxParticipationPct} \cdot \text{ADV}_i}$.
   - Flag position if $\text{DTL}_i > \text{MaxDTLThreshold}$.
2. **Spread Spike & Depth Collapse Audit**:
   - Compute $\text{SpreadRatio} = \frac{\text{CurrentSpread}}{\text{NormalSpread}}$. Flag if $> 2.0x$.
   - Compute $\text{DepthDropPct} = 1.0 - \frac{\text{CurrentDepth}}{\text{NormalDepth}}$. Flag if $> 50\%$.
3. **Liquidity-Adjusted VaR (L-VaR)**:
   - Compute $\text{L-VaR} = \text{VaR} + \frac{1}{2} \cdot \text{Notional} \cdot \left(\text{RelativeSpread} + \text{ImpactCoeff} \cdot \frac{\text{Position}}{\text{ADV}}\right)$.
4. **Audit Report Generation**: Output structured `RealTimeLiquidityReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Static ADV Assumptions**: Using 30-day historical ADV during market panic when active liquidity collapses.
- **Ignoring Depth Collapse**: Tracking bid-ask spread while ignoring L2 order book volume thinning.
- **Underestimating Fire-Sale Slippage**: Assuming linear unwinding costs when liquidating large concentrated positions.

## Verification

- Instantiate `RealTimeLiquidityMonitorEngine`. Input position ($100,000$ shares @ $\$100$, $\text{ADV}=200,000$, $\text{ParticipationCap}=10\% \implies \text{DTL}=5.0$ days, $\text{Spread}=0.50$ vs normal $0.10 \implies 5.0x$ spike) $\implies$ verify `LIQUIDITY_RISK_ALERT` status, DTL breach flagged, spread spike detected, and L-VaR calculated.
- Run `python scripts/test_real_time_liquidity_monitor.py`.

## Related Skills

- `liquidity-adjusted-position-sizing`
- `portfolio-stress-test-including-liquidity-crunch-scenarios`
---
