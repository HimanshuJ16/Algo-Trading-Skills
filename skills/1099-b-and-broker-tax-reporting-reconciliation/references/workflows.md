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
                  arrive up to 2 yr later
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

Broker reporting cadence (US, per IRS regulations and FINRA 4530):

| Document | Earliest availability | Latest practical arrival |
|----------|----------------------|--------------------------|
| Preliminary 1099-B draft | Jan 31 | Mid-February |
| Final 1099-B | Feb 15 | End of February |
| Corrected 1099-B (1st wave) | End of February | April |
| Corrected 1099-B (late) | Anytime up to 3 yr | Per IRS reporting rules |

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
assert metrics["missing_in_broker"] == 0, "Resolve end-of-year settlement disconnects first"
```

See `SKILL.md` Decision Points table for response-by-classification.

### Phase 4 — Discrepancy Resolution

For each `DiscrepancyReason`, follow this dispatch:

```
MISSING_IN_BROKER:
  ⋅ Check broker's "trades" view (not 1099-B) in the days after EOY.
  ⋅ Most often: Dec 30/31 trade → settlements in next calendar year.
  ⋅ Document the list of NEXT-year settlement trades; keep on file for FY2 reconciliation.

MISSING_IN_INTERNAL:
  ⋅ Broken import / missed write / transfer-in failure.
  ⋅ HALT — fix the data lake; do NOT silently include a 8949 entry with a missing basis.

WASH_SALE_FLAG_MISMATCH or WASH_SALE_AMOUNT_MISMATCH:
  ⋅ Cross-reference both internal wash tracker and broker's box 1g / 1099-B.
  ⋅ Follow `wash-sale-rule-tracking-us` for the authoritative calculation window.
  ⋅ Apply Form 8949 column (f) code W + column (g) adjustment.

BASIS_OUTSIDE_TOLERANCE or PROCEEDS_OUTSIDE_TOLERANCE:
  ⋅ If covered security and broker's basis ≠ internal:
        col(e) = broker's basis (per IRS Form 8949 instructions)
        col(g) = internal − broker, code B
        Attach explanation statement if discrepancy > $50.
  ⋅ If noncovered security and broker's basis ≠ internal:
        col(e) = internal correct basis
        col(g) = 0, no code needed (or code O + statement).
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
4. File Form 1040-X (Amended U.S. Individual Income Tax Return) **only** if the changes exceed **$100 OR a substantive tax-change**. Per IRS instructions, do not amend unless necessary.

## Operational Integration

- **CI / pre-run checks** — re-run this skill against a fixture of internal-vs-broker known-bad data; verify discrepancy counts match the test set.
- **Monitoring** — alert on `missing_in_broker > 5% of internal_count` and `total_basis_delta > 1000 USD`. Integrate with `runbook-automation-for-common-incident-types`.
- **Audit trail** — every run writes a JSON-serialized `ReconciliationResult` plus input ledger checksums under a dated path. Retention: minimum 7 years (US Pub 583).
- **PII handling** — see `standards.md` Security / PII guidance. Never route raw 1099-B through LLM tooling without redaction.

## Mid-Year (Non-EOY) Reconciliation

Useful for risk-managed intraday positions, wash-sale surplus accumulation, and §475 traders with mark-to-market election.

1. Run reconciliation on a quarterly cadence (Mar 31, Jun 30, Sep 30, Dec 31).
2. Broker 1099-B filings are produced by the broker on-demand for mid-year; users must personally request the "year-to-date" statement.
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
