---
name: us-reg-sho-short-sale-locate-requirements
description: "Institutional regulatory compliance skill for US SEC Regulation SHO (17 CFR 242.200-204), enforcing Rule 200(g) order markings (LONG, SHORT, SHORT_EXEMPT with a named 242.201(c)/(d) basis), Rule 203(b)(1) short sale locate verification and reservation, and the Rule 201 short sale price test (alternative uptick rule) against the current national best bid."
domain: US Regulatory Compliance & Market Structure
subdomain: SEC Regulation SHO (Short Sale Regulations)
tags:
- sec-reg-sho
- rule-200
- rule-203
- rule-201
- locate-requirements
- short-sale
- order-marking
- ssr-uptick-rule
brokers_frameworks:
- sec-reg-sho
- finra-cats
- quickfix
version: "2.0.0"
author: Quant Engineering
license: MIT
---

## When to Use

Use this skill when building or auditing the pre-trade gate that sits between an equity
strategy and a US execution venue, under **SEC Regulation SHO** ([17 CFR 242.200-204](https://www.ecfr.gov/current/title-17/chapter-II/part-242)).

It provides mechanisms to:
- Validate **Rule 200(g)** order markings, and require a named statutory basis whenever an
  order is marked `SHORT_EXEMPT` — the marking is permissible *only* where [242.201(c) or
  242.201(d)](https://www.law.cornell.edu/cfr/text/17/242.201) is satisfied.
- Enforce **Rule 203(b)(1)** locate verification before a short sale reaches a venue, checking
  locate identity, symbol alignment, expiry, and remaining capacity, then *reserving* the
  capacity against the order so it cannot be spent twice.
- Enforce the **Rule 201** short sale price test while a restriction is in force: a `SHORT`
  order in a covered security must be priced strictly above the current national best bid
  ([242.201(b)(1)(i)](https://www.law.cornell.edu/cfr/text/17/242.201)).
- Produce an auditable decision record for every order the gate sees, including rejections.

## When NOT to Use

- **As the marking decision itself.** Deciding that a sale is "long" turns on ownership and
  deliverability under [242.200(a)-(f)](https://www.law.cornell.edu/cfr/text/17/242.200) —
  net long position, aggregation units, settlement-date deliverability. Those facts live in
  the books-and-records system. This engine validates the marking it is handed; it does not
  derive it, and it will pass a `LONG` order that was marked wrongly upstream.
- **As the Rule 201 trigger.** Under [242.201(b)(3)](https://www.law.cornell.edu/cfr/text/17/242.201)
  the **listing market** determines the 10% decline and makes it available under 242.603(b).
  The authoritative input is the SIP Reg SHO price test indicator. `evaluate_local_trigger()`
  is a staleness check on that feed, never a substitute for it.
- **For securities outside Rule 201's scope.** The price test applies to *covered securities*
  — NMS stocks as defined in 242.600(b)(65). Rule 200(g) marking and Rule 203(b)(1) locates
  reach equity securities generally, so do not assume the three rules share a universe.
- **To claim a Rule 203(b)(2) locate exception.** Broker-to-broker reliance, deemed ownership,
  and bona fide market making are deliberately not implemented. Claiming one is a documented
  firm decision with its own supervisory record, not something an order gate should infer.
- **For Rule 204 close-out.** Fails-to-deliver are a clearing-participant obligation measured
  from settlement date, handled in the clearing stack. See `references/standards.md`.

## Prerequisites

- Python 3.9+ (standard library only).
- A prime broker / clearing firm locate feed (ETB and HTB) that supplies a locate identifier,
  symbol, quantity, and validity window. Reg SHO prescribes no locate lifetime — the TTL in
  this engine is firm policy, and industry practice is good-for-the-trading-day.
- The **SIP Reg SHO short sale price test indicator** (UTP and CTA) for Rule 201 state, plus a
  real-time NBBO feed for the price comparison itself.
- A supervisory procedure for `SHORT_EXEMPT` eligibility. [242.201(c)](https://www.law.cornell.edu/cfr/text/17/242.201)
  requires written policies reasonably designed to prevent an order being incorrectly
  identified as priced above the national best bid.

## Workflow

1. **Ingest locates.** `grant_locate(locate_id, symbol, quantity, lender_id, ttl_hours)`.
   A duplicate `locate_id` raises `RegSHOError` rather than overwriting: silently replacing a
   record resets `quantity_used` to zero and re-opens capacity already spent, which is exactly
   the double-count the gate exists to prevent.
2. **Track Rule 201 state from the SIP.** Call `trigger_rule_201_ssr(symbol)` when the price
   test indicator turns on and `deactivate_rule_201_ssr(symbol)` when it turns off. Leave
   `effective_through` unset unless you have an authoritative end time — Rule 201(b)(1)(ii)
   runs the restriction for the remainder of the trigger day *and the following day*, and a
   guessed trading calendar that lifts it early is a worse failure than one that lifts it late.
   Run `evaluate_local_trigger(prior_close, last_trade_price)` alongside as a feed-health
   check: a local 10% decline with no SIP flag means escalate, not override.
3. **Gate the order.** Pass an `OrderIntent` to `validate_order_intent()`. `LONG` passes with
   no locate. `SHORT` and `SHORT_EXEMPT` both require a locate — the Rule 201 marking does not
   reach Rule 203 (SEC Division of Trading and Markets, Rule 201 FAQ).
4. **Read the decision, not the exception.** The gate never raises on the order path; a
   structurally invalid order comes back as a non-compliant result so the rejection is logged.
   `RegSHOError` is raised only by registry administration (`grant_locate`,
   `release_locate_reservation`), where the caller is firm operations code.
5. **Release what does not trade.** An approval *reserves* locate capacity. If the order is
   cancelled, rejected by the venue, or lost to a session drop, call
   `release_locate_reservation(order_id)`. Skipping this leaks capacity and blocks legitimate
   shorts for the rest of the locate's life. A genuinely new short sale needs a new order ID.
6. **Archive.** Every decision is appended to `engine.audit_log` with a timezone-aware
   timestamp, the locate status, and any `short_exempt_reason`. Persist it under the firm's
   books-and-records policy (see `assets/checklist.md`).

## Common Pitfalls

- **Retrying a pre-trade check and double-spending the locate.** A timed-out or replayed
  validation for the same order must not reserve capacity twice. This engine remembers, by
  `order_id`, every decision that actually *reserved* capacity: a repeat returns that original
  decision and reserves nothing, and a repeat carrying *different* terms is rejected outright.
  Rejections are not remembered — they reserved nothing, so an order re-submitted once its
  cause is fixed is evaluated afresh rather than frozen at its first refusal.
- **Letting NaN or a missing bid pass the price test.** `float("nan") <= nbb` is `False`, so an
  unvalidated NaN price satisfies the Rule 201 comparison by default, and an `nbb_price` of
  `0.0` from a stale feed lets any positive price through. Both turn a market data fault into a
  silent compliance bypass. When the restriction is in force and no valid current national best
  bid is available, reject — the price test is defined against the bid and cannot be evaluated
  without one.
- **Marking `SHORT_EXEMPT` for bona fide market making.** Market making is a Rule
  **203(b)(2)(iii)** *locate* exception, not a Rule 201 price-test exception. The only
  market-maker basis in Rule 201 is the narrow **(d)(2)** odd-lot exception. An order marked
  short exempt on a market-making rationale is a Rule 200(g)(2) marking violation.
- **Treating "short exempt" as relief from the locate requirement.** It is not. SEC staff have
  answered "No" to whether the short exempt marking may be used for an order qualifying for a
  Rule 203 locate exception; the two regimes are independent.
- **Rebuilding the 10% trigger locally and trusting it.** The listing market's determination is
  the compliance trigger. A locally computed decline will disagree at the margins — corporate
  action adjustments, the closing price the listing market actually publishes — and acting on
  it in the permissive direction shorts into a live restriction.
- **Never releasing reservations.** Locate pools that only ever decrease look conservative and
  then silently starve the desk. Reserve on approval, release on anything that is not a fill.
- **Reusing a locate for a later short after covering intraday.** Permitted only under the
  narrow conditions in SEC Reg SHO FAQ 4.4, and never for threshold or hard-to-borrow
  securities — a deficiency FINRA has specifically called out. Do not build it into the gate.
- **Comparing prices at the epsilon.** Sub-penny increments are not exactly representable in
  binary floating point. Bias the comparison toward rejection: a spurious rejection costs a
  fill, a spurious approval executes a prohibited short sale.

## Verification

```bash
python -m unittest discover -s skills/us-reg-sho-short-sale-locate-requirements/scripts
```

Covers Rule 200(g) marking validation including an unqualified `SHORT_EXEMPT`; locate identity,
symbol mismatch, expiry (with a fixed clock and a naive prime-broker timestamp), and the exact
capacity boundary; the Rule 201 price test at, above, and within one epsilon of the bid;
fail-closed handling of a missing, zero, negative, NaN, and infinite national best bid;
Rule 201(c) claims verified against the bid and Rule 201(d) bases bypassing it; retry
idempotency, reused order IDs with different terms, and the reservation release lifecycle;
and the advisory-only behaviour of the local 10% trigger.

## Related Skills

- `short-selling-borrow-cost-and-availability-modeling`
- `sec-rule-15c3-5-risk-controls-us`
- `us-reg-nms-order-protection-rule-compliance`
- `eu-short-selling-regulation-disclosure-thresholds`
- `record-retention-periods-by-jurisdiction`
