---
name: options-flow-unusual-activity-detection
description: >-
  Use when screening an options trade feed for unusual activity — prints whose size is large relative to the series' open interest and ADV and whose premium is plausibly institutional — and inferring buyer/seller direction from the quote rule, with explicit handling of what OPRA does not tell you.
domain: Quant Research & Alt Data
subdomain: Options Order Flow Analytics & Smart Money Tracking
tags: ["options-flow", "unusual-activity", "vol-to-oi", "options-sweep", "block-trades", "smart-money", "quant-research"]
brokers_frameworks: ["OPRA / Polygon Options Feed", "Python Dataclasses"]
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when building a research signal, alt-data sentiment indicator, or real-time scanner that screens option prints for unusually large trades. The premise is that a print far larger than the series' standing open interest and average daily volume, carrying six figures of premium, is more likely to be an informed institutional position than routine two-sided flow.

Each print is scored on three independent size gates and one direction inference:

- Volume-to-open-interest, $V/OI = \text{PrintVolume} / \text{OpenInterest}$ (default gate $\ge 1.5$).
- Volume-to-ADV, $V/ADV = \text{PrintVolume} / \text{ADV}$ (default gate $\ge 2.0$).
- Premium, $\text{PrintVolume} \times \text{Price} \times \text{ContractMultiplier}$ (default gate $\ge \$100{,}000$).
- Aggressor side from the quote rule: at/above the ask $\implies$ buyer-initiated, at/below the bid $\implies$ seller-initiated.

## When NOT to Use

- **As a directional signal on its own.** The consolidated feed carries no side and no open/close flag (see Prerequisites). Every label here is an inference, and the quote rule is right about 83% of the time on *classifiable* option trades (Savickas & Wilson 2003).
- **On multi-leg, spread, or delta-hedged flow.** Each leg prints separately with no linkage. The long leg of a vertical or a market maker's hedge looks identical to a naked directional bet. Savickas & Wilson found index-option complex trades (15% of their sample) to be the worst-classified subset.
- **With the shipped thresholds unchanged.** $1.5\times$, $2.0\times$ and $\$100{,}000$ are library defaults, not published standards. On SPX or a mega-cap, $\$100{,}000$ of premium is routine; on an illiquid single name the gates may never fire. Calibrate per liquidity tier.
- **On cumulative session volume.** The engine scores one print. Feeding a series' running daily volume changes what every threshold means — by the close, cumulative $V/OI > 1.5$ is ordinary for any active series.
- **As a true sweep detector.** A sweep is one parent order filled across several venues within milliseconds; identifying it requires correlating prints across exchanges. `UNUSUAL_BULLISH_SWEEP` names the classic screen, not a verified multi-venue sweep.
- **As a market-abuse surveillance control.** Insider-trading and manipulation surveillance has its own evidentiary standard — see `eu-market-abuse-regulation-mar-surveillance` and `insider-trading-controls-for-alternative-data-usage`.

## Prerequisites

- Option prints with per-print `volume`, `execution_price`, `option_type`, and `contract_multiplier` (100 for standard US listed equity/index options; OCC contract adjustments and non-US markets can differ, so pass it rather than assume it).
- The **quote in force at the print** (`bid`/`ask`). A stale or non-contemporaneous NBBO produces confidently wrong side labels that the engine cannot detect. Pass `None` when no quote is available — never a zero placeholder.
- Series-level `open_interest` and `adv` for that exact symbol/expiry/strike/right, not the underlying's. Pass `None` when unavailable; the corresponding gate then cannot clear.
- Awareness of what the feed omits. The OPRA Equity and Index Last Sale message (Binary Data Recipient Interface Specification §6.01) carries only header, symbol, expiration, strike, volume, premium price, trade identifier and trading session identifier — **no aggressor side and no opening/closing indicator**. Open interest appears only in the End of Day Summary message (§6.03); OCC computes it overnight and it does not move intraday, so intraday $V/OI$ always compares today's print to yesterday's OI. Buy/sell and open/close broken out by participant type is a separate paid product (Cboe Open-Close Volume Summary).

## Workflow

1. **Validate the print before scoring it.**
   - `volume` must be a positive integer contract count; `execution_price`, `adv`, `contract_multiplier` finite and non-negative (multiplier strictly positive); `option_type` one of CALL/C/PUT/P.
   - **Decision point — missing is not zero.** A series whose OI or ADV was not supplied must be passed as `None`, not `0`. `None` marks the gate *unevaluable* (it cannot clear, and the print is not flagged); `0` means a genuine zero, which yields an infinite ratio and does clear. Conflating the two turns a data outage into a wave of false positives.

2. **Compute the three metrics.**
   $$V/OI = \frac{V}{OI}, \qquad V/ADV = \frac{V}{ADV}, \qquad \text{Premium} = V \times P \times M$$
   - **Decision point — do not substitute volume for an undefined ratio.** With $OI = 0$ the ratio is infinite, not "equal to the volume": scoring a contract count against a ratio threshold would flag any 2-contract print on a newly listed series.
   - The multiplier $M$ is a per-series term. Hard-coding 100 misprices adjusted contracts and every non-US market.

