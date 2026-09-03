# Standards — websocket-reconnection-with-state-recovery

## 0. How to read this document

Section 1 is **published engineering practice** (AWS), quoted because the skill's backoff
formula is taken from it. Section 2 is **venue fact** from official exchange documentation,
with the fetch date recorded. Sections 3–4 are **this repository's engineering standards** —
recommended defaults, not requirements.

No regulator sets a reconnect delay, a jitter factor, or a gap-fill page size. Every number
in Sections 3–4 is an operational default to calibrate. The venue limits in Section 2 are
the opposite: they are published, enforced, and breaching them locks you out of the venue
during the outage you are trying to survive.

## 1. Engineering practice — what "jitter" actually means

AWS's *Exponential Backoff And Jitter* (AWS Architecture Blog) defines three variants, and
the skill's `jitter_factor` reproduces two of them exactly:

| Variant | Formula | `jitter_factor` |
|---|---|---|
| Full Jitter | `sleep = random(0, min(cap, base * 2**attempt))` | `1.0` |
| Equal Jitter | `temp = min(cap, base * 2**attempt); sleep = temp/2 + random(0, temp/2)` | `0.5` |
| Decorrelated Jitter | `sleep = min(cap, random(base, sleep * 3))` | not implemented |

The article's simulation finds Full Jitter completes the work in the fewest total calls,
and concludes that jittered backoff "should be considered a standard approach for remote
clients."
<https://aws.amazon.com/blogs/architecture/exponential-backoff-and-jitter/>

**The load-bearing detail is where the randomness sits.** In all three variants the delay
is drawn *inside* `cap`. A formula of the shape `capped + uniform(0, capped * f)` — which
this skill shipped before v2.0.0 — is neither Full nor Equal Jitter: it exceeds the
documented ceiling by a factor of `1 + f` (45 s against a 30 s cap at `f = 0.5`) and leaves
every client waiting at least the full exponential delay, so the reconnect burst is smeared
rather than flattened. Correcting that is the reason `jitter_factor` now defaults to `1.0`
and means "fraction of the capped delay that is randomised".

## 2. Venue fact — connection lifetime, limits, and what recovery is actually available

Fetched 2026-09-02 from official documentation. Re-verify before relying on any row: limits
and response schemas change without notice.

### 2.1 Binance Spot

| Item | Documented value |
|---|---|
| Connection lifetime | "A single connection to the API is only valid for 24 hours; expect to be disconnected after the 24-hour mark." |
| Keepalive | "The WebSocket server will send a `ping frame` every 20 seconds. If the WebSocket server does not receive a `pong frame` back from the connection within a minute the connection will be disconnected." |
| Connection attempts | "There is a limit of 300 connections per attempt every 5 minutes. The connection is per IP address." |

<https://developers.binance.com/docs/binance-spot-api-docs/websocket-api/general-api-information>

Two consequences drive the design:

1. **Reconnects are scheduled events, not just faults.** A bot running for a week is
   evicted at least seven times with nothing wrong. `on_connection_lost(scheduled=True)`
   exists so an expected rotation does not escalate the failure backoff — and it is still
   jittered, because every client that connected in the same minute is evicted in the same
   minute.
2. **A no-backoff reconnect loop is self-inflicted denial of service.** 300 attempts per
   5 minutes per IP is roughly one per second sustained; a tight retry loop spends the
   budget in seconds.

### 2.2 Binance — what a sequence break actually recovers to

The documented local-order-book procedure ends with two rules that are *not* "fetch the
missing range":

- "If the event first update ID (`U`) is greater than the update ID of your local order
  book + 1, you have missed some events. **Discard your local order book and restart the
  process from the beginning.**"
- On USD-M futures, "each new event's `pu` should be equal to the previous event's `u`,
  otherwise initialize the process from step 3" — i.e. re-snapshot.

<https://developers.binance.com/docs/derivatives/usds-margined-futures/websocket-market-streams/How-to-manage-a-local-order-book-correctly>

This is why `resynchronize(symbol, next_sequence_id)` is a first-class method and why a
failed fill fails **closed**. For a depth stream the honest configuration is *no*
`rest_gap_fill_fn` at all: every gap latches, and the caller re-snapshots.

### 2.3 Where range gap-fill is genuinely available

`GET /api/v3/aggTrades` is the clean case — it is addressed by id, not by time:

| Parameter | Documented behaviour |
|---|---|
| `fromId` | "ID to get aggregate trades from INCLUSIVE" |
| `limit` | default 500, **max 1000** |
| Weight | IP weight 4 |

<https://developers.binance.com/docs/binance-spot-api-docs/rest-api/market-data-endpoints>

