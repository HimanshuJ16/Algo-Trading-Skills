---
name: best-execution-record-keeping-global
description: >-
  Use when execution quality must be screened against a benchmark and every screening
  decision retained as tamper-evident evidence under MiFID II Article 27 and FINRA Rule
  5310. A screen that flags outliers, not a determination that best execution was
  achieved.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: regulatory-compliance-global
  tags: compliance, risk, regulatory, best-execution, audit
  brokers_frameworks: any
  version: "2.1.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when capturing trade executions and you need (a) an execution-quality
screen that flags outliers against a benchmark, and (b) a tamper-evident record of every
trade and every screening decision, retained as evidence.

`BestExecutionRecordKeepingGlobalEngine` in `scripts/best_execution_record_keeping_global.py`
screens one record at a time, accumulates every exception it finds, and appends the
record plus verdict to a hash-chained audit log that `verify_audit_log()` can re-check.

## When NOT to Use

- **As a determination that best execution was achieved.** `is_compliant=True` means
  "nothing flagged by this screen". Best execution under MiFID II Article 27(1) and FINRA
  Rule 5310 is a multi-factor, process-based obligation weighing price, costs, speed,
  likelihood of execution and settlement, size, and nature. A slippage threshold tests
  one factor. Never report a green flag as evidence of compliance.
- **As your archive.** `export_audit_log()` writes a JSON file. Retention obligations
  differ by regime (see `references/standards.md`) and no plain file on writable storage
  satisfies any of them. Hash chaining makes tampering *detectable*; it does not make
  records *immutable*.
- **To produce Rule 605 or Rule 606 reports.** Those are broker-dealer and market-centre
  obligations with prescribed formats. A buy-side firm is the *recipient* of a Rule
  606(b)(3) report, not the filer. This engine produces neither.
- **To satisfy a specific jurisdiction out of the box.** The engine is deliberately
  jurisdiction-neutral. Which tags are mandatory, which timestamp granularity applies,
  and how long records must be kept are all constructor inputs or your own storage
  decisions, because the answers differ per regime and per firm status.
- **As a substitute for a periodic execution-quality review.** Per-order screening does
  not replace the regular and rigorous review FINRA expects at minimum quarterly, on a
  security-by-security and type-of-order basis, from members not reviewing order by order.

## Prerequisites

- Trade capture producing, per order: identifiers, side, quantity, venue, algo/trader/
  client IDs, individual fills with price and quantity, and ISO-8601 **UTC** timestamps
  with an explicit offset.
- A benchmark price per order (arrival price, interval VWAP, TWAP) captured **before or
  during** execution, not reconstructed afterwards from the fills themselves.
- A decision, written down, on: which `regulatory_tags` your regime requires, what
  `slippage_tolerance` is defensible per instrument and order type, which
  `TimestampPrecision` your trading activity attracts, and your retention period.
- Retention-locked or externally anchored storage for the exported log.

## Workflow

1. **Configure the engine to your obligation, not to the defaults.** Pass
   `required_tags` naming the tags your regime actually requires — the default empty
   tuple only checks that *some* tag exists, which nearly anything passes. Pass
   `timestamp_precision` matching your activity under RTS 25 (1 s voice/RFQ/negotiated,
   1 ms other activity, 1 µs high-frequency algorithmic trading); the default checks
   nothing, because there is no universal figure.

2. **Set `slippage_tolerance` deliberately and record why.** It is a firm risk parameter
   with **no regulatory basis** — no regulator prescribes a number. A single tolerance
   across a large-cap and an illiquid small-cap will generate false positives on one and
   miss real outliers on the other.

3. **Capture the benchmark before you need it.** A record submitted with
   `benchmark_price=None` is flagged as *not assessed*, with `slippage` set to `nan` and
   `slippage_evaluated=False`. That is the correct outcome — a missing benchmark is an
   evidence gap, not a clean execution.

4. **Screen each record with `run_best_ex_checks()`.** It returns *every* violation in
   `violations`, not just the last one. Read the list, not only `is_compliant`: a record
   can breach slippage *and* be missing tags, and both belong in the compliance file.

5. **Verify the chain before relying on the log.** `verify_audit_log()` returns an empty
   list when the log is internally consistent, or one message per problem identifying the
   entry by sequence number. Run it before any export or examiner request.

6. **Anchor the head hash externally.** `head_hash` is the tip of the chain. Publish or
   escrow it on a schedule. Without an external anchor, a party who can rewrite the whole
   log can recompute a self-consistent chain and verification will pass.

