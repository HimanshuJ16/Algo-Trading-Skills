# Standards for Counterparty Concentration Risk

| Metric | Engineering Standard |
|---|---|
| Single Broker NAV Cap | A single prime broker should not hold more than 35% of total portfolio NAV in combined cash/margin/positions. |
| Exposure Measurement Basis | Balances net within a broker (signed), but the cap and the HHI are applied to the resulting **magnitude**. A net-negative balance at a counterparty is exposure to it, never spare capacity. This is an engineering convention for custody/PB balance concentration — it is deliberately not a regulatory large-exposure calculation (no netting sets, collateral haircuts, or short-position offsetting rules). |
| Undefined Concentration | Where the denominator is undefined — NAV ≤ 0 for routing, zero gross exposure for HHI — the module fails loud (blocked decision / raised error) rather than emitting a benign-looking default. |
| CDS Distress Threshold | A broker CDS spread above 250 bps should immediately pause routing of new long/cash allocations to that broker. A missing or non-finite CDS quote must block routing rather than default to healthy. |
| Multi-Broker Readiness | At least 2 active prime broker connections should be maintained live for instant failover routing. |
| Blocked Decision Handling | When no broker is compliant, the routing decision must block execution entirely (`blocked=True`) and trigger manual review — never route to the distressed primary by default. |

These thresholds are engineering defaults for this reference implementation, not regulatory prescriptions — calibrate to the fund's counterparty risk policy, prime brokerage agreements, and applicable regulatory regime before production use.
