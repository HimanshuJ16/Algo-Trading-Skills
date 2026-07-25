# Deep Workflow Reference — exchange-multicast-feed-handling

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

1. **Dual A/B Multicast Group Binding**:
   - Bind UDP sockets to multicast groups for Channel A and Channel B (e.g. CME MDP 3.0).

2. **Deduplication & Packet Arbitration**:
   - Process first arriving packet $S$ from either Channel A or Channel B.
   - Ignore duplicate arrival of packet $S$ from secondary channel.

3. **Packet Re-Sequencing & Out-of-Order Buffering**:
   - Buffer future out-of-order packets ($S > S_{\text{expected}}$).

4. **TCP Historical Gap Re-transmission**:
   - If both channels miss packet range $[S_{\text{expected}}, S - 1]$, issue TCP historical re-transmission request and drain buffer upon recovery.

## Production Implementation Reference

- Reference code: `scripts/multicast_handler.py` (`ExchangeMulticastFeedHandler`, `MulticastChannel`, `MulticastPacket`).
- Automated unit tests: `scripts/test_multicast_handler.py`.
