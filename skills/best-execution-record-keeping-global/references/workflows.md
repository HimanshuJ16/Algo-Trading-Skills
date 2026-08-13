# Best Execution Workflows

Full procedure behind `SKILL.md`. Load this when implementing, not when deciding whether
the skill applies.

## 0. Establish scope before writing code

Answer these first — they are constructor inputs and storage decisions, and getting them
wrong silently produces a compliant-looking log that evidences nothing:

1. **Which rules bind this firm?** Most reporting obligations in `references/standards.md`
   bind execution venues, market centres, or broker-dealers. If the firm is a buy-side
   manager, its obligations are typically the best-execution *process* duty plus
   recordkeeping — not Rule 605/606 filing or RTS 27 publication.
2. **Which timestamp granularity applies?** Map the trading activity to the RTS 25 table.
   High-frequency algorithmic trading attracts 1 µs; voice, human-intervention RFQ, and
   negotiated transactions attract 1 s; everything else 1 ms.
3. **Which tags are mandatory?** Name them in `required_tags`. Leaving it empty only
   checks that the tag dictionary is non-empty, which a single junk key satisfies.
4. **What is the retention period and the storage medium?** Five years under MiFID II
   Article 16(6) (seven on request), six under FINRA Rule 4511 absent another period.
5. **What slippage tolerance is defensible, per instrument class?** Write down the
   rationale — it is a firm parameter, not a regulatory threshold, and you will be asked
   to justify it rather than to prove it matches a rule.

## Pre-Trade

1. **Algorithm selection.** Ensure the chosen algo matches the client's execution
   objectives and any specific instruction. A specific client instruction narrows the
   best-execution duty to the terms of that instruction — record it.
2. **Benchmark capture.** Record the prevailing benchmark immediately before submission.
   Capturing it later, or deriving it from the fills, destroys its value as an
   independent comparator.

## Trade Execution

1. **Timestamping.** Record UTC timestamps with an explicit offset for order creation,
   submission, venue acceptance, and execution. The engine requires `creation`,
   `submission`, and `execution` by default and checks that they are parseable, UTC, and
   non-decreasing in that order. Add venue acceptance to `required_timestamps` if your
   record set includes it.
2. **Fills processing.** Aggregate fills with price, quantity, and individual timestamps.
   Every fill needs numeric, finite `price` and `qty`; a malformed fill is recorded as a
   violation rather than crashing the pipeline.

## Post-Trade

1. **Screening.** Call `run_best_ex_checks(record)`. It:
   - validates fills, returning the volume-weighted average price;
   - validates timestamps for presence, parseability, UTC, ordering, and (if configured)
     recorded precision;
   - checks regulatory tags, both presence and the specific keys you named;
   - computes slippage in an adverse-cost convention — positive means worse than
     benchmark, for buys and sells alike — and flags it only when strictly greater than
     the tolerance;
   - accumulates **all** violations rather than overwriting;
   - hashes the record and appends a chained audit entry, unconditionally.

2. **Interpretation.** Read `violations` and `slippage_evaluated`, not just
   `is_compliant` and `slippage`:

   | Outcome | Meaning |
   |---|---|
   | `is_compliant=True` | Nothing flagged by this screen. **Not** a best-execution determination. |
   | `slippage_evaluated=False`, `slippage` is `nan` | Execution quality was **not assessed** — no benchmark, no usable fills, or an unrecognised side. An evidence gap, not a pass. |
   | `violations` non-empty | Every listed exception belongs in the compliance file. |

3. **Chain verification.** Run `verify_audit_log()` before export or before responding to
   a request. Empty list means internally consistent. Any message identifies the entry by
   sequence number and the nature of the break: a modified record, a modified entry, a
   broken link, or a sequence gap from an insertion, deletion, or reordering.

4. **External anchoring.** Take `head_hash` on a schedule and put it somewhere the log's
   custodian cannot rewrite. This is the step that converts internal consistency into
   evidence; skipping it leaves a chain that a wholesale rewrite can reproduce.

5. **Archiving.** `export_audit_log(path)` writes UTF-8 JSON. That is a serialisation
   step, not an archive. Move the output to storage meeting your regime's requirement —
   WORM, or an audit-trail system satisfying the amended SEC Rule 17a-4 — and retain it
   for the applicable period.

6. **Periodic review.** Aggregate the log into the periodic execution-quality review your
   regime expects. For FINRA members not reviewing order by order, that is at minimum
   quarterly, security-by-security and type-of-order. Per-order screening does not
   discharge it.

## Failure modes observed in production

- **A skipped check filed as a pass.** A record with no benchmark reported 0% slippage and
  "Best execution standards met" — the check never ran. Now flagged, with `slippage` as
  `nan`.
- **A violation erased by a later violation.** The worst exception on a record was
  overwritten by whichever check ran last. Now accumulated.
- **A rejected record leaving no trace.** A missing execution timestamp returned early
  with an empty hash and no audit entry, discarding the record most likely to be
  questioned. Now always logged and hashed.
- **A hash that proved nothing.** Per-record hashes stored beside their records let an
  editor recompute the hash. Now chained and verifiable.
- **Local-time or naive timestamps.** Unreconstructable ordering across venues after a
  daylight-saving transition. Now flagged.
- **Mangled export on a non-UTF-8 default codepage.** Writing without an explicit encoding
  corrupted or refused non-ASCII instrument and venue names on Windows. Now explicit.
- **A stale regulatory citation.** Reports built against RTS 28 or SEC Regulation Best
  Execution — one deleted, one withdrawn. Check `references/standards.md` before citing.

## Production implementation reference

- Reference code: `scripts/best_execution_record_keeping_global.py`
  (`BestExecutionRecordKeepingGlobalEngine`, `TradeRecord`, `BestExAnalysis`,
  `TimestampPrecision`, `BestExRecordError`).
- Automated unit tests: `scripts/test_best_execution_record_keeping_global.py`.
- Run with `python -m unittest discover -s skills/best-execution-record-keeping-global/scripts`.
