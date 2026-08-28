# Workflows — sec-rule-15c3-5-risk-controls-us

## 0. Establish the limit set before the gate runs

```python
limits = SecRule15c35Limits(
    firm_credit_cap_usd=25_000_000.0,
    account_credit_cap_usd=2_000_000.0,
    max_single_order_notional_usd=500_000.0,
    max_single_order_qty=25_000.0,
    max_price_collar_pct=0.05,
    max_order_rate_per_sec=250,
    burst_window_sec=1.0,
    duplicate_window_sec=1.0,
    restricted_symbols={"XYZ", "ABC"},
)
engine = SecRule15C35RiskControlsUsEngine(limits=limits)
```

Every cap must be finite and positive; `account_credit_cap_usd` may not exceed
`firm_credit_cap_usd`; `max_order_rate_per_sec` must be an `int >= 1`. A mis-configured
limit set raises at construction rather than producing verdicts — a gate that cannot
state its own limits must not issue any.

The values above are illustrative. Rule 15c3-5 sets none; derive yours from the firm's
capital, clearing arrangements, customer base and instrument liquidity, and keep the
derivation with the limits. FINRA's 2026 report treats an undocumented threshold as a
finding in its own right.

`SecRule15c35Limits` is frozen. To change it:

```python
engine.replace_limits(
    limits.with_updates(account_credit_cap_usd=3_000_000.0),
    authorised_by="risk.officer@firm.example",
    reason="Approved intraday increase for client onboarding, ticket RISK-4412",
)
```

`with_updates` re-runs the full validation, and `replace_limits` refuses a blank
authoriser or reason and logs the before/after at `WARNING`. Set the reversion time when
you make the change: FINRA's report names temporary adjustments that never revert.

## 1. Evaluation order

`evaluate_market_access_order` runs in this sequence:

1. **Structural validation.** Any failure emits `INVALID_ORDER` **alone** and stops.
   Checked: non-empty string `order_id`, `account_id`, `symbol`; finite `quantity > 0`;
   finite `price > 0`; finite `accumulated_credit_used_usd >= 0` and
   `accumulated_firm_credit_used_usd >= 0`; `side` in `{BUY, SELL, SELL_SHORT}` after
   strip/upper; `is_bona_fide_market_making` a bool; `timestamp_sec` `None` or finite.
2. **`SINGLE_ORDER_QTY_CAP`** — `quantity > max_single_order_qty`.
3. **`SINGLE_ORDER_NOTIONAL_CAP`** — `quantity * price > max_single_order_notional_usd`.
4. **`CREDIT_CAP_EXCEEDED`** — `accumulated_credit_used_usd + notional > account cap`.
5. **`FIRM_CREDIT_CAP_EXCEEDED`** — `accumulated_firm_credit_used_usd + notional > firm cap`.
6. **`PRICE_COLLAR_FAT_FINGER` / `REFERENCE_PRICE_UNAVAILABLE`** — an unusable
   `nbbo_mid_price` blocks with `REFERENCE_PRICE_UNAVAILABLE` and does *not* mask a
   simultaneous size or credit breach; otherwise `abs(price - mid) > collar * mid`.
7. **`SHORT_SALE_LOCATE_MISSING`** — `SELL_SHORT` without a non-blank `short_locate_id`,
   unless the market making exception applies (see §4).
8. **`RESTRICTED_SECURITY`** — normalised symbol in the normalised restricted set.
9. **`RAPID_ORDER_BURST` / `DUPLICATE_ORDER_DETECTED`** — the cumulative controls (§5).

Steps 2–9 accumulate: an order can breach several controls and the result lists all of
them. Only step 1 short-circuits, because a violation list computed from unusable input
misleads the audit trail.

## 2. Why structural validation comes first

| Input | Naive gate | This gate |
|---|---|---|
| `quantity = float('nan')` | every comparison `False` → **allowed** | `INVALID_ORDER` |
| `quantity = -1e9` at $150 | notional `-$150bn`, under every cap → **allowed** | `INVALID_ORDER` |
| `price = float('nan')` | collar and notional both inert → **allowed** | `INVALID_ORDER` |
| `accumulated_credit_used_usd = nan` | credit check inert → **allowed** | `INVALID_ORDER` |
| `side = "SHORT"` | not `"SELL_SHORT"` → locate skipped → **allowed** | `INVALID_ORDER` |
| `nbbo_mid_price = 0.0` | `if mid > 0:` → collar skipped → **allowed** | `REFERENCE_PRICE_UNAVAILABLE` |
| `short_locate_id = "   "` | truthy → **allowed** | `SHORT_SALE_LOCATE_MISSING` |

Each row is a real fail-open, and none of them occurs in a backtest: they arrive from a
malformed upstream message, a dropped market data tick or a mis-mapped side field.

## 3. The price collar

```python
deviation = abs(price - mid)
if deviation > collar_pct * mid:      # multiply, never divide
    reject()
```

The division form `abs(price - mid) / mid > collar_pct` rejects an order priced at
*exactly* the collar for a subset of reference prices — mid $402.69$ with price
$422.8245$ divides to `0.05000000000000001`, which is `> 0.05`. The multiplied form
compares one rounding step earlier and admits the compliant order.

An unusable reference price — `None`, `0.0`, negative, NaN, infinite or non-numeric —
produces `REFERENCE_PRICE_UNAVAILABLE`. This is a deliberate trade: a reference-data
outage becomes a trading outage rather than an open gate. Decide, before deployment, what
your fallback reference is (last trade, prior close, a secondary vendor) and feed it in as
`nbbo_mid_price` explicitly, rather than letting the gate guess. The engine ships **no**
default reference price for exactly this reason.

