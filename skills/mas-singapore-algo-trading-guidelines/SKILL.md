---
name: mas-singapore-algo-trading-guidelines
description: >-
  Pre-trade compliance gate for algorithmic orders routed to Singapore Exchange, auditing SFA licensing and SGX Approved Trader registration, Clearing Member pre-execution value limits, the Forced Order Range and Force Key, and the SGX-ST circuit breaker band around the five-minute-lagged reference price.
domain: Regulatory Compliance Global
subdomain: Singapore SFA Licensing & SGX Pre-Trade Controls
tags: ["mas", "singapore", "sfa", "sgx", "circuit-breaker", "forced-order-range", "pre-trade-risk", "kill-switch"]
brokers_frameworks: ["Securities and Futures Act 2001 (Singapore)", "SGX-ST Rule 8.14 with Regulatory Notice 8.14.1 and Practice Note 8.10A", "SGX-ST Practice Note 8.6 (Forced Order Range) and Regulatory Notice 11.4.2(g) (Force Key)", "SGX Futures Trading Rules 2.13.2, 2.13.4, 3.9.1(3), 4.1.15", "SGX RegCo Algorithmic Trading Regulatory Guide", "Python Dataclasses"]
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when gating algorithmic order flow into Singapore Exchange — SGX-ST securities or SGX-DT derivatives — from an entity regulated by the Monetary Authority of Singapore under the Securities and Futures Act 2001 (SFA).

Start here by correcting the most common misconception about this jurisdiction. **Singapore has no algorithm registration regime and MAS issues no algorithm identifier.** There is no Singapore equivalent of SEBI's exchange-assigned Algo-ID. MAS licenses *entities* (Capital Markets Services licence or exemption) and registers *representatives*; SGX registers *members and Approved Traders* (Futures Trading Rules 2.13.2, 2.13.4). Algorithms themselves are registered nowhere. Any gate that blocks SGX orders for want of a "MAS algorithm registration number" is enforcing a requirement that does not exist.

What does bind a Singapore algorithmic order, and what this skill actually audits:

- **Entity licensing and the registered human behind the flow** — SFA licensing, and the SGX-registered Approved Trader under whose authority the order is entered.
- **Pre-execution value limits** — SGX FTR 3.9.1(3) and Practice Note 3.9.1(3) require Clearing Members to set pre-execution limits on their trading participants, checked either at the Clearing Member's system or by SGX's exchange-hosted Pre-Trade Risk Controls module. The limit *values* are set by the firm and its Clearing Member.
- **Forced Order Range** — SGX-ST Practice Note 8.6: an order priced outside the range must be confirmed with the Force Key (Regulatory Notice 11.4.2(g)) before it may be submitted.
- **Circuit breakers** — SGX-ST Rule 8.14 with Regulatory Notice 8.14.1 and Practice Note 8.10A; SGX-DT price limits under FTR 4.1.15 and the individual contract specifications.
- **Automated trading controls** — SGX RegCo's Algorithmic Trading Regulatory Guide, key aspects of which were formalised into the Futures Trading Rules and SGX-ST Rules following the SGX RegCo consultation of 21 September 2023.

## When NOT to Use

- **As evidence of a licence or a registration.** The engine reads flags the caller supplies. It cannot confirm that a Capital Markets Services licence is live or that an Approved Trader registration is current. The authoritative sources are the MAS Financial Institutions Directory and the SGX register of Approved Traders.
- **As a replacement for the exchange's or the Clearing Member's gate.** Every SGX order is checked against pre-execution limits at the Clearing Member's system or by SGX's own Pre-Trade Risk Controls module, and the matching engine applies the circuit breaker itself. This engine predicts those outcomes so the algorithm can avoid them; it does not substitute for them.
- **For entity-level obligations that do not attach to a single order.** Business continuity, technology risk management, the register of Sponsored Access customers, trade surveillance and audit-trail retention are periodic or systemic obligations, not per-order checks. See `singapore-mas-notice-on-cyber-hygiene-for-trading-systems` for the MAS cyber baseline.
- **For board-lot and tick-size validation.** Lot rounding and minimum bid size conformance are separate rejection paths — see `minimum-fill-size-and-lot-rounding-logic`.
- **Outside Singapore.** The Forced Order Range, the Force Key and SGX's circuit breaker construction are SGX mechanisms. Do not carry the 10% band or the ±30 bids into another venue's gate.

## Prerequisites

