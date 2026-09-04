---
name: order-book-imbalance-signal-pipeline
description: >-
  Use when a strategy trades on the relative size of resting queues. Computes signed
  top-of-book and depth-aggregated imbalance plus the imbalance-weighted mid, rejecting
  crossed books and out-of-order updates before a signal reaches execution.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: real-time-architecture
  tags: order-book-imbalance, queue-imbalance, weighted-mid-price, micro-price, l2-book, hft-signals, fast-path
  brokers_frameworks: "Generic L2 Depth Feed; Python Dataclasses; Python Standard Library"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when a strategy consumes Level-2 depth and acts on the *relative
size of the resting queues* — market making, short-horizon order book momentum,
passive-order repricing, or execution timing inside a larger parent order. The
engine computes two things and is careful about the difference between them:

- **Signed queue imbalance** $I = \dfrac{V_{\text{bid}} - V_{\text{ask}}}{V_{\text{bid}} + V_{\text{ask}}} \in [-1, +1]$, optionally aggregated over $N$ price levels per side.
- **Imbalance-weighted mid-price** $W = \dfrac{V_{\text{bid}} P_{\text{ask}} + V_{\text{ask}} P_{\text{bid}}}{V_{\text{bid}} + V_{\text{ask}}}$ — a heavy bid queue pulls the fair price toward the ask.

Queue imbalance is one of the few microstructure features with a documented,
replicated one-tick-ahead relationship to the direction of the next mid-price
move (Gould & Bonart, 2015, on 10 liquid Nasdaq names).

The engine's real job is **refusing to sign off on a book it cannot trust**. It
sits between a feed handler and an execution worker, so a `NaN` volume, a
negative size, a crossed book or an out-of-order update must not leave here
wearing a directional signal. Every such update returns `UNRELIABLE` with `None`
numerics and is counted by kind.

## When NOT to Use

- **Expecting sub-microsecond generation from this code.** Measured on the
  reference platform (CPython 3.11, commodity laptop, warm loop): ~1.9 µs per
  `process_l2_update` call, versus ~1.4 µs for the same arithmetic with no
  validation. Sub-microsecond OBI is a C++/Rust/FPGA result. Use this for
  research, replay and moderate-rate live paths; see
  `binary-protocol-parsing-for-low-latency-feeds` and
  `memory-mapped-ring-buffer-for-ultra-low-latency` for the latency work itself.
- **As the order book itself.** This consumes an already-maintained book. Depth
  mutation, thread safety and L3 order-ID bookkeeping belong to
  `order-book-depth-processing-l2-l3`; snapshot/delta resynchronisation to
  `market-data-snapshot-plus-delta-reconciliation`.
- **As a research harness.** There is no forward-return, Information Coefficient
  or hit-ratio machinery here — that is
  `order-book-microstructure-signal-research`. Calibrate the threshold there,
  then run it here.
- **On a venue whose displayed depth you have no reason to trust.** OBI reads
  *displayed* size, which is exactly what layering manipulates. See the pitfalls.
- **Across threads on one instance.** The engine holds per-symbol timestamp state
  and takes no lock. Shard by symbol or synchronise upstream.
- **As Stoikov's micro-price.** $W$ is the weighted mid, not
  $P_{\text{micro}} = M + g(I, S)$. It is not a martingale and needs no
  calibration — and delivers correspondingly less.

## Prerequisites

- A maintained L2 book supplying, per update, the touch prices and queue sizes,
  plus the levels behind the touch if `depth_levels > 1`.
- A **per-instrument calibrated** threshold. `0.60` is the module default, not a
  finding. Gould & Bonart report the imbalance/next-move relationship as
  "considerable" for large-tick stocks and only "moderate" for small-tick ones,
  so a threshold transplanted across tick-size regimes is untested by
  construction. Calibrate in `order-book-microstructure-signal-research`.
- A **chronologically ordered** stream per symbol. The engine flags a regression;
  it cannot reorder.
- Python 3.10+. Standard library only.

## Workflow

