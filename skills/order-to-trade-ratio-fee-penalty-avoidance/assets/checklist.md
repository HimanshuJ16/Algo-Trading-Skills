# Pre-Flight Checklist — Order-to-Trade Ratio Fee & Penalty Avoidance

## Regime identification
- [ ] Ratio **definition** read from the venue's own document, not assumed? RTS 9 subtracts 1 (`(orders/transactions) − 1`); NSE-style slabs do not.
- [ ] `OTRConvention` set to match, and the configured limits expressed in the **same** convention?
- [ ] **Granularity** confirmed — per instrument (RTS 9 Art. 2, ICE per Designated Product) or per member across the segment (NSE daily)?
- [ ] **Observation period** confirmed — end of session, trading day, or a shorter venue window (RTS 9 recital 7 permits shorter)?
- [ ] **Consequence** classified — venue-rule breach, per-message charge, flat per-session charge, or non-monetary sanction?
- [ ] `max_count_otr` / `max_volume_otr` taken from the venue's current published schedule rather than a remembered "50:1"?

## Message counting
- [ ] Cancels and modifies included, not just new orders? RTS 9 Art. 1(a) counts all input messages.
- [ ] Limit **modify** weighted as **2** orders (Annex: a modification entails a cancellation and a new insertion)?
- [ ] Two-sided quotes weighted **2**, quote modifies **4**, quote deletes **2**?
- [ ] Order types not in the Annex counted against the most similar listed type (Art. 3(4))?
- [ ] Art. 1(a) exempt cancels passed as `exempt_cancels` — auction uncrossing, connectivity loss, kill-switch — so a correctly-firing risk control cannot manufacture a breach?
- [ ] Venue-specific exclusions applied **before** the engine? SEBI/NSE: orders within 0.75% of LTP, DMM orders, SME/ETF, auction/pre-open/block/post-close sessions.

## Inputs
- [ ] `transactions` counts **partially** executed orders too (Art. 1(b))?
- [ ] `ordered_volume` and `traded_volume` in the same RTS 9 Art. 1(c) unit for the asset class (shares / nominal / lots / tCO2)?
- [ ] Counts scoped to one instrument and one session, matching what `OTRInstrumentSession` claims to be?

## Ratio evaluation
- [ ] **Both** ratios evaluated, with breach on either (Art. 3(2): "either or both")?
- [ ] `binding_ratio` read before remediating? A volume breach is not fixed by sending fewer messages.
- [ ] `excess_messages == 0` **not** read as compliant — it is zero on a volume-only breach and at an exact count-limit touch?
- [ ] Zero-transaction instruments returning `OTR_NOT_CALCULABLE_NO_TRANSACTIONS` rather than a fabricated ratio, and handled as freeze rather than allow?
- [ ] `warning_threshold_pct` understood as your own operational margin, not a venue tier?

## Penalty estimate
- [ ] Penalty schedule matches the venue's actual structure — flat single tier (Eurex ESU), progressive slabs (NSE), or not tier-modelled at all (ICE flat per-session charge)?
- [ ] `penalty_currency` set to the venue's currency rather than left at a default?
- [ ] Eurex: the monthly under-four-exceedance waiver and sliding scale understood as **not** modelled, so the estimate is an upper bound?
- [ ] NSE: non-monetary consequences tracked separately — 15-minute cooling-off at a daily ratio of 500+, proprietary-trading suspension after more than ten penalised days in thirty rolling trading days?

## Aggregation & reconciliation
- [ ] Per-instrument reports folded with `aggregate_worst_instrument`, never averaged into a venue-wide ratio?
- [ ] Engine output reconciled against the venue's own daily OTR report, with the venue treated as authoritative on any divergence?
- [ ] Divergence investigated against the three usual causes — an exclusion the venue does not apply, a missed Annex weight, or venue-side messages the client never sent?

## Remediation
- [ ] Throttle action actually reduces counted messages (widen the repricing deadband, lengthen quote refresh) rather than merely reordering them?
- [ ] Any deliberate taker-fill "ratio reset" priced against the surcharge it avoids, and checked for self-match / wash exposure?
