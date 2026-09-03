---
name: market-data-feed-arbitration-across-vendors
description: >-
  Use when a strategy prices off two independent vendors for the same instrument and
  must decide per tick whether a tradeable, cross-verified price exists. Covers
  divergence tolerance, stale and frozen feed quarantine, and total blackout.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: real-time-architecture
  tags: real-time-architecture, feed-arbitration, dual-vendors, bad-tick-filter, price-divergence, stale-feed-failover, redundancy
  brokers_frameworks: "CME MDP 3.0 (UDP Feed A / Feed B line arbitration); SEC Regulation NMS (Rule 612 minimum pricing increment, Market Data Infrastructure); MiFID II RTS 6 (Commission Delegated Regulation (EU) 2017/589); SEC Rule 15c3-5 (market access erroneous-order controls)"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Invoke this skill when the same instrument arrives from **two genuinely independent sources** — a direct exchange feed against an aggregator, a consolidated tape against a proprietary feed, two commercial vendors — and a strategy must price off them continuously. The component answers one question per tick:

> Is there a price we are entitled to trade on, and has anything actually verified it?

Those are two different claims, and conflating them is what makes a redundant feed pair dangerous rather than safe. A price emitted during a failover is *usable* but nothing checks it; a price emitted while two feeds disagree is neither.

## When NOT to Use

- **For A/B line arbitration on one exchange feed.** CME MDP 3.0 sends every packet on both "UDP Feed A" and "UDP Feed B" precisely so UDP loss on one line is covered by the other. Those are copies of one stream in one sequence space, arbitrated *losslessly by packet sequence number* — first copy wins, duplicate discarded, gap triggers recovery. Price-divergence logic on identical lines is strictly worse than sequence arbitration. See `sequence-number-gap-detection-for-feeds`.
- **As a substitute for book reconciliation.** This compares one scalar price per vendor. Snapshot-plus-delta consistency belongs in `market-data-snapshot-plus-delta-reconciliation`.
- **With three or more sources.** Two feeds cannot identify which one is wrong (see the workflow below). With three, median/MAD outlier filtering *can* attribute the outlier — use `multi-source-price-reconciliation-tie-breaking`.
- **Across unlike price bases.** Vendor A's last trade against Vendor B's quote midpoint diverges by roughly half a spread permanently. Normalise first: `multi-exchange-feed-normalization`.
- **As a risk control.** It emits a trust flag; it does not stop trading. Wire `is_trusted=False` into `kill-switch-and-drawdown-circuit-breakers` or `graduated-response-to-data-quality-degradation` to make it act.

## Prerequisites

- Two vendor streams for the same symbol on the **same price basis**, plus a per-vendor entitlement to use them for the intended purpose.
- A **single local receipt clock** for both feeds — never vendor- or exchange-supplied event timestamps. Staleness is a duration; measuring it across two vendors' clocks measures their skew instead (`clock-skew-correction-for-tick-timestamps`).
- A per-instrument divergence tolerance calibrated from recorded cross-vendor history, floored at the instrument's minimum price increment expressed in percent.
- A supervisor timer able to call the health check at an interval well below the stale threshold.

## Workflow

1. **Confirm the two feeds are actually independent.**
   - **Decision point:** if both lines carry the same sequence space, stop — this is A/B arbitration, not vendor arbitration, and belongs in a sequence-number handler.

2. **Floor the divergence tolerance at one tick.**
   - A tolerance below one minimum price increment makes every legal one-tick disagreement a breach. Under Reg NMS Rule 612 an NMS stock quoted at or above $1.00 moves in $0.01 increments, so one tick exceeds 5 bps for any stock under $20 — the common "5 bps" default silently mis-fires across most of the sub-$20 universe.

3. **Validate every tick before it reaches state.**
   - Reject non-finite and non-positive prices at the boundary. NaN fails every comparison (`nan <= tolerance` is `False`), so an unchecked NaN routes to the divergence branch and is published as a tradeable price.
   - **Decision point:** a tick older than that vendor's last observation is a replay — drop it. Overwriting a newer observation rewinds the vendor's age and can un-stale a feed that has actually died.

4. **Classify staleness before comparing prices.**
   - One feed stale → fail over to the survivor; the price is usable but no longer cross-verified.
   - **Decision point:** if the survivor is itself a quarantined feed, do *not* promote it silently. Emit the price untrusted — being last does not make it right.
   - Both feeds stale → no price at all. `consensus_price` is `None`, not the last good value.

5. **Detect the blackout the tick path cannot see.**
   - An arriving tick is always fresh, so a vendor is only ever *seen* as stale by its counterpart's traffic. When both vendors go silent — the outage the whole component exists for — no tick arrives and nothing is evaluated. A supervisor must call the health check on a timer.

