# Workflows for 1099-B and Broker Tax Reporting Reconciliation

End-to-end procedures for institutional-grade reconciliation.

## End of Year (EOY) Reconciliation Lifecycle

```
[Data Freeze]   [Broker Retrieval]   [Internal Normalization]
        │                │                      │
        ▼                ▼                      ▼
   Jan 15*         Initial: ~Feb 15*        daily ETL
                  Doc-retrieval cadence:     (already running)
                  corrected forms may
                  arrive up to 3 yr later
        \                │                      /
         \               ▼                     /
          ─────────► [Run Engine] ◄───────────
                       │
                       ▼
                [Discrepancy Resolution]
                       │
                       ▼
                  [CPA Handoff]
                       │
                       ▼
                  [IRS Filing]
```

(*) Calendar dates assume US tax year = calendar year. Corporations with fiscal years scale these.

### Phase 1 — Data Freeze (target: Jan 15)

1. **Lock internal trade ledgers** for the prior tax year. No new manual trades enter.
2. **Apply all delayed corporate actions** — split adjustments, cost-basis corrections from late-arriving reorg notices. Spin-offs typically arrive 30–60 days post-record-date.
3. **Treat wash sales** — compute disallowed amounts using IRC §1091 30-day-window logic (`wash-sale-rule-tracking-us` skill).
4. **Reconcile FX gain/loss** if any lots are non-USD — apply IRS spot-rate convention at sold_date.
5. **Snapshot** the ledger as the "EOY-final" version. This snapshot is the input to the reconciliation engine.

### Phase 2 — Broker Document Retrieval (target: Feb 1–Mar 15)

