# Standards — feed-handler-canary-deployment

## 0. How to read this document

Sections 1-3 are **regulatory touchpoints**: obligations or published supervisory
expectations, with the jurisdiction and the binding/non-binding status stated. Section 4
is **protocol fact** taken from an exchange specification. Sections 5-7 are **engineering
standards** — this repository's recommended practice, not legal requirements.

Every numeric threshold in Sections 5-7 is an operational default to be calibrated
against your own universe and release history. No regulator has set a canary allocation
percentage, an observation window, or a price-agreement tolerance. Nothing here
substitutes for your compliance function's determination of which regime applies.

## 1. Is a feed handler release in regulatory scope at all?

This matters, and the honest answer is "it depends on your firm and your interpretation".

RTS 6 and its supervisory guidance are written about *algorithms, algorithmic trading
systems and algorithmic trading strategies*. A market data feed handler is not a trading
algorithm; it is a component of the trading system that feeds one. Two consequences:

- For a firm **outside** the scope of MiFID II algorithmic trading (a US-only
  broker-dealer, a non-EU proprietary trader, an individual trading own capital), nothing
  in Sections 2-3 binds you. Treat them as engineering practice.
- For an **in-scope** EU/UK investment firm, the provisions most likely to bite are those
  about changes to the trading *system* and its external dependencies, not the
  algorithm-deployment article. See Section 3, which names data feed changes explicitly.

Do not present a feed handler upgrade to an operator as a regulated "algorithm
deployment" without that determination having been made.

## 2. EU / UK — Commission Delegated Regulation (EU) 2017/589 ("RTS 6")

**Status:** binding regulation. **Applicability:** investment firms engaged in
algorithmic trading authorised under MiFID II (Directive 2014/65/EU). The UK operates a
materially equivalent onshored version supervised by the FCA.

The articles bearing on a release are grouped as follows. Articles 5-8 sit in Chapter II,
Section I (*Testing and deployment of trading algorithms, systems and strategies*);
Articles 9-11 in Section 2 (*Post-deployment management*); Articles 15-16 in the later
section on means to ensure resilience, which is a different control layer from this skill.

| Article | Title |
|---|---|
| Art. 5 | General methodology |
| Art. 6 | Conformance testing |
| Art. 7 | Testing environments |
| Art. 8 | Controlled deployment of algorithms |
| Art. 9 | Annual self-assessment and validation |
| Art. 10 | Stress testing |
| Art. 11 | Management of material changes |
| Art. 15 | Pre-trade controls on order entry |
| Art. 16 | Real-time monitoring |

Article 8 is the regulatory shape of a canary release. The regulation's recitals describe
controlled deployment as applying to algorithms that are new, previously deployed at
another venue, or **materially modified in architecture**, and as being achieved by
setting cautious limits on the number of financial instruments traded, on price, order
value and order count, on strategy positions and on the number of markets involved, while
monitoring the algorithm's activity more intensively.

Read across to this skill: the limit that transfers is **the number of instruments**,
which is what `canary_percentage` bounds. The order-value, position and market-count
limits in Art. 8 have no analogue here, because this router publishes prices, not orders.
It is therefore not a substitute for the controls in `canary-releases-for-strategy-code-changes`
or for the Art. 15 pre-trade controls; it sits upstream of both.

Primary text: EUR-Lex ELI <https://eur-lex.europa.eu/eli/reg_del/2017/589/oj/eng>;
UK onshored text <https://www.legislation.gov.uk/eur/2017/589>.

## 3. EU — ESMA Supervisory Briefing on Algorithmic Trading (26 February 2026)

**Status:** supervisory convergence guidance for national competent authorities and
firms. It is not law; treat it as supervisory expectation.
Reference: ESMA74-1505669079-10311.

Points that bear directly on a feed handler release:

- Testing is required "following each 'material change' or 'substantial update'" of an
  algorithm, algorithmic trading system or strategy (para. 30). The briefing warns
  specifically that a series of small changes can accumulate into a material change
  without ever being tested — the case for treating each feed handler release as a
  testable event rather than routine maintenance.
- A material change is defined as any modification that may alter the behaviour, risk
  profile or compliance posture of the algorithm or system, and firms are "required to
  timestamp, approve, and record all material changes" (para. 31). `router.events`
  carries the timestamp, the authoriser and the detail for exactly this reason.
- The briefing's non-exhaustive table of change types warranting retesting includes, under
  **External Dependencies**: "Replacing third-party providers or data feeds, changes to
  the trading systems, or changes in access arrangements" (para. 31). This is the closest
  published supervisory statement that a feed handler or data feed change is in scope.
