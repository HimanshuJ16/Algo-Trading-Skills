# Real-Time Architecture Standards — exchange-multicast-feed-handling

| Protocol / Standard | Feed Channel Type | Primary Recovery | Fallback Recovery |
|---|---|---|---|
| CME MDP 3.0 | UDP Multicast | Dual A/B Line Arbitration | TCP Historical Re-transmission |
| NASDAQ MoldUDP64 | UDP Multicast | Downstream Packet Buffer | Historical Server Request |
| Eurex EMDI | UDP Multicast | Multi-Feed Resequencer | Historical Gap Server |

## Category

`real-time-architecture` — see top-level `mappings/` directory.
