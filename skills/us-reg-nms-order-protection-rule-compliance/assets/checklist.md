# Institutional SEC Regulation NMS Rule 611 Operations Checklist

## Protected NBBO Ingestion & Router Setup
- [ ] **SIP / Direct Feed Calibration**: Ingest real-time automated quotes across all registered US equity exchanges (NASDAQ, NYSE, Cboe, IEX, MEMX).
- [ ] **Automated vs Manual Quote Identification**: Ensure non-automated quotes are flagged and excluded from Protected NBBO calculations.
- [ ] **Microsecond Clock Synchronization**: Synchronize trading host clocks via PTP IEEE 1588v2 for microsecond-accurate quote/execution matching.

## Trade-Through Auditing & Statutory Exemptions
- [ ] **Intermarket Sweep Order (ISO) Marking**: Verify ISO routing logic tags outgoing orders with `FIX Tag 18=f` and routes simultaneous sweep orders.
- [ ] **Self-Help Protocol (Rule 611(b)(1))**: Configure automated latency triggers (> 1 sec) to execute `declare_self_help(venue_id)`.
- [ ] **Benchmark / VWAP Exemption Validation**: Tag benchmark/VWAP executions with `Rule 611(b)(7)` exemption markers.
- [ ] **Flickering Quote Exemption Tracking**: Verify quote timestamp delta ($\le 1.0\ \text{sec}$) before flagging trade-through violations.

## FINRA CAT Regulatory Reporting & Examination Defense
- [ ] **6-Year Audit Trail Retention**: Archive all `Rule611AuditResult` logs, Protected NBBO snapshots, and Self-Help declarations.
- [ ] **FINRA CAT Reconciliation**: Reconcile internal execution audit logs against FINRA CAT (Consolidated Audit Trail) submissions.
- [ ] **Annual Best Execution & Rule 611 Review**: Conduct annual Rule 611 compliance audit signed off by Chief Compliance Officer (CCO).

