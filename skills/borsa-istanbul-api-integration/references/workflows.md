# BISTECH FIX Workflow

1. **Session Level**:
   - Establish TCP connection to BIST gateway.
   - Send Logon (MsgType=A). Wait for Logon acknowledgment.
   - Begin periodic Heartbeat (MsgType=0) transmission.
   - On sequence number mismatch, handle Resend Request (MsgType=2) or Sequence Reset (MsgType=4).

2. **Order Routing**:
   - Submit New Order Single (MsgType=D) ensuring `ClOrdID` is globally unique. Never
     reuse a `ClOrdID` that has an order still working under it.
   - Wait for Execution Report (MsgType=8) with `ExecType=0` (New).
   - To cancel, send Order Cancel Request (MsgType=F) specifying `OrigClOrdID`.

3. **Cancel Handling (request, not cancellation)**:
   - MsgType=F *requests* cancellation of the remaining quantity. The order stays live at
     the venue until it is answered. Move it to `OrdStatus=6` (Pending Cancel) and keep
     applying fills.
   - The venue answers in one of three ways: an Execution Report with `ExecType=6`
     (Pending Cancel) acknowledging the request, an Execution Report with `ExecType=4`
     (Canceled) completing it, or an **Order Cancel Reject (MsgType=9)** refusing it.
   - On MsgType=9 the order was never canceled — return it to `OrdStatus=1`
     (Partially filled) or `0` (New) according to its fill state. The usual cause is that
     the order filled or went inactive before the request arrived.
   - Only `ExecType=4` makes the order terminal. Do not release risk budget or reuse the
     `ClOrdID` while the order is Pending Cancel.

4. **Execution Handling**:
   - Parse Execution Report (MsgType=8).
   - **Deduplicate on `ExecID` (tag 17) before applying.** Resend Request recovery replays
     application messages, and a replayed report is otherwise indistinguishable from a new
     one; applying it twice double-counts the fill.
   - Update `CumQty` and calculate dynamic average price (`AvgPx`) from `LastQty`/`LastPx`.
   - Reject any report that would take `CumQty` beyond `OrderQty`; an overfill means a
     duplicate escaped deduplication or the venue erred. Alert and reconcile.
   - If `ExecType=4` (Canceled) or `ExecType=8` (Rejected), transition order to terminal state.
