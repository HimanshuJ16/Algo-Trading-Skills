# Institutional SEC Regulation SHO Operations Checklist

## Locate Management & Pre-Trade Order Marking
- [ ] **Prime Broker Locate API Ingestion**: Automated ingestion of Easy-to-Borrow (ETB) lists and Hard-to-Borrow (HTB) locate approvals.
- [ ] **Mandatory Order Marking (Rule 200)**: Enforce explicit order tags (`LONG`, `SHORT`, `SHORT_EXEMPT`) on all outbound order messages.
- [ ] **Locate ID Verification (Rule 203(b)(1))**: Reject all short sale orders lacking a valid, unexpired Locate ID before reaching execution venues.
- [ ] **Real-Time Locate Capacity Reservation**: Deduct order quantities from locate pool inventory in real time to prevent double-counting.

## Circuit Breaker (Rule 201 SSR) Monitoring
- [ ] **10% Intraday Drop Detection**: Automated monitoring of intraday price declines vs prior day close to trigger Rule 201 SSR status.
- [ ] **Alternative Uptick Price Test Enforcement**: Reject `SHORT` orders priced at or below current National Best Bid ($\le \text{NBB}$) when SSR is active.
- [ ] **SHORT_EXEMPT Exception Audit**: Verify statutory exception eligibility before tagging orders as `SHORT_EXEMPT`.

## Failures-to-Deliver (Rule 204) & Audit Archival
- [ ] **Rule 204 FTD Resolution**: Monitor clearing house (NSCC/DTCC) FTD reports and execute mandatory buy-ins by T+3 / T+5.
- [ ] **6-Year Audit Trail Retention**: Archive all locate allocations, order markings, SSR triggers, and compliance rejection logs.

