# Total Return Swap (TRS) Operations Checklist

## Pre-Trade Booking & Legal Setup
- [ ] **ISDA Master Agreement & CSA**: Confirm an active ISDA Master Agreement and Credit Support Annex with the prime broker, and record the CSA's VM threshold and Minimum Transfer Amount in `TRSContractConfig`.
- [ ] **Reference Asset Identification**: Verify ticker, ISIN, exchange, trading currency and the initial fixing price.
- [ ] **Reset Basis**: Confirm the trade is **share-locked** (fixed shares, resetting notional). If the Confirmation specifies a fixed notional with a resetting share count, this engine does not model it.
- [ ] **Booked Notional Cross-Check**: `quantity_shares × initial_reference_price` must agree with the booked notional. `config.consistency_warnings()` flags a disagreement — clear it before pricing.
- [ ] **Funding Leg Calibration**: Verify benchmark, spread in bps, and that the day count matches the benchmark's market convention (`ACT/360` for SOFR/€STR/EURIBOR/Fed Funds; `ACT/365` for SONIA/TONA). A convention mismatch is warned, not corrected.
- [ ] **Dividend Basis**: Read the Confirmation's Dividend Amount election — Record, Ex or Paid Amount (2002 ISDA EDD §10.1) — and set `dividend_basis` accordingly. Supply `record_date` on every `DividendEvent` if the election is Record.
- [ ] **Dividend Pass-Through**: Confirm the pass-through percentage or withholding haircut. ISDA's defined amounts are 100% of the **gross** dividend; anything less is a negotiated term.
- [ ] **Initial Margin**: Confirm the IM basis actually in force — BCBS-IOSCO standardised schedule (15% of notional for equity), ISDA SIMM, or a bilateral house grid — and that IM is deposited and **segregated**.

## Daily Risk & Mark-to-Market Monitoring
- [ ] **Side-Signed MtM**: Compute MtM by passing the correct `TRSSide`. Never read the receiver's figure and negate it by hand.
- [ ] **Synthetic Delta Tracking**: Reconcile `synthetic_delta_shares` (+shares receiver / −shares payer) against physical positions for net exposure caps.
- [ ] **Variation Margin**: VM fully collateralises a negative MtM at a zero threshold, subject only to the MTA. **Never net initial margin against a VM call** — IM is segregated and cannot be re-hypothecated.
- [ ] **Margin Reported as Three Numbers**: Check `variation_margin_due_usd`, `initial_margin_requirement_usd` and `maintenance_margin_requirement_usd` separately; they scale with the period notional, which moves with the reference price.

## Reset Settlement & Corporate Actions
- [ ] **Dividend Period Eligibility**: Confirm each dividend's relevant date falls in `(period_start, period_end]`. Review `TRSSettlement.excluded_dividend_ids` — a dividend you expected and did not get is usually a period-boundary or basis question, not a bug.
- [ ] **Extraordinary Dividends**: Confirm treatment separately; ISDA excludes Extraordinary Dividends and Excess Dividend Amounts from the gross cash dividend unless the Confirmation says otherwise. This engine does not classify them.
- [ ] **§871(m) Withholding**: For dividend equivalents to a non-US person, the statutory rate is **30%** unless reduced by treaty. A delta-one TRS is in scope under the Notice 2024-44 phase-in. Confirm the counterparty's treaty documentation and the current IRS notice before applying a reduced rate.
- [ ] **Benchmark Rate Fixing**: Ingest the official published fixing for the period; do not substitute a broker-quoted rate.
- [ ] **Warnings Cleared**: `TRSSettlement.warnings` must be empty, or every entry explained, before the reset is signed off.
- [ ] **Reset Settlement Reconciliation**: Reconcile `net_cashflow_usd` against the prime broker's statement for the same period and side.
