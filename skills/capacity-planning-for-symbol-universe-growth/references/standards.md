# Standards for Capacity Planning

## Wire overhead (fixed by the protocol standards)

Framing is charged **per packet**, not per message.

| Layer | Bytes | Source |
|---|---|---|
| Ethernet preamble + SFD | 8 | IEEE 802.3 |
| Ethernet header (dst/src/type) | 14 | IEEE 802.3 |
| Frame check sequence | 4 | IEEE 802.3 |
| Interframe gap | 12 | IEEE 802.3 |
| **L1/L2 subtotal** | **38** | |
| IPv4 header (no options) | 20 | RFC 791 |
| UDP header | 8 | RFC 768 |
| TCP header (minimum, no options) | 20 | RFC 793 |
| **UDP/IPv4/Ethernet total** | **66** | |
| **TCP/IPv4/Ethernet total** | **78** | |

A VLAN tag adds 4 bytes. These constants are exported by the script as
`UDP_IPV4_ETHERNET_OVERHEAD_BYTES` and `TCP_IPV4_ETHERNET_OVERHEAD_BYTES`.

### MoldUDP64 batching

Nasdaq's ITCH transport batches messages: "When multiple messages are available for
dissemination ... they are batched to be encapsulated in the same MoldUDP64 Downstream
packet." The packet carries a 20-byte header (10-byte session, 8-byte sequence number,
2-byte message count) plus a 2-byte length field per message block, up to a maximum
packet size of 1472 bytes. Since ITCH messages are tens of bytes, one datagram routinely
carries dozens of messages — so per-message framing charges are wrong by a large factor.
Model this with `msgs_per_packet` and `packet_overhead_bytes`.

## Feed-level multipliers (published by the feed operator)

| Factor | Value | Source |
|---|---|---|
| Redundant A/B lines | **2x** — "the traffic projections are for one stream only ... for those Multicast Data Subscribers who elect to receive both streams of data, the bandwidth requirements would be double" | OPRA Revised Capacity Projections, Sept 15 2025 |
| Retransmissions | **+10%** — "the required bandwidth should be increased by 10% to account for retransmissions" | OPRA Revised Capacity Projections, Sept 15 2025 |

These are OPRA's figures for the US options SIP. They are a well-sourced starting point,
not a universal constant — confirm the equivalent numbers for your own venue.

## Burst measurement window

OPRA states its projections are based on messages per 100-millisecond intervals, and that
"the use of a 10-millisecond interval reflects system utilization during bursts of
traffic." Peak rates should therefore be derived from a sub-second window, not a
one-second average. For scale, OPRA projected 12.322 billion total messages per day
effective October 2025, rising to 13.575 billion by July 2026.

## Utilization headroom

Do not design a link or a core to run near 100% utilization. In an M/M/1 queue the mean
response time is `1/(mu - lambda)`; expressed in utilization `rho`, queueing delay grows
with `1/(1 - rho)` and diverges as `rho -> 1`. At 80% utilization mean response time is
already about 5x the service time. A 60% ceiling (`max_network_utilization`, configurable)
is a conservative operating point that keeps you off the knee of that curve, and it also
absorbs the sub-second microbursts an averaged utilization figure hides.

Note: earlier versions of this document attributed the headroom rule to "TCP bufferbloat".
That is a distinct phenomenon — excessive buffering in network devices inflating latency
under load — and it is not the reason to leave headroom on a UDP multicast market data
link. The queueing and microburst arguments above are the operative ones.

## Safety margin

Multiplying forecast peak load by 1.5x-2.0x is a **common engineering heuristic**, not a
published standard, and this skill does not apply it by default (`safety_margin=1.0`).
Prefer the specific, sourced multipliers above (A/B redundancy, retransmission overhead,
utilization ceiling) over a blanket fudge factor, and be careful not to double-count a
generic margin against a utilization ceiling that already reserves headroom.

## Partitioning

Partition by symbol (e.g., A-M on Core 1, N-Z on Core 2) rather than using lock-based
shared state. The consequence is that a single symbol's stream cannot be spread across
cores: if the hottest symbol exceeds one core's throughput the design is infeasible at any
core count, which `single_symbol_exceeds_core` reports.

## Sources

- OPRA / SIAC, *Revised OPRA Capacity Projections*, September 15 2025 — https://cdn.opraplan.com/documents/notices/OPRA_Capacity_Projections_Update_0925.pdf
- Nasdaq, *MoldUDP64 Protocol Specification v1.00* — https://www.nasdaqtrader.com/content/technicalsupport/specifications/dataproducts/moldudp64.pdf
- Nasdaq, *TotalView-ITCH 5.0 Specification* — https://www.nasdaqtrader.com/content/technicalsupport/specifications/dataproducts/NQTVITCHSpecification.pdf
- University of Aberdeen, *Ethernet Frame Calculations* — https://www.erg.abdn.ac.uk/users/gorry/course/lan-pages/enet-calc.html
- Virtamo, *Queueing Theory: The M/M/1 queue* (Aalto/TKK lecture notes) — https://www.netlab.tkk.fi/opetus/s383143/kalvot/E_mm1jono.pdf
