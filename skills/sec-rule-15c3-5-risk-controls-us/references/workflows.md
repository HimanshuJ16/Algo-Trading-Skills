# Workflows for SEC Rule 15c3-5 Risk Controls US

1. **Pre-Trade Order Ingestion**:
   - Ingest order payload and calculate notional value ($Q \times P$).
2. **Mandatory Pre-Trade Checks**:
   - Audit credit cap, single order notional/qty caps, price collars, Reg SHO locate, and restricted list.
3. **Rejection & Enforcement**:
   - Block order immediately if any SEC Rule 15c3-5 violation is detected.
4. **Audit Trail Logging**:
   - Record order decision, violation list, and microsecond evaluation latency.