# Workflows for FIX Protocol Session Management

1. **Logon Handshake**:
   - Issue Logon (Tag 35=A), await response, and transition to LOGGED_IN.
2. **Heartbeat & Liveness Monitoring**:
   - Audit HeartBtInt (Tag 108) and issue TestRequest (Tag 35=1) if idle.
3. **Gap Detection & Resend Request**:
   - Audit incoming MsgSeqNum (Tag 34) and issue ResendRequest (Tag 35=2) on gaps.
4. **Sequence Resync & Logout**:
   - Process SequenceReset (Tag 35=4) and execute graceful Logout (Tag 35=5).
