---
name: early-exercise-assignment-risk-management
description: >-
  Quantitative options risk engine for detecting early exercise and assignment risk on short American calls/puts, evaluating ex-dividend dividend capture vs extrinsic value, and triggering position roll warnings.
domain: Options & Derivatives Trading
subdomain: Option Assignment Risk & Volatility
tags: ["early-exercise", "assignment-risk", "american-options", "ex-dividend", "extrinsic-value", "covered-calls", "option-greeks"]
brokers_frameworks: ["CBOE Options", "OCC", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in options market making desks, covered call income strategies, and multi-leg option spread management. American-style options allow the option holder to exercise early prior to expiration. Short call position holders face extreme early assignment risk on the eve of an **ex-dividend date** whenever the upcoming dividend amount $D$ exceeds the option's remaining extrinsic value ($D > \text{Extrinsic}$). If assigned, short call holders lose their stock and are forced to pay the dividend out-of-pocket.

## Prerequisites

- Short option position details (`symbol`, `option_type` e.g. `'CALL'`/`'PUT'`, `strike`, `market_price`, `underlying_price`, `days_to_expiry`).
- Dividend event details (`upcoming_dividend_usd`, `days_to_ex_div`).

## Workflow

1. **Intrinsic & Extrinsic Value Computation**:
   - $\text{Intrinsic}_{\text{call}} = \max(0, S - K)$, $\text{Intrinsic}_{\text{put}} = \max(0, K - S)$.
   - $\text{Extrinsic Value} = \text{Market Price} - \text{Intrinsic Value}$.
2. **Ex-Dividend Short Call Audit**:
   - Compare $D_{\text{usd}}$ against $\text{Extrinsic Value}$.
   - If $\text{DaysToExDiv} \le 1.0$ and $D_{\text{usd}} > \text{Extrinsic Value} \implies$ Flag `CRITICAL_ASSIGNMENT_RISK`.
3. **Deep ITM Short Put Audit**:
   - If Put ITM and $\text{Extrinsic Value} \le 0.05 \implies$ Flag `HIGH_ASSIGNMENT_RISK`.
4. **Remediation & Action Dispatch**:
   - Issue `CLOSE_OR_ROLL_SHORT_CALL` directive to buy back or roll the option before market close prior to ex-div date.
5. **Audit Report Generation**: Output structured `EarlyExerciseAuditReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Holding Short Calls Through Ex-Div Eve**: Holding deep ITM short calls when $D > \text{Extrinsic}$, resulting in unexpected assignment and dividend liability payout.
- **Conflating American with European Options**: Fearing early assignment on European-style index options (SPX, NDX), which cannot be exercised prior to expiration.
- **Ignoring Low Extrinsic Value ITM Puts**: Leaving deep ITM short puts unmonitored as interest rates rise, leading to early exercise assignment.

## Verification

- Instantiate `EarlyExerciseRiskEngine`. Submit short call position (Strike = \$100, Underlying = \$105, Option Price = \$5.20 -> Intrinsic = \$5.00, Extrinsic = \$0.20). Upcoming dividend = \$1.00 paid tomorrow. Verify engine flags `CRITICAL_ASSIGNMENT_RISK` ($D = \$1.00 > \text{Extrinsic} = \$0.20$), calculates assignment probability = 100%, and recommends `CLOSE_OR_ROLL_SHORT_CALL`.
- Run `python scripts/test_early_exercise_assignment_risk_management.py`.

## Related Skills

- `dividend-futures-and-forward-modeling`
- `options-chain-data-normalization-across-vendors`
---
