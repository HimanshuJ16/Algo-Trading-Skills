# Real-Time Architecture Standards — multi-region-active-active-tick-ingestion

## Engine parameters

| Parameter | Specification | Description |
|---|---|---|
| Ingest architecture | Active-active, 2+ regions | Redundant copies of **one logical stream**, not independent vendor feeds |
| Deduplication key | `MD5(symbol:sequence_id:price:volume)` | Region-independent tick identity. Price and volume are rendered with `float.hex()` (exact binary precision), **not** a fixed decimal format |
| Excluded from the key | Arrival / exchange timestamp | Only a valid identity component if every region receives it bit-identically; a vendor that re-stamps per region defeats dedup while still passing a single-region test |
| Hash function | MD5, `usedforsecurity=False` | Non-cryptographic content fingerprint. The flag keeps it available on FIPS-mode hosts, where a bare `hashlib.md5()` raises `ValueError` |
| Signature window TTL | 10.0 s (default) | Size from the measured worst-case inter-region arrival spread, not from a round number |
| Cache capacity bound | 200,000 entries (default) | Hard memory bound. Must exceed `ttl_seconds × peak messages/second` summed across regions — evicting an in-window entry lets a duplicate through as a fresh first arrival |
| Eviction | Front-eviction of an insertion-ordered cache | O(1) amortised per tick. A full-cache rescan per tick grows linearly with cache size and, under concurrent ingest, mutates the cache mid-iteration |
| Arbitration policy | First arrival wins | Output is in **arrival** order, which is not sequence order |
| Liveness signal | Messages seen + last receipt time per region | Win rate is a latency statistic, not a liveness statistic |
| Concurrency | Internal re-entrant lock | One feed-handler thread per region calls the same arbiter |

## Venue precedent for first-copy-wins arbitration

The pattern is not invented here — venues already specify it for their own redundant lines,
and the regional variant is the same algorithm moved outward one layer.

| Venue / feed | Redundancy | Arbitration rule | Source |
|---|---|---|---|
| CME MDP 3.0 incremental feed | Every packet is disseminated on both UDP Feed A and UDP Feed B, so UDP loss on one line is covered by the other | "UDP Feed A and UDP Feed B should be used for arbitration": process packets by incremental packet sequence number and discard a packet whose sequence number has already been processed. Each channel has its own sequence space, which is reset periodically (CME documents a weekly reset — confirm against the current channel specification before sizing a dedup window across a reset). | [CME MDP 3.0 — Incremental Feed Arbitration](https://www.cmegroup.com/confluence/display/EPICSANDBOX/MDP+3.0+-+Incremental+Feed+Arbitration); [CME MDP 3.0 — Dissemination](https://cmegroupclientsite.atlassian.net/wiki/display/EPICSANDBOX/MDP+3.0+-+Dissemination) |

**The consequence that matters most here:** because arbitration accepts the first copy from
*either* line, a sequence gap that survives arbitration means the packet was lost on **both**
lines. Deduplication and gap detection are therefore complementary, never substitutes — the
dedup cache is structurally incapable of telling you about a message no source delivered.
The same holds region-for-region. Recovery belongs to a retransmission or re-snapshot path
(`sequence-number-gap-detection-for-feeds`), not here.

Nasdaq's MoldUDP64 transport carries a per-packet sequence number with a re-request mechanism
and is commonly deployed as redundant A/B multicast on the same principle; the specification
text was **not** independently verified in this pass, so treat it as a pointer rather than a
citation and confirm against the venue's own feed specification:
<https://www.nasdaqtrader.com/content/technicalsupport/specifications/dataproducts/moldudp64.pdf>.

## Regulatory & operational notes

No regulator surveyed here prescribes an active-active market data ingest topology, a dedup
window length, or a signature scheme. Those are engineering choices. What regulation does
reach is the *obligation to have business continuity arrangements at all*, and only in some
jurisdictions and for some firm types.

| Instrument | Applies to | What it actually requires | Mandatory? |
|---|---|---|---|
| Commission Delegated Regulation (EU) 2017/589 (**MiFID II RTS 6**), Art. 14 "Business continuity arrangements" | EU investment firms engaged in algorithmic trading under MiFID II Art. 17 | Arrangements "appropriate to the nature, scale and complexity of its business", documented in a durable medium, covering "a range of possible adverse scenarios ... including the unavailability of systems, staff, work space, external suppliers or **data centres** or loss or alteration of critical data and documents", with procedures for relocating to a back-up site where appropriate; reviewed and tested **annually**. Regional data-centre unavailability is explicitly an in-scope scenario. The rule mandates having arrangements — it does not prescribe active-active over active-passive, nor any RTO. | **Yes**, for in-scope firms | 
| SEC **Regulation SCI** (17 CFR 242.1000 et seq.) | **SCI entities only** — SCI self-regulatory organisations, SCI alternative trading systems above the volume thresholds, plan processors, exempt clearing agencies. **Not** ordinary broker-dealers or proprietary trading firms. | Business continuity and disaster recovery plans with backup and recovery capabilities that are "sufficiently resilient and geographically diverse". | **No** for a trading firm — do not cite it as authority for a member firm's ingest topology |

Market data licensing is the operational constraint most often missed: running the same feed
into two regions is generally two simultaneous connections and can be two entitlements, priced
and reported separately. Confirm the venue's redistribution and connection terms before
deploying a second region — see `market-data-entitlement-and-licensing-per-venue`.

Sources (retrieved 2026-08-26):

- Commission Delegated Regulation (EU) 2017/589 (RTS 6) — <https://eur-lex.europa.eu/eli/reg_del/2017/589/oj/eng>.
  UK onshored text: <https://www.legislation.gov.uk/eur/2017/589>.
- CME Group Client Systems Wiki, MDP 3.0 — Incremental Feed Arbitration and MDP 3.0 —
  Dissemination (URLs above).
- SEC Regulation SCI, 17 CFR 242.1001(a)(2)(v) — scope and geographic-diversity language.
