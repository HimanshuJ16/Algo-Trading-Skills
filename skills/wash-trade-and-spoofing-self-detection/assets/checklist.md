# Sign-off checklist — wash trade & layering self-surveillance

Derived from the Verification section of `SKILL.md`. Every threshold below is a
**calibrated heuristic, not a regulatory limit** — record the rationale for each.

## 1. Beneficial ownership (do this first)

- [ ] **Ownership map sourced from legal-entity/account reference data**, not from the
      trading system, and loaded as `beneficial_owner_map`.
- [ ] **Every sub-account, desk and trader id resolves** to a master beneficial owner.
      Confirmed by ingesting one event per account and checking `get_trader_metrics`
      aggregates them onto the same owner.
- [ ] **Cross-account self-cross proven detectable**: a BUY from one account crossed by a
      SELL from another account of the same owner raises an alert naming that owner.
- [ ] **Unrelated participants proven not to collide**: two owners with no mapping raise
      nothing on the same order pair.
- [ ] **`strategy_id` populated** per order where the originating algorithm/desk is known,
      for the FINRA Rule 5210.02 relatedness test.

## 2. Clocks and event integrity

- [ ] **Timestamps are timezone-aware** end to end. Naive timestamps are rejected by the
      engine; confirm nothing upstream is attaching one silently.
- [ ] **Business clocks synchronised** to the applicable standard before relying on
      sub-second lifespan logic (MiFID II RTS 25: 1 ms, or 100 µs for HFT).
- [ ] **Duplicate `event_id` raises**, and the duplicate-detection window
      (`max_tracked_event_ids`) exceeds the largest replay the pipeline can produce.
- [ ] **Re-use of a live `order_id` raises.**
- [ ] **A rejected event leaves no state** — counters, book and history unchanged, and the
      corrected event replays cleanly.

## 3. Self-match detection

- [ ] **Crossing test, not equality**: resting BUY 150.05 vs incoming SELL 150.00 alerts;
      resting BUY 149.00 vs incoming SELL 150.00 does not.
- [ ] **Unpriced orders cross every own level.**
- [ ] **`wash_trade_window_seconds` left at `None`** unless a narrower "matched trade"
      pattern is deliberately intended — and the false negatives that buys are accepted in
      writing.
- [ ] **Relatedness behaviour confirmed**: distinct known strategies give MEDIUM, same or
      unknown give CRITICAL, and neither is suppressed.
- [ ] **Partial fills leave the residual on the book** and it can still self-match.
- [ ] **Native venue SMP/STP is configured separately** and is not being relied on as a
      defence — CME treats it as a preventive tool that does not cover the Globex pre-open.

## 4. Layering / spoofing detection

- [ ] **`CANCEL_AFTER_FILL` shape verified**: layers placed, opposite-side execution, then
      the layers pulled — one HIGH alert, on the cancel that meets both tests.
- [ ] **One alert per execution**; later cancels against a settled context are silent.
- [ ] **Market-maker negative check run against live-like flow**: withdrawal at
      2.0× the executed size stays silent at the 3.0× default.
- [ ] **Same-side cancels and out-of-window cancels raise nothing.**
- [ ] **`layering_size_ratio` and `min_layered_orders` calibrated per instrument liquidity
      tier**, with the observed false-positive rate recorded.

## 5. Metrics and alert hygiene

- [ ] **Ratio arithmetic verified**: 9 cancels / 10 placements = 90.0%.
- [ ] **Lifespan arithmetic verified**: cancels at 100 ms and 300 ms = 200.0 ms.
- [ ] **`unmatched_cancels` reviewed** — a rising count means the pipeline is dropping
      placements or the history bound is too tight.
- [ ] **Negative lifespans excluded, not averaged**, and investigated as clock defects.
- [ ] **Ratio alert latches** (once per owner) and re-arms only via
      `reset_cancellation_ratio_alert`.
- [ ] **The ratio is documented internally as a firm-side heuristic**, distinct from the
      venue's RTS 9 order-to-trade limits.

## 6. Audit trail and escalation

- [ ] **Alert ids are unique within a second and stable across replays** of the same stream.
- [ ] **Detection parameters persisted alongside every alert** (`beneficial_owner_map`,
      thresholds, engine version) so a decision is reconstructable years later.
- [ ] **`requires_human_review` honoured**: no downstream system treats an alert as a
      finding, auto-files it, or auto-disables a strategy on a single indicator. Spoofing
      under CEA s.4c(a)(5)(C) requires scienter; FINRA Rule 5210.03 requires a frequent
      pattern.
- [ ] **Retention set per jurisdiction, not one global number**: MiFID II RTS 6 Article 28
      order records five years; US broker-dealer retention under SEA Rule 17a-4 and
      FINRA Rule 4511(b). The US order audit trail is **CAT** (SEC Rule 613) — OATS was
      retired on 1 September 2021.
- [ ] **Escalation path defined** to a named compliance owner, with the response time
      recorded.
- [ ] **`alerts` is drained and persisted**; it grows for the life of the engine.
- [ ] **`expire_orders_before` scheduled on session boundaries** against the venue's own
      end-of-day state.

## 7. Documented limitations acknowledged

- [ ] Firm-side view only — reconciled against venue execution reports.
- [ ] Single venue and single instrument key; no cross-venue or cross-instrument screening.
- [ ] Two patterns only; momentum ignition, quote stuffing, ping orders and marking the
      close are not covered here.
- [ ] Ownership and algorithm relatedness are supplied, never inferred.
- [ ] In-order streaming assumed; out-of-order feeds sequenced upstream.