1. **Configure the threshold and depth before the first tick, not per tick.**
   - **Decision point — the threshold has no safe default at zero.** $I \ge 0$
     holds for a perfectly balanced book, so a threshold of `0.0` emits
     `HIGH_BUY_PRESSURE` on *every* update, and a negative one does the same. The
     constructor rejects anything outside $(0, 1]$ rather than letting a
     mis-typed config trade.
   - **Decision point — `depth_levels` fixes what the number means.** Aggregating
     3 levels on deep ticks and 1 on thin ones produces a series whose definition
     changes tick to tick. An update carrying less depth than configured is
     rejected as `INSUFFICIENT_DEPTH`, never silently downgraded.

2. **Validate before any arithmetic.** Order matters, because bad values survive
   the obvious guards.
   - A `NaN` volume passes a `total <= 0.0` check — every comparison against
     `NaN` is false — and yields a `NaN` imbalance, a `NaN` weighted mid, and a
     `NEUTRAL` classification.
   - A negative volume stays finite and drives $|I|$ *outside* $[-1, +1]$: a bid
     of $-100$ against an ask of $200$ gives $I = -3.0$, comfortably past any
     threshold, i.e. a maximum-conviction signal from an impossible book.

3. **Reject a crossed or locked book rather than measuring it.**
   - **Decision point — a crossed book is a synchronisation failure, not a
     market state.** $P_{\text{bid}} > P_{\text{ask}}$ means the two sides came
     from different moments. The imbalance stays arithmetically well defined and
     becomes economically meaningless. Locked books ($P_{\text{bid}} =
     P_{\text{ask}}$) are legal on some venues and are opt-in via
     `allow_locked_book`; crossed books are always refused.

4. **Reject a malformed or out-of-order timestamp.** `timestamp_ns` must be a
   non-negative integer: a float `NaN` would be accepted by the ordering
   comparison (every comparison against `NaN` is false), stored, and would then
   disable the check for the following update too. Timestamps are compared inside
   one symbol's own feed clock domain, so this detects reordering only —
   wall-clock staleness is a different problem needing a synchronised host clock
   (`clock-skew-correction-for-tick-timestamps`). A rejected update does not
   advance the per-symbol clock, so one bad tick cannot lock out the ones behind
   it.

5. **Compute imbalance over `depth_levels`, and the weighted mid on the touch
   only.** $W$ is defined on the touch prices; weighting deeper prices by deeper
   sizes produces a different quantity with no standard interpretation.

6. **Classify on the value you report.**
   - **Decision point — never round the reported figure while classifying the raw
     one.** That combination lets a result read `imbalance = 0.60` next to
     `signal_type = NEUTRAL`, and any audit reconciling the two finds a
     contradiction that is not in the data.

7. **Dispatch without exposing the feed loop.** An exception from the strategy
   callback is caught, counted in `callback_error_count` and logged with its
   traceback; the next tick is still processed. `KeyboardInterrupt` and other
   `BaseException`s still propagate, so shutdown works.

8. **Check the report before trusting any aggregate.** `generate_report()`
   returns `OBI_PIPELINE_CLEAN` only when nothing was rejected and no callback
   failed; otherwise `OBI_PIPELINE_DEGRADED` with a per-kind breakdown. A
   non-zero rejection count means any imbalance statistic from that run was
   computed on a partial sample.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Trading displayed depth as if it were committed liquidity.** OBI is computed
  from *displayed* size, which is precisely the quantity layering manipulates —
  and a naive imbalance consumer is the intended counterparty. Spoofing is
  prohibited (in US futures, CEA §4c(a)(5)(C), 7 U.S.C. §6c(a)(5)(C)), but
  enforcement is ex-post and does nothing for a fill you already took. Require
  persistence: an imbalance that appears and vanishes inside a few milliseconds
  is not the same observation as one that rests.
- **Reusing a threshold across instruments.** The predictive strength of queue
  imbalance is tick-size dependent. A `0.60` calibrated on a large-tick index
  future is an untested number on a small-tick equity or a crypto pair, and the
  code cannot tell the difference.
- **Mixing the two imbalance conventions.** This skill uses the signed form
  $I \in [-1, +1]$. Much of the literature — Stoikov included — uses
  $I' = V_{\text{bid}}/(V_{\text{bid}} + V_{\text{ask}}) \in [0, 1]$, where
  $I = 2I' - 1$. A threshold of $0.60$ lifted from a paper using $I'$ is a
  threshold of $0.20$ here: three times looser than intended.
