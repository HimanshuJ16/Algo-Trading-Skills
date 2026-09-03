# Standards: Broker API Version Migration

Two kinds of statement appear below and they are not interchangeable. **Cited** items
come from a published schema, RFC, or regulation and are reproduced with their source.
**Calibrated** items are engineering thresholds with no external authority behind them;
they are starting points to be re-derived from your own V1 baseline. No regulator or
exchange publishes a canary error-rate threshold, and a document that presents one as a
standard is inventing it.

## 1. Target-schema fidelity (cited)

The single highest-severity defect class in an API migration is a payload field that
does not exist in the target version. Verified against the Coinbase Advanced Trade
`CreateOrder` reference
(<https://docs.cdp.coinbase.com/api-reference/advanced-trade-api/rest-api/orders/create-order>),
used here as the worked example because its v1→v2 shape change is representative:

| Concern | Value |
|---|---|
| Top-level fields | `client_order_id`, `product_id`, `side`, `order_configuration` |
| `side` | `BUY` or `SELL` |
| Sizes and prices | **strings**, not numbers |
| Market | `market_market_ioc` (`base_size` or `quote_size`); `market_market_fok` documented as perpetuals-only |
| Limit | `limit_limit_gtc`, `limit_limit_gtd` (needs `end_time`), `limit_limit_fok` — each requires `limit_price` |
| Stop | `stop_limit_stop_limit_gtc` / `_gtd`, requiring `base_size`, `limit_price`, `stop_price` **and** `stop_direction` |
| `stop_direction` | `STOP_DIRECTION_STOP_UP` or `STOP_DIRECTION_STOP_DOWN` |

**There is no `stop_stop_gtc` key.** A prior revision of this skill emitted one. Note
the general shape of the trap: a *plausible* key name, consistent with the surrounding
naming convention, that the spec does not contain. Plausibility is not verification.

Also note what changes in a version bump beyond names: the product identifier moves
from `instrument_id`/`symbol` to `product_id`, the size moves from the top level into
`order_configuration.base_size`, and numeric prices become decimal strings. Any one of
those, missed, produces a rejected order at best.

## 2. Order-semantic preservation (cited, by inference from the schemas)

Time-in-force is encoded *in the configuration key itself* in this API, so a translator
that ignores `time_in_force` does not merely lose a field — it silently rewrites the
order. `limit_limit_gtc` for a requested IOC leaves a resting order the strategy
believes was cancelled.

The rule that generalises: **when the target version cannot express the requested order,
raise.** Substituting the nearest available semantic converts a translation error into a
position.

## 3. Decimal serialisation (cited: IEEE 754 / Python semantics)

Prices must be serialised through `decimal.Decimal` with fixed-point formatting:

- `str(1e-05)` → `'1e-05'`. Exponent notation is rejected by many decimal-string
  parsers.
- `str(0.1 + 0.2)` → `'0.30000000000000004'`. Binary floating point has no exact
  representation for most decimal fractions.
- `Decimal(0.1)` inherits the same error; `Decimal(str(0.1))` does not. Always route
  through `str` when accepting a float.
- `format(value, 'f')` never emits an exponent and preserves the caller's scale.

## 4. Latency comparison (calibrated thresholds, cited statistics)

**Thresholds (calibrated — re-derive these).** The defaults shipped here are a V2 mean
within +5% of the V1 mean and a V2 p99 within 1.20× the V1 p99. They are plausible for a
REST order path and arbitrary for anything else.

**Sample-size requirement (cited reasoning).** A p99 estimated from *n* samples is
determined by roughly the top `n/100` observations: at n = 200 that is two data points.
The tracker therefore reports `percentiles_reliable` and refuses to gate the p99 arm
below a configured minimum (default 1000). A fixed sliding window compounds the problem
in the other direction — the last 1000 samples of a 48-hour shadow phase describe the
last few minutes of it — so the implementation uses **reservoir sampling** (Vitter,
Algorithm R) to hold a uniform sample of the whole phase in fixed memory, alongside
exact streaming `count`, `mean` and `max`.

**On the two-sample t-test.** Do not gate V1-vs-V2 round-trip times on a t-test with a
pass condition of *p > 0.05*. That construction is wrong in two independent ways:

1. **It accepts the null hypothesis.** A large p-value is a failure to detect a
   difference, not evidence of equivalence. With small or noisy samples it is the
   *easiest* result to obtain, so the gate is most likely to pass exactly when it has
   the least information. The correct construction is an **equivalence test** — two
   one-sided tests (TOST) against a pre-specified equivalence margin, e.g. "V2 mean is
   within +5% of V1 mean" — which requires evidence to pass. (Schuirmann, D. J., "A
   comparison of the two one-sided tests procedure and the power approach for assessing
   the equivalence of average bioavailability", *Journal of Pharmacokinetics and
   Biopharmaceutics* 15(6), 1987.)
2. **Its assumptions do not hold for latency.** RTT samples are right-skewed,
   heavy-tailed, and serially correlated (queueing, GC pauses, TCP retransmits,
   session-time-of-day effects). Independence is violated regardless of sample size, and
   the mean is in any case the wrong statistic for an execution path where the tail is
   what hurts. Compare **percentiles** with bootstrap confidence intervals, and compare
   distributions with a distribution-free test (Mann–Whitney U) if a hypothesis test is
   wanted at all.

