# Pre-Flight / Sign-off Checklist — eu-short-selling-regulation-disclosure-thresholds

## Scope — settle this before computing any percentage
- [ ] The share is admitted to trading on an EU/EEA venue **and** its principal trading venue is in the Union (Art. 16); it is not on ESMA's exempted-shares list.
- [ ] The relevant competent authority for the share is identified (ESMA Financial Instrument Reference Data) and recorded on the position.
- [ ] Any Art. 17 market-making exemption relied on is backed by a written notification made to the home competent authority at least 30 calendar days before first use, with no objection raised.
- [ ] Sovereign debt (Art. 7) and sovereign CDS (Art. 14) positions are routed to their own process — this engine does not cover them.

## Position calculation
- [ ] Positions are aggregated at the correct level: per legal entity, and per fund/sub-fund then management entity and group where DR 918/2012 Arts. 12-13 apply.
- [ ] Quantities are **delta-adjusted share equivalents** (DR 918/2012 Annex II Part 1), not raw share counts — cash at delta 1, derivatives at their current delta.
- [ ] ETF/index exposure is included by look-through, and ADR/GDR exposure is included (ESMA Q&A A4.6, A4.7).
- [ ] The denominator is total issued share capital across **all** classes, ordinary and preference, voting or not (Art. 2(1)(l); A6.6) — not free float, not a vendor share count.
- [ ] The position is taken at midnight at the end of the trading day (Art. 9(2)); intraday peaks are not filed.

## Thresholds and bands
- [ ] The percentage is **truncated** to two decimals, never rounded (ESMA Q&A A5.6) — 0.49999% is 0.49%, not 0.50%.
- [ ] The threshold test runs on the truncated figure, and the truncated figure is what appears on the filing.
- [ ] Private NCA notification triggers at 0.10% and each 0.10% above (Art. 5(2), as lowered from 0.2% by DR (EU) 2022/27 with effect from 31 January 2022).
- [ ] Public disclosure triggers at 0.50% and each 0.10% above (Art. 6(2)).
- [ ] `previously_notified_percentage` is populated from the actual filing history — a `None` produces a notification every run by design.
- [ ] Movement inside an already-notified band produces no filing (ESMA Q&A A5.7).
- [ ] Falling **below** a threshold is filed, and leaving the 0.5% regime updates the public register, not just the NCA.

## Deadline
- [ ] `nca_timezone` is the IANA zone of the **RCA's Member State**, and `next_trading_day` implements that Member State's trading calendar (ESMA Q&A A5.2).
- [ ] No part of the stack — code, cron, runbook, alert text — states "15:30 CET" as the deadline. 15:30 in Helsinki is an hour earlier in UTC than 15:30 in Berlin.
- [ ] The filing scheduler runs off `notification_deadline_local`, re-derived each day so DST transitions are picked up.
- [ ] `next_weekday_excluding_holidays` has been replaced with a real holiday-aware calendar before anything files for real.
- [ ] A `notification_deadline_basis` other than `COMPUTED` is treated as a configuration defect to fix, not a deadline to guess.

## Article 12 execution gate
- [ ] Short sale orders in shares are gated by `evaluate_short_sale_order` **before** dispatch, not by the position-level advisory flag.
- [ ] Every covering arrangement is one of borrowed / agreement to borrow / located-and-confirmed (Art. 12(1)(a)-(c)) and carries a durable-medium evidence reference (ITS 827/2012 Art. 7).
- [ ] Presence on an easy-to-borrow list alone is not accepted as an Art. 12 arrangement.
- [ ] ETF, depositary-receipt and derivative orders are not blocked by Art. 12 — and are still counted towards the Arts. 5/6 position.
- [ ] A locate gap blocks the next order but never suppresses a disclosure already owed on the existing position.

## Evidence and operations
- [ ] The snapshot, truncated figure, band, previous band, resolved deadline and audit note are retained for each evaluation.
- [ ] CRITICAL (uncovered exposure) and WARNING (public disclosure due) log channels reach the desk, not only a log file.
- [ ] Emergency measures under Arts. 18-23 are tracked, and affected instruments run with overridden thresholds.
- [ ] On a platform without a system IANA tz database (Windows), `tzdata` is installed — otherwise deadline resolution raises.
