---
name: broker-failover-secondary-account-routing
description: >-
  Use when order flow must continue on a backup broker account after the primary
  degrades. Classifies which failures are safe to fail over, refuses to re-send an order
  that may already be working, and pins reducing orders to the account holding the
  position.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: broker-integration
  tags: broker-integration, failover, circuit-breaker, high-availability, duplicate-order-prevention, position-integrity, resilience
  brokers_frameworks: "Binance Spot REST API; RFC 9110 (503 / Retry-After); SEC Regulation SHO (17 CFR 242.200/203); SEC Rule 15c3-5; MiFID II RTS 6 (Reg (EU) 2017/589)"
  version: "3.0.0"
  author: algo-trading-skills-contributors
---

# Broker Failover & Secondary Account Routing

## When to Use

Invoke this skill when a trading system holds a backup broker relationship and needs
order flow to continue after the primary degrades — connection loss, sustained 5xx,
throttling, or an outage mid-session. It provides a circuit breaker over two broker
legs, with the failover decision gated on *why* the primary failed.

A generic circuit breaker guards idempotent reads, where retrying elsewhere is free.
This one guards live order submission across two **accounts**, which breaks three of the
pattern's usual assumptions:

> **A failed call is not a failed order.**
> **Not every error is an outage.**
> **A probe is a real order.**

And one assumption that endpoint failover never has to think about at all: **positions
do not net across accounts.** Failing a *closing* order over to the backup account does
not flatten anything — it leaves you long in one account and short in the other, with
double the gross exposure.

## When NOT to Use

- **As a retry mechanism.** It routes each order once, to one place. Retrying the same
  intent is a different problem with different safety rules — see
  `order-placement-idempotency`.
- **As reconciliation.** It *detects* that an outcome is ambiguous and refuses to act;
  resolving "did the primary take my order?" needs the broker's order-state stream or a
  status query, which you supply. See `webhook-based-order-fill-notifications`.
- **As a risk control.** Pre-trade limits belong above the router and must apply to both
  accounts. A second broker is a second market-access path, not an exemption —
  `sec-rule-15c3-5-risk-controls-us`.
- **As a kill switch.** Opening the circuit stops *new* flow to the primary; it does not
  withdraw working orders. That is `kill-switch-and-drawdown-circuit-breakers`.
- **For failover between endpoints of the same account** (a regional DR flip, a second
  gateway). There the position-integrity problem does not exist and the routing is
  simpler — see `multi-region-failover-for-broker-connectivity` and
  `smart-order-router-failover-on-venue-outage`.

## Prerequisites

- **A funded, entitled, tested secondary account** with the same instruments enabled.
  A backup you have never placed an order through is not a backup.
- **Stable `client_order_id`s per order intent.** This is the key you reconcile on when
  a call is ambiguous. Note what it does *not* buy you here: a client order id
  de-duplicates at the broker that issued it, and the secondary broker has never seen
  it. **Cross-broker duplicates cannot be prevented by the broker** — only by not
  sending the second order.
- **An order-status resolver**, ideally. Without one, every ambiguous primary failure
  becomes a hard stop for that order. With one, the router can ask "is it working?" and
  keep going.
- **Seeded positions per account.** The router's position map is a cache. Seed it from
  each broker's own position report at session start, or a REDUCE order will be judged
  on stale information.
- **Symbol mappings for every tradable instrument**, and `strict_symbol_mapping=True` in
  production.
- **Documented business continuity arrangements** where required. EU-authorised
  investment firms are subject to MiFID II RTS 6 (Reg (EU) 2017/589) Article 14, which
  requires business continuity arrangements for algorithmic trading systems,
  proportionate to the business and documented in a durable medium.

## Workflow

1. **Classify the failure before deciding anything.** This is the whole skill. Four
   classes, three different answers:

   | Class | Meaning | Failover? |
   |---|---|---|
   | `UNAVAILABLE` | Connection refused, DNS failure — the request never left | **Yes**, safe |
   | `RATE_LIMITED` | 429/418 — not accepted, but back off this leg | **Yes**, and honour `Retry-After` |
   | `AMBIGUOUS` | Timeout, connection reset, 5xx — it may be working | **No** — resolve first |
   | `REJECTED` | Bad symbol, insufficient buying power, size limit | **No** — the order is dead |

   Anything unrecognised is `AMBIGUOUS`. The errors are not symmetric: a wrong
   "ambiguous" costs one status query, a wrong "failed" costs a duplicate order in an
   account that will never net it off.

2. **Never fail over an ambiguous outcome.** Binance's REST documentation states the
   general case for 5xx: "It is important to **NOT** treat this as a failure operation;
   the execution status is **UNKNOWN** and could have been a success." Either resolve it
   through a status query, or stop and make the caller reconcile. A resolver that itself
   errors means *still ambiguous* — never read a failed status check as "not there".

3. **Never fail over a terminal rejection.** "Insufficient buying power" is the primary's
   pre-trade risk control working. Routing it to a second account is shopping for a
   broker that will say yes. Re-raise it, and do **not** count it against broker health —
   otherwise the strategy's own bad orders trip the breaker.