The shipped implementation deliberately performs no hypothesis test. It reports
descriptive statistics against explicit tolerances and returns **undecided** rather than
"pass" when the sample is too small — which is the honest form of the same gate.

## 5. Schema-drift detection

- **Recurse.** Comparing top-level keys only misses the common case: a type change
  inside a nested object or list element. On a promotion gate, a false negative is the
  dangerous direction, so the comparison descends into dicts and list elements and
  reports drift by dotted path (`order.fills.[].price`).
- **Additive change is not drift.** Fields present only in V2 are recorded but do not
  fail the gate; a version that adds fields is backward compatible for a client that
  ignores unknown ones.
- **`null` proves nothing.** JSON `null` carries no type information, and neither does
  an empty list. These are reported as *unverified paths* rather than counted as either
  a match or a mismatch — so a shadow phase cannot pass on a payload that happened to be
  mostly null.
- **`bool` is not `int`.** JSON `true` and `1` are different values; a version that
  changed one to the other changed its contract.
- **Strict primitive typing.** `int` versus `float` is reported as drift. This is
  deliberate strictness: a price that arrives as `100` where `100.0` was expected is
  usually a serialiser change worth knowing about.

## 6. Concurrency and determinism

- Phase state, counters, the audit log, the affinity map and the latency reservoirs are
  all mutated under a single lock.
- **Canary routing must be deterministic per order.** It is computed as a stable
  BLAKE2b digest of the client order id, mapped into [0, 1). Python's built-in `hash()`
  of a `str` is salted per process by `PYTHONHASHSEED`, so it would assign the same
  order to different versions in different replicas and after any restart.
- Shadow reads execute the V1 call on the calling thread and hand only the V2 call to a
  bounded background pool. A shadow that can block the production path is not a shadow.
- Python cannot interrupt a worker thread blocked in a socket read. The shadow callable
  **must** carry its own network timeout; the pending-shadow cap bounds the number of
  stuck threads, it does not cancel them.

## 7. Rollback

- **Reachable from every phase**, and never rejected by a transition check — it is the
  emergency path.
- **Latched.** Leaving `ROLLBACK_V1` requires an explicit operator call with a reason,
  so an automated ramp scheduler cannot re-promote the version that just failed. The
  migration then restarts from the gate sequence rather than resuming at the percentage
  that broke.
- **Calibrated SLA.** Reverting should be a state change, not a redeploy — an in-process
  phase flip, no restart, effective on the next routing decision. Any specific
  millisecond figure is an internal SLO; measure yours rather than adopting one.
- Keep the V1 code path deployed through `V2_ONLY` and for an agreed stability period
  after it. A rollback target you have deleted is not a rollback target.

## 8. Regulatory touchpoints

Jurisdiction-specific. None of these applies universally; check which regime you are
actually in.

**EU — MiFID II RTS 6, Commission Delegated Regulation (EU) 2017/589**, applying to
investment firms engaged in algorithmic trading and authorised in the EU. Relevant
articles:

- **Article 6 (Conformance testing)** — requires conformance testing with the trading
  venue's system, including prior to the deployment or a material update of the
  algorithmic trading system, trading algorithm or algorithmic trading strategy. A
  broker/venue API version change is a material update; the production canary described
  in this skill does **not** discharge this obligation.
- **Article 7 (Testing environments)** — testing must be undertaken in an environment
  separated from production.
- **Article 8 (Controlled deployment of algorithms)** — supports phased, monitored
  introduction into live trading, which is what the canary phase implements.
- **Article 12 (Kill functionality)** — the ability, as an emergency measure, to
  immediately withdraw any or all outstanding orders. Note this is a *separate control*
  from the migration rollback: rolling back to V1 stops new orders going to V2; it does
  not pull working orders. See `kill-switch-and-drawdown-circuit-breakers`.

Text: <https://eur-lex.europa.eu/eli/reg_del/2017/589/oj/eng>. Article numbering and
titles above were confirmed against secondary indices of the regulation; consult the
EUR-Lex text before relying on any of it for a compliance decision.

**US — 17 CFR 240.15c3-5 (market access rule)**, applying to broker-dealers with market
access. Paragraph (b) requires documented risk-management controls reasonably designed
to manage the financial, regulatory and other risks of market access;
paragraph (c)(1)(i) requires the financial controls to be applied on a **pre-trade**
basis; paragraph (d)(1) requires them to be under the **direct and exclusive control**
of the broker-dealer. The architectural consequence for a migration: the version router
belongs *below* the risk layer, so neither canary branch can route around a control, and
the controls must exist on both the V1 and V2 paths simultaneously throughout the
cutover. See `sec-rule-15c3-5-risk-controls-us`.

**Deprecation signalling.** Where the migration is driven by a published retirement
date, RFC 8594 (`Sunset`), RFC 9745 (`Deprecation`) and RFC 8288 (`Link` relations) are
the relevant standards — covered in `broker-api-deprecation-notice-monitoring`.
