# Standards for B3 Exchange Connectivity

**Facts below were verified against the sources listed at the end on 2026-08-12.**
Exchange interfaces change; re-verify against B3's current specifications before
relying on any of this for a production build.

## Terminology correction

**UMDF stands for "Unified Market Data Feed."** B3's own documentation states:
"The UMDF (Unified Market Data Feed) platform provides low latency, state of the
art market data service." Earlier revisions of this file expanded it as "Unicast
Market Data Format," which is wrong twice over — the acronym is *Unified*, and
the feed is distributed over UDP **multicast**, not unicast.

## Protocol suite comparison

| | **LEGACY_FIX_FAST** | **MODERN_BINARY_SBE** |
|---|---|---|
| Order entry | FIX 4.4 | B3 Binary Order Entry (BOE) / "Binary EntryPoint" |
| Order entry session layer | Standard FIX session | FIXP (FIX Performance Session Layer) |
| Order entry encoding | FIX tag=value | SBE (Simple Binary Encoding) |
| Order entry transport | TCP | TCP |
| Market data | UMDF FIX/FAST | B3 Binary UMDF (SBE) |
| Market data transport | UDP multicast | UDP multicast |
| Book model | MBP and Top of Book available | **MBO only** — Binary UMDF does not support MBP or TOB |
| Gap recovery | TCP Replayer, TCP Historical Replayer, Snapshot Recovery stream | **No TCP recovery channel** — sequence tracking + snapshot recovery |
| Available since | Long-standing | Mid-2023, in parallel with legacy |

Latency figures are deliberately **omitted**. Earlier revisions of this file
quoted precise ranges ("50–200 µs round trip", "10–50 µs multicast delivery")
that could not be traced to any B3 or vendor publication. Measure latency in your
own colocation footprint rather than trusting an unsourced number.

## Recovery mechanisms — the reason for this skill's core constraint

This asymmetry is what `B3IntegrationEngine` enforces.

### Legacy FIX/FAST UMDF

B3 documents three recovery paths alongside the incremental multicast stream:

- **TCP Replayer** — "allows a client to request messages that were already sent
  through the incremental stream during the day."
- **TCP Historical Replayer** — "an alternative feed with higher response time
  that allows querying all incremental MD messages (up to the message with
  sequence number 1)."
- **Snapshot Recovery stream** — for late joiners and "massive loss of messages."

B3 notes the TCP recovery methods suit small-scale losses, while snapshot
recovery suits late joiners or large gaps.

### Binary UMDF (SBE)

Connectivity vendor OnixS reports: *"There is no current support for a Binary
UMDF feed TCP recovery channel for gap filling"* — explicitly contrasting this
with legacy FIX/FAST capabilities.

**This does not mean packet loss is unrecoverable.** SBE consumers recover
through sequence-number tracking and snapshot mechanisms; vendor handlers
implement "sequence number tracking, RptSeq/LastRptSeq synchronization, Snapshot
Recovery, Channel Reset, and EmptyBook mechanisms." What is absent is the
*TCP gap-fill channel* that the legacy feed offers.

> **Correction to earlier revisions.** This skill previously instructed
> implementers to "request gap fills via B3's dedicated gap recovery TCP channel
> (separate from market data)" for SBE feeds. That is the exact facility
> reported not to exist for Binary UMDF, and the instruction contradicted the
> same document's own statement that SBE has no TCP recovery. Recover via
> sequence tracking and snapshot/refresh instead.

> **Confidence and dating.** The "no TCP recovery channel" claim is
> **vendor-reported**, not quoted from a B3 specification, and is qualified as
> "current" by its source. It is the premise behind a hard `ValueError` in this
> skill's engine, so verify it against B3's Binary UMDF specification before
> depending on it. If B3 adds a TCP recovery channel, this skill's constraint
> becomes over-strict rather than unsafe — an acceptable failure direction.

## Migration status — legacy is not a neutral long-term choice

The binary interfaces (Binary UMDF + Binary Order Entry) have run in parallel
with FIX/FAST market data and FIX 4.4 order entry since **mid-2023**. There is no
publicly mandated full decommissioning date for the legacy APIs, but B3 has been
reducing FIX order entry gateways in phases:

| Segment | Phase |
|---|---|
| Derivatives | Q4 2025 |
| Equities | 10 April – 15 May 2026 |