- Firm control config (`SingaporeAlgoControlConfig`: `algo_id`, `approved_trader_id`, `is_approved_trader_registered`, `has_cms_licence_or_exemption`, `is_pre_deployment_tested`, `has_kill_switch`, `max_order_value`, `limit_currency`, `max_order_rate_per_sec`, `circuit_breaker_band_pct`, `forced_order_range_bids`).
- Order payload (`SgxOrderRequest`: `algo_id`, `symbol`, `side`, `quantity`, `limit_price`, `currency`, `min_bid_size`, `forced_order_range_ref_price`, `circuit_breaker_ref_price`, `opposite_best_price`, `is_circuit_breaker_eligible`, `session`, `force_key_confirmed`, `current_order_rate_per_sec`).
- The **circuit breaker reference price**: the last traded price at least five minutes earlier. Not the current mid, not the current last done.
- Today's **circuit breaker eligibility** for the instrument. SGX-ST assesses this daily: the reference price at the start of the Market Day must be at least 0.50 in the instrument's underlying currency (JPY 500 for yen-denominated instruments).
- The firm's own **calibrated** value and message-rate ceilings. SGX publishes neither — see `references/standards.md`.
- For SGX-DT, the **contract's own** price limit, which replaces the 10% securities default.

## Workflow

1. **Reject structurally invalid input before auditing anything.** A NaN or infinite price compares `False` against every ceiling and would otherwise be approved; a non-positive quantity yields a non-positive order value that passes every ceiling. Both raise `ValueError`, as does an order priced in a currency other than the ceiling's — comparing a USD notional against an SGD limit understates risk rather than overstating it.
2. **Entity and Approved Trader governance.** Audit the CMS licence or exemption, the current Approved Trader registration, and that the order's `algo_id` matches the config's. An order tagged for one algorithm audited against another's limits is a governance failure, not a formality $\implies$ `REJECTED_UNLICENSED_ENTITY`, `REJECTED_UNREGISTERED_APPROVED_TRADER`, `REJECTED_ALGO_ID_MISMATCH`.
3. **Automated trading controls.** Pre-deployment testing sign-off and an armed kill switch $\implies$ `REJECTED_ALGO_NOT_TESTED`, `REJECTED_NO_KILL_SWITCH`.
4. **Pre-execution value limit.** Value a limit order at its limit and a market order at the opposite best price. An order that can be priced by neither **fails closed** $\implies$ `REJECTED_UNPRICEABLE_ORDER`; over the firm's ceiling $\implies$ `REJECTED_PRE_EXECUTION_LIMIT`.
5. **Circuit breaker band — check the potential trade price, not the order price.** The mechanism runs only during continuous trading and only for eligible instruments. Then:
   - Establish marketability: a market order always matches; a limit order matches only if it crosses `opposite_best_price`. Unknown book state resolves **conservatively** to "may be marketable", so a missing field can never make a breaching order look safe.
   - Test the worst *knowable* potential trade price — the limit price for a limit order, the opposite best price for a market order — against $\pm$`circuit_breaker_band_pct` of the reference price. The band is **inclusive**, so a breach requires the price to be strictly outside it; compare unrounded and round only for reporting.
   - A **marketable** order outside the band $\implies$ `REJECTED_CIRCUIT_BREAKER_BAND`. A **non-marketable** order outside the band is *not* rejected — it rests, and is recorded as a warning because it is a latent Cooling-Off trigger for whoever aggresses it later.