4. **Pin reducing orders to the account that holds the position.** Mark every closing or
   reducing order `PositionEffect.REDUCE`. In an account with no position the identical
   instruction opens new exposure. In US equities it is also a compliance event: Reg SHO
   Rule 200(c) deems a person to own securities "only to the extent that he has a net
   long position", Rule 200(g) permits a "long" mark only where the seller owns the
   security, and Rule 203(b)(1) requires a locate before a short sale order is accepted.
   Netting the two accounts requires the independent-trading-unit conditions of Rule
   200(f), including a written plan. If the holding account is down, the correct answer
   is to refuse and escalate — not to open a position somewhere else.

5. **Bound the half-open probes.** Each probe is capital at risk against a broker you
   believe is down. Generic implementations permit several concurrent trial calls
   (resilience4j's `permittedNumberOfCallsInHalfOpenState` defaults to 10); here the
   default is one, claimed under an explicit permit so concurrent callers cannot all
   probe at once.

6. **Ignore stale successes.** A slow call that started before the breaker tripped can
   return after it. Honouring it closes a circuit that other threads opened for good
   reason. Tag each attempt with the circuit generation and discard successes from an
   older one.

7. **Measure the recovery timeout on a monotonic clock.** `time.time()` moves when NTP
   steps it; a backward jump holds the circuit open indefinitely.

8. **Handle "both legs down" as its own outcome.** It is the case that matters most and
   the one most likely to be untested. The caller needs a typed error naming the order,
   not whatever the secondary happened to raise.

> Full failure-classification tables and phase procedure: see `references/workflows.md`.
> Cited standards, Reg SHO analysis, and thresholds: see `references/standards.md`.
> Printable go-live checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Failing over on a timeout.** The primary may have accepted the order. The secondary
  cannot de-duplicate it, because it has never seen the client order id. You end up with
  two live orders in two accounts and no automatic way to net them.
- **Treating every exception as a clean failure.** `except Exception: route_to_secondary`
  is the single most damaging line this pattern attracts.
- **Failing over a business rejection.** An order the primary refused on buying power or
  size gets filled at the backup — the failover has quietly become a risk-control bypass.
- **Counting rejections toward the breaker.** A strategy sending bad orders then trips
  the circuit and pushes healthy flow to the backup for no reason.
- **Failing over a closing order.** It opens new exposure instead of reducing any, and
  in US equities produces a sale in an account that owns nothing.
- **Assuming positions net across accounts.** They do not, absent a Rule 200(f)
  aggregation-unit plan. Two accounts flat against each other is *double* gross exposure,
  double margin, and two settlement obligations.
- **Unbounded half-open probing.** Every concurrent caller sends a live order to a broker
  believed to be down.
- **Letting a stale success close the circuit.** One slow call returning late resets the
  breaker and the failure counter, and flow returns to a broker that is still broken.
- **Using the wall clock for the recovery timeout.** An NTP step forward probes early; a
  step backward never probes at all.
- **Silently passing an unmapped symbol through.** The same ticker can denote a different
  instrument at the other broker. Use strict mapping in production.
- **Leaving the secondary leg untested.** A backup that has never taken an order is an
  assumption, not a control. Exercise it on a schedule.
- **Forgetting the backup has its own limits.** Different rate limits, different order
  types, different margin treatment, different pattern-day-trade and margin accounting
  per account. Flow that fits the primary may not fit the backup.

## Verification

- Run `python -m unittest discover -s skills/broker-failover-secondary-account-routing/scripts`
  and confirm all tests pass.
- Make the primary accept an order and then raise `TimeoutError`. Confirm the router
  raises `AmbiguousOrderStateError` carrying the `client_order_id`, and that the
  secondary received **nothing**.
- Supply an `order_status_resolver` that finds the order; confirm the router returns the
  real primary fill and still does not touch the secondary. Then make the resolver raise
  and confirm the outcome stays ambiguous rather than becoming "not found".
- Make the primary raise a `REJECTED` `BrokerError`; confirm it is re-raised, the
  secondary is untouched, and the circuit stays `CLOSED` with `primary_failures == 0`.
- Make the primary raise `UNAVAILABLE`; confirm the order reaches the secondary with the
  secondary's symbol mapping applied.
- Fail both legs; confirm `AllBrokersUnavailableError` names the order.
- Seed a long position in the primary, take the primary down, and submit a
  `PositionEffect.REDUCE` sell. Confirm `PositionAffinityError` and that no short was
  opened at the secondary.
- Trip the circuit, wait past the recovery timeout, then fire ten concurrent orders and
  confirm exactly **one** reached the primary.
- Start a slow successful call, trip the circuit from other threads while it is in
  flight, then let it return. Confirm the circuit is still `OPEN`.
- Patch `time.time` to a fixed value and confirm recovery still happens on schedule.
- Call `manual_open`, wait past the recovery timeout, and confirm the primary is still
  not probed until `manual_reset`.
- With `strict_symbol_mapping=True`, submit an unmapped symbol and confirm
  `SymbolMappingError` — not an ambiguous outcome, and no failover.

## Related Skills

- `order-placement-idempotency`
- `broker-api-idempotent-cancel-requests`
- `broker-agnostic-adapter-interface`
- `multi-region-failover-for-broker-connectivity`
- `smart-order-router-failover-on-venue-outage`
- `multi-broker-consolidated-position-view`
- `multi-broker-rate-limit-handling`
- `kill-switch-and-drawdown-circuit-breakers`
- `webhook-based-order-fill-notifications`
- `us-reg-sho-short-sale-locate-requirements`
- `sec-rule-15c3-5-risk-controls-us`
- `counterparty-and-broker-concentration-risk`
