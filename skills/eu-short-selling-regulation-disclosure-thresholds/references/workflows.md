# Deep Workflow Reference — eu-short-selling-regulation-disclosure-thresholds

Full technical procedure behind `SKILL.md`. Reference implementation:
`scripts/eu_short_selling_regulation_disclosure_thresholds.py`; tests:
`scripts/test_eu_short_selling_regulation_disclosure_thresholds.py`.

Two obligations run in parallel and never gate each other:

```
Arts. 5/6  position  →  truncate  →  band  →  compare with last notified  →  action + deadline
Art. 12    order     →  instrument type  →  exemptions  →  covering arrangement + evidence  →  allow/block
```

## Full Procedure

### 0. Establish the reporting entity and the scope

- Determine **at what level** the position is calculated before calculating it.
  Delegated Regulation (EU) No 918/2012 Arts. 12-13: per legal entity; for
  management entities, per fund or sub-fund and then aggregated at management
  entity and group level. ESMA Q&A A6.2 places umbrella structures at sub-fund
  level and master-feeder structures at master-fund level. A percentage computed
  over the wrong perimeter is not the reportable figure.
- Determine the **relevant competent authority** for the share. ESMA's Financial
  Instrument Reference Data identifies the RCA per share; the RCA determines both
  where you file and — critically — the clock and calendar the deadline runs on.
- Check the **Art. 16 exemption**: the regime applies only where the share is
  admitted to trading on a Union venue *and* its principal trading venue is in the
  Union. ESMA publishes the exempted-shares list. Set
  `is_exempt_principal_venue_outside_union=True` for a listed share and the engine
  returns `OUT_OF_SSR_SCOPE`.
- Check **Art. 17**: market making and primary market operations are exempt only
  once the firm has notified its home competent authority in writing, not less
  than 30 calendar days before first use, and the authority has not objected. The
  flag records a completed notification, not an intention.

### 1. Build the delta-adjusted position

- `long_shares_qty` and `short_shares_qty` are **delta-adjusted share
  equivalents** per DR 918/2012 Annex II Part 1: cash positions carry delta 1;
  every derivative contributes its delta-adjusted equivalent, computed using the
  current implied volatility and the closing or last price of the underlying.
- Include ETF and index exposure by look-through, and ADR/GDR exposure
  (ESMA Q&A A4.6, A4.7) — all of which are in scope for Arts. 5/6 even though
  they are outside Art. 12.
- Exclude instruments giving claims to shares **not yet issued** where ESMA says
  so (subscription rights, convertible bonds — see A6.3), and note that shares in
  discretionarily managed funds are handled at fund/manager level (A6.1).
- The denominator is total issued share capital, all classes, ordinary and
  preference, voting or not (Art. 2(1)(l); A6.6).
- Fractional quantities are expected and accepted; the engine rejects negative,
  NaN and infinite quantities rather than propagating them into a percentage.

### 2. Freeze the position at the Art. 9(2) relevant time

- The reportable position is the one held at **midnight at the end of the trading
  day**. Set `position_date` to that trading day.
- Intraday maxima are not reportable. Do not wire this engine to a live position
  feed and file on every peak.

### 3. Truncate, then test the threshold

- `_truncate_to_centipct` applies `ROUND_DOWN` at two decimals per ESMA Q&A A5.6.
  `net_short_percentage` is the figure that goes on the filing;
  `net_short_percentage_exact` is retained for reconciliation only.
- All threshold arithmetic runs on **integers in units of 0.01 percentage
  points**. No boundary outcome depends on binary floating point, and the engine
  refuses a configured threshold finer than 0.01%.
- Worked boundaries:

  | Net short | Exact % | Filed % | Band | Status |
  |---|---|---|---|---|
  | 99,990 / 100,000,000 | 0.09999% | 0.09% | none | `BELOW_REPORTING_THRESHOLDS` |
  | 100,000 / 100,000,000 | 0.10000% | 0.10% | 0.10% | `PRIVATE_NCA_NOTIFICATION_REQUIRED` |
  | 319,900 / 100,000,000 | 0.31990% | 0.31% | 0.30% | `PRIVATE_NCA_NOTIFICATION_REQUIRED` |
  | 499,990 / 100,000,000 | 0.49999% | 0.49% | 0.40% | `PRIVATE_NCA_NOTIFICATION_REQUIRED` |
  | 500,000 / 100,000,000 | 0.50000% | 0.50% | 0.50% | `PUBLIC_DISCLOSURE_REQUIRED` |

### 4. Compare bands and decide the action

- Band = the highest threshold reached: `0.10 + k × 0.10` for the largest `k ≥ 0`
  not exceeding the truncated figure; `None` below 0.10%.