7. **Archive under your retention rule.** Export, then move the file to storage that
   meets your regime's requirement, and keep it for the applicable period — five years
   under MiFID II Article 16(6) (up to seven on a competent authority's request), six
   years under FINRA Rule 4511 where no other period is specified.

> Full procedure: see `references/workflows.md`.
> Per-regime rules, current status, and sources: see `references/standards.md`.
> Printable sign-off checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Reading a pass as a best-execution determination.** The single most dangerous
  misreading of this skill. See When NOT to Use.
- **Treating a missing benchmark as a clean execution.** Before version 2.1.0 the engine
  reported `slippage=0.0` and "Best execution standards met" for a record with no
  benchmark — the check was skipped and the skip looked like a pass. Test
  `slippage_evaluated`, never the number.
- **Reading only the last violation.** The engine used to overwrite its own `notes`, so a
  33% slippage breach on a record that was also missing tags was filed as "Missing
  regulatory tags". Read `violations`.
- **Assuming a failed record was logged.** It now always is. Previously a missing
  execution timestamp returned early with an empty hash and *no* audit entry, discarding
  precisely the record an examiner asks about.
- **Believing a per-record hash proves anything.** A hash stored next to the record it
  hashes is recomputable by whoever edits the record. Only the chain plus an external
  anchor gives evidence.
- **Citing RTS 28 as a live obligation.** Article 27(6) of MiFID II and its RTS 28 annual
  top-five-venue reports were deleted by Directive (EU) 2024/790; ESMA told national
  authorities to deprioritise supervision from 13 February 2024.
- **Citing SEC Regulation Best Execution.** It was proposed in December 2022 and
  **withdrawn** on 12 June 2025 without being adopted. FINRA Rule 5310 remains the
  operative US best-execution rule for FINRA members.
- **Asserting microsecond or nanosecond timestamps are universally mandatory.** RTS 25
  sets granularity by activity and never mentions nanoseconds. Over-specifying wastes
  infrastructure; under-specifying breaches.
- **Assuming WORM is required.** SEC Rule 17a-4 has permitted an audit-trail alternative
  since the October 2022 amendments took effect on 3 January 2023. WORM remains one
  permitted option, not the only one.
- **Confusing whose obligation it is.** Rule 605 binds market centres and, since the 2024
  amendments, broker-dealers introducing or carrying at least 100,000 customer accounts.
  Rule 606 binds broker-dealers routing customer orders. RTS 27 bound execution venues. A
  buy-side quant firm is generally none of these.
- **Naive or local-time timestamps.** A timestamp with no offset, or one in local time,
  is flagged. Recording "10:00:02" with no offset makes cross-venue sequencing
  unreconstructable after a daylight-saving transition.

## Verification

- Confirm a record with **two** simultaneous breaches reports both in `violations` and
  both in `notes` (`test_all_violations_recorded_not_just_the_last`).
- Confirm a record with no benchmark, and one with no fills, are each **non-compliant**
  with `slippage_evaluated=False` and `slippage` as `nan` — not `0.0`.
- Confirm a record failing validation still produces a non-empty `record_hash` and a
  single audit-log entry.
- Confirm slippage is adverse-cost signed: buying above *and* selling below the benchmark
  both yield positive slippage; confirm the volume-weighted average is used, not the
  simple mean (90@10 and 110@90 against a 100 benchmark gives +8%, not 0%).
- Confirm slippage exactly equal to the tolerance does **not** flag (strict `>`).
- Confirm tampering is detected: edit a logged record, delete an entry, reorder entries,
  and flip an `is_compliant` verdict — `verify_audit_log()` must report each.
- Confirm identical records hash identically across engine instances.
- Confirm the export round-trips non-ASCII instrument and venue names as UTF-8.
- Run `python -m unittest discover -s skills/best-execution-record-keeping-global/scripts`
  and confirm a 100% pass rate.

## Related Skills

- `us-reg-nms-order-protection-rule-compliance`
- `eu-market-abuse-regulation-mar-surveillance`
- `mifid-ii-algo-trading-compliance-eu`
- `transaction-cost-analysis-tca-integration`
- `post-trade-execution-quality-scorecard`
- `record-retention-periods-by-jurisdiction`
- `clock-synchronization-ptp-for-trading-hosts`
- `structured-logging-for-post-incident-forensics`
