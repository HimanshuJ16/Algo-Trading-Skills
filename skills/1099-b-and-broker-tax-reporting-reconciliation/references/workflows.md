# Workflows for 1099-B and Broker Tax Reporting Reconciliation

## End of Year (EOY) Reconciliation Lifecycle

1. **Data Freeze (Jan 15)**: Lock all internal trade ledgers for the prior tax year. Ensure all delayed corporate actions and dividends have been applied internally.
2. **Broker Document Retrieval (Feb 15)**: Automatically or manually retrieve finalized 1099-B CSV/XML files from the clearing broker's portal.
3. **Data Normalization**: Run transformation scripts to convert broker-specific formats into standard `TaxLot` schemas.
4. **Automated Reconciliation**: Execute the `S1099BAndBrokerTaxReportingReconciliationEngine`.
5. **Discrepancy Resolution**: 
   - Analyze lots flagged as "Missing in 1099-B" (typically Dec 31 trades settling in January).
   - Investigate "Wash Sale Mismatches".
6. **CPA Handoff**: Generate the final consolidated Form 8949 data and securely transmit it to tax advisors.