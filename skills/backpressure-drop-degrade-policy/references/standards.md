# Broker & Framework Coverage — backpressure-drop-degrade-policy

| Broker / Framework | Relevance to this skill |
|---|---|
| Python `asyncio.Queue` / `collections.deque` | Standard library queues requiring custom overflow handling to avoid blocking the WebSocket read loop or raising unhandled `QueueFull`. Note `deque(maxlen=N)` **already** discards from the opposite end on append when full — an implicit drop-oldest policy. |
| ZeroMQ (`PUB/SUB`, `PUSH/PULL`) | Provides a High Water Mark (`ZMQ_SNDHWM` / `ZMQ_RCVHWM`); behaviour at the HWM differs by socket type (`PUB` drops, `PUSH` blocks), so the effective policy must be chosen deliberately per socket. |
| Apache Kafka / Redis Streams | Backpressure surfaces as consumer-group lag rather than a full local queue; the drop/degrade decision moves to retention and consumer scaling. |
| RxPY / ReactiveX | Backpressure-shaped operators (`sample`, `throttle_last`, `debounce`, buffering with bounded replay). |

## Python queue semantics relied on by this skill

| Behaviour | Documented statement | Source |
|---|---|---|
| Individual `deque` appends/pops are thread-safe | "Deques support thread-safe, memory efficient appends and pops from either side of the deque with approximately the same O(1) performance in either direction." | [Python `collections` docs](https://docs.python.org/3/library/collections.html#collections.deque) |
| A full bounded `deque` silently discards on append | "Once a bounded length deque is full, when new items are added, a corresponding number of items are discarded from the opposite end." | same |

The first statement covers *individual* operations only. It does **not** make a
sequence of operations atomic: reading `len(queue)` and then popping that many
times races with a concurrent consumer and raises `IndexError`. All pops in
`scripts/backpressure_policy.py` are individually guarded and performed under a
lock for this reason.

## Regulatory & Operational Notes

This skill concerns engineering practice, not investment advice, and no
regulator prescribes a queue overflow policy. The material below identifies
where system-resilience obligations sit for firms that are in scope; confirm
applicability for your own registration status and jurisdiction.

### EU — investment firms engaged in algorithmic trading

The instrument that applies to **an investment firm's own** trading systems is
**RTS 6**, Commission Delegated Regulation (EU) 2017/589, which specifies the
organisational requirements of investment firms engaged in algorithmic trading
(supplementing Directive 2014/65/EU, Article 17(1)). Relevant provisions:

- **Article 10** — the firm must test that its algorithmic trading systems and
  the associated procedures and controls "can withstand increased order flows or
  market stresses." Capacity-under-overload behaviour is squarely in scope.
- **Article 16** — the firm must "monitor in real time all algorithmic trading
  activity that takes place under its trading code… for signs of disorderly
  trading."

> **Do not conflate RTS 6 with Article 48 of MiFID II.** They are different
> instruments with different addressees. Article 48 imposes
> systems-resilience and circuit-breaker obligations on **regulated markets**
> (extended to MTFs and OTFs by Article 18(5)) — that is, on **trading venues**,
> not on participant firms. A trading firm designing its own backpressure policy
> is governed by RTS 6, not by Article 48.

### India — SEBI

SEBI circular **SEBI/HO/MIRSD/MIRSD-PoD/P/CIR/2025/0000013**, *Safer
participation of retail investors in Algorithmic trading*, dated **4 February
2025**, sets the framework for retail algorithmic trading (broker and algo-provider
obligations, algo registration and identification). Implementation was
subsequently deferred by follow-up circulars.

> **Verification note.** The circular number, title, and date were confirmed
> against SEBI's website. The full text was **not** machine-readable at the time
> of writing, so this file makes **no claim** about specific capacity-monitoring,
> throughput, or alerting provisions within it. Third-party summaries describing it
> as one that "outlines expectations for system resilience, capacity monitoring,
> and alerting mechanisms" are unverified against SEBI's own text. Read the primary
> circular before relying on it for any compliance conclusion.

## Sources

| Claim | Source |
|---|---|
| `deque` thread-safety and bounded-length discard behaviour | Python Standard Library, `collections.deque` — https://docs.python.org/3/library/collections.html#collections.deque |
| RTS 6 scope: organisational requirements of investment firms engaged in algorithmic trading; Art. 10 stress/capacity testing; Art. 16 real-time monitoring | Commission Delegated Regulation (EU) 2017/589 — https://eur-lex.europa.eu/eli/reg_del/2017/589/oj/eng |
| Article 48 addresses regulated markets, extended to MTFs/OTFs via Article 18(5) | Directive 2014/65/EU (MiFID II), Article 48 — https://www.esma.europa.eu/publications-and-data/interactive-single-rulebook/mifid-ii |
| SEBI circular number, title, and date (4 Feb 2025) | SEBI — https://www.sebi.gov.in/legal/circulars/feb-2025/safer-participation-of-retail-investors-in-algorithmic-trading_91614.html |
| Implementation timeline extension | SEBI — https://www.sebi.gov.in/legal/circulars/sep-2025/extension-of-timeline-for-implementation-of-sebi-circular-dated-february-04-2025-on-safer-participation-of-retail-investors-in-algorithmic-trading-_96979.html |

Confirm current regulatory requirements independently — see `mappings/regulatory-coverage.md`.
