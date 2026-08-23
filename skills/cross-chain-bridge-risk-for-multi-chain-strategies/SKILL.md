---
name: cross-chain-bridge-risk-for-multi-chain-strategies
description: Quantitative crypto risk management module for evaluating cross-chain
  bridge security, wrapped token de-pegging, finality latency SLAs, and enforcing
  in-flight capital caps.
domain: Crypto Risk & DeFi
subdomain: Multi-Chain Bridge Risk
tags:
- cross-chain
- bridge-risk
- de-peg
- wrapped-token
- finality-latency
- in-flight-caps
- stargate
- wormhole
brokers_frameworks:
- DeFi Protocols
- Python Dataclasses
version: "1.2.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when designing multi-chain DeFi arbitrage algorithms, cross-chain yield farming strategies, or multi-chain liquidity rebalancing pipelines. Cross-chain bridges carry unique smart-contract, validator, and wrapped-token de-pegging risks. Chainalysis counted **$2B stolen across 13 cross-chain bridge hacks** as of August 2022 — **69% of all funds stolen in 2022 to that point** — including Ronin (over $600M via five of nine compromised validator keys), Wormhole (~120,000 wETH, over $320M, minted without collateral), and Nomad ($190M).

**Currency of that evidence:** bridges led the theft tables in 2022, but they no longer do. Chainalysis's 2025 figures show centralized-service compromises dominating (88% of Q1 2025 losses; the $1.5B Bybit hack) and DeFi losses suppressed. The caps in this module exist because a bridge exploit is a *tail loss that can take the whole position*, not because bridges are today's leading vector — don't cite the 2022 statistic as a present-day fact.

This module monitors wrapped asset parity ($|P_{\text{wrapped}} - P_{\text{native}}| / P_{\text{native}}$), tracks finality delays, and enforces in-flight capital caps per bridge protocol (default $\le 15\%$ NAV) with lowest-risk (audit-ranked) failover routing.

## When NOT to Use

- **As a substitute for security due diligence.** The audit-score gate is a coarse scalar; deep diligence (audit reports, validator-set composition, upgrade keys) is the `smart-contract-audit-requirements-before-defi-integration` skill's job.
- **For exchange (CEX) transfers.** This governs on-chain bridge risk only; exchange withdrawal risk is a different surface.
- **As a ledger of in-flight capital.** Decisions are advisory and the engine is stateless: an approval does not increment `current_inflight_usd`. If you evaluate transfers in a loop without re-registering the executed balance, every call is measured against the same stale figure and the aggregate NAV cap is never enforced. The caller owns that bookkeeping.
- **Without feed validation.** Dead or malformed price feeds raise errors here by design — wiring this engine to a feed that can return zeros/NaN without handling those errors reintroduces the exact failure this gate exists to prevent.
- **With uncalibrated defaults.** 15% NAV cap / 1.0% de-peg / 120-min SLA are engineering defaults, not validated standards — calibrate to the mandate.

## Prerequisites

- Real-time prices for native assets ($P_{\text{native}}$) and bridge-wrapped assets ($P_{\text{wrapped}}$), validated positive and finite.
- Bridge protocol registry with attributes: `finality_delay_minutes`, `max_nav_pct_cap` (share of NAV, in (0, 1]), `current_inflight_usd`, `audit_score_pct` in [0, 100].
- A post-execution hook that writes the new in-flight balance back via `register_bridge` before the next evaluation.
- **Unit convention:** `max_depeg_threshold_pct` and `audit_score_pct` are percentage points (1.0 = 1%, 95.0 = 95%); `max_nav_pct_cap` is a fraction in (0, 1] (0.15 = 15%). Mixing them is the most likely configuration error.

## Workflow

1. **Bridge Protocol Audit**: Ingest bridge parameters and calculate current in-flight capital. Decision point: if `audit_score_pct` is below the configured floor, the bridge is ineligible for both primary and failover routing.
2. **Wrapped Token De-Peg Audit**:
   - $\text{Depeg Pct} = \left|\frac{P_{\text{wrapped}} - P_{\text{native}}}{P_{\text{native}}}\right| \times 100\%$.
   - If $\text{Depeg Pct} \ge 1.0\% \implies$ trigger `DEPEG_ALERT` and block new transfers — **without rerouting**: the wrapped asset is impaired on every bridge that mints it.
   - The gate compares the **exact** de-peg; the `depeg_pct` on the decision is rounded to 2dp for reporting only. Comparing the rounded figure would silently disable any threshold finer than ~0.005%.
   - Decision point: a zero, negative, or non-finite price is a broken feed — it raises `ValueError`, never a 0.0% "all clear".
3. **Pre-Transfer Risk Evaluation**:
   - For a proposed transfer of amount $V > 0$:
   - Cap check: $\frac{\text{Current In-Flight} + V}{\text{NAV}} > \text{Max Bridge NAV Cap}$ blocks the primary (equality passes).
   - Finality check: $\text{Finality Delay} > \text{Max Allowed SLA Delay}$ blocks the primary (equality passes).