The collar is a single firm-wide percentage. Across a mixed universe that is a weak
design: the LULD Plan bands an S&P 500 name at 5% and a $2.00 stock at 20%. Feed a
per-instrument collar by constructing a limit set per instrument tier, or extend
`SecRule15c35Limits` with a per-symbol lookup before deploying across a wide universe.

## 4. Regulation SHO locate

`SELL_SHORT` requires a non-blank `short_locate_id`. The engine verifies that a locate
*reference* is present and recorded; it cannot verify that a real, documented locate
exists at the stock loan desk. Wire the field to the locate system's identifier so the
audit trail resolves — 17 CFR 242.203(b)(1)(iii) requires compliance to be *documented*,
and an opaque token satisfies the gate without satisfying the rule.

The bona-fide market making exception (203(b)(2)(iii)) is disabled by default. Enabling it
takes two independent acts:

```python
limits = limits.with_updates(allow_market_maker_locate_exception=True)   # firm-level
order = MarketAccessOrder(..., side="SELL_SHORT", is_bona_fide_market_making=True)
```

Every accepted order under the exception logs at `WARNING`. Whether activity is bona-fide
market making is a firm determination that this gate cannot make; the two-key design keeps
it from being asserted order-by-order without the firm having taken a position.

Note what sits *upstream*: Rule 200(g) order marking. A short sale mis-marked `SELL`
arrives at this gate as a long sale and the locate check never runs. The marking control
belongs to the order management system — see `us-reg-sho-short-sale-locate-requirements`.

## 5. Cumulative controls

Both windows are per-engine, per-process, in-memory:

- **Burst** — timestamps per `account_id` in a rolling `burst_window_sec`; the count
  *including this message* is compared with `max_order_rate_per_sec`. Every evaluated
  order counts, accepted or rejected: a rejected order was still a message sent to the
  gate, and a runaway algorithm firing rejects is the case the control exists for.
- **Duplicates** — a fingerprint of `(account_id, symbol, side, quantity, price)` for each
  order the gate *accepted*, retained for `duplicate_window_sec`. Rejected orders do not
  seed the window: they never reached the venue, so a corrected resubmission is not a
  duplicate of anything. `symbol` is normalised for case and whitespace; `account_id` is
  not, because it is an opaque system-issued key that two distinct accounts may differ in
  only by case.

  This is an *economic* duplicate control — the (c)(1)(ii) limb about orders "that
  indicate duplicative orders". It is not idempotency: a retried submission carrying the
  same `order_id` but different economics passes, and a retry after a network timeout on
  a submission the venue already accepted is a different problem with a different fix.
  See `order-placement-idempotency`.

Both windows are pruned on every evaluation, so state stays bounded by the window, not by
the session.

**The scaling limitation is structural, not incidental.** Ten gateway processes each
enforcing 100 messages/second enforce 1,000 firm-wide. If the limit is a firm limit, the
counter must be shared — Redis, a dedicated risk gateway, or a single-threaded gate every
order passes through. The engine is thread-safe within a process (`threading.Lock` around
the window state), which is not the same thing.

Timestamps: supply `timestamp_sec` from a single monotonic clock domain, non-decreasing,
or leave it `None` and let the engine call its own clock. Mixing wall-clock timestamps
from different hosts into one window produces windows that expire backwards. Inject a
deterministic clock for tests:

```python
engine = SecRule15C35RiskControlsUsEngine(
    limits=limits, clock=itertools.count(1000.0, 60.0).__next__
)
```

## 6. The decision record

Persist the whole `MarketAccessCheckResult`, not a boolean:

| Field | Why it is in the record |
|---|---|
| `order_id`, `is_allowed` | the decision |
| `triggered_violations` | which control fired, as rule codes an auditor can group on |
| `rejection_reasons` | the values that fired it — the limit *and* the observed figure |
| `notional_usd` | the computed exposure, so the record does not need re-derivation |
| `latency_microseconds` | the gate's own cost, for the pre-trade latency budget |
| `audit_notes` | one-line human-readable summary, matching the emitted log line |

Rejections log at `WARNING`, acceptances at `INFO`. Write the record to durable storage:
(b) requires the supervisory procedures and a written description of the controls to be
preserved as books and records per § 240.17a-4(e)(7), and the (e)(1) annual review and
(e)(2) CEO certification are evidenced from these records. An audit trail that exists only
in process memory is not an audit trail. Alarm on a failure to write.

## 7. What this engine does not cover

| Obligation | Where it lives |
|---|---|
| (c)(2)(i) other pre-order-entry requirements — trading halts, special order types, odd lots, Regulation NMS | order management / routing layer |
| (c)(2)(iii) access restricted to pre-approved persons and accounts | identity and access management |
| (c)(2)(iv) immediate post-trade execution reports to surveillance | post-trade pipeline |
| (d) direct and exclusive control | an organisational property: who can change the limits, and whether anyone outside the broker-dealer can bypass the gate |
| (e)(1) annual documented effectiveness review, (e)(2) CEO certification | compliance calendar |
| Rule 200(g) order marking | order management system |
| Kill switch / mass cancel | `kill-switch-and-drawdown-circuit-breakers`, `execution-algorithm-kill-switch-integration` |
| Override / bypass logging | `risk-control-bypass-audit-logging` |
| Testing the gate itself | `risk-control-unit-testing-framework` |

A green result from this engine says the order passed the controls implemented here. It
does not say the firm complies with Rule 15c3-5.
