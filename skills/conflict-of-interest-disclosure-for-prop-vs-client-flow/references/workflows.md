# Workflows for Prop vs. Client Conflict Auditing

1. **Order Input & Tagging**:
   - Tag order capacity: `PROP` or `CLIENT`.
   - Record `info_barrier_id` and client institutional status.
2. **Conflict Search**:
   - Search pending client orders matching `symbol` and `side`.
3. **Rule 5320 (Manning) Check**:
   - If `side == BUY` and $P_{prop} \ge P_{client\_limit} \implies$ Potential Conflict.
   - If `side == SELL` and $P_{prop} \le P_{client\_limit} \implies$ Potential Conflict.
4. **Exception Audit**:
   - Check 1: `Prop.info_barrier_id != Client.info_barrier_id` $\implies$ `APPROVED` (No-Knowledge Exception).
   - Check 2: `Client.is_institutional` AND `Client.opted_out_5320` AND `Value >= $100,000` $\implies$ `APPROVED` (Institutional Exception).
5. **Enforcement Action**:
   - If no exception applies: `REJECT_PROP_ORDER` or `PASS_THROUGH_CLIENT_FILL`.