6. **Compare only observations that are close enough in time to be comparable.**
   - Two feeds read at two instants are two different observations. Averaging them manufactures a price that never existed; blaming a vendor for the difference blames it for the market having moved.
   - **Decision point:** when the observations are not simultaneous, take the **freshest** price, attribute nothing, and mark it unverified if it also breaches tolerance.

7. **Arbitrate simultaneous, fresh observations.**
   - Within tolerance → emit the midpoint as the only cross-verified state.
   - Beyond tolerance → **do not quarantine on the first disagreeing tick.** A fast market produces exactly this signature for as long as one feed leads.

8. **Attribute on evidence before falling back on policy.**
   - **Evidence:** a vendor still delivering ticks but repeating one price while the counterpart moves is demonstrably not tracking the market. Quarantine it — this attribution requires no policy choice.
   - **Policy:** a divergence that persists past the confirmation window with no distinguishing evidence resolves to the operator-configured reference vendor. **With two sources this is a preference, not a detection** — record it as such rather than reporting the other vendor as a detected outlier.
   - Everything in between is emitted with `is_trusted=False`. Downstream must not open new risk on an unresolved price.

9. **Release quarantines on hysteresis, never on the first agreement.**
   - Require N consecutive clean comparisons. Releasing immediately flaps between quarantine and consensus tick by tick.

10. **Alert on transitions, not on ticks.**
    - Log on state change. A per-tick error log is a log storm on a hot path in exactly the fast market where the logs matter. EU firms should note that RTS 6 Article 16 requires real-time alerts within five seconds of the relevant event.

> Full procedure: see `references/workflows.md`.
> Standards and sourced citations: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Treating divergence as evidence of a bad tick.** Cross-vendor disagreement is dominated by relative latency. The SEC's Market Data Infrastructure release describes the structural gap: proprietary feed subscribers "receive more content-rich data faster" than consolidated-tape consumers. A feed that is merely *ahead* is not an outlier.
- **Blaming a fixed vendor on divergence.** Defaulting to the primary on every breach means a bad tick *on the primary* is published as the arbitrated price while the healthy secondary is reported as the outlier — the failure inverted.
- **Tolerances below one tick.** 0.05% on a $12 stock is half a minimum increment; every legal one-cent disagreement raises an alarm.
- **Stale-price-as-consensus.** A vendor frozen at its last price for ten seconds still "agrees" with anything close to it. Only a comparison between two *fresh* observations means anything.
- **Blackout invisibility.** Stale detection driven only by arriving ticks cannot fire when every feed dies, which is the one outage that matters most.
- **Reporting zero divergence when nothing was compared.** A failover result carrying `divergence = 0.0` reads on a dashboard as "the feeds agreed exactly."
- **Averaging non-simultaneous prices.** The midpoint of a current price and a three-second-old price is a number no venue ever quoted, and it lags.
- **Quarantine flapping.** Without hysteresis, one clean tick releases the quarantine and the next breach re-raises it, several times a second.
- **Promoting a quarantined feed on failover.** The last feed standing may be the one you distrusted five seconds ago.
- **Vendor event timestamps as receipt times.** Staleness computed across two vendors' clocks measures clock skew, and a skew of more than the stale threshold marks a perfectly healthy feed dead.

## Verification

- Feed two simultaneous ticks within tolerance and confirm `decision == CONSENSUS`, `is_cross_verified is True`, and the midpoint price.
- Inject a 5% spike on one vendor and confirm the first divergent tick returns `DIVERGENCE_UNRESOLVED` with `is_trusted False` and **no** quarantine, then confirm a divergence persisting past the confirmation window escalates to `QUARANTINE_ACTIVE` labelled as the configured reference-vendor policy.
- Let a real move propagate to the second feed inside the confirmation window and confirm nothing is quarantined.
- Hold one vendor's price constant while the other moves, and confirm the frozen vendor is quarantined on evidence with `FROZEN_PRICE`.
- Stop both feeds and confirm the health check returns `NO_TRUSTED_FEED` with `consensus_price is None`.
- Submit NaN, infinite, zero and negative prices and confirm each raises before entering state.
- Run `python -m unittest discover -s skills/market-data-feed-arbitration-across-vendors/scripts` and confirm a 100% pass rate.

## Related Skills

- `clock-skew-correction-for-tick-timestamps`
- `sequence-number-gap-detection-for-feeds`
- `multi-source-price-reconciliation-tie-breaking`
- `vendor-outage-fallback-data-source-hierarchy`
- `market-data-latency-monitoring-per-vendor`
- `market-data-snapshot-plus-delta-reconciliation`
- `multi-exchange-feed-normalization`
- `graduated-response-to-data-quality-degradation`
- `broker-status-page-monitoring-integration`
---
