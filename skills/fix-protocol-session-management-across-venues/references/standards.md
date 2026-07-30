# Standards for FIX Protocol Session Management

| Metric | Engineering Standard |
|---|---|
| FIX Standard Compliance | FIX session MUST comply with FIX 4.2 / 4.4 / 5.0 Session Layer Spec. |
| Heartbeat Timeout Limit | TestRequest MUST be issued if no message received within $1.5 \times \text{HeartBtInt}$. |
| Sequence Gap Handling | ResendRequest (Tag 35=2) MUST be issued immediately upon detecting MsgSeqNum gap. |
