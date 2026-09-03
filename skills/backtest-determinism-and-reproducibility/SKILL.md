---
name: backtest-determinism-and-reproducibility
description: >-
  Use when identical backtest code and data produce different P&L or Sharpe across runs,
  making optimisation untrustworthy: deterministic seeding, strict input sort order, a
  simulated clock replacing wall-clock reads, and run-to-run divergence detection.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: backtesting-methodology
  tags: backtesting-methodology, reproducibility, determinism, random-seed, bit-identical, audit-checksum, regression-testing
  brokers_frameworks: "Determinism Reproducibility Engine; Python random / hashlib; NumPy"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Invoke this skill when building backtest simulation engines or CI regression suites. If backtest runs produce varying P&L curves or Sharpe ratios across identical code and data, parameter optimization becomes impossible to trust — you cannot tell an improvement from noise. Non-determinism comes from unseeded RNGs, set iteration order, unordered event streams, wall-clock reads, and floating-point accumulation order.

## What This Can and Cannot Guarantee

**It cannot guarantee bit-identical results in general, and no tool can.** PyTorch's reproducibility guidance states plainly that "completely reproducible results are not guaranteed across PyTorch releases, individual commits, or different platforms," and that results may differ between CPU and GPU "even when using identical seeds." The same holds for BLAS thread counts, SIMD code paths, and library versions.

Determinism is a property of a **pinned environment** — same platform, same library versions, same thread configuration. This skill does two things within that envelope:

1. **Eliminates the controllable sources**: seeding, event ordering, clock reads.
2. **Detects divergence when it happens**, by comparing exact float bits.

Point 2 is the part that must not be compromised. Earlier versions of this skill rounded every float to 6 decimals before hashing, which meant the canonical non-determinism signature — `sum([0.1]*10)` (`0.9999999999999999`) versus `0.1*10` (`1.0`) — was reported as bit-identical. A detector that cannot see the thing it was built to detect is worse than no detector, because it produces confident false assurance.

## When NOT to Use

- **As a tamper-evident audit record.** The checksum is an unkeyed SHA256: anyone can edit a trade log and recompute it. For an authenticated record see `backtest-audit-trail-for-regulatory-review`.
- **To compare runs across different environments.** Differing Python/NumPy/BLAS versions or thread counts will legitimately diverge; that is an environment-pinning problem, not something a checksum can fix.
- **To validate strategy correctness.** A perfectly reproducible backtest can be reproducibly wrong — see `lookahead-bias-elimination`.

## Prerequisites

- A master integer seed (e.g. `42`).
- `PYTHONHASHSEED` set **in the environment before the interpreter starts**.
- Pinned library versions and a fixed thread configuration for any run you intend to compare.

## Workflow

1. **Set `PYTHONHASHSEED` before launching Python.** This is the trap that catches people: hash randomization is fixed at interpreter startup, so `os.environ["PYTHONHASHSEED"] = "0"` inside running code does **nothing** while looking like it works. Launch as `PYTHONHASHSEED=0 python backtest.py`. `check_hash_seed()` reports whether the current process is safe; it cannot fix it.
2. **Seed the RNGs — and know what seeding misses.** `apply_master_seeds()` seeds `random`, NumPy's legacy global `RandomState`, and torch when present. NumPy documents `np.random.seed` as a legacy function and recommends a dedicated `Generator`; code calling `np.random.default_rng()` draws fresh OS entropy and stays non-deterministic no matter how often you seed the global. Use `make_numpy_generator()` and thread it through explicitly.
3. **Sort the event stream by `(timestamp, symbol, sequence_id)`.** All three keys are required — a missing timestamp is rejected rather than defaulted to `0.0`, which would silently move that event to the front. Duplicate sort keys are also rejected: Python's sort is stable, so tied events would keep their input order and the result would depend on how the file happened to be read.
4. **Replace wall-clock reads with `SimulatedClock`.** Inject it rather than monkey-patching `time.time()` — patching a global affects every library in the process, including ones whose correctness depends on real time. The clock refuses to move backwards, which surfaces an unsorted stream immediately.
5. **Checksum with exact float bits and compare runs.** `audit_reproducibility()` reports `is_bit_identical` and, on failure, `first_divergence_index` so you can go straight to the offending trade instead of staring at two mismatched hex strings.

> Full procedure: see `references/workflows.md`.
> Standards and sources: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Rounding before hashing.** Any tolerance applied before comparison erases exactly the sub-ulp divergences that signal non-determinism. `float_precision` exists for callers who explicitly want tolerant comparison, and it logs a warning saying what it costs.
- **Setting `PYTHONHASHSEED` from inside Python.** It has no effect. The assignment succeeds, the variable reads back correctly, and set iteration order stays random.
- **Assuming dicts and sets behave alike.** Dicts have preserved insertion order since Python 3.7 and are deterministic. **Sets are not** — iterating a symbol universe as a set gives a different order per process. With `PYTHONHASHSEED` at 0, 1, and 2, `{'AAPL','MSFT','GOOG','TSLA'}` iterates in three different orders. Iterate a sorted list.
- **Seeding only the NumPy global.** `np.random.seed()` does not affect any `Generator` created by `default_rng()`.
- **NaN in a trade log.** `json.dumps` renders every NaN as the same token, so two runs that both corrupted to NaN hash identically and are declared reproducible. Non-finite values are now rejected outright.
- **Unsigned notional mistaken for P&L.** Summing `quantity * price` across a round trip gives gross turnover, not equity: BUY 100@150 then SELL 100@155 sums to 30,500 while the realized P&L is 500. Cash flow must carry the side's sign.
- **Monte Carlo without a fixed seed.** Slippage or execution-timing perturbations reseeded per run make every comparison meaningless.
- **Treating a matching checksum as proof of correctness.** It proves two runs agreed, nothing more.

## Verification

- Run `python -m unittest discover -s skills/backtest-determinism-and-reproducibility/scripts` — 40 tests, 100% pass rate.
- **The test that matters**: feed `sum([0.1]*10)` and `0.1*10` as prices for otherwise identical runs and confirm `is_bit_identical` is `False`. Pre-2.0 this returned `True`.
- Run a backtest twice with identical inputs and confirm matching checksums; change the master seed and confirm the checksum shifts.
- Confirm a missing `timestamp` or a duplicate `(timestamp, symbol, sequence_id)` raises `DeterminismError` rather than sorting silently.
- Confirm `SimulatedClock.advance_to()` rejects a backwards jump.

## Related Skills

- `walk-forward-validation-setup`
- `monte-carlo-strategy-robustness-testing`
- `vectorized-vs-event-driven-backtest-tradeoffs`
- `backtest-audit-trail-for-regulatory-review` — for an authenticated audit record, which this skill's unkeyed checksum is not
- `reproducible-ml-training-pipelines` — the same problem for model training
- `dependency-pinning-and-reproducible-builds` — pinning the environment determinism depends on
---