- **Treating weighted-mid divergence from mid as a second signal.**
  $W - M \equiv I \cdot s / 2$ exactly, where $s$ is the spread. It is the
  imbalance times the spread and nothing more; scoring it alongside $I$ double
  counts one observation.
- **Calling $W$ the micro-price and inheriting claims that belong to Stoikov's.**
  Stoikov's micro-price is $M + g(I, S)$, a calibrated martingale estimator that
  outperformed the weighted mid in his tests. $W$ has neither the calibration nor
  the martingale property.
- **Reporting an empty book as `NEUTRAL`.** Zero size on both sides is an absence
  of data, not a balanced market. Collapsing the two means an outage looks like
  a calm market to everything downstream.
- **Deepening the book to "reduce noise".** Levels 2–5 are not a noise filter for
  a tick-horizon signal — top-of-book queue imbalance is the documented one-tick
  predictor, and deeper book shape carries information at *longer* horizons.
  Adding depth changes the horizon the signal speaks to; it does not clean up the
  one you had.
- **Passing the full ladder as `bid_depth` / `ask_depth`.** Those fields hold the
  levels *behind* the touch. Including level 1 double-counts the best queue and
  inflates $|I|$ toward the heavy side; the engine rejects it as
  `MALFORMED_DEPTH` by checking that depth prices move strictly away from the
  touch.
- **Letting a strategy exception kill the feed handler.** A raising callback used
  to propagate straight through `process_l2_update` into the market data loop,
  stopping every symbol because one strategy failed on one tick.
- **Blocking on the signal path.** A DB write, JSON serialisation or a synchronous
  REST call inside the callback puts that latency between every tick and the next
  one. Hand off to a queue and return.

## Verification

- **Threshold guard**: `imbalance_threshold` of `0.0`, `-0.5`, `1.5`, `NaN`,
  `inf` or `True` must raise `OBIConfigurationError` at construction.
- **Known-value case**: bids $800@100.00$ against asks $200@101.00$ gives
  $I = +0.60$ exactly (inclusive `>=` → `HIGH_BUY_PRESSURE`), $W = 100.80$,
  $M = 100.50$, $s = 1.00$.
- **Weighted-mid identity**: for every accepted book, $W = M + I_{\text{top}} \cdot s / 2$
  to floating-point tolerance. This catches a swapped price/volume pairing that a
  restatement of the implementation's own formula would not.
- **Direction**: a heavy bid queue must put $W$ strictly above $M$ and strictly
  below $P_{\text{ask}}$; a heavy ask queue, strictly below $M$ and above
  $P_{\text{bid}}$. One-sided books saturate at $I = \pm 1$ with $W$ collapsing
  onto the far touch.
- **Fail-closed**: negative, `NaN`, `inf`, non-numeric volume; zero, negative or
  `NaN` price; crossed book; locked book (default); empty book; a `NaN`,
  non-integer or negative timestamp; a regressing timestamp — each must return
  `UNRELIABLE` with every numeric field `None`, the matching `rejection_reason`,
  and no callback invocation. `strict=True` must raise `OBIValidationError`
  instead.
- **Report/classification agreement**: bids $79\,999$ against asks $20\,001$ must
  classify `NEUTRAL` *and* report an imbalance strictly below $0.60$.
- **Depth**: `depth_levels=2` with $(800, 200)$ at the touch and $(200, 600)$
  behind it gives $I = 200/1800$; $W$ stays $100.80$; a ladder repeating level 1,
  an out-of-order ladder, one shorter than `depth_levels`, or a generator
  instead of a concrete sequence must all be rejected as data, never raise.
- **Dispatch isolation**: a callback raising `RuntimeError` must not propagate,
  must increment `callback_error_count`, must log at `ERROR`, and must not stop
  the next tick — while `KeyboardInterrupt` from a callback still propagates.
- Run `python -m unittest discover -s skills/order-book-imbalance-signal-pipeline/scripts`
  and confirm 61/61 pass.

## Related Skills

- `order-book-depth-processing-l2-l3`
- `order-book-microstructure-signal-research`
- `microstructure-noise-filtering-for-hf-signals`
- `queue-position-modeling-for-passive-orders`
- `wash-trade-and-spoofing-self-detection`
- `clock-skew-correction-for-tick-timestamps`
- `binary-protocol-parsing-for-low-latency-feeds`
- `tick-to-trade-latency-measurement`