- Testing methodologies, procedures and **internal authorisations to deploy** must be well
  documented, and supervisors assess compliance from that documentation (para. 29).

## 4. Protocol fact — what a symbol-level canary can actually be

From the Nasdaq TotalView-ITCH 5.0 specification
(<https://www.nasdaqtrader.com/content/technicalsupport/specifications/dataproducts/NQTVITCHSpecification.pdf>):

- **The feed is a single sequenced stream.** "The TotalView ITCH feed is made up of a
  series of sequenced messages", offered over SoupBinTCP, compressed SoupBinTCP, or
  MoldUDP64. There is no per-symbol subscription. A canary handler therefore parses the
  entire stream regardless of how few symbols it is trusted to publish: the canary bounds
  blast radius, not cost.
- **Prices are exact.** "Prices are integer fields, supplied with an associated
  precision... a field flagged as Price (4) has an implied 4 decimal places." Two correct
  decoders of the same message produce the same value, which is why the default
  price-agreement tolerance in this skill is exact equality rather than a relative band.
- **Do not key routing on the stock locate code.** Locate codes are "dynamically assigned
  each day" and "there should be no expectation that the assignment will be the same
  across multiple days". Route on the symbol; a locate-keyed canary set silently
  re-shuffles overnight.

Other venues differ in detail. CME MDP 3.0, for instance, assigns products to multicast
channels through its published `config.xml`, so the granularity there is the channel
rather than the message stream. Verify the granularity for each venue you consume before
assuming it; the general point holds either way, which is that subscription granularity is
set by the venue and is coarser than one symbol.

## 5. Engineering standard — allocation must be reproducible

*Recommended practice, not a regulatory requirement.*

- Bucket on a **fixed digest** of the symbol. Python's built-in `hash()` for `str` is
  salted per process via `PYTHONHASHSEED`, so `hash(symbol) % 100` yields a different
  canary set in every process and after every restart.
- Use a threshold over a fixed bucket count so that ramping the percentage **only adds**
  symbols. A scheme that re-shuffles at each ramp step invalidates the observation you
  just made at the previous step.
- Choose a bucket count finer than your smallest intended allocation. With 100 buckets, a
  requested 0.5% allocation cannot be expressed and silently becomes ~1%.
- `hashlib.md5` raises on FIPS-enforcing Python builds. Where the hash has no security
  purpose, prefer `hashlib.blake2b`, which is always available.

## 6. Engineering standard — audit thresholds are policy, not standards

*Recommended practice, not a regulatory requirement. Calibrate all of it.*

| Knob | Default in `scripts/canary_router.py` | Why |
|---|---|---|
| `price_tolerance` | `0.0` (exact) | Both handlers decode the same message (Section 4). Any tolerance is a class of decode defect you have chosen not to detect. |
| `max_allowed_error_rate` | `0.01` | Combined mismatch+exception fraction of audited ticks. |
| `min_ticks_before_rollback` | `10` | Prevents the first mismatch in a small sample from reading as a 100% error rate. Raise it for high-tick-rate feeds. |
| `max_allowed_exceptions` | `0` | An unhandled exception in a decoder is a failed canary regardless of rate. |

Illustrative deployment shape — again, a starting point to calibrate, not a standard:

| Stage | Allocation | What it is for |
|---|---|---|
| Pinned whitelist | Deliberately awkward symbols | Exercises the formats the hash will not select. |
| Percentage ramp | 10% → 50% | Statistical coverage across the universe. |
| Full promotion | 100% | Canary becomes the incumbent. |

Observation windows should be chosen from **message counts and the session events
covered** (open auction, halt/resume, close), not from elapsed minutes. A quiet hour
exercises less of a parser than one halted instrument.

## 7. Engineering standard — what this control does and does not cover

*Recommended practice, not a regulatory requirement.*

- **Covered**: field-level decode divergence, non-finite and non-positive prices,
  decoder exceptions, and the blast radius of any of these.
- **Not covered**: packet loss and sequence gaps (both handlers miss the same packet and
  agree — see `sequence-number-gap-detection-for-feeds`), latency regression, memory
  growth, order book *state* divergence beyond the audited field, and correctness of a
  message type neither handler emits.
- Pair the two outputs by message identity (sequence number, or symbol plus exchange
  timestamp). Arrival-order pairing silently degrades into comparing different messages.
- Retention of the deployment event record is set by your applicable regime; this skill
  asserts no retention period.

## Category

`real-time-architecture` — see top-level `mappings/` directory.
