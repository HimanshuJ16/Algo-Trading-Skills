# BIST Integration Readiness Checklist

- [ ] Network connectivity to BIST Simulator/Production IP and Port verified.
- [ ] FIX credentials (SenderCompID, TargetCompID, Passwords) securely injected.
- [ ] Heartbeat mechanism confirmed stable at 30 seconds.
- [ ] Sequence number persistence and recovery logic tested.
- [ ] NewOrderSingle validation covers Price, Qty, Side, and TIF — including non-finite
      quantities (NaN passes a bare `qty <= 0` check), non-positive limit prices, and
      Price wrongly present on an OrdType=Market order.
- [ ] ClOrdID uniqueness enforced; resubmitting an ID with an order still working under it
      is refused, not allowed to overwrite its fill state.
- [ ] Execution Report handling correctly computes partial fills and average price.
- [ ] Execution Reports deduplicated on ExecID (tag 17) so that Resend Request recovery
      cannot double-count a fill.
- [ ] Overfill guard verified: a report taking CumQty beyond OrderQty is rejected, alerted,
      and reconciled — never absorbed.
- [ ] Cancel lifecycle verified end to end: MsgType=F leaves the order Pending Cancel and
      still fillable; ExecType=4 makes it terminal; **Order Cancel Reject (MsgType=9)
      returns it to a working state**. Confirm a fill delivered during the cancel race is
      still applied.
- [ ] TimeInForce values confirmed against the BISTECH specification for your market —
      standard FIX tag 59 code points are not all accepted everywhere.
- [ ] BISTECH FIX Certification scenarios passed (if preparing for PROD).
- [ ] Latency metrics logging enabled for all network boundaries.
