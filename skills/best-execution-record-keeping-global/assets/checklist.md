# Best Execution & Record-Keeping Checklist

Complete before relying on this engine's output as compliance evidence.

## Scope — do this first

- [ ] **Obligations identified.** Written determination of which rules bind this firm.
      Most reporting obligations bind execution venues, market centres, or broker-dealers,
      not buy-side managers. Rule 605/606 filing and RTS 27 publication are **not**
      buy-side obligations.
- [ ] **No withdrawn or deleted rules cited.** RTS 28 (deleted, Directive (EU) 2024/790)
      and SEC Regulation Best Execution (withdrawn 12 June 2025) do not appear anywhere in
      the firm's policy as live obligations.
- [ ] **Retention period fixed** and mapped to the storage medium: 5 years under MiFID II
      Article 16(6) (7 on competent-authority request), 6 years under FINRA Rule 4511
      absent another period.

## Configuration

- [ ] **`required_tags` names the actual tags** the regime requires. Not left empty — an
      empty tuple only checks that the tag dictionary is non-empty.
- [ ] **`timestamp_precision` matches the RTS 25 row** for this firm's trading activity
      (1 µs high-frequency algorithmic; 1 s voice, human-intervention RFQ, negotiated;
      1 ms otherwise). Not set to microsecond by reflex.
- [ ] **`slippage_tolerance` calibrated per instrument class**, with the rationale written
      down. Acknowledged as a firm risk parameter with no regulatory basis.
- [ ] **`required_timestamps` covers the firm's record set**, including venue acceptance
      if captured.

## Data quality

- [ ] Timestamps are ISO-8601 with an explicit UTC offset — not naive, not local time.
- [ ] Clock synchronisation to UTC is verified at the infrastructure level. The engine
      checks the *recorded precision of the string*; it cannot confirm clock accuracy or
      traceability, which RTS 25 Article 4 governs.
- [ ] Benchmark captured **before or during** execution, never reconstructed from the
      fills it is meant to assess.
- [ ] Every fill carries finite numeric `price` and `qty`.

## Screening output

- [ ] **`violations` is read, not just `is_compliant`.** A record can breach several
      checks at once and all of them belong in the file.
- [ ] **`slippage_evaluated` is checked before quoting a slippage figure.** `nan` with
      `slippage_evaluated=False` means the screen did not run — an evidence gap, not a
      clean execution.
- [ ] **No pass is reported as a best-execution determination.** `is_compliant=True` means
      nothing flagged by one screen against one factor.

## Audit trail

- [ ] `verify_audit_log()` returns an empty list before every export and every request.
- [ ] **`head_hash` is anchored externally** on a defined schedule. Without this, a
      wholesale rewrite of the log reproduces a self-consistent chain and verification
      passes on a forged record.
- [ ] Export destination meets the applicable requirement — WORM, or an audit-trail
      system satisfying SEC Rule 17a-4 as amended (effective 3 January 2023). A JSON file
      on writable storage satisfies neither.
- [ ] Records that **failed** screening are present in the log. Their absence indicates a
      pipeline that drops exactly the records an examiner asks about.

## Periodic review

- [ ] Execution-quality review runs on the cadence the regime expects — for FINRA members
      not reviewing order by order, at minimum quarterly, security-by-security and
      type-of-order.
- [ ] Regulatory citations in the firm's policy re-verified within the last 12 months.
      This area has changed repeatedly since 2021.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Jurisdiction(s) and firm regulatory status: ___________________________
- Slippage tolerance used, and rationale: ___________________________
- Timestamp granularity row applied (RTS 25): ___________________________
- Retention period and storage medium: ___________________________
- External anchor location for `head_hash`: ___________________________
