---
name: feed-handler-canary-deployment
description: >-
  Use when releasing a new market data parser and first live exposure should cover a
  slice of symbols only. Both decoders run in parallel, prices are audited tick by tick
  against the incumbent, and divergence triggers auto-rollback.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: real-time-architecture
  tags: real-time-architecture, canary-deployment, feed-handler, symbol-routing, comparative-audit, auto-rollback, zero-downtime
  brokers_frameworks: "Canary Feed Router; Python Real-Time Engine"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Invoke this skill when upgrading a market data feed handler — a new ITCH/MDP/FIX parser
version, a rewritten WebSocket client, a changed normalisation or scaling step — and you
want the first live exposure to that code to cover a slice of the universe rather than
all of it. Both the incumbent ($V_{\text{stable}}$) and the candidate ($V_{\text{canary}}$)
decode the same stream in parallel; the router decides, per symbol, **whose decoded output
is published downstream**, audits the two decoders tick by tick, and trips a breaker that
reverts every symbol to $V_{\text{stable}}$ when they diverge.

The failure this guards against is your own decoder: a wrong implied-decimal scale, a
mis-sized struct field, a message type silently ignored, a book that drifts after a
replace message.

## When NOT to Use

- **As a way to receive less data.** Exchange direct feeds are not subscribable per
  symbol. Nasdaq TotalView-ITCH is "a series of sequenced messages" over SoupBinTCP or
  MoldUDP64 covering all instruments; a canary handler still consumes and parses 100% of
  the stream. A symbol-level canary bounds *blast radius*, never bandwidth, CPU, or
  market-data entitlement cost — budget for two full handlers.
- **As gap or packet-loss detection.** This compares two decoders against each other. If
  both miss the same UDP packet they agree perfectly and the audit stays green. Sequence
  continuity is a separate control — see `sequence-number-gap-detection-for-feeds`.
- **To compare two different vendors or venues.** Two feeds of the same instrument
  legitimately differ in price and timing; grading that with this breaker produces
  constant false rollbacks. Use `market-data-feed-arbitration-across-vendors`.
- **When the two handlers share mutable book state.** The audit assumes independent
  decode paths. If the canary mutates the same in-memory order book as the baseline, a
  canary defect has already corrupted the baseline's output and there is nothing left to
  compare against.
- **For strategy or order-routing code.** Canarying code that sends orders is a
  *notional* problem, not a symbol-routing one — see
  `canary-releases-for-strategy-code-changes`.
- **When the handler is stateful and the canary starts mid-session.** A book-building
  handler that joins after the open has no valid state; it must be seeded from a snapshot
  before its output is comparable. See `market-data-snapshot-plus-delta-reconciliation`.

## Prerequisites

- A running $V_{\text{stable}}$ instance and a $V_{\text{canary}}$ instance consuming the
  **same** stream, with independent state.
- A way to pair the two outputs **by message identity** — exchange sequence number, or
  (symbol, exchange timestamp). Pairing by arrival order is not sufficient; see Pitfalls.
- A published-output layer that consults `route_symbol()` per tick rather than caching a
  routing table computed once at startup.
- Capacity for two full-universe handlers, and market data entitlements that permit a
  second consuming process.
- Written promotion and rollback criteria, and a named authoriser, fixed before the canary
  starts. For EU/UK firms in scope of MiFID II RTS 6 this is a documentation obligation,
  not a nicety — see `references/standards.md`.

## Workflow

1. **Allocate the Canary Symbol Subset**:
   - `FeedHandlerCanaryRouter(canary_percentage=10.0, canary_symbols=[...])`. Allocation is
     a stable digest bucket over the symbol, resolved at 0.01% granularity.
   - **Decision point — never bucket on Python's built-in `hash()`.** String hashing is
     salted per process (`PYTHONHASHSEED`), so `hash(symbol) % 100` gives a *different*
     symbol set in every process and after every restart. Two handlers would each believe
     they own a symbol, or neither would.
   - **Decision point — the hash will pick liquid names in proportion to the universe, not
     in proportion to risk.** Pin the awkward instruments explicitly via `canary_symbols`:
     a sub-dollar quote, a symbol with a suffix or a non-ASCII name, a halted or
     newly-listed instrument, one that trades in a different tick regime.

2. **Route Output and Audit Aligned Pairs**:
   - Publish $V_{\text{canary}}$ output only for symbols `route_symbol()` marks canary.
   - Call `audit_tick_pair(symbol, price_stable, price_canary, sequence_number=...,
     canary_sequence_number=...)` on pairs decoded **from the same message**. Mismatched
     sequence numbers raise `ValueError` — a caller alignment bug, not a feed defect, and
     never counted as a canary error.

3. **Grade Agreement Exactly, Not Approximately**:
   - Default `price_tolerance=0.0`. Two correct decoders reading the same message must
     produce the same number: ITCH prices are integer fields with an implied precision,
     not floating point.
   - **Decision point — raise the tolerance only if the two handlers legitimately produce
     different representations** (a changed rounding convention you have deliberately
     accepted). Every basis point of tolerance is a class of decode defect you have chosen
     not to detect.
   - Non-finite (`NaN`, `±Inf`) and non-positive prices count as mismatches on either side.
     They are the signature of a decode defect, not a divisor to be guarded around.

