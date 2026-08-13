# Backtesting Methodology Standards — backtest-determinism-and-reproducibility

## Determinism requirements

| Requirement | Mechanism | Verification criteria |
|---|---|---|
| String hash stability | `PYTHONHASHSEED` set in the environment **before** interpreter start | `check_hash_seed()` returns True; set iteration order stable across processes |
| Random number seeding | `random.seed(S)`, NumPy legacy global, torch when present | Identical draw sequences across runs |
| Modern NumPy RNG | Explicitly seeded `Generator` via `make_numpy_generator()` | Two engines with the same seed produce identical draws |
| Event stream order | Total sort on `(timestamp, symbol, sequence_id)`, all keys required, ties rejected | Result independent of input order |
| Time source | `SimulatedClock` injected; no `time.time()` in strategy logic | Clock never moves backwards |
| Divergence detection | SHA256 over exact IEEE-754 float bits (`float.hex()`) | Sub-ulp differences reported as divergent |

## Why exact float bits, not rounded decimals

The signature of non-determinism in numerical code is a difference in
*accumulation order*, not a large numeric error. Two mathematically equivalent
expressions differ in the last bits:

```
sum([0.1] * 10) -> 0.9999999999999999
0.1 * 10        -> 1.0
```

Floating-point addition is not associative, so a different reduction order —
from a different BLAS thread count, a different SIMD path, or a different
library version — produces exactly this kind of difference. Rounding to six
decimals before hashing maps both values to `1.000000` and reports the runs as
identical. Hashing `float.hex()` preserves every bit and reports them as
divergent, which is the whole point of the exercise.

`float_precision` is available for callers who deliberately want tolerant
comparison; it warns on construction that divergences below the chosen
threshold will be reported as identical.

## Limits of reproducibility

Bit-identical results **cannot be guaranteed in general**. PyTorch's
reproducibility documentation states that "completely reproducible results are
not guaranteed across PyTorch releases, individual commits, or different
platforms", that results may not be reproducible "between CPU and GPU
executions, even when using identical seeds", and that the available steps only
"limit the number of sources of nondeterministic behavior for a specific
platform, device, and PyTorch release."

Treat determinism as a property of a **pinned environment**. Within it, this
skill removes the controllable sources and detects the rest. Outside it,
divergence is expected and is an environment-pinning problem — see
`dependency-pinning-and-reproducible-builds`.

## PYTHONHASHSEED semantics

Python documents that if the variable "is not set or set to `random`, a random
value is used to seed the hashes of str and bytes objects", that an integer
value "is used as a fixed seed for generating the hash() of the types covered by
the hash randomization", and that "specifying the value 0 will disable hash
randomization."

Because environment variables are processed at interpreter startup, the value
**cannot be changed from inside a running process**. Assigning
`os.environ["PYTHONHASHSEED"]` succeeds and reads back correctly while changing
nothing — which is why `check_hash_seed()` only reports.

Observed effect on a symbol universe (`{'AAPL','MSFT','GOOG','TSLA'}`,
CPython 3.11):

| PYTHONHASHSEED | Iteration order |
|---|---|
| 0 | MSFT, AAPL, GOOG, TSLA |
| 1 | AAPL, TSLA, GOOG, MSFT |
| 2 | GOOG, AAPL, TSLA, MSFT |

Dicts are unaffected — insertion order has been preserved since Python 3.7.
Sets are not. Iterate a sorted list when order affects results.

## NumPy seeding

NumPy documents `numpy.random.seed` as "a convenience, legacy function that
exists to support older code that uses the singleton RandomState", and states
that "best practice is to use a dedicated `Generator` instance rather than the
random variate generation methods exposed directly in the random module."

Consequence for determinism: seeding the global does **not** affect any
`Generator` produced by `np.random.default_rng()`. A backtest mixing the two
will appear seeded while remaining non-deterministic. `make_numpy_generator()`
returns a `Generator` seeded from the master seed for explicit threading.

## Checksum scope

The trade-log checksum is an **unkeyed SHA256**. It detects accidental
divergence between runs. It is not tamper-evidence: anyone able to edit a trade
log can recompute the digest. For an authenticated record, see
`backtest-audit-trail-for-regulatory-review`.

Earlier revisions of this skill specified `MD5(trade_list_json)` in the workflow
while the implementation used SHA256, and described the result as a
"cryptographic audit checksum". The documentation now matches the code, and the
framing is corrected: this is a divergence detector, not an audit signature.

## Sources

| Claim | Source |
|---|---|
| PYTHONHASHSEED semantics; 0 disables hash randomization; env vars processed at startup | Python docs, *Command line and environment* — https://docs.python.org/3/using/cmdline.html#envvar-PYTHONHASHSEED |
| `numpy.random.seed` is legacy; prefer a dedicated `Generator` | NumPy docs, *numpy.random.seed* — https://numpy.org/doc/stable/reference/random/generated/numpy.random.seed.html |
| Completely reproducible results are not guaranteed across releases, commits, platforms, or CPU/GPU | PyTorch docs, *Reproducibility* — https://docs.pytorch.org/docs/stable/notes/randomness.html |
| Dict insertion-order preservation (3.7+) and set order variation | Verified empirically on CPython 3.11.4 (table above) |

## Category

`backtesting-methodology` — see top-level `mappings/` directory.