- `previously_notified_percentage` is the figure last filed for this issuer by
  this reporting entity. The engine bands it the same way and compares.

  | Previous band | Current band | `disclosure_action` |
  |---|---|---|
  | `None` (no filing history) | `None` | `NO_ACTION` |
  | `None` (no filing history) | any band | notify (conservative default) |
  | 0.30% | 0.30% | `NO_ACTION` — inside the notified band (A5.7) |
  | 0.30% | 0.40% | `NOTIFY_NCA` |
  | 0.30% | `None` | `NOTIFY_NCA` — the fall below 0.1% is notifiable |
  | 0.50% | 0.40% | `NOTIFY_NCA_AND_DISCLOSE_PUBLICLY` — the public register must be updated |
  | 0.60% | 0.70% | `NOTIFY_NCA_AND_DISCLOSE_PUBLICLY` |

- A `None` history is deliberately treated as "not yet notified". It is the safe
  direction, but it means an unpopulated field produces a notification every run.
  Populate it from the filing record.

### 5. Resolve the deadline

- Art. 9(2): not later than **15:30 on the following trading day**, in the local
  time and on the trading-day calendar **of the Member State of the RCA**
  (ESMA Q&A A5.2, A9.3).
- Supply `nca_timezone` as an IANA name (`Europe/Berlin`, `Europe/Helsinki`,
  `Europe/Dublin`, `Europe/Lisbon`, …) and `next_trading_day` as that Member
  State's calendar. The returned `notification_deadline_local` is timezone-aware,
  so DST is handled by the tz database rather than by an offset constant.
- The engine **fails closed**. Missing pieces are reported as
  `RCA_TIMEZONE_NOT_CONFIGURED`, `TRADING_CALENDAR_NOT_CONFIGURED` or
  `POSITION_DATE_NOT_SUPPLIED`, with `notification_deadline_local = None`. There
  is no CET fallback, because a wrong deadline is a late filing while a missing
  one is a prompt.
- `next_weekday_excluding_holidays` skips weekends only. It is a stopgap for
  tests and demos and will produce the wrong filing date around public holidays.
  Replace it before it drives a real filing.
- The engine also rejects a calendar callable that returns a `datetime`, or a day
  on or before the position date — both are silent-wrong-deadline bugs otherwise.

### 6. Gate short sale orders (Art. 12)

`evaluate_short_sale_order` is a separate call with its own inputs:

1. **Instrument type.** Only `SHARE` is in scope. `ETF`, `DEPOSITARY_RECEIPT` and
   `DERIVATIVE` return `ART12_NOT_APPLICABLE` and are allowed — they still feed
   the Arts. 5/6 position (A4.6, A4.7).
2. **Exemptions.** Art. 16 exempted shares and Art. 17 market making return
   `ART12_NOT_APPLICABLE`.
3. **Covering arrangement.** One of `BORROWED`, `AGREEMENT_TO_BORROW`,
   `LOCATE_ARRANGEMENT` (Art. 12(1)(a)-(c)); `NONE` blocks the order with
   `NO_ART12_COVERING_ARRANGEMENT`.
4. **Durable-medium evidence.** ITS 827/2012 Art. 7 requires the arrangement,
   confirmation and instruction in a durable medium. An arrangement asserted
   without `locate_evidence_reference` blocks with `NO_DURABLE_MEDIUM_EVIDENCE` —
   an unevidenced locate is not a demonstrable locate, and ESMA has said that
   merely appearing on an easy-to-borrow list does not by itself satisfy Art. 6
   of the ITS.
5. An unrecognised `covering_arrangement` raises rather than defaulting to
   covered. Fail closed on the execution gate.

### 7. Keep the two regimes independent

- `evaluate_short_position_disclosure` reports `art12_status` and
  `is_short_execution_allowed` as an **advisory** read of the position's coverage
  flag, and logs at CRITICAL when a net short position carries no recorded
  arrangement — but the Arts. 5/6 status, action and deadline are computed
  regardless.
- A net short position built entirely from derivatives is not an uncovered short
  sale; `_assess_position_coverage` therefore reports `ART12_NOT_APPLICABLE` for a
  net-long or exempt position rather than manufacturing a breach.
- The authoritative Art. 12 decision for any actual order is
  `evaluate_short_sale_order`, not the position-level advisory flag.

## Operational notes

- **Retention.** Keep the position snapshot, the truncated figure, the band, the
  previous band, the resolved deadline and the audit note. A filing you cannot
  reconstruct is a filing you cannot defend on review.
- **Alerting.** `NOTIFY_NCA_AND_DISCLOSE_PUBLICLY` logs at WARNING,
  `NOTIFY_NCA` at INFO, uncovered exposure at CRITICAL. Route the CRITICAL and
  WARNING channels to the desk, not just to a file.
- **Scheduling.** Fire the evaluation after the RCA Member State's close and
  schedule the filing job against `notification_deadline_local`, not against a
  fixed UTC or CET cron. Re-derive it each day so DST transitions are picked up.
- **Emergency measures.** Under Arts. 18-23 an NCA or ESMA may lower thresholds
  or ban short selling in specific instruments at short notice. Track those
  decisions and override `private_nca_threshold_pct` /
  `public_disclosure_threshold_pct` for the affected instruments — the engine
  accepts any threshold expressible to 0.01%.
