---
name: conflict-of-interest-disclosure-for-prop-vs-client-flow
description: >-
  Use when a broker-dealer trades proprietarily in US equities alongside unexecuted
  customer orders, under FINRA Rule 5320 Manning: the no-knowledge information barrier,
  negative consent and minimum price improvement.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: regulatory-compliance-global
  tags: finra-rule-5320, manning-rule, prop-vs-client, conflict-of-interest, information-barrier, mifid-ii
  brokers_frameworks: "FINRA; SEC Rule 605/606; Generic Broker Engine"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill in broker-dealer architectures or multi-desk firms that run proprietary trading alongside customer order handling in **US equities**. FINRA Rule 5320 prohibits a member that holds an unexecuted customer order from trading the same security on the same side of the market for its own account **at a price that would satisfy that customer order**, unless it immediately thereafter fills the customer up to size at the same or better price — or unless one of the Rule's supplementary-material exceptions applies.

The direction of that test is the single most-often inverted piece of logic in Manning implementations:

| Held customer order | A proprietary trade "satisfies" it at | Permitted proprietary prices |
|---|---|---|
| BUY limit $150.00 | a purchase at **$150.00 or lower** | $150.01 or higher (limit + Rule 5320.06 increment) |
| SELL limit $150.00 | a sale at **$150.00 or higher** | $149.99 or lower (limit − Rule 5320.06 increment) |

If your gate blocks the firm from *paying up* over a customer buy limit, it has the rule backwards: it is permitting the actual front-running and blocking the harmless trades.

## When NOT to Use

- **Non-US-equity instruments.** Rule 5320 applies to NMS stocks and OTC Equity Securities. It does not reach options, futures or fixed income.
- **Non-US jurisdictions.** The EU/UK analogue is a *misuse-of-information* prohibition with different mechanics and no share/value opt-out (Commission Delegated Regulation (EU) 2017/565 Art. 67; UK COBS 11.3.5A R). Do not port the 10,000-share threshold outside the US — it has no counterpart there. See `mifid-ii-algo-trading-compliance-eu`.
- **As the firm's whole Rule 5320 programme.** This engine covers the price test and the .01/.02/.05/.06 exceptions. It does not implement the riskless-principal (.03) or ISO (.04) exceptions, does not perform the customer fill the Rule requires when the firm does trade at a satisfying price, and does not discharge the written order-handling procedures required by Rule 5320.07.
- **As market-abuse surveillance.** Trading ahead of a *third party's* order flow is a different problem — see `wash-trade-and-spoofing-self-detection` and `eu-market-abuse-regulation-mar-surveillance`.

## Prerequisites

- Order capacity tagging: principal vs. agency, carried to the venue on FIX `OrderCapacity(528)` (with `OrderRestrictions(529)`). `Rule80A(47)` is deprecated as of FIX 4.3 — do not build new integrations on it.
- A snapshot of the firm's unexecuted customer orders in the security, with limit price, size and the barrier/desk that holds them.
- Per-customer reference data: FINRA Rule 4512(c) institutional-account status, whether the Rule 5320.01 written disclosure was given at account opening **and annually thereafter**, and whether the customer opted **in** to Rule 5320 protection.
- An attested inventory of which desks sit behind an effective information barrier — a distinct `info_barrier_id` string is a label, not a control.
- Optionally, the current inside spread, needed for the Rule 5320.06 increment on sub-$1.00 and OTC securities.

## Workflow

1. **Order Ingestion**: Build a `PropOrder` with `symbol`, `side`, `quantity`, `price`, `info_barrier_id`, `security_type` (`NMS_STOCK` / `OTC_EQUITY`), `trading_unit_type` and `barriers_effective`.
2. **Gate the Order**: Call `evaluate_prop_order` and branch on `is_approved`, or call `enforce_prop_order`, which raises `Rule5320ViolationError` so a missing branch cannot leak an order to the market.
3. **Fail Closed on Bad Input**: An unrecognised side, a non-finite or non-positive price, a non-positive quantity, or a client order that cannot be parsed returns `INVALID_ORDER_PARAMETERS` and is **not** approved. A compliance gate that cannot evaluate an order must not pass it.
4. **Price Test with Minimum Price Improvement**: Every same-symbol, same-side customer order is compared using the direction table above, widened by the Rule 5320.06 increment: $0.01 for NMS stocks at or above $1.00, and the lesser of the tier increment or half the inside spread otherwise. A sub-penny "improvement" over a customer limit does not clear the Rule.
5. **Exception Audit, per client order** — and note that these are per *order*, not per audit:
   - **Odd lot (.05)**: a customer order for less than one round lot does not attach the obligation.
   - **No-knowledge (.02)**: distinct, effective information barriers between the desk holding the customer order and the proprietary desk. For **OTC Equity Securities this exception is not available to the market-making desk** — only to a non-market-making unit.
   - **Large order / institutional, on negative consent (.01)**: available where the account is institutional under Rule 4512(c), **or** the order is 10,000 shares or more **and** at least $100,000 in value — both, not either. It requires the written disclosure to have been given, and is lost the moment the customer opts in.