4. **Bridge Routing & Failover**:
   - Route to the **lowest-risk compliant bridge**: candidates are ranked by `audit_score_pct` (descending), then `finality_delay_minutes` (ascending); the first candidate satisfying finality, cap, and audit-floor constraints wins. If none qualifies, the transfer is blocked.
5. **Book the Execution**: after the transfer settles, re-register the selected bridge with its new `current_inflight_usd` (`register_bridge` replaces the profile). Decision point: if the transfer's outcome is unknown — in flight, timed out, unconfirmed — book it as *outstanding* rather than assuming it failed. Treating an unconfirmed transfer as a no-op understates in-flight capital in exactly the situation where the bridge is least trustworthy.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Single Bridge Concentration**: Concentrating most of fund capital in one bridge contract, exposing the fund to total loss upon an exploit — $2B across 13 bridge hacks as of Aug 2022 (Ronin, over $600M, after Lazarus obtained five of nine validator keys; Wormhole, over $320M, by minting ~120,000 wETH without collateral; Nomad, $190M, via a faulty update that let anyone copy the exploit). Wormhole's failure *was* an unbacked-wrapped-token event — precisely what the de-peg gate here watches for.
- **Trusting a Broken Price Feed**: NaN or zero prices previously slipped through de-peg math (`NaN >= threshold` is False) — this engine rejects non-finite/non-positive prices; bypassing that check re-opens the hole.
- **Ignoring Optimistic Rollup Withdrawal Delays**: canonical rollup withdrawals to L1 take **days, not minutes**. Optimism's docs state the Standard Bridge "requires a minimum 7-day wait" (10,080 min); Arbitrum's docs cite a **6.4-day** challenge period on assertions alongside a seven-day safeguard on canonical bridge withdrawals — close, but not the same number, so set the SLA per path rather than assuming a shared 7-day constant. Third-party "fast" bridges bypass the window by trusting an intermediary. This is separate from L2-native finality, which Optimism documents as ~2 s soft and ~15–30 min hard — know which of the three your transfer actually uses.
- **Reading `depeg_pct` as the Gate Input**: the reported figure is rounded to 2dp, the gate is exact, so at the boundary they can disagree — a true 0.999997% de-peg is *approved* against a 1.0% threshold while the decision record shows `depeg_pct = 1.0`. That is correct (the real value is under the threshold), but it reads like a contradiction. When reconciling a decision, recompute from the prices rather than from the rounded field.
- **Booking an Approval as Settled Capital**: an approved decision is a recommendation, not a completed transfer. Incrementing in-flight on approval (rather than on settlement) overstates exposure; never incrementing it understates exposure and defeats the cap entirely.
- **Negative-Amount Cap Evasion**: a negative transfer amount mathematically reduces projected in-flight capital and dodges NAV caps; the engine rejects non-positive amounts.
- **Stale In-Flight Balances**: the engine re-reads `current_inflight_usd` on every call and never writes it. Two $50k evaluations against a $100k balance and a 15% cap on $1M NAV both return "15.0% — approved", while the true post-execution figure is 20%. Re-register between transfers.
- **Unmonitored Wrapped Asset De-pegs**: Continuing to bridge into a wrapped token that is experiencing liquidity drain and de-pegging from native backing.
- **De-peg is asset-level, not bridge-level**: rerouting to another bridge does not cure an impaired wrapped asset — the halt is systemic by design.

## Verification

- Instantiate `CrossChainBridgeRiskManager` (NAV $1M, de-peg threshold 1.0%, SLA 120 min). Register `STARGATE_POOL` (cap 15%, finality 15 min, in-flight $100k, audit 95) and `SYNAPSE_POOL` (cap 15%, finality 20 min, in-flight $20k, audit 90) and `ARBITRUM_CANONICAL` (finality 10,080 min).
  - Native ETH $3,000, wrapped $2,910 → de-peg exactly 3.0% → blocked, no reroute. Correcting wrapped to $2,970.3 (0.99%) with a $20k transfer → approved on STARGATE.
  - $60k to STARGATE ($160k = 16% > 15%) → rerouted to SYNAPSE (8%). $50k to STARGATE ($150k = exactly 15%) → approved (equality passes).
  - Any transfer to ARBITRUM_CANONICAL → rerouted to STARGATE (audit 95 ranks above SYNAPSE's 90, regardless of registration order).
  - NaN, zero, `None`, or string prices → `ValueError`, not approval; `True` is rejected as a transfer amount.
  - With `max_depeg_threshold_pct=0.001`, native $1,000 / wrapped $1,000.04 (a true 0.004% de-peg) → **blocked**, though the reported `depeg_pct` rounds to 0.0.
  - Evaluate $50k to STARGATE three times without re-registering: all three return approved at 15.0% and `current_inflight_usd` stays $100k — the caller's bookkeeping obligation, not an engine bug.
- Run `python -m unittest discover -s skills/cross-chain-bridge-risk-for-multi-chain-strategies/scripts`.

## Related Skills

- `cross-chain-address-reuse-privacy-risk`
- `smart-contract-audit-requirements-before-defi-integration`
