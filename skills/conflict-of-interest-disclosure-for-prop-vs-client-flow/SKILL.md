---
name: conflict-of-interest-disclosure-for-prop-vs-client-flow
description: Compliance engine for auditing Proprietary (Prop) vs. Client Agency order
  flow, enforcing FINRA Rule 5320 (Manning Rule) front-running prohibitions, information
  barriers, and mandatory disclosures.
domain: Compliance & Regulation
subdomain: Order Handling & Conflicts
tags:
- finra-rule-5320
- manning-rule
- prop-vs-client
- conflict-of-interest
- information-barrier
- mifid-ii
brokers_frameworks:
- FINRA
- SEC Rule 605/606
- Generic Broker Engine
version: 1.0.0
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in broker-dealer architectures or multi-desk quantitative firms that run both Proprietary (Prop) trading algorithms and Client Agency order routing. **FINRA Rule 5320 (Manning Rule)** and **MiFID II Article 23/27** prohibit a firm from trading for its own account on the same side of the market as an unexecuted customer order at a price that would satisfy the customer order, unless an information barrier (No-Knowledge Exception) or institutional disclosure opt-out is in place.

## Prerequisites

- Order capacity tagging: `PROP` (Principal) vs. `CLIENT` (Agency).
- Information Barrier identifiers (`info_barrier_id`) and institutional disclosure opt-out status flags.

## Workflow

1. **Order Ingestion**: Receive proposed `PROP` order (`symbol`, `side`, `price`, `quantity`, `desk_id`, `info_barrier_id`).
2. **Pending Client Order Audit**: Query active unexecuted `CLIENT` limit orders for the same symbol and side.
3. **Manning Rule Evaluation**:
   - For `BUY` orders: Check if $P_{prop} \ge P_{client\_limit}$.
   - For `SELL` orders: Check if $P_{prop} \le P_{client\_limit}$.
4. **Exception Handling**:
   - **No-Knowledge Exception**: If `prop.info_barrier_id != client.info_barrier_id`, pass check (Information Barrier active).
   - **Institutional Opt-Out**: If client has signed annual disclosure opt-out for orders $\ge 10,000$ shares / $\$100,000$, pass check.
5. **Violation Resolution**: If check fails and no exception applies, block Prop order or trigger mandatory price-improvement pass-through to the customer.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Sharing Order Book State across Desks**: Allowing a proprietary execution algorithm to query the firm's internal client limit order book, destroying the Information Barrier.
- **Ignoring Size Thresholds**: Applying the institutional opt-out exception to retail orders under 10,000 shares / $100,000.
- **Un-tagged Orders**: Submitting orders to exchanges without clear `Capacity` (Principal vs Agency) FIX tags (Tag 47).

## Verification

- Instantiate `PropVsClientConflictEngine`. Submit a pending retail CLIENT buy order for 1,000 shares of AAPL @ $150.00. Submit a PROP buy order @ $150.00 from the same information barrier domain without an opt-out. Verify that the engine blocks the Prop order with a `FINRA Rule 5320 Violation`. Set distinct `info_barrier_id` values and verify the No-Knowledge exception passes.
- Run `python scripts/test_conflict_of_interest_disclosure_for_prop_vs_client_flow.py`.

## Related Skills

- `best-execution-record-keeping-global`
- `broker-account-margin-call-handling`
---
