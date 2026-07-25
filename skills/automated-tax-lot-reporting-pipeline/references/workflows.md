# Workflows for Automated Tax Lot Reporting

1. **Trade Normalization**: Extract execution records from the broker API (e.g., Interactive Brokers) or the internal FIX gateway. Convert them into standardized `TradeRecord` objects.
2. **Ledger Configuration**: Initialize the `AutomatedTaxLotReportingPipelineEngine`. In the US, HIFO (Highest-In, First-Out) is commonly preferred for tax optimization, though FIFO is the IRS default if not specified.
3. **Chronological Processing**: Ensure records are fed into the engine strictly ordered by `timestamp_ms`. Out-of-order execution breaks the FIFO sequence.
4. **Report Generation**: Extract the `RealizedGainRecord` array to generate Form 8949 (Sales and Other Dispositions of Capital Assets).