# Standards: Broker Failover and Secondary Account Routing

Two kinds of statement appear below. **Cited** items come from a published RFC,
regulation, or vendor specification and are reproduced with their source. **Calibrated**
items are engineering defaults with no external authority behind them — no regulator or
exchange publishes a circuit-breaker failure threshold, and a document presenting one as
a standard is inventing it.

## 1. The failure classification (the load-bearing part)

The circuit breaker is the well-known half of this pattern. The half that actually
protects capital is deciding *which* failures may be failed over.

| Class | Typical cause | Was the order received? | Failover |
|---|---|---|---|
| `UNAVAILABLE` | TCP connection refused, DNS resolution failure | No — nothing was sent | Safe |
| `RATE_LIMITED` | HTTP 429, or 418 after ignoring 429s | No | Safe, plus backoff on that leg |
| `AMBIGUOUS` | Read timeout, connection reset, HTTP 5xx | **Unknown** | **Forbidden** until resolved |
| `REJECTED` | Bad symbol, insufficient buying power, size limit | Yes, and refused | **Forbidden** — the order is dead |

**Cited — 5xx does not mean failure.** Binance's Spot REST general API information
states: "HTTP `5XX` return codes are used for internal errors; the issue is on Binance's
side. It is important to **NOT** treat this as a failure operation; the execution status
is **UNKNOWN** and could have been a success."
<https://developers.binance.com/docs/binance-spot-api-docs/rest-api/general-api-information>

**Cited — 503 is explicitly a temporary condition.** RFC 9110 Section 15.6.4 defines 503
Service Unavailable as "the server is currently unable to handle the request due to a
temporary overload or scheduled maintenance", and states the server "SHOULD generate a
Retry-After header field". `Retry-After` (Section 10.2.3) accepts either delay-seconds or
an HTTP-date, so a parser must handle both. Status 429 is defined in RFC 6585, which
RFC 9110 does not obsolete.

**Cited — throttling escalates.** Binance sends `Retry-After` with both 429 and 418, and
418 means the IP was auto-banned "for continuing to send requests after receiving `429`
codes". Failing over does not excuse you from backing off the throttled leg.

**Connect timeout versus read timeout.** These are different classes and most HTTP
clients distinguish them: a connect timeout means no request was sent (`UNAVAILABLE`), a
read timeout means it was sent and the response was lost (`AMBIGUOUS`). Adapters should
raise an explicit classification rather than leaving the router to guess; the router's
default for anything unrecognised is `AMBIGUOUS`.

## 2. Cross-broker duplicates cannot be prevented at the broker

A `client_order_id` prevents a duplicate at the broker that issued it. The secondary
broker has never seen that id and has no way to recognise the order. There is therefore
**no broker-side mechanism** that makes cross-account failover idempotent — the only
control is refusing to send the second order until the first is resolved.

This is why `AMBIGUOUS` is fatal to the submission rather than merely cautionary, and
why the resolver contract matters: a status query that itself fails leaves the outcome
ambiguous, and must never be collapsed into "not found".

## 3. Positions do not net across accounts (Reg SHO)

The defining risk of *account* failover, absent from endpoint failover. A strategy long
100 shares in the primary account that sends its closing sell to the secondary account
does not flatten: it ends long 100 in one account and short 100 in the other — double
the gross exposure, double the margin, two settlement obligations.

In US equities this is also a compliance event. From 17 CFR 242.200 (Regulation SHO):

- **Rule 200(c)**: "A person shall be deemed to own securities only to the extent that he
  has a net long position in such securities."
- **Rule 200(g)**: sell orders in equity securities must be marked "long", "short", or
  "short exempt", and may be marked long **only** where the seller owns the security and
  it is (or will be by settlement) in the broker-dealer's physical possession or control.
- **Rule 200(f)**: netting across independent trading units is available only under the
  aggregation-unit conditions — a documented written plan identifying each unit and its
  trading objective, net position determined per unit, traders pursuing only that unit's
  strategy, and each trader assigned to a single unit.
- **Rule 203(b)(1)** (17 CFR 242.203): a broker-dealer may not accept a short sale order,
  or effect one for its own account, without having borrowed the security, arranged to
  borrow it, or having reasonable grounds to believe it can be borrowed and delivered on
  the due date.

Consequence for the router: an order that reduces exposure is pinned to the account
holding the position. If that account is unreachable, the correct behaviour is to refuse
and escalate. Opening a position in the other account is not a degraded version of
closing one — it is the opposite trade.