Affected participants receive new IP addresses, ports, and TargetCompIDs from B3
and must reconfigure by their assigned cutoff — **connections fail to log in** if
the change is missed. Treat `LEGACY_FIX_FAST` as a maintained-but-shrinking path
and confirm current gateway status with B3 before starting new legacy work.

## Network and session notes

- Order entry is a point-to-point TCP session; the market data feed is UDP
  multicast and requires IGMP joins and multicast-capable routing.
- Separate multicast channels exist per asset class and stream type (instrument
  definition, incremental, recovery/snapshot).
- FIXP uses a Negotiate/Establish handshake and bidirectional sequence numbers.
- Standard FIX 4.4 sessions use logon/logout, heartbeats, test requests, and
  sequence-number gap fill.

Colocation and access-provider details are intentionally not asserted here. An
earlier revision named specific Equinix facilities; that could not be verified
and access arrangements should be confirmed directly with B3.

## CompID format

`scripts/b3_brazil_exchange_api_integration.py` applies a conservative whitelist
of `^[A-Za-z0-9_]{1,12}$`. **This is this skill's defensive default, not a
constraint quoted from a B3 specification** — no published B3 CompID grammar was
located. Its purpose is to reject whitespace, control characters, and delimiters
that have no place in a session identifier. Confirm the real limits with B3
Membership Services and override via `comp_id_pattern` if they differ.

## Regulatory notes

B3 is a Brazilian exchange supervised by the **Comissão de Valores Mobiliários
(CVM)**. Participants are subject to B3's own access, certification, and market
data licensing rules.

Earlier revisions asserted that "both protocols support regulation-mandated
features like short sale price tests and position limits." That claim was
removed: no source was found for it, and "short sale price test" is a US
Regulation SHO concept that should not be transplanted onto the Brazilian regime.
Pre-trade risk controls remain sound engineering practice regardless; confirm
specific obligations with B3 and CVM rather than with this file.

## Sources

| Claim | Source |
|---|---|
| UMDF = Unified Market Data Feed; TCP Replayer, TCP Historical Replayer, Snapshot Recovery stream | B3, *FIX/FAST UMDF* — https://www.b3.com.br/en_us/solutions/platforms/puma-trading-system/for-developers-and-vendors/fix-fast-umdf/ |
| UMDF platform overview and asset-class coverage | B3, *UMDF – Unified Market Data Feed* — https://www.b3.com.br/en_us/solutions/platforms/puma-trading-system/for-developers-and-vendors/umdf-unified-market-data-feed/ |
| FIX/FAST UMDF message specification | B3, *UMDF Market Data Specification* (PDF) — https://www.b3.com.br/data/files/A4/11/B5/27/B1C6C6106B9896C6DC0D8AA8/UMDF_MarketDataSpecification_v2.1.7.pdf |
| Binary UMDF has no TCP recovery channel; MBO only, no MBP/TOB; BOE uses FIXP+SBE; parallel operation since mid-2023 | OnixS, *New B3 Binary UMDF Market Data feed and B3 Binary Order Entry platform interfaces* — https://www.onixs.biz/insights/new-b3-binary-umdf-market-data-feed-and-b3-binary-order-entry-platform-interfaces.-what-you-need-to-know |
| SBE handler recovery mechanisms (sequence tracking, RptSeq/LastRptSeq, Snapshot Recovery, Channel Reset, EmptyBook) | OnixS, *B3 Binary UMDF SBE Market Data Handler* — https://www.onixs.biz/b3-binary-umdf-sbe-feed-market-data-handler.html |
| FIX order entry gateway reduction: Derivatives Q4 2025, Equities 10 Apr – 15 May 2026; login failure if unconfigured | Trading Technologies, *B3: Upcoming B3 FIX Order Entry Gateway Migration for Equities* — https://tradingtechnologies.com/support-updates/b3-upcoming-b3-fix-order-entry-gateway-migration-for-equities-action-required-jpx-h1-2026-changes-and-more-2/ |
| Binary EntryPoint certification scope | B3, *Certification Script — Binary EntryPoint, Equities & Derivatives v1.4* (PDF) — https://www.b3.com.br/data/files/AD/85/6A/46/3F3D29106EEC8429AC094EA8/Certification_Script_Binary_Entrypoint_Equities_Derivatives_v1.4_ENG.pdf |

## Category

`global-market-integration`
