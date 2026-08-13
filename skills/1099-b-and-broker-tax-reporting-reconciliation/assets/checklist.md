# Operational Checklist for 1099-B Tax Reconciliation

Use this checklist before, during, and after running the reconciliation skill
against production data. Every item is a **halt point** if it fails — do not
proceed with the next phase until the current one is green.

---

## 1. Pre-Reconciliation (data-side prerequisites)

- [ ] **Internal pricing snapshot for Dec 31 is finalized** (or fiscal-year-end equivalent).
- [ ] **All corporate actions** for the tax year have been applied to the internal ledger: splits, mergers (cash/stock), spin-offs (§355), stock dividends, return-of-capital, taxable stock distributions.
- [ ] **All wash-sale disallowed amounts** for the year have been computed and stored on each internal `TaxLot`.
- [ ] **Multi-currency normalization** complete — every `proceeds` / `cost_basis` field is in USD; spot-rate convention applied at `sold_date`.
- [ ] **No open tickets** for unresolved P&L breaks in the upstream ledger.
- [ ] **`TaxLot` ingest contract verified**: every input row passes the engine's `_validate_lot` (positive quantity, sold ≥ acquired, non-empty symbol).
- [ ] **PII handling confirmed**: data path encrypted at rest + in flight; access audited (see `references/standards.md` Security / PII guidance).

## 2. Broker Document Acquisition

- [ ] Confirm the broker file is the **final/corrected** version (not the preliminary draft).
- [ ] If broker ports to multiple clearing firms: gather every account statement.
- [ ] Verify **PDF→CSV parsing** totals back to the broker summary page (random spot-checks at three or more rows).
- [ ] Confirm **1099-DA** (digital-asset) file has been routed to the digital-asset–specific pipeline *if* applicable; do not feed digital-asset rows into this engine.
- [ ] File arrival timestamp recorded (drives §1091 wash-sale adjustments in adjacent tax years).

## 3. Engine Construction

- [ ] Construct `ToleranceConfig` with explicit values appropriate to the asset mix:
  - Options only (small basis): `absolute_usd=Decimal("0.05")`, `relative_basis_pct=Decimal("0.0001")`
  - Equities + ETFs (mixed): `absolute_usd=Decimal("0.05")`, `relative_basis_pct=Decimal("0.00005")`
  - Bonds / large lots ($100k+): `absolute_usd=Decimal("0.50")`, `relative_basis_pct=Decimal("0.0001")`
- [ ] Inject a logger (optional) — must export JSON-friendly structured fields for downstream observability (`log-aggregation-and-centralized-observability` pattern).
- [ ] Single-process / single-thread: confirm the engine is being used synchronously; concurrent calls must each have their own instance.

## 4. Execution

- [ ] Run the engine: `engine.process_reconciliation()` → `ReconciliationResult`.
- [ ] Serialize the result to JSON; attach as an audit artifact.
- [ ] Compute `result.metrics()`; record counts and dollar deltas for the reconciliation log.
- [ ] Compare against the prior year's reconciliation pattern — if `discrepancy_count` increases by >50% YoY, investigate before signing off.

## 5. Per-Discrepancy Disposition (manual review)

Each item below is gated by **materiality** — only review items above these thresholds. Anything below the threshold should be marked in the log as "auto-accepted under tolerance" with a one-line rationale.

| Discrepancy | Auto-accept threshold | Manual-review threshold | Escalation |
|-------------|----------------------|--------------------------|------------|
| `BASIS_OUTSIDE_TOLERANCE` (∣Δ∣) | ≤ $1.00 | $1.00 < ∣Δ∣ ≤ $100.00 | ∣Δ∣ > $100.00 — CPA review |
| `PROCEEDS_OUTSIDE_TOLERANCE` (∣Δ∣) | ≤ $1.00 | $1.00 < ∣Δ∣ ≤ $100.00 | ∣Δ∣ > $100.00 — CPA review |
| `WASH_SALE_FLAG_MISMATCH` | n/a — always review | any Δ | CPA review when broker flagged = true & internal = false |
| `WASH_SALE_AMOUNT_MISMATCH` (∣Δ∣) | ≤ $5.00 | $5.00 < ∣Δ∣ ≤ $100.00 | ∣Δ∣ > $100.00 — Form 8949 statement required |
| `MISSING_IN_BROKER` | only valid if Dec 30/31 trade settling in next year (document the trade ID) | any others | procedural — escalate to data team |
| `MISSING_IN_INTERNAL` | never auto-accept | always | HALT pipeline; do not file until resolved |

## 6. Post-Reconciliation

- [ ] Manual review of **all flagged discrepancies above the manual-review threshold** is complete.
- [ ] **Justification documented** in the audit artifact for every accepted large discrepancy (or every unresolvable one).
- [ ] **Form 8949 payload generated** and validated against `result.matched_total` (number of rows = matched_clean + matched_with_discrepancies).
- [ ] **CPA sign-off** transmitted and recorded alongside the reconciliation artifact.
- [ ] **Reconciliation log archived** to long-term storage (7-year retention minimum).
- [ ] **Runbook feedback**: any new failure mode observed in this run gets added to `references/workflows.md` "Recovery / Failure Modes" within 24 hours.

## 7. Rollback

If a reconciliation artifact is invalidated post-CPA-signoff (e.g. broker issues a corrective correction):

- [ ] Re-load both ledgers after broker-class **freshness TTL** is reached.
- [ ] Re-run the engine; diff against the previously-accepted result.
- [ ] If the new result materially changes the Form 8949 — file Form 1040-X per Service thresholds (> $100 OR substantive tax change).
- [ ] If immaterial — annotate the artifact with the new run + accept the original as the canonical.

## 8. Roll-forward / Continuous Improvement

- [ ] Capture any **new pitfall** observed during this run; submit as a `docs:` PR against `references/standards.md`.
- [ ] Capture any **new decision point** observed; submit as a `docs:` PR against `SKILL.md` "Decision Points" table.
- [ ] Capture any **engine bug / edge case** observed; submit as `fix:` PR against the script with a unit test reproducing.