3. **Infer the aggressor side from the quote rule.**
   - Price $\ge$ ask $\implies$ `BUY_AT_ASK`; price $\le$ bid $\implies$ `SELL_AT_BID`; strictly inside $\implies$ `MID_MARKET`.
   - **Decision point — no quote means no side.** A missing quote, a non-positive ask, or a crossed quote ($bid > ask$) yields `UNCLASSIFIED`. Never default to a buy: with a zero-filled quote every print satisfies "price $\ge$ ask", and an entire quote outage reads as aggressive institutional buying.

4. **Classify only when all three gates clear.**
   - Gates cleared + `BUY_AT_ASK` $\implies$ `UNUSUAL_BULLISH_SWEEP` (call) / `UNUSUAL_BEARISH_SWEEP` (put).
   - Gates cleared + `SELL_AT_BID` $\implies$ `UNUSUAL_BEARISH_BLOCK` (call) / `UNUSUAL_BULLISH_BLOCK` (put) — *only* if the print opens a position, which the feed cannot confirm.
   - Gates cleared + `MID_MARKET` $\implies$ `UNUSUAL_FLOW_NEUTRAL`; + `UNCLASSIFIED` $\implies$ `UNUSUAL_FLOW_UNCLASSIFIED`. Both set `direction_is_inferred = False`: size is real, direction is not evidence. Exclude them from sentiment aggregation rather than counting them as neutral.
   - Any gate not cleared $\implies$ `ROUTINE_FLOW`.

5. **Emit the audit report.** `OptionsFlowAnomalyReport` carries the three metrics, `gates_passed`, `gates_unevaluable`, the aggressor side, `direction_is_inferred`, and the human-readable `audit_notes` line. Use `scan()` for a batch: it logs and skips an unscoreable print instead of aborting the feed.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Treating a missing quote as a buy.** With `bid = ask = 0.0`, `price >= ask` is true for every print, so a quote outage produces a flood of "aggressive bullish sweeps" precisely when the data is least trustworthy. A quote that is absent, non-positive, or crossed must yield `UNCLASSIFIED`, and an unclassified print must never carry a directional label.
- **Substituting volume for an undefined ratio.** Falling back to $V/OI = V$ when $OI = 0$ mixes units: a 2-contract print on a newly listed series then "clears" a $1.5\times$ ratio gate.
- **Hard-coding the 100 multiplier.** OCC adjustments for splits, mergers and spin-offs can change a series' deliverable and multiplier, and non-US markets use their own contract sizes. A wrong multiplier misstates premium by an order of magnitude in both directions.
- **Reading V/OI as an opening-interest measurement.** Open interest is computed overnight by OCC and is fixed for the session, so $V/OI$ compares today's print against yesterday's OI. It shows only that *some* of the print must have been opening — a large buy at the ask can still be a short being bought back.
- **Confusing delta hedges and spread legs for directional speculation.** A market maker hedging inventory, or the long leg of a vertical, prints exactly like a naked bet. Volume filters cannot separate them; only linked-leg or open/close data can.
- **Failing to identify the aggressor side at all.** Assuming every large print is a buy inverts the signal on roughly half of it.
- **Trusting the labels as if they were observed.** The quote rule signs 83% of classifiable option trades correctly, and outside-quote and reversed-quote prints are systematically misclassified. Aggregate many prints; do not trade one.
- **Leaving the defaults in place across liquidity tiers.** The same $\$100{,}000$ gate is noise on SPX and unreachable on a thin single name.

## Verification

- Instantiate `OptionsFlowUnusualActivityDetectionEngine()`. Score a print of 5,000 contracts on 1,000 OI and 1,000 ADV at the $\$5.00$ ask ($V/OI = 5.0$, $V/ADV = 5.0$, premium $\$2{,}500{,}000$): expect `UNUSUAL_BULLISH_SWEEP`, all three entries in `gates_passed`. A 10-contract print on 5,000 OI inside a $\$1.95/\$2.05$ spread: expect `ROUTINE_FLOW`.
- Boundary: 1,500 contracts on 1,000 OI and 750 ADV at $\$0.6\overline{6}$ sits exactly on all three gates ($1.5\times$, $2.0\times$, $\$100{,}000$) and must flag; 1,499 on 1,000 OI must not.
- Negative checks: the same large print with `bid=ask=None` (or `0.0`) must return `UNUSUAL_FLOW_UNCLASSIFIED`, not a sweep; `open_interest=None` must leave `v_oi` in `gates_unevaluable` and `is_unusual` false; `open_interest=0` on a 2-contract, $\$200$-premium print must not flag; `contract_multiplier=10` must divide the premium by ten; and zero/negative volume, negative or non-finite prices, a zero multiplier, an unknown option type, and a non-positive threshold in `config` must each raise `ValueError`.
- Run `python scripts/test_options_flow_unusual_activity_detection.py`.

## Related Skills

- `options-chain-data-normalization-across-vendors`
- `options-backtesting-with-realistic-iv-surface`
- `order-book-microstructure-signal-research`
- `wash-trade-and-spoofing-self-detection`
