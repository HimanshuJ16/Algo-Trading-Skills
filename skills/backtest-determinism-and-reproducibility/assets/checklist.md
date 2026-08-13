# Pre-Flight / Sign-off Checklist — backtest-determinism-and-reproducibility

Use this before considering the skill's implementation complete.

## Environment (must be pinned before any comparison is meaningful)
- [ ] `PYTHONHASHSEED` set **in the environment before the interpreter starts** (`PYTHONHASHSEED=0 python backtest.py`)
- [ ] No code path attempts to set `PYTHONHASHSEED` from inside Python — it silently does nothing
- [ ] `check_hash_seed()` returns True in the run being audited
- [ ] Python, NumPy, and BLAS versions pinned
- [ ] Thread counts fixed (`OMP_NUM_THREADS`, `OPENBLAS_NUM_THREADS`) — a different reduction order changes the last bits
- [ ] Understood that bit-identical results are **not** guaranteed across platforms, versions, or CPU/GPU, regardless of seeding

## Seeding
- [ ] `random`, NumPy legacy global, and torch (if used) seeded from one master seed
- [ ] **Every `np.random.default_rng()` call site** is seeded explicitly via `make_numpy_generator()` — seeding the NumPy global does not reach them
- [ ] Monte Carlo / slippage / execution-timing perturbations draw from a seeded generator
- [ ] No RNG is constructed from OS entropy anywhere in the backtest path
- [ ] Audit-only usage constructs the engine with `apply_seeds_on_init=False` rather than silently reseeding global state

## Ordering
- [ ] Event stream sorted on `(timestamp, symbol, sequence_id)` before replay
- [ ] All three sort keys present on every event — no defaulting
- [ ] `sequence_id` values distinct enough that no duplicate sort key exists
- [ ] No iteration over a `set` where order affects results (sets vary per process; dicts are insertion-ordered and safe)
- [ ] Symbol universes iterated as sorted lists

## Time
- [ ] `SimulatedClock` injected; no `time.time()`, `datetime.now()`, or `time.monotonic()` in strategy logic
- [ ] `time` module **not** monkey-patched globally
- [ ] Clock advanced per event; backwards jumps surface as errors

## Divergence detection
- [ ] Checksums computed over **exact float bits** — `float_precision` left as `None`
- [ ] If `float_precision` is set, the resulting blindness to sub-threshold divergence is understood and accepted in writing
- [ ] Non-finite values rejected rather than serialised (two NaN runs would otherwise hash identically)
- [ ] Backtest run twice in CI with the build failing on divergence
- [ ] `first_divergence_index` surfaced in the failure message so the offending trade is locatable
- [ ] Changing the master seed produces a different checksum (proves the seed is actually wired in)

## Reporting
- [ ] `net_cash_flow` understood as signed cash effect — equal to realised P&L only when the position is flat
- [ ] Checksum **not** presented as tamper-evidence; it is an unkeyed digest anyone can recompute
- [ ] For an authenticated audit record, `backtest-audit-trail-for-regulatory-review` used instead

## Automated testing
- [ ] Run `python scripts/test_reproducibility_engine.py` — 40 tests, 100% pass rate
- [ ] Confirmed the key regression: `sum([0.1]*10)` vs `0.1*10` as prices reports `is_bit_identical = False`

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment pinned and recorded (Python / NumPy / BLAS / thread counts): ___________________________
