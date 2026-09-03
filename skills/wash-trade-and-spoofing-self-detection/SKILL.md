---
name: wash-trade-and-spoofing-self-detection
description: >-
  Use when a firm wants to see the shape of a self-match or of layering in its own order
  flow before an exchange or regulator does; streams over the firm's own events.
  Preventing a self-match at the venue is exchange-self-match-prevention-configuration.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: regulatory-compliance-global
  tags: wash-trade, spoofing, layering, market-manipulation, self-match-detection, beneficial-ownership, cea-4c-a, finra-rule-5210, mifid-ii-rts-6
  brokers_frameworks: "CEA s.4c(a)(1),(2)(A) and s.4c(a)(5)(C) (7 U.S.C. 6c(a), Dodd-Frank s.747); CFTC Antidisruptive Practices interpretive guidance (78 FR 31890); CME Rule 534 (Wash Trades Prohibited) and Rule 575 (Disruptive Practices); FINRA Rule 5210 Supplementary Material .02 (self-trades) and .03 (disruptive quoting); Exchange Act s.9(a)(2), s.10(b) / Rule 10b-5; Commission Delegated Regulation (EU) 2017/589 (RTS 6) Article 13; Python Dataclasses"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when a firm runs order-generating processes whose combined output could,
without anyone intending it, produce the shape of a wash trade or of layering — and the
firm wants to see that shape before a regulator or exchange does.

Two patterns are screened on the firm's own event stream:

1. **Self-match / wash trade.** An incoming order that would cross the firm's own resting
   order on the opposite side of the same instrument, under the same **beneficial owner**.
   The prohibition is **CEA s.4c(a)(1) and (2)(A)** (7 U.S.C. 6c(a)) for futures and
   **CME Rule 534** on Globex; **FINRA Rule 5210.02** governs self-trades in securities.
2. **Layering / spoofing.** An execution on one side accompanied by the withdrawal of
   materially larger same-owner size on the other. **CEA s.4c(a)(5)(C)** (added by
   Dodd-Frank s.747) names spoofing as "bidding or offering with the intent to cancel the
   bid or offer before execution"; **FINRA Rule 5210.03 Type 1** describes the securities
   shape; **CME Rule 575** is the Globex analogue.

It is the streaming, US-centric counterpart to the batch EU screening in
`eu-market-abuse-regulation-mar-surveillance`, and the intent-layer companion to the
venue-side mechanism in `exchange-self-match-prevention-configuration`.

## When NOT to Use

- **Not as the mechanism that prevents a self-match.** This module observes; the *venue*
  prevents. Configure native SMP/STP through `exchange-self-match-prevention-configuration`
  and treat this engine's output as the audit and calibration layer around it. CME's own
  guidance treats SMP as a preventive tool, not a defence — self-matching on more than an
  incidental basis may still be deemed to violate Rule 534.
- **Not as a determination that a rule was broken.** Wash trades under CEA s.4c(a) turn on
  intent; CME Rule 534 applies a "knew or should have known" standard; spoofing under
  s.4c(a)(5)(C) requires **scienter** — the CFTC's interpretive guidance (78 FR 31890,
  28 May 2013) states that reckless conduct does not violate the provision. Intent is not
  in the order stream. Every output here is an indicator for a human analyst.
- **Not a substitute for the venue's own view.** The engine sees the firm's orders as the
  firm sent them. Rejects, venue-side modifications and queue position are invisible;
  reconcile against execution reports.
- **Not cross-venue or cross-instrument.** Alerts group by (beneficial owner, instrument).
  Manipulation of a future through its underlying, or across two venues, is out of scope.
- **Not the EU filing path.** A suspicion arising in EU/EEA instruments becomes a STOR
  under MAR Article 16 and Delegated Regulation (EU) 2016/957 — see
  `eu-market-abuse-regulation-mar-surveillance`.
- **Not a position-netting or fee-saving tool.** To cross opposing internal orders
  deliberately rather than detect them accidentally, see `multi-order-netting-before-routing`.

## Prerequisites

- Python 3.9+, standard library only.
- An order event stream carrying `event_id`, `order_id`, `trader_id`, `account_id`,
  `symbol`, `side`, `quantity`, `action` (`PLACE` / `CANCEL` / `FILL`) and a
  **timezone-aware** `timestamp`. Naive timestamps are rejected: sub-second lifespan logic
  on an ambiguous clock is not defensible, and MiFID II RTS 25 (Delegated Regulation (EU)
  2017/574) bounds business-clock divergence from UTC at 100 microseconds for HFT.
- `price` where the order carries one. `None` means unpriced — a market order (which
  crosses every own level) or a bare cancel.
