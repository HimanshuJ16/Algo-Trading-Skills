# Workflows for CME Group FIX API

1. **Session Logon Protocol**:
   - Outbound: `35=A|34=1|49=SENDER|56=CME|98=0|108=30|`
   - Inbound: Wait for `35=A` confirmation from CME.
2. **Order Construction Workflow**:
   - `35=D` (New Order Single)
   - `34=OutboundSeqNum`
   - `49=SenderCompID`, `56=TargetCompID`
   - `50=OperatorID` (Rule 576)
   - `1028=N` (Automated Indicator)
   - `7928=SMP_ID`, `8000=O` (Self-Match Prevention)
   - `55=Symbol`, `54=Side`, `38=Qty`, `44=Price`, `40=OrderType`
3. **Inbound Message Processing**:
   - Inspect `Tag 34` (MsgSeqNum).
   - If `Tag 34 == ExpectedSeqNum`: Process message, increment `ExpectedSeqNum`.
   - If `Tag 34 > ExpectedSeqNum`: Emit `35=2` (ResendRequest for range `[ExpectedSeqNum, Tag 34 - 1]`).
   - If `Tag 34 < ExpectedSeqNum`: Ignore if `PossDupFlag=Y` (Tag 43), else log critical sequence error.