6. **Forced Order Range.** Compute the distance from the range's reference price in minimum bid sizes. Outside the range without a Force Key confirmation $\implies$ `REJECTED_FORCED_ORDER_RANGE`; with one, the order proceeds and the deliberate override is recorded as a warning.
7. **Message rate ceiling.** The caller owns the counter; this engine is stateless and cannot enforce a rate on its own $\implies$ `REJECTED_ORDER_RATE_LIMIT`.
8. **Audit report generation.** Every check runs; nothing short-circuits. Output `SgxPreTradeComplianceReport` carrying the full `breaches` tuple, the `warnings` tuple, and a `status` set to the most serious breach. Checks that did not run report `None`, never `0.0`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Demanding a MAS algorithm registration number.** MAS issues none. A gate that rejects SGX orders for a missing "MAS Algorithm Identifier" blocks entirely legitimate flow on a fabricated requirement, and — worse — creates false confidence that a real control is in place. What Singapore actually registers is the entity's licence and the Approved Trader's registration.
- **Treating the circuit breaker as an order-price collar.** It is not. The Cooling-Off Period triggers when an *incoming order seeks to match* outside the band; the unfilled quantity is then rejected. A resting buy limit 40% below the market is not rejected on entry. A gate that blocks it produces false rejections; a gate that ignores the marketable case produces false approvals.
- **Comparing against the current mid or the current last done.** The reference price is the last traded price **at least five minutes earlier**. In exactly the fast market where the mechanism matters, a live-mid comparison and the exchange will disagree — and the exchange wins.
- **Rounding the deviation before comparing it to the band.** Rounding 10.0049% to 10.00% and then testing `> 10.0` approves an order that is genuinely outside the band. Compare unrounded; round only for the report. The same applies to the value ceiling.
- **Letting NaN through a risk gate.** `float('nan') > limit` is `False`, so every ceiling silently passes and the order is approved. Validate for finiteness before comparing, never after.
- **Applying the 10% band to SGX-DT.** 10% is the SGX-ST securities figure. Derivatives price limits are set per contract under FTR 4.1.15 and the contract specifications. Hard-coding 10% across both is wrong in both directions.
- **Applying the band to every security and every session.** Circuit breaker eligibility is assessed **daily** and requires a start-of-day reference price of at least 0.50 in the underlying currency. The mechanism does not run during the opening and closing routines.
- **Presenting a house limit as a regulatory threshold.** SGX requires that pre-execution limits exist and be set by the Clearing Member; it publishes no figure. Shipping `max_order_value=1_000_000.0` as a "MAS limit" is regulatory misinformation, and shipping it unreviewed means nobody ever calibrates it.
- **Treating the Force Key as a prohibition.** Practice Note 8.6 permits an order outside the Forced Order Range once it has been confirmed. Blocking it outright breaks legitimate workflows; passing it silently loses the audit record of a deliberate override.
- **Reporting a figure for a check that never ran.** A report that says "0.00% deviation" when no reference price was supplied asserts something the engine never evaluated. An audit trail that lies is worse than one that says "not evaluated".
- **Stopping at the first breach.** An order can be unlicensed *and* over the value ceiling *and* outside the band at once. Remediation needs the full list, not the first item.

## Verification

- Instantiate `MasSingaporeAlgoComplianceEngine`. Audit a compliant marketable BUY (`symbol="D05"`, `quantity=1_000`, `limit_price=30.00`, `circuit_breaker_ref_price=30.00`, `opposite_best_price=30.00`, `min_bid_size=0.01`, `forced_order_range_ref_price=30.00`, `session="CONTINUOUS"`) against a fully compliant config $\implies$ verify `SGX_PRE_TRADE_APPROVED`, `breaches == ()`, `order_value == 30_000.0`.
- Confirm the circuit breaker bites on marketability, not on price alone: `limit_price=36.00` with `opposite_best_price=35.00` $\implies$ `REJECTED_CIRCUIT_BREAKER_BAND`; the same 36.00 order with `opposite_best_price=None` must still reject (conservative), while `limit_price=20.00` with `opposite_best_price=30.00` must return `SGX_PRE_TRADE_APPROVED` with a latent-trigger warning.
- Confirm the band is inclusive and unrounded: `limit_price=33.00` against a 30.00 reference (exactly +10%) must pass; `limit_price=11.00049` against a 10.00 reference (+10.0049%) must reject.
- Confirm scope gating: `is_circuit_breaker_eligible=False`, or `session="CLOSING_ROUTINE"`, must leave `circuit_breaker_deviation_pct` at `None` with no band breach.
- Confirm the Forced Order Range: 31.00 against a 30.00 reference at a 0.01 bid is 100 bids away $\implies$ `REJECTED_FORCED_ORDER_RANGE`; the same order with `force_key_confirmed=True` $\implies$ approved with a Force Key warning.
- Confirm the gate fails closed on bad input: a NaN or infinite `limit_price`, a zero or negative `quantity`, an unknown `side`, and a `currency` that differs from `limit_currency` must each raise, never return an approved report.
- Confirm the audit trail is complete: an unlicensed, unregistered, kill-switchless order that is also over the value ceiling, outside the band, outside the Forced Order Range and over the rate ceiling must carry **all seven** breaches, with `status == "REJECTED_UNLICENSED_ENTITY"`.
- Run the test suite:
```bash
cd skills/mas-singapore-algo-trading-guidelines/scripts
python -m unittest test_mas_singapore_algo_trading_guidelines
```

## Related Skills

- `singapore-exchange-sgx-api-integration`
- `singapore-mas-notice-on-cyber-hygiene-for-trading-systems`
- `hong-kong-sfc-algorithmic-trading-guidelines`
- `japan-fsa-high-speed-trading-registration`
- `kill-switch-and-drawdown-circuit-breakers`
- `minimum-fill-size-and-lot-rounding-logic`
