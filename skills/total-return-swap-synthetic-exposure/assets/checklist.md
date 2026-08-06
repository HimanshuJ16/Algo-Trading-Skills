# Institutional Total Return Swap (TRS) Operations Checklist

## Pre-Trade Trade Booking & Legal Setup
- [ ] **ISDA Master Agreement & CSA**: Confirm active ISDA Master Agreement and Credit Support Annex (CSA) with Prime Broker.
- [ ] **Reference Asset Identification**: Verify reference asset ticker, ISIN, exchange currency, and initial fixing price.
- [ ] **Funding Leg Calibration**: Verify benchmark rate (SOFR/ESTR), spread bps, and day-count convention (`ACT/360` vs `ACT/365`).
- [ ] **Initial Margin Requirement**: Confirm Initial Margin (IM) collateral is deposited in prime custody account.

## Intraday Risk & Daily MtM Monitoring
- [ ] **Daily Mark-to-Market (MtM)**: Compute daily MtM incorporating capital price movement and accrued benchmark interest.
- [ ] **Synthetic Delta Tracking**: Reconcile synthetic share delta ($\Delta$) against physical portfolio positions for net exposure caps.
- [ ] **Variation Margin Calls**: Monitor daily variation margin (VM) thresholds to prevent prime broker default triggers.

## Reset Period Settlement & Corporate Actions
- [ ] **Manufactured Dividend Audits**: Verify ex-dividend dates and deduct dividend tax withholding (e.g. Section 871(m)).
- [ ] **Benchmark Rate Fixing**: Ingest official daily SOFR/ESTR fixing rates for interest calculation.
- [ ] **Periodic Reset Cash Flow Settlement**: Execute `process_reset_period()` to confirm net settlement cash flows with prime broker statement.