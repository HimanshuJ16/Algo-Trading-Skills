# Deep Workflow Reference — websocket-reconnection-with-state-recovery

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

1. **Connection Disconnect Intercept & Exponential Backoff**:
   - Intercept WebSocket drop event.
   - Compute retry delay $T_{\text{retry}} = \min(T_{\text{max}}, T_{\text{base}} \times 2^k) + \text{uniform}(0, \text{jitter})$.

2. **Re-Establish Connection & Re-Subscribe**:
   - Connect socket, transition to `AUTHENTICATED` $\to$ `SUBSCRIBED`.
   - Issue subscription frames for all symbols in `subscribed_symbols`.

3. **Sequence Gap Detection**:
   - Inspect sequence ID $S_{\text{new}}$ on first message.
   - If $S_{\text{new}} > S_{\text{last}} + 1$, transition to `RECOVERING_GAP`.

4. **REST Gap Recovery & Stream Resume**:
   - Fetch missing sequence range $[S_{\text{last}} + 1, S_{\text{new}} - 1]$ via REST API.
   - Emit gap-filled messages in order before resuming `STREAMING` state.

## Production Implementation Reference

- Reference code: `scripts/ws_recovery.py` (`WebSocketStateRecoveryManager`, `ConnectionState`, `WSMessage`).
- Automated unit tests: `scripts/test_ws_recovery.py`.