Jurisdiction: US equities. Other markets have their own short-sale regimes (see
`eu-short-selling-regulation-disclosure-thresholds`), but the arithmetic — two accounts
do not net — is universal.

## 4. Pre-trade controls apply to both legs

**Cited.** 17 CFR 240.15c3-5, applying to broker-dealers with market access.
Paragraph (b) requires a documented "system of risk management controls and supervisory
procedures reasonably designed to manage the financial, regulatory, and other risks" of
market access. Paragraph (c)(1)(i) requires controls to "[p]revent the entry of orders
that exceed appropriate pre-set credit or capital thresholds in the aggregate for each
customer and the broker or dealer", and (c)(1)(ii) to "[p]revent the entry of erroneous
orders, by rejecting orders that exceed appropriate price or size parameters".

Architectural consequence: the risk layer sits **above** the router, so neither leg can
be routed around, and the limits are enforced in aggregate across both accounts rather
than per-account. A failover that moves flow to an account with looser limits has
defeated the control.

This is also why a `REJECTED` classification must never fail over: the rejection *is* a
risk control firing.

## 5. Business continuity (EU)

**Cited.** Commission Delegated Regulation (EU) 2017/589 (MiFID II RTS 6) Article 14,
"Business continuity arrangements", within Section 3 of Chapter II on resilience of
trading systems. An investment firm must have business continuity arrangements for its
algorithmic trading systems appropriate to the nature, scale and complexity of its
business, documented in a durable medium, effectively dealing with disruptive incidents
and, where appropriate, ensuring timely resumption of algorithmic trading, adapted to the
trading systems of each venue accessed.

A secondary broker relationship is a business continuity arrangement. Article 12 (kill
functionality) is a **separate** control: opening the circuit stops new flow to the
primary; it does not withdraw outstanding orders.

Jurisdiction: EU-authorised investment firms engaged in algorithmic trading. Text:
<https://eur-lex.europa.eu/eli/reg_del/2017/589/oj/eng>. Article numbers and titles were
confirmed against secondary indices of the regulation; consult the EUR-Lex text before
relying on any of it for a compliance decision.

## 6. Circuit breaker semantics

The pattern was popularised by Michael Nygard in *Release It!*; Fowler's summary
(<https://martinfowler.com/bliki/CircuitBreaker.html>) describes the half-open state as
producing "a trial call, which will either reset the breaker if successful or restart the
timeout if not". Neither source mandates a specific number of trial calls.

**How many probes is a design choice, not a standard.** Resilience4j exposes
`permittedNumberOfCallsInHalfOpenState` and defaults it to 10. That default suits cheap
idempotent reads. Here each probe is a live order against a broker believed to be down,
so this implementation defaults to **one**, claimed under an explicit permit — otherwise
every concurrent caller probes simultaneously, which is unbounded, not "a single test
order".

Two implementation properties that are not part of the classical description but are
required for correctness under concurrency:

- **Generation tagging.** A call that started before the breaker tripped can return
  after it. Without a generation tag, that stale success closes a circuit other threads
  opened for good reason and resets the failure counter with it.
- **Monotonic timing.** The recovery timeout must be measured with a monotonic clock.
  `time.time()` moves when NTP steps it; a backward step holds the circuit open forever.

**Calibrated defaults — re-derive these.** Three consecutive failures to trip, a 60 s
recovery timeout, one probe, one success to close. Reasonable for a REST order path and
arbitrary for anything else. Derive them from your own primary's observed error
distribution, and remember the cost asymmetry: tripping early costs you the primary's
pricing and fee tier, tripping late costs you fills.

## 7. Symbol translation

Brokers namespace instruments differently (`AAPL` versus `AAPL STK SMART` versus
`AAPL.S`). Two rules:

- **Fail closed on an unmapped symbol in production** (`strict_symbol_mapping=True`).
  Passing the canonical ticker through is convenient in development and dangerous live:
  the same string can denote a different instrument at the other broker, and the failure
  mode is a filled order in the wrong thing.
- **A mapping fault is not a broker failure.** Nothing was sent, so it is neither
  ambiguous nor a reason to fail over. It surfaces as its own error type.

## 8. What the backup does not inherit

A secondary account is a different account at a different firm. It has its own rate
limits, supported order types, margin treatment, fee schedule, market-data entitlements,
settlement conventions, and per-account margin and day-trading accounting. Flow sized for
the primary may be rejected, throttled, or margined differently at the backup. Validate
the backup under realistic load before relying on it, and re-validate when either firm
changes terms.

Note also that a backup concentrates counterparty risk differently rather than removing
it — see `counterparty-and-broker-concentration-risk`.
