# BISTECH FIX Workflow

1. **Session Level**:
   - Establish TCP connection to BIST gateway.
   - Send Logon (MsgType=A). Wait for Logon acknowledgment.
   - Begin periodic Heartbeat (MsgType=0) transmission.
   - On sequence number mismatch, handle Resend Request (MsgType=2) or Sequence Reset (MsgType=4).

2. **Order Routing**:
   - Submit New Order Single (MsgType=D) ensuring `ClOrdID` is globally unique.
   - Wait for Execution Report (MsgType=8) with `ExecType=0` (New).
   - If canceled, send Order Cancel Request (MsgType=F) specifying `OrigClOrdID`.

3. **Execution Handling**:
   - Parse Execution Report (MsgType=8).
   - Update `CumQty` and calculate dynamic average price (`AvgPx`).
   - If `ExecType=4` (Canceled) or `ExecType=8` (Rejected), transition order to terminal state.