- An **account → beneficial owner map**. Without it ownership is a string comparison and
  the cross-account self-cross this skill exists to catch is invisible. Ownership is
  supplied, never inferred.
- `strategy_id` per order where known, for the FINRA Rule 5210.02 relatedness test.
- Detection parameters calibrated to the firm's own microstructure. The defaults
  (90% cancel ratio, 1,000 ms window, 3.0x size ratio, 2 layered orders) are **library
  heuristics, not regulatory thresholds** — no regulator prescribes a number.

## Workflow

1. **Map beneficial ownership before anything else.** Construct
   `WashTradeAndSpoofingDetectionEngine(beneficial_owner_map={...})` keyed by account id
   (trader id also resolves). Every detector and every metric groups by owner, because
   grouping by raw trader id dilutes one manipulator's activity across the accounts it
   trades and hides same-owner/different-account crossing entirely.
2. **Feed every event through `ingest_order_event`.** It validates, screens, and returns
   the alerts that event raised. The check methods are called by it; call them directly
   only for a pre-trade query, and note that `check_spoofing_pattern_on_fill` opens
   detector state as a side effect.
   - **Decision point — a duplicate `event_id` is an error, not a warning.** A replayed
     event inflates both the cancellation ratio and the withdrawn-size test, so the engine
     raises rather than double-counting.
   - **Decision point — a naive timestamp is rejected outright.** Silently assuming UTC
     turns a clock bug into a fabricated sub-second lifespan.
3. **Read the self-match alert as a crossing test, not a price-equality test.** A resting
   own bid at 150.05 is reached by an incoming own offer at 150.00; the venue matches them
   at 150.05. An equality test reports nothing and the print still happens. Unpriced orders
   cross every own level.
   - **Decision point — a resting order's age does not make it safe.** The self-match
     window defaults to `None` (any resting order). Setting `wash_trade_window_seconds`
     narrows it to a "matched trade" pattern and buys false negatives; do that only
     deliberately.
4. **Read severity as review priority, not legal conclusion.** A self-cross between two
   *known and different* `strategy_id` values is reported at MEDIUM, because FINRA Rule
   5210.02 generally treats self-trades from unrelated algorithms as bona fide. It is
   never suppressed — CME Rule 534 carries no equivalent carve-out, and an unknown
   `strategy_id` is treated conservatively as related.