6. **Evaluate Every Order Before Approving**: An exception that covers one resting customer order says nothing about the next one. The engine approves only when every matching order is non-conflicting or excepted, and reports all conflicts in `result.conflicts`.
7. **Violation Resolution**: Block the proprietary order, or execute the customer order up to its size at the same or better price contemporaneously, as Rule 5320(a) requires. Persist the full `ConflictAuditResult` for supervisory review.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Inverting the Price Test**: Treating a proprietary buy *above* a customer's buy limit as the conflict. The violation is buying at or below it — that purchase is the fill the customer's resting order should have received.
- **Approving on the First Exception Found**: Returning APPROVED as soon as one resting customer order qualifies for the no-knowledge exception, without checking the rest of the book. The order behind it, held by the same desk, is then front-run silently.
- **Failing Open on Unparseable Input**: A side string of `"buy"` or `"B"` matching neither branch of a price test and falling through to "no conflict". Normalise, then reject what will not normalise.
- **Reading the .01 Exception as an Opt-Out Flag**: Rule 5320.01 works by negative consent — the firm must have *disclosed* and given the customer a meaningful opportunity to opt **in**. Absence of an opt-in is not the same as the presence of a valid disclosure; model both facts.
- **Loosening the Large-Order Threshold to OR**: 10,000 shares or more *unless less than $100,000 in value* means both conditions. A 10,000-share order in a $5 stock is not a large order for this purpose.
- **Blanket No-Knowledge for OTC Market Making**: Rule 5320.02 withholds the exception from the market-making desk in OTC Equity Securities. An NMS-stock configuration silently applied to OTC flow over-approves.
- **Sub-Penny Price Improvement**: Buying at $150.001 over a $150.00 customer buy limit does not satisfy the order, but it does not clear the Rule 5320.06 increment either.
- **Sharing Order-Book State Across Desks**: Letting a proprietary algorithm query the firm's customer order book at all, then claiming the no-knowledge exception. The exception rests on the barrier working, not on it being declared.
- **Binary Float Price Comparison**: Rule 5320 turns on exact equality with a customer limit price. Compare in `Decimal`; the engine converts floats through `str()` for that reason.
- **Un-tagged Capacity**: Sending orders without `OrderCapacity(528)`, leaving the audit trail unable to distinguish principal from agency flow.

## Verification

- Instantiate `PropVsClientConflictEngine` with a pending 500-share retail CLIENT **buy** limit for AAPL at $150.00 on `DESK_A`. Submit a PROP buy at $150.00 from `DESK_A`: expect `FINRA_RULE_5320_TRADING_AHEAD`. Submit a PROP buy at $140.00: expect the **same** violation. Submit at $150.005: still blocked, because the improvement is below the Rule 5320.06 increment. Submit at $150.01: approved, with no exception recorded — clearing the increment means there was no conflict to except.
- Set distinct `info_barrier_id` values and confirm `NO_KNOWLEDGE_BARRIER`; then set `security_type=OTC_EQUITY` with `trading_unit_type=MARKET_MAKING` and confirm the exception is withdrawn.
- Put two resting client orders in the book, one behind a barrier and one on the proprietary desk, and confirm the order is still blocked.
- Submit a prop order with `side="B"` and with a `NaN` price; confirm both are rejected rather than passed.
- Run `python -m unittest discover -s skills/conflict-of-interest-disclosure-for-prop-vs-client-flow/scripts` and confirm all tests pass.

## Related Skills

- `best-execution-record-keeping-global`
- `mifid-ii-algo-trading-compliance-eu`
- `sec-rule-15c3-5-risk-controls-us`
- `wash-trade-and-spoofing-self-detection`
