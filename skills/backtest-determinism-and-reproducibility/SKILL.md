---
name: backtest-determinism-and-reproducibility
description: Use when designing backtest execution frameworks to enforce deterministic
  random seeding, sort input data streams strictly, eliminate async race conditions,
  and verify bit-identical P&L results via cryptographic audit checksums.
domain: algorithmic-trading
subdomain: backtesting-methodology
tags:
- backtesting-methodology
- reproducibility
- determinism
- random-seed
- bit-identical
- audit-checksum
- regression-testing
brokers_frameworks:
- Determinism Reproducibility Engine
- Python NumPy
- Random
version: '1.0'
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when building backtest simulation engines or automated CI/CD regression test suites. If backtest runs produce varying P&L curves or Sharpe ratios across identical code and data inputs, strategy parameter optimization becomes impossible to trust. Non-determinism stems from unseeded pseudo-random number generators, unordered dictionary iteration, async thread race conditions, or unanchored system clocks. This skill guarantees 100% bit-identical backtest results.

## Prerequisites

- Master integer random seed $S_{\text{master}}$ (e.g. `seed=42`).
- Fixed sorting specification for input bar/tick event streams.

## Workflow

1. **Inject Global Master Random Seeds**:
   - Enforce fixed seeding across all RNG libraries (`random.seed(S)`, `numpy.random.seed(S)`, `torch.manual_seed(S)`).
   - Set environment variable `PYTHONHASHSEED=0`.

2. **Enforce Deterministic Data Stream Sorting**:
   - Sort input event streams strictly by `(timestamp, symbol, sequence_id)`. Resolve identical-timestamp ties deterministically by symbol name.

3. **Replace System Clocks with Simulated Time**:
   - Rebind `time.time()` calls to simulated bar/tick event timestamps ($T_{\text{sim}}$).

4. **Generate Cryptographic Audit Checksum**:
   - Compute SHA256/MD5 hash of resulting trade execution logs and equity curve array:
     $$\text{Hash}_{\text{audit}} = \text{MD5}(\text{trade\_list\_json})$$
   - Assert $\text{Hash}_{\text{run1}} \equiv \text{Hash}_{\text{run2}}$.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Unseeded Monte Carlo Simulations**: Running slippage or execution timing perturbations without fixing random seed states across runs.
- **Unordered Set/Dict Iteration**: Iterating over Python sets or dict keys when iterating strategy universe symbols, introducing non-deterministic execution order.
- **Calling System Clock `time.time()`**: Using live wall-clock timestamps inside strategy signal generation logic instead of simulated event time.

## Verification

- Execute backtest twice with identical inputs, verifying bit-identical trade log checksums ($\text{MD5}_1 == \text{MD5}_2$).
- Change master random seed and verify deterministic checksum shift.
- Run `python scripts/test_reproducibility_engine.py` and confirm 100% pass rate.

## Related Skills

- `walk-forward-validation-setup`
- `monte-carlo-strategy-robustness-testing`
- `vectorized-vs-event-driven-backtest-tradeoffs`
---
