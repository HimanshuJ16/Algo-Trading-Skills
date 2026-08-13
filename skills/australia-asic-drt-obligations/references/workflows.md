# Workflows for ASIC DRT Reporting

1. **Trade Execution**: Desk executes a 5-year AUD Interest Rate Swap.
2. **UTI Generation**: The trading system or middleware generates a Unique Transaction Identifier (UTI) per ISO 23897 (20-52 chars, uppercase alphanumeric, LEI-prefixed).
3. **Data Aggregation**: The system fetches the counterparty's LEI (ISO 17442) and the product's UPI (ISO 4914, "QZ"-prefixed) from the DSB.
4. **Validation Check**: Run `AsicDrtReportingEngine.batch_validate(trades, reporting_date, holidays)` every evening at 18:00 AEST, passing a Sydney public-holiday set so the T+2 (or T+4 for linking-identifier trades) deadline is computed in business days.
5. **Exceptions Management**: Any trades flagged as `is_ready_for_reporting = False` are routed to the Middle Office Exception queue to locate/repair missing or structurally invalid LEIs/UTIs/UPIs. Trades flagged `is_late_submission = True` are escalated for late-reporting remediation.
6. **XML Serialization**: Trades passing validation are serialized into ISO 20022 XML and submitted to DTCC or another approved Trade Repository (TR).