The 1000-record page is the direct source of the `max_gap_fill_size=1000` default. A gap
wider than one page needs paging; a gap wide enough to need many pages is an outage, and
the correct response to an outage is a re-snapshot, not a refetch loop that spends weight-4
calls against a venue that is already degraded. `GET /api/v3/depth` (weight 5–250 by
`limit`) is the snapshot endpoint that recovery actually goes through.

### 2.4 Coinbase Advanced Trade

Coinbase documents `sequence_num` explicitly as a **detection** mechanism, not a recovery
one: "Sequence numbers that are greater than one integer value from the previous number
indicate that a message has been dropped", and "your feed consumer should be designed to
handle sequence gaps and out of order messages." No retransmission endpoint is documented;
the heartbeats channel is offered as the way to notice missed messages.
<https://docs.cdp.coinbase.com/coinbase-app/advanced-trade-apis/websocket/websocket-overview>

Note also that `market-data-snapshot-plus-delta-reconciliation` treats Coinbase
`sequence_num` as a per-connection message counter rather than a per-book version. Confirm
the scope against the current documentation before keying a per-symbol watermark on it —
if the counter is per connection, one watermark per symbol is the wrong shape.

**Net:** of the two venues named in this skill's frontmatter, one supports range gap-fill
for *trade* streams only, and neither supports it for order book deltas. Any claim that a
WebSocket gap is generally repairable by REST-fetching the missing sequence range is wrong.

## 3. Engineering standard — defaults and what they are for

*Recommended practice, not requirements. Calibrate all of it.*

| Knob | Default in `scripts/ws_recovery.py` | Why |
|---|---|---|
| `base_backoff_sec` | `1.0` | Ceiling for the *first* retry, not a floor added to it. Under full jitter attempt 0 draws in `[0, 1]`. |
| `max_backoff_sec` | `30.0` | Hard ceiling. Never exceeded, at any attempt index. |
| `jitter_factor` | `1.0` | AWS Full Jitter. Use `0.5` (Equal Jitter) only when a floor under the delay is needed. |
| `max_gap_fill_size` | `1000` | One page of Binance `aggTrades`. Anything larger escalates to re-snapshot. |
| `max_retained_messages` | `10_000` | Bounded `processed_messages`. `0` disables retention; unbounded is a leak. |
| `requires_auth` | `False` | Public market data has no auth step. `True` for private/order-update streams. |
| exponent clamp | `2**30` | Keeps `base * 2**attempt` off the float-overflow that an unbounded counter hits past ~1024 attempts. |

**What this control covers:** reconnect lifecycle and backoff, deterministic
re-subscription from desired state, per-symbol sequence continuity across the reconnect
boundary, provable gap fill where the venue supports it, and a fail-closed synchronisation
gate where it does not.

**What it does not cover:** liveness detection on a silent socket
(`graceful-degradation-to-polling-fallback`), multi-stream continuity with out-of-order
buffering and venue retransmission tiers (`sequence-number-gap-detection-for-feeds`),
order book assembly from snapshot plus deltas
(`market-data-snapshot-plus-delta-reconciliation`), SDK-level duplicate subscription
bookkeeping (`websocket-reconnect-without-duplicate-subscriptions`), and acting on the
unsynchronised signal (`capital-preservation-mode-for-degraded-conditions`).

## 4. Engineering standard — fail closed, and prove the fill

*Recommended practice, not a requirement.*

A gap-fill response is accepted only when it covers the requested range **exactly**: same
symbol, exact count, contiguous, ascending. Anything else is a failed fill. The reasoning
is asymmetric cost:

- A rejected-but-actually-complete fill costs one re-snapshot.
- An accepted-but-incomplete fill advances the watermark past messages nobody ever saw.
  Every later continuity check then passes, and the local book is wrong for the rest of the
  session with no error, no warning, and no way to notice from the data.

The same asymmetry is why an absent `rest_gap_fill_fn` latches rather than passing the
message through, and why messages for a latched symbol are withheld rather than delivered
with a flag. A caller that ignores a boolean gate is a realistic failure; a caller that
ignores an empty list is not.

## Regulatory note

No jurisdiction prescribes a reconnect delay, a jitter factor, or a gap-recovery
procedure. Firms in scope of MiFID II algorithmic trading obligations carry general
real-time monitoring and resilience duties under Commission Delegated Regulation (EU)
2017/589 (RTS 6) that a market-data continuity break plainly touches, but nothing there
sets a number for any knob in this skill. Treat this document as engineering practice and
let your compliance function determine which regime applies. Retention of
connectivity-event and gap records is set by your applicable regime; this skill asserts no
retention period.