Broker reporting cadence (US). The only hard date here is the statutory one:
recipient statements for Form 1099-B are due **February 15** of the year
following the calendar year reported (extended from the general 31 January
information-return date; see [Publication 1099, *General Instructions for
Certain Information Returns*](https://www.irs.gov/publications/p1099)). If
February 15 falls on a weekend or a DC legal holiday it moves to the next
business day. Every other row below is observed industry practice, not a rule:

| Document | Earliest availability | Latest practical arrival | Basis |
|----------|----------------------|--------------------------|-------|
| Preliminary 1099-B draft | Late January | Mid-February | Broker practice |
| Final 1099-B | Statutory due date **Feb 15** | Broker practice | Pub. 1099 |
| Corrected 1099-B (1st wave) | End of February | April | Broker practice |
| Corrected 1099-B (late) | Any time | Up to 3 years after the original filing | See below |

On corrections, the Instructions for Form 1099-B are specific: on receiving a
transfer statement or issuer statement showing the original return was
incorrect, the broker must file a corrected Form 1099-B **within 30 days**, but
need not do so at all if the statement arrives **more than 3 years** after the
original was filed. That 3-year tail is why a reconciliation artifact has to
stay reproducible long after filing.

- Pull from the clearing broker's portal **or** the consolidated IRS Reporting Service feed.
- Verify the file is the **final** version (not the preliminary draft). Brokers mark preliminary files explicitly.
- Parse the CSV/JSON into `TaxLot` records. The engine does not parse PDF bundles — do this step externally (PDF tables → CSV via a dedicated parser).

### Phase 3 — Reconciliation Run

```python
from s_1099_b_and_broker_tax_reporting_reconciliation import (
    S1099BAndBrokerTaxReportingReconciliationEngine,
    TaxLot,
    ToleranceConfig,
)
from decimal import Decimal

engine = S1099BAndBrokerTaxReportingReconciliationEngine(
    ToleranceConfig(
        absolute_usd=Decimal("0.05"),       # 5¢ penny tolerance
        relative_basis_pct=Decimal("0.0001")  # 1 bp on basis
    )
)
engine.load_internal_lots(parse_lots("internal_eoy_final.csv"))
engine.load_broker_lots(parse_lots("1099b_broker_final.csv"))

result = engine.process_reconciliation()
metrics = result.metrics()
# Every reason has a key, present and zero when unseen.
assert metrics["missing_in_broker"] == 0, (
    "Internal lots absent from the 1099-B — resolve each one (see Phase 4) "
    "before generating Form 8949 rows"
)
assert metrics["missing_in_internal"] == 0, (
    "Broker reported a disposition the ledger never saw — HALT, do not file"
)
```

See `SKILL.md` Decision Points table for response-by-classification.

### Phase 4 — Discrepancy Resolution

For each `DiscrepancyReason`, follow this dispatch:

```
MISSING_IN_BROKER:
  ⋅ Do NOT write this off as "it settled in January". Box 1c is the trade
    date, so a Dec 30/31 sale is on the CURRENT year's 1099-B. A missing row
    is a real break until proven otherwise.
  ⋅ Work the causes in order:
      1. Internal ledger booked on settlement date instead of trade date —
         mis-dates every year-boundary lot. Fix upstream; do not tolerate.
      2. Open short sale — the broker does not report it until the year the
         customer delivers a security to close. The only routine legitimate
         cause. Document and carry to the closing year.
      3. Lot held at a different account or broker than the 1099-B being
         reconciled.
      4. Broker aggregated several dispositions onto one row (box 1a
         "various"), so no per-lot counterpart exists to match.
      5. Corporate action the internal ledger treated as a disposition and
         the broker did not (or vice versa).
  ⋅ Document the disposition of every one before filing.

MISSING_IN_INTERNAL:
  ⋅ Broken import / missed write / transfer-in failure.
  ⋅ HALT — fix the data lake; do NOT silently include a 8949 entry with a missing basis.

WASH_SALE_FLAG_MISMATCH or WASH_SALE_AMOUNT_MISMATCH:
  ⋅ Cross-reference both internal wash tracker and broker's box 1g / 1099-B.
  ⋅ Follow `wash-sale-rule-tracking-us` for the authoritative calculation window.
  ⋅ Apply Form 8949 column (f) code W + column (g) adjustment.

BASIS_OUTSIDE_TOLERANCE:
  ⋅ FIRST establish which side is wrong. Code B asserts the broker's basis is
    incorrect; do not apply it before the internal basis is substantiated
    against trade tickets and applied corporate actions.
  ⋅ If COVERED (Form 8949 box A or D):
        col(e) = the broker's box 1e figure, even though it is incorrect
        col(f) = B
        col(g) = broker − internal        <-- NOT internal − broker
  ⋅ If NONCOVERED (Form 8949 box B or E):
        col(e) = the correct internal basis
        col(g) = -0-, no adjustment code

PROCEEDS_OUTSIDE_TOLERANCE:
  ⋅ Not a code-B situation — code B addresses basis only.
  ⋅ Verify against the trade ticket / confirmation and reconcile the gross vs
    net question first (1099-B box 6 says whether proceeds are reported gross
    or net of commissions; an internal ledger booking net proceeds against a
    gross-reporting broker produces a systematic, one-signed delta across
    every lot).
  ⋅ Escalate an unexplained proceeds difference to the broker for a corrected
    1099-B. Do not paper over it with an adjustment code.
```

### The column (g) sign, once, carefully

The "Worksheet for Basis Adjustments in Column (g)" in the Instructions for
Form 8949 defines it as:

- **line 1** — the basis shown on Form 1099-B (box 1e)
- **line 2** — the correct basis
- line 1 > line 2 ⇒ enter `line 1 − line 2` in column (g) as a **positive** number
- line 2 > line 1 ⇒ enter `line 2 − line 1` in column (g) as a **negative** number

The engine's `basis_delta` is `internal − broker`, which is line 2 − line 1 —
the *opposite* orientation. So:

```
col(g) = broker_basis − internal_basis = −basis_delta
```

Reading `basis_delta` straight into column (g) doubles the basis error instead
of cancelling it. Use `MatchPair.form_8949_column_g_basis_adjustment`, which
applies this sign and returns `0.00` for noncovered pairs:

```python
for pair, discrepancies in result.matched_with_discrepancies_seq:
    adjustment = pair.form_8949_column_g_basis_adjustment
    # covered + broker basis too low  -> negative column (g)
    # covered + broker basis too high -> positive column (g)
    # noncovered                      -> 0.00 (correct basis goes in column (e))
```

### Phase 5 — CPA Handoff

- **Generate the consolidated 8949 payload** — every matched_clean + every accepted-discrepancy forms one row.
- **Attach reconciliation log** — the entire `ReconciliationResult` serialized to JSON (metrics + Discrepancy list with internal/broker IDs and signed deltas).
- **Securely transmit** — encrypted zip + signal messenger, never email body.
- **Wait for CPA sign-off** before any Form 8949 entry is finalized.

### Phase 6 — Sub-Workflow: Corrected 1099-B Re-Run

If the broker issues a corrected 1099-B (rare after Mar 15, not uncommon for the May-October follow-up waves for wash-sale cross-account adjustments):

1. Re-load both ledgers (clear the engine via `.clear()` first).
2. Diff the corrected 1099-B rows against the prior accepted set.
3. Re-issue the Form 8949 rows that changed, marked "corrected" in the underlying pipeline.
4. Decide on Form 1040-X (Amended U.S. Individual Income Tax Return). The Instructions for Form 1040-X set **no dollar threshold** — there is no IRS de minimis amount below which an amendment is excused, only the filing time limits (generally 3 years from filing the original return, or 2 years from paying the tax, whichever is later). What the instructions *do* say is not to amend for math errors, which the IRS corrects itself. Any dollar figure your firm uses to trigger this step is an internal materiality policy and should be recorded as such in the artifact, alongside the tax-preparer's judgment.

## Operational Integration

- **CI / pre-run checks** — re-run this skill against a fixture of internal-vs-broker known-bad data; verify discrepancy counts match the test set.
- **Monitoring** — alert on `missing_in_broker > 5% of internal_count` and `total_basis_delta > 1000 USD`. Integrate with `runbook-automation-for-common-incident-types`.
- **Audit trail** — every run writes a JSON-serialized `ReconciliationResult` plus input ledger checksums under a dated path. Output ordering is deterministic (internal-ledger order), so a rerun over the same input diffs cleanly against the previously accepted artifact. For retention periods see `standards.md` → Security / PII guidance; note that the corrected-1099-B tail runs up to 3 years past the original filing.
- **PII handling** — see `standards.md` Security / PII guidance. Never route raw 1099-B through LLM tooling without redaction.

## Mid-Year (Non-EOY) Reconciliation

Useful for risk-managed intraday positions, wash-sale surplus accumulation, and §475 traders with mark-to-market election.

1. Run reconciliation on a quarterly cadence (Mar 31, Jun 30, Sep 30, Dec 31).
2. There is no mid-year 1099-B — it is an annual information return. Reconcile instead against the broker's year-to-date **realized gain/loss statement** or the raw trade-confirmation feed, and treat the result as a pipeline health check rather than a tax filing input. Expect the broker's YTD wash-sale and basis figures to be provisional until the annual form is cut.
3. Tighter tolerances may be appropriate (e.g. `$0.01` absolute) since mid-year aggregation rules are simpler than EOY.
4. Most discrepancies in mid-year reconciliation indicate **internal** issues (corporate action mis-applied, missing dividend reinvestment, wash-sale window from previous-month round trip).

## Recovery / Failure Modes

| Failure | Recoverable? | Recommended response |
|---------|--------------|----------------------|
| Broker portal is down at retrieval time | YES | Use broker's Trading API + manual request via customer service; never delay write |
| Internal ledger fails to load (corrupt CSV) | PARTIAL | Recover from snapshot (`database-backup-and-point-in-time-restore-testing`); re-load lot by lot, validate quantities |
| Discrepancy explosion (>20% of ledger) | DEPENDS | Halt; check for date-bucket drift (e.g. lots dated 2025-01-01 vs 2024 EOY); likely a date format issue |
| `total_basis_delta` $0 but matched_clean <99% | PROCESS BUG | Engine tolerance may be too lax; inspect matched_with_discrepancies for systematic patterns |
| Engine raises on input | YES | `_validate_lot` rejects (negative qty, backwards date); fix the input source, do not bypass |

## Runbook snippet: weekly reconciliation (lightweight)

For firms running weekly reference-data validation:

```
each monday morning:
    pull last-week's trades from internal ledger
    pull last-week's trade confirmations from broker API
    construct synthetic TaxLot derived from internal ledger
    construct synthetic TaxLot derived from broker API output
    run S1099BAndBrokerTaxReportingReconciliationEngine
    if matched_total / total_count < 0.95:
        page on-call via runbook-automation
```

This is a smoke test — it does not replace the EOY reconciliation; it confirms the pipeline is healthy before broker corrections cascade in.
