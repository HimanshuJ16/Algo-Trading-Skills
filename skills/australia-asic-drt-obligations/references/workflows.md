# Workflows for ASIC DRT Reporting

1. **Trade Execution**: Desk executes a 5-year AUD Interest Rate Swap.
2. **UTI Generation**: The trading system or middleware generates a Unique Transaction Identifier (UTI).
3. **Data Aggregation**: The system fetches the counterparty's LEI and the product's UPI from the DSB.
4. **Validation Check**: Run `AsicDrtReportingEngine.batch_validate()` every evening at 18:00 AEST.
5. **Exceptions Management**: Any trades flagged as `is_ready_for_reporting = False` are routed to the Middle Office Exception queue to locate missing LEIs/UPIs.
6. **XML Serialization**: Trades passing validation are serialized into ISO 20022 XML and submitted to DTCC or another approved Trade Repository (TR).
