# Deep Workflow Reference — market-data-feed-arbitration-across-vendors

This file holds the full technical procedure referenced by `SKILL.md`.

## 0. Preconditions

- The two feeds are **independent sources**, not two lines of one stream. Identical lines
  (CME MDP 3.0 UDP Feed A / Feed B) are arbitrated by packet sequence number, losslessly,
  and must never be run through price-divergence logic.
- Both vendors publish the **same price basis** for the symbol. Last trade against quote
  midpoint diverges by roughly half a spread permanently.
- Every tick is stamped with a **local receipt time from one clock**. Never a vendor or
  exchange event timestamp.

## 1. Ingest and validate

1. Normalise the vendor key; reject anything that is not one of the two configured feeds
   rather than routing it to a default.
2. Reject non-finite and non-positive prices. `NaN` compares `False` against every
   threshold, so an unvalidated `NaN` reaches the divergence branch and is published.
3. Drop a tick whose timestamp precedes that vendor's last observation. A replayed tick
   that overwrites a newer one rewinds the feed's age and can un-stale a dead feed.
4. Record whether this tick *changed* the vendor's price. That is the input to
   frozen-feed detection later.

## 2. Staleness, before any price comparison

| Condition | Result | Trust |
|---|---|---|
| Neither vendor has ever ticked | `NO_TRUSTED_FEED`, no price | untrusted |
| One vendor has ever ticked, fresh | `SINGLE_FEED` | trusted, not cross-verified |
| One vendor stale, one fresh | `FAILOVER` to the fresh feed | trusted, not cross-verified |
| One vendor stale, survivor is itself quarantined | `FAILOVER` | **untrusted** |
| Both stale | `NO_TRUSTED_FEED`, no price | untrusted |

Staleness uses a strict `>` comparison against `max_stale_seconds`: a feed exactly at the
limit is still healthy.

## 3. Blackout detection (the case ticks cannot reach)

An arriving tick is always fresh, so a vendor is only ever observed as stale by its
counterpart's traffic. If both vendors go silent, no tick arrives and no arbitration runs.
A supervisor must call the health-check entry point on a timer, at an interval well below
`max_stale_seconds`. Without it, the total outage this component exists to survive is the
one outage it cannot see.

## 4. Comparability gate

Compute the age gap between the two feeds' last observations.

- Gap `<= max_comparison_age_seconds` → the observations are treated as simultaneous and
  may be compared and averaged.
- Gap above it → they are two different moments. Emit the **freshest** price, never the
  average, and attribute nothing to either vendor. If the prices also breach tolerance,
  mark the result untrusted: the disagreement may be a real move *or* a bad tick on the
  fresher feed, and nothing available distinguishes them.

## 5. Divergence

Relative divergence against the midpoint, symmetric in the two vendors:

$$\delta = \frac{|P_A - P_B|}{(P_A + P_B)/2} \times 100\%$$

The tolerance comparison is inclusive at the boundary and guarded against floating-point
representation error, so a divergence of exactly the tolerance is a consensus rather than
a breach.

- $\delta \le$ tolerance, observations simultaneous → `CONSENSUS`. Emit the midpoint. This
  is the **only** cross-verified state.
- $\delta >$ tolerance → proceed to attribution.

## 6. Attribution: evidence before policy

**Step 1 — evidence.** If one vendor's price has been unchanged for at least
`frozen_price_seconds` while the counterpart's changed within that window, that vendor is
still delivering ticks but is no longer tracking the market. Quarantine it and price from
the counterpart. This attribution needs no policy input: a genuine market move eventually
appears on both feeds.

**Step 2 — hold-down.** Otherwise, start a divergence episode and emit the reference
vendor's price as **untrusted** (`DIVERGENCE_UNRESOLVED`) until
`divergence_confirmation_seconds` has elapsed. A fast market — an earnings gap, a halt
resumption — produces exactly this signature for as long as one feed leads, and
quarantining on the first divergent tick is how a redundant pair loses its healthy feed.
The episode timer measures time since the last *comparable, in-tolerance* comparison; it
is not restarted by observations too far apart in time to carry evidence.

**Step 3 — policy.** If the divergence survives the window with no distinguishing
evidence, fall back to the operator-configured reference vendor and quarantine the other.
**With exactly two sources this is a preference, not a detection**: nothing in the data
identifies which feed is wrong. Report it as policy so post-incident review is not misled.
With three or more sources, median/MAD filtering *can* attribute the outlier — that is
`multi-source-price-reconciliation-tie-breaking`, not this skill.

## 7. Recovery

A quarantine releases only after `recovery_consecutive_ticks` consecutive clean
comparisons, each requiring both feeds fresh, simultaneous and within tolerance. Releasing
on the first agreement flaps between quarantine and consensus tick by tick. The release
itself is logged; the reason the quarantine was raised (frozen feed vs policy fallback) is
retained so it is not silently relabelled on later results.

## 8. Consuming the result

| Flag | Meaning | Consumer obligation |
|---|---|---|
| `is_trusted = False` | No defensible price this tick | Do not open new risk. Widen quotes, pause, or escalate. |
| `is_cross_verified = False` | Price usable, nothing corroborates it | Acceptable for continuity; consider reducing size or tightening downstream limits. |
| `consensus_price is None` | No price at all | Fail closed. Never substitute the last known value inside the arbitrator. |
| `quarantined_vendor` set | Running degraded on one source | Alert; a degraded pair has no redundancy left. |

Alerting is emitted on state transitions, not per tick — a per-tick error log is a log
storm in precisely the fast market where the logs are needed. EU firms should note the
five-second real-time alert ceiling in RTS 6 Article 16.

## 9. Session boundaries

Reset per-symbol state at a session boundary. An overnight gap otherwise presents as a
stale feed followed by a large divergence at the next open.

## Production Implementation Reference

- Reference code: `scripts/feed_arbitrator.py` (`MarketDataFeedArbitrator`,
  `ArbitratedTickResult`, `ArbitrationDecision`, `VendorStatus`).
- Automated unit tests: `scripts/test_feed_arbitrator.py`.
