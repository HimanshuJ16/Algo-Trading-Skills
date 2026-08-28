# Workflows — order-to-trade-ratio-fee-penalty-avoidance

## 0. Establish the venue's regime before writing any code

Read the venue's own document, not a rule of thumb. Record four things:

1. **Definition** — "unexecuted orders to transactions" (RTS 9, subtract 1) or a plain
   "orders per trade" slab (NSE). Set `OTRConvention` accordingly.
2. **Granularity** — per instrument (RTS 9, ICE per Designated Product) or per member
   across the segment (NSE daily member-level). This decides what one
   `OTRInstrumentSession` represents.
3. **Observation period** — end of session (RTS 9 Art. 3(1), which permits shorter venue
   windows under recital 7), trading day (NSE, Eurex), or calendar month for aggregation
   (Eurex ESU, NSE collection).
4. **Consequence** — a venue-rule breach (RTS 9), a per-message charge (Eurex ESU, NSE
   slabs), a flat per-session charge (ICE), or non-monetary sanctions (NSE cooling-off and
   proprietary-trading suspension).

Mismatching any of the four produces a number that looks like the venue's and is not.

## 1. Filter the message stream to what the venue counts

Before counting anything, remove what the regime excludes. This step needs order-book and
session state and is therefore **outside** the engine.

- RTS 9 Art. 1(a): drop cancellation messages sent subsequent to auction uncrossing, a
  loss of venue connectivity, or use of a kill functionality — pass them as
  `exempt_cancels` so the count is auditable rather than silently reduced.
- SEBI/NSE: drop algo orders entered or modified within 0.75% of the LTP; drop DMM
  market-making orders; drop SME/ETF/designated-market-maker securities in the Equity
  segment; drop odd lot, auction, block, pre-open, post-close, periodic call auction and
  IPO call auction sessions.
- Everything surviving is counted, including cancels — the common error is to count only
  new orders.

## 2. Weight the surviving messages by RTS 9 Annex type

    weighted = limit_submits*1 + limit_modifies*2 + countable_limit_cancels*1
             + quote_submits*2 + quote_modifies*4 + countable_quote_cancels*2

`weighted_order_message_count(session)` does this. The two weights that matter most in
practice are the limit modify (2) and the quote modify (4): a repricing market maker that
counts one-per-message sees roughly half its real ratio.

## 3. Compute both ratios

    gross_count  = weighted_messages / transactions
    gross_volume = ordered_volume / traded_volume

    RTS9 convention:  ratio = gross - 1
    Gross convention: ratio = gross

`transactions` counts totally *or partially* executed orders. `ordered_volume` and
`traded_volume` must share the RTS 9 Art. 1(c) unit for the asset class.

**If `transactions == 0`:** stop. The ratio is not calculable — do not substitute 1. If
messages were also zero the member is idle and order flow is allowed; otherwise every
message sent is unexecuted and order flow is frozen.

## 4. Classify against the limits

    breach  = count_ratio >= max_count_otr  OR  volume_ratio >= max_volume_otr
    warning = count_ratio >= max_count_otr * warning_pct
              OR volume_ratio >= max_volume_otr * warning_pct

RTS 9 Art. 3(2) breaches on "either or both". `binding_ratio` records which one bound, so
the remediation matches the cause: a count breach needs fewer messages, a volume breach
needs smaller orders or more fills — throttling message rate does nothing for it.

## 5. Estimate the charge

`tiered_penalty(total_messages, transactions, tiers, convention)` charges each tier on the
messages falling between `ratio_from * transactions` and `ratio_to * transactions`.

- **Eurex ESU form** — one tier from the limit, unbounded: reduces to
  `(messages − limit) × fee`. The monthly under-four-exceedance waiver and the sliding
  scale are not modelled, so the result is an upper bound.
- **NSE slabs** — `NSE_ALGO_OTR_PENALTY_TIERS_2018`, progressive brackets.
- **ICE** — flat per breaching session; do not model with tiers, count `status ==
  OTR_BREACH_PENALTY_ACTIVE` sessions instead.

`excess_messages` is a count-ratio quantity only. It is zero on a volume-only breach and
zero when the count ratio sits exactly at the limit; neither means compliant.

## 6. Act, then fold across instruments

| Status | Action | Meaning |
|---|---|---|
| `OTR_COMPLIANT_SAFE` | `ALLOW_ORDER` | Both ratios inside the warning margin |
| `OTR_WARNING_THROTTLE_ACTIVE` | `THROTTLE_ORDER_MODIFICATIONS` | Either ratio at the margin — widen the repricing deadband, lengthen the quote refresh interval |
| `OTR_BREACH_PENALTY_ACTIVE` | `FREEZE_ORDER_MODIFICATIONS_REQUIRE_TAKER_FILL` | Either ratio at or above the limit |
| `OTR_NOT_CALCULABLE_NO_TRANSACTIONS` | `FREEZE_ORDER_MODIFICATIONS_REQUIRE_TAKER_FILL` | Messages sent, nothing executed |

`aggregate_worst_instrument(reports)` returns the most severe per-instrument report for a
venue-level kill decision. Never average across instruments: RTS 9 charges on the single
worst instrument, and an average buries it.

## 7. Reconcile against the venue

The venue is the system of record. Pull its daily OTR report (ICE delivers per-member CSVs
by 06:00 GMT/BST the next business day; NSE publishes member OTR data daily in the Member
Portal `<Member folder>/Investigation/Dnld`) and diff against this engine's counts.
Persistent divergence usually means an exclusion applied in step 1 that the venue does not
apply, an Annex weight missed in step 2, or venue-side messages the client never sent
(triggered stops, peg re-quotes, market-operations cancellations).