4. **Ramp or Roll Back**:
   - Healthy over the observation window → `set_canary_percentage(50.0,
     authorised_by=...)`, then `promote_to_full(authorised_by=...)`. Ramping only ever
     *adds* symbols, so a ramp never re-shuffles which handler owns an instrument.
   - The breaker trips automatically when the combined mismatch+exception rate exceeds
     `max_allowed_error_rate` (after `min_ticks_before_rollback` audited ticks), or on the
     first unhandled canary exception (`max_allowed_exceptions=0`).
   - **Decision point — a tripped breaker ends the deployment.** `set_canary_percentage()`
     raises `RuntimeError` afterwards; there is no "try again at 5%". Fix the decoder and
     start a new deployment.
   - Export `router.events` — timestamped `RAMP` / `PROMOTION` / `ROLLBACK` records — with
     your change management evidence.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Bucketing on `hash(symbol)`**: salted per process. The routing table differs between
  the publisher and the auditor, and changes on every restart. Use a fixed digest.
- **Auditing unaligned ticks**: pairing the canary's *n*-th tick with the baseline's
  *n*-th tick works only while neither drops or coalesces a message. Once they drift, a
  moving market manufactures mismatches and a still market hides real ones. Pair by
  sequence number.
- **Treating `NaN` as agreement**: `abs(nan - p) / p > tolerance` is `False`, so a naive
  relative-difference check scores a `NaN` price as a perfect match — the one output that
  most certainly indicates a broken decoder.
- **Guarding the divisor and moving on**: `if price_stable <= 0: diff = 0` turns a corrupt
  zero-price tick into a pass. A zero or negative decoded price *is* the finding.
- **A tolerance that swallows the bug class you are hunting**: a 10 bp tolerance passes
  every tick-rounding and sub-penny scaling defect, which is exactly what a parser change
  breaks.
- **Reading `audit_tick_pair() is True` as "this tick matched"**: it means the *deployment*
  may continue. A single mismatch still returns `True`. Read `get_audit_summary()` for
  tick-level counts.
- **Canary Symbol Selection Bias**: a 10% hash slice of a large-cap universe is 10% of
  well-behaved symbols. Parser defects live in the odd formats — pin them explicitly.
- **Lacking Automated Rollback**: requiring an engineer to notice a canary memory leak
  delays rollback by minutes of corrupted prices reaching strategies.
- **Shared Mutable State Contention**: letting canary and baseline mutate one in-memory
  order book means a canary defect corrupts the baseline too, and the comparison becomes
  meaningless.
- **Unsynchronised counters**: `self.ticks += 1` from several feed threads is not atomic.
  Lost increments understate the error rate, which is the number the breaker fires on.
- **Ramping a rolled-back deployment**: re-enabling the canary "at a smaller percentage"
  after a rollback routes live symbols back to a release that has already failed.
- **Counting the canary as free capacity**: two handlers means double the decode cost and,
  on some venues, a second entitled connection.

## Verification

- Run the unit suite and confirm every test passes:
  `python -m unittest discover -s skills/feed-handler-canary-deployment/scripts`
- Allocate 10% over a 5,000-symbol synthetic universe and confirm the realised share is
  within ~2 points of 10%, and that the canary set at 10% is a **subset** of the set at
  50% after `set_canary_percentage(50.0)`.
- Run `route_symbol()` in two processes with different `PYTHONHASHSEED` values and confirm
  identical routing — this is what a `hash()`-based bucket fails.
- Feed `audit_tick_pair("AAPL", 150.0, float("nan"))`, `(..., 0.0, 150.0)` and
  `(..., 150.0, 150.0001)` and confirm each is counted as a mismatch under the default
  tolerance.
- Pass disagreeing `sequence_number` / `canary_sequence_number` and confirm `ValueError`,
  with the tick counter unchanged.
- Raise one exception inside the canary decoder and confirm immediate rollback, that
  `route_symbol()` then returns `V_stable` with `reason == "rolled_back"` for every symbol,
  that a second rollback does not overwrite the first reason, and that
  `set_canary_percentage()` raises afterwards.
- Confirm `router.events` carries a timestamped, attributed record for each ramp,
  promotion and rollback.

## Related Skills

- `market-data-feed-arbitration-across-vendors`
- `sequence-number-gap-detection-for-feeds`
- `market-data-snapshot-plus-delta-reconciliation`
- `canary-releases-for-strategy-code-changes`
- `automated-rollback-triggers-on-anomaly-detection`
- `blue-green-deployment-for-live-strategy-updates`
- `market-data-replay-harness-for-integration-testing`
- `graceful-shutdown-draining-in-flight-ticks`
- `broker-api-changelog-diffing-tool`
- `mifid-ii-algo-trading-compliance-eu`
