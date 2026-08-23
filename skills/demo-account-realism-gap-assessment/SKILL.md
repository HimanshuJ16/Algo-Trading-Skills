---
name: demo-account-realism-gap-assessment
description: Use when evaluating trading performance on paper/demo broker accounts
  to systematically compare fill latency, slippage, queue depth, and partial fill
  rates against live production executions, calculating a realism reliability index.
domain: algorithmic-trading
subdomain: broker-integration
tags:
- broker-integration
- demo-account
- paper-trading
- realism-gap
- slippage-analysis
- execution-fidelity
brokers_frameworks:
- Broker Environment Assessor
- Python Analytics
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill prior to deploying a paper-tested trading strategy into live capital. Broker paper/demo accounts systematically inflate performance metrics by granting instant fills, ignoring limit order queue depth, suppressing market impact, and under-reporting slippage. This skill records matched execution logs from demo and live trades to quantify the execution realism gap and apply Sharpe ratio haircut factors.

## When NOT to Use

- **As the only gate on a live promotion decision.** $R$ measures execution fidelity alone. It says nothing about selection bias or backtest overfitting — for that, use the Deflated Sharpe Ratio (Bailey & López de Prado, 2014), which corrects a different and equally fatal source of inflation. The two are complementary, not substitutes.
- **With few matched executions.** A score computed from a handful of fills is indicative only; the assessor flags `is_sample_sufficient=False` below `min_samples` and you should not size capital on a flagged result.
- **Across mismatched trading conditions or instruments.** Demo fills from a quiet midday session compared against live fills at the open measure the session difference, not the environment difference. The same applies to differing symbol sets, which the assessor flags in `warnings` rather than silently scoring.
- **As a substitute for a live micro-lot pilot.** The live logs are the ground truth here; if you have none, this skill has nothing to compare against and cannot manufacture a baseline.
- **To present demo results as achieved performance.** Simulated/hypothetical results carry mandatory disclosure obligations in some jurisdictions — see `references/standards.md`.

## Prerequisites

- Execution logs from paper/demo environments: environment label, symbol, **side (BUY/SELL)**, arrival mid-price, fill price, requested and filled quantity, submission and fill timestamps.
- Sample execution logs from live micro-lot trades for baseline comparison, in the same schema and labelled `LIVE`.
- Both sets must be finite, positively priced, and non-transposed; the assessor rejects degenerate records rather than scoring them (see Workflow step 1).

## Workflow

1. **Record and Validate Execution Logs**:
   - Capture side, order submission timestamp, fill timestamp, arrival mid-price, fill price, requested quantity, and filled quantity.
   - Validation is fail-closed by design. Transposed demo/live lists, non-finite or non-positive prices, `filled_qty` outside `[0, requested_qty]`, and a `fill_time` preceding `submission_time` all raise `ValueError`. Each of these previously produced a *higher* realism score from worse data, which is the dangerous direction for a control that gates capital.

2. **Compute Latency & Slippage Discrepancy**:
   - Latency Delta: $\Delta t = \text{Latency}_{\text{live}} - \text{Latency}_{\text{demo}}$.
   - Slippage is **signed by trade direction**, following the Perold implementation-shortfall convention: for a BUY, filling above arrival is a cost; for a SELL, filling below arrival is a cost. Positive = adverse, negative = price improvement.
     $$\Delta P = \text{Slippage}_{\text{live}} - \text{Slippage}_{\text{demo}}$$
   - Do **not** take absolute values. Demo environments routinely fill at or better than the arrival mid; under an absolute-value comparison that price improvement cancels live adverse cost and the environments look identical when they are furthest apart.

3. **Calculate Realism Score ($R \in [0, 1]$)**:
   - Composite index weighing latency parity (30%), slippage match (40%), and fill rate ratio (30%).
   - The slippage term decays as $\exp(-\max(0, \Delta P) / \text{slippage\_decay\_bps})$. A demo that is *more* adverse than live is conservative and is not penalised.
   - Weights, the decay scale, and the promotion threshold are tunable conventions, not derived or mandated values — see `references/standards.md`.

4. **Apply the Sharpe Discount (heuristic)**:
   - Scale down paper/demo return expectations prior to live allocation:
     $$\text{Sharpe}_{\text{adjusted}} = \text{Sharpe}_{\text{demo}} \times R \quad (\text{when } \text{Sharpe}_{\text{demo}} > 0)$$
   - **A non-positive demo Sharpe is returned unchanged.** Multiplying a negative Sharpe by $R < 1$ moves it toward zero, so a losing strategy would appear to *improve* the less realistic its execution was. The assessor emits a warning instead.
   - `unadjusted_demo_sharpe` is a required argument. There is no defensible default: a fabricated input yields a fabricated allocation recommendation.
   - Treat this discount as a heuristic sizing haircut, not a validated estimator — it has no formal literature basis.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Assuming Demo Fills Equal Live Fills**: Relying on 100% demo fill rates for illiquid options or micro-cap stocks.
- **Ignoring Time-of-Day Volatility**: Comparing demo fills during quiet market hours against live fills during market open/close.
- **Unmapped Order Types**: Demo accounts executing complex bracket/stop-loss orders differently than live engines.
- **Taking the Absolute Value of Slippage**: Demo fills at or better than the arrival mid produce *price improvement*. Comparing $|{\cdot}|$ magnitudes lets that improvement offset live adverse cost, so a demo that is maximally optimistic scores as near-perfect parity. Always sign slippage by side.
- **Scaling a Negative Sharpe by $R$**: The discount is multiplicative, so applying it to a losing demo strategy moves the Sharpe *toward zero* — the worse the execution realism, the better the strategy looks. Only discount a positive Sharpe.
- **Letting Bad Data Score as Parity**: A zero live latency, a zero arrival price, or a `NaN` fill silently produced the *best* possible sub-score in a naive implementation. Reject degenerate records; never let missing data read as realism.
- **Scoring a Handful of Fills**: Two demo and two live executions yield a confident-looking $R$ with no statistical support. Check `is_sample_sufficient` before acting on the number.
- **Transposing Demo and Live Logs**: Passing them in the wrong order inverts every ratio and yields $R = 1.0$ — a perfect score that greenlights full deployment. The `environment` label on each record is validated against the list it is supplied in.

## Verification

- Input sample demo and live execution logs and verify Realism Score $R$ calculation against the hand-derived weighting.
- Confirm a demo log showing price improvement (`fill_price` better than `arrival_price`) *lowers* $R$ rather than raising it.
- Confirm a negative `unadjusted_demo_sharpe` is returned unchanged, with a warning.
- Confirm transposed demo/live lists, non-finite prices, and zero live latency all raise `ValueError`.
- Run `python -m unittest discover -s skills/demo-account-realism-gap-assessment/scripts` and confirm 100% pass rate.

## Related Skills

- `sandbox-vs-production-endpoint-drift`
- `paper-to-live-promotion-checklist`
- `execution-realistic-simulation`
---