5. **Let the layering context settle before judging a fill.** FINRA Rule 5210.03 Type 1
   places the cancellations *after* the opposite-side execution, so a fill cannot be scored
   when it arrives. `check_spoofing_pattern_on_fill` opens a context recording the
   opposite-side orders resting at that instant; subsequent cancels attach to it through
   `check_layering_on_cancel` and the alert fires when the count and size tests are met.
   - **Decision point — size and count are what separate layering from quoting.** A
     two-sided market maker cancels opposite-side size around nearly every fill. Requiring
     `layering_size_ratio` (default 3.0x the executed quantity) across `min_layered_orders`
     (default 2, matching Rule 5210.03's "multiple limit orders") is what keeps the
     detector from firing on ordinary quote maintenance.
6. **Treat the cancellation ratio as hygiene, and let it latch.** It fires once per owner
   and stays latched until `reset_cancellation_ratio_alert`; re-emitting on every
   subsequent event buries the alert that mattered. It is cancels/placements — **not** the
   MiFID II RTS 9 order-to-trade ratio, which Delegated Regulation (EU) 2017/566 places on
   the *trading venue*, per member and per instrument, on both volumes and numbers.
7. **Escalate to a human, and retain the parameters.** Every alert carries
   `requires_human_review=True`, an `indicator_reference` and a deterministic `alert_id`.
   Persist the detection parameters beside the alerts: a threshold you cannot reconstruct
   years later is a threshold you cannot defend.

> Full procedure: see `references/workflows.md`.
> Legal sources and calibration guidance: see `references/standards.md`.
> Printable sign-off checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Testing price equality instead of crossing.** The wash-trade condition is that the
  orders *would match*, not that they carry the same limit. A resting own bid better than
  the incoming own offer executes; equality testing misses every such print and reports a
  clean book.
- **Comparing raw ids instead of resolving the beneficial owner.** Wash-trade exposure
  attaches to the owner. Two desks with different trader ids and different account ids
  under one entity self-cross, and an id comparison finds nothing. The mirror-image error
  is matching on a shared generic account string and flagging unrelated participants.
- **Scoring a fill at the moment it arrives.** In the Rule 5210.03 Type 1 shape the
  cancellations follow the execution, so the decisive evidence does not exist yet. A
  detector that only inspects history at fill time never sees the canonical pattern.
- **Alerting on any opposite-side cancellation near a fill.** Without a size and count
  test this is a market-maker alarm, not a surveillance control — and an alert channel that
  cries wolf all day is the one nobody reads on the day it is right.
- **Emitting "VIOLATION" from an automated detector.** Spoofing under CEA s.4c(a)(5)(C)
  requires scienter and FINRA Rule 5210.03 requires a frequent pattern. Labelling a single
  indicator a violation both overstates the finding and, in a produced record, hands a
  regulator the firm's own characterisation of its conduct.
- **Re-emitting a threshold alert on every subsequent event.** A latched ratio breach that
  fires ten thousand times is the same information ten thousand times, and it displaces
  everything else in the queue.
- **Deriving alert ids from a whole-second timestamp.** Two alerts in the same second
  collide, and an id that cannot be cited is not an audit trail. Ids here are derived from
  the contributing event ids and are stable across reruns.
- **Assuming SMP makes the exposure go away.** CME's guidance treats self-match prevention
  as a preventive tool, not a defence; it does not operate during the Globex pre-open, and
  self-matching on more than an incidental basis may still be deemed to violate Rule 534.
- **Citing "CFTC Rule 1.38" as the wash-trade prohibition.** 17 CFR 1.38 is "Execution of
  transactions", the competitive-execution requirement. The wash-sale prohibition is
  CEA s.4c(a)(1),(2)(A) (7 U.S.C. 6c(a)). A wrong citation in a compliance record is worse
  than no citation.
- **Assuming one retention period.** MiFID II RTS 6 Article 28 requires order records for
  five years; US broker-dealer retention runs on SEA Rule 17a-4 and FINRA Rule 4511(b)
  (six years where no other period applies). OATS was retired on 1 September 2021 — the US
  order audit trail is CAT under SEC Rule 613.

## Verification

- **Crossing, not equality**: a resting BUY at 150.05 followed by an own SELL at 150.00
  must raise exactly one wash-trade alert; a resting BUY at 149.00 with an own SELL at
  150.00 must raise none. An unpriced order must cross every own level.
- **Ownership**: with `beneficial_owner_map={"ACC1": "ENTITY_A", "ACC2": "ENTITY_A"}`, a
  BUY from `T1`/`ACC1` crossed by a SELL from `T2`/`ACC2` must alert with
  `beneficial_owner_id == "ENTITY_A"`. Without a map, the same two events must not alert.
- **Age**: a resting order placed an hour earlier must still self-match under the default
  `wash_trade_window_seconds=None`, and must not when a 2.0-second window is set.
- **Relatedness**: distinct known `strategy_id` values downgrade the alert to `MEDIUM`
  without suppressing it; equal or unknown values give `CRITICAL`.
- **Layering after the fill**: three 5,000-lot offers, a 100-lot BUY execution, then the
  offers pulled — the first cancel must raise nothing (one order is not "multiple") and the
  second must raise one `HIGH` alert with `pattern_shape == CANCEL_AFTER_FILL`. Further
  cancels must not re-alert.
- **Market-maker negative check**: two 100-lot offers withdrawn around a 100-lot fill
  (2.0x, below the 3.0x default) must raise nothing. Same-side cancels, and cancels outside
  the window, must raise nothing.
- **Metrics**: ten placements and nine cancels give `cancellation_ratio_pct == 90.0`;
  cancels at 100 ms and 300 ms give `avg_order_lifespan_ms == 200.0`; a cancel whose
  placement was never seen increments `unmatched_cancels` and is excluded from the average,
  as is a cancel timestamped before its own placement.
- **Latching**: the ratio alert appears exactly once even as the ratio climbs to 100%, and
  reappears only after `reset_cancellation_ratio_alert`.
- **Audit trail**: two self-crosses within the same second must have different `alert_id`
  values, and the same stream replayed on a fresh engine must produce identical ids.
- **Negative checks — each must raise `SurveillanceError`**: a naive timestamp, a duplicate
  `event_id`, re-use of a live `order_id`, a non-positive or non-finite price or quantity,
  a blank identifier, a non-enum `side` or `action`, and a non-aware cutoff passed to
  `expire_orders_before`. A rejected event must leave no state behind.
- Run `python -m unittest discover -s skills/wash-trade-and-spoofing-self-detection/scripts`
  and confirm a 100% pass rate.

## Related Skills

- `exchange-self-match-prevention-configuration`
- `eu-market-abuse-regulation-mar-surveillance`
- `multi-order-netting-before-routing`
- `order-to-trade-ratio-fee-penalty-avoidance`
- `mifid-ii-algo-trading-compliance-eu`
- `sec-rule-15c3-5-risk-controls-us`
- `uk-fca-algorithmic-trading-systems-controls`
- `cross-account-aggregate-risk-view`
