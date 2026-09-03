---
name: asic-market-integrity-rules-automated-trading
description: Automated Order Processing gate for Australian licensed securities
  markets under ASIC Market Integrity Rules (Securities Markets) 2017 Part 5.6 —
  reject-outcome pre-trade filters on value, volume and price deviation, scoped
  Rule 5.6.3(1)(d) suspension of AOP by authorised person, client, product, market
  or algorithm, the 5.6.3(1)(e) cancellation hand-off for messages already in the
  market, and recorded filter-parameter control under 5.6.3(2).
domain: regulatory-compliance-global
subdomain: regulatory
tags:
- compliance
- asic
- australia
- pre-trade-filter
- kill-switch
brokers_frameworks:
- generic
version: "2.0.0"
author: System
license: MIT
---

## When to Use

Use this skill when building or auditing the pre-trade gate a **trading participant of an Australian licensed securities market** must place in front of every trading message it sends through an Automated Order Processing (AOP) system. RG 241 applies it to participants of the markets operated by ASX Limited, Cboe Australia Pty Limited, National Stock Exchange of Australia Limited and Sydney Stock Exchange Limited.

Part 5.6 of the ASIC Market Integrity Rules (Securities Markets) 2017 requires four things this engine implements, and each rejection traces to the clause that requires it:

- **Rule 5.6.1(a) / 5.6.3(1)(a)-(b)** — appropriate automated filters for AOP. RG 241.35 says a filter may pass a message, pass and flag it on an exception report, pass it to a designated trading representative (DTR), or reject it outright. This engine implements the reject outcome.
- **Rule 5.6.3(1)(d)** — controls enabling *immediate* suspension, limitation or prohibition of all AOP, AOP in respect of ACOP, **or AOP in respect of one or more authorised persons, clients, financial products or markets** (RG 241.52). The scope granularity is part of the obligation.
- **Rule 5.6.3(1)(e)** — controls enabling suspension of further entry of trading messages in a series **and cancellation of messages in that series already in the market** (RG 241.55, RG 241.58).
- **Rule 5.6.3(2)** — direct participant control over the filters and filter parameters at administrator level (RG 241.47-241.48), with every parameter change recorded under Rule 5.6.3(1)(a) (RG 241.43).

## When NOT to Use

- **Outside Australian licensed securities markets.** Part 5.6 binds trading participants of the markets named above. Do not port this control set to another regulator on the assumption the obligations coincide — for the EU use `mifid-ii-algo-trading-compliance-eu`, for the UK `uk-fca-algorithmic-trading-systems-controls`, for the US `sec-rule-15c3-5-risk-controls-us`. A separate ASIC instrument, the Futures Markets Rules, governs futures participants and is not what this skill cites.
- **As the participant's whole Part 5.6 programme.** This is the pre-trade gate and the suspension control only. It does not produce the system design documentation, the initial certification under Rule 5.6.6, the review of material changes under Rule 5.6.8, the annual review under Rule 5.6.8A or the annual notification under Rule 5.6.8B, and it does not discharge the Part 5.5 trading infrastructure obligations that Part 5.6 builds on.
- **As real-time surveillance.** RG 241.81-241.87 expect monitoring in real time or close to it, exception reports reviewed at least daily, and post-trade analysis of historic order and trading patterns. A pre-trade filter sees one message at a time and cannot detect a pattern across a series; pair it with `wash-trade-and-spoofing-self-detection` and `eu-market-abuse-regulation-mar-surveillance` for pattern-level detection.
- **As a source of numeric limits.** Part 5.6 prescribes no value, volume or deviation figure. RG 241.36 states expressly that what is "appropriate" depends on the participant's system capabilities, and the nature, scale and complexity of its business. Every number configured here is the participant's own risk policy and must be justified in the certification documentation — never presented to ASIC as a regulatory minimum.
- **As a DTR workflow.** RG 241.35(c) and RG 241.85 contemplate a filter routing a message to a designated trading representative to amend, cancel or release. This engine has no review queue; a breach is rejected. Layer alert and DTR-review outcomes on top if your business needs them, but a breach must never silently pass.

## Prerequisites

- Python 3.9+ (standard library only).
- **Numeric input contract:** `price`, `qty` and `reference_price` must be `int` or `float`. `decimal.Decimal`, strings and `bool` are treated as non-finite and rejected with `NON_FINITE_INPUT` — the gate fails closed rather than mis-reading them, so convert at the system boundary.
- A `AsicMarketIntegrityConfig` (frozen) carrying `max_order_value_aud`, `max_order_volume` and `max_price_deviation_pct`, approved by the firm's compliance officer. All limits must be positive and finite and the deviation must fall in `(0.0, 1.0]`; anything else raises `ValueError` at construction, because a disabled control is a misconfiguration and RG 241.45 states ASIC would not accept an AOP system where filters or filter parameters could be deactivated.
- A real-time, valid reference price (last traded or mid) per instrument. A zero, negative or non-finite reference price is rejected rather than used, so a flaky reference feed becomes a trading outage rather than a silent control bypass. Decide that trade-off before deployment.
- The order's identity fields — `client_id`, `authorised_person_id`, `algorithm_id`, `market` — populated wherever you intend to be able to suspend AOP at that granularity. A halt scoped to a client that orders never carry is inert. Scope values are matched after stripping and case-folding, so a halt on `BHP.AX` also catches `bhp.ax`; if two of your identities differ only by case, canonicalise them at the boundary before they reach this gate.
- A `cancel_series_callback` wired to the order management system if you rely on this component for Rule 5.6.3(1)(e)(ii)/(iv). Without one, halts record `NOT_CONFIGURED` and log a warning: this module holds no order book and cannot cancel resting messages itself.
- **Threshold convention, applied uniformly:** the configured limit is itself permitted; a breach requires exceeding it.

## Workflow

1. **Construct the config and filter once, at start-up.** `AsicMarketIntegrityConfig` validates its limits on construction and is frozen thereafter.
2. **Check the halt state first, before any numeric comparison.** `AsicAopPreTradeFilter.run_checks` calls `kill_switch.halt_blocking(order)`, which tests the global halt and then each of the order's identities. A global halt yields `KILL_SWITCH_ACTIVE`; a scoped halt yields `AOP_SCOPE_HALTED` naming the scope.
   - **Decision point — scope the halt to the source when you have identified it.** RG 241.53 contemplates suspending messages "from a particular source (e.g. a particular authorised person, account or algorithm)". Halting all AOP because one algorithm misbehaved is itself a market-integrity event for the participant's other clients. Use `trigger_scoped_halt`; reserve `trigger_kill_switch` for systemic failure.
3. **Reject non-finite input before comparing anything.** `NaN > limit` is `False` in Python, so an unguarded NaN price or quantity passes every numeric limit. Reject `NaN`, `±Inf`, `bool` and non-numeric types outright.
4. **Reject non-positive quantity, price or reference price.** A zero reference price would raise `ZeroDivisionError` inside the deviation check, taking the control offline at the moment it is most needed.
5. **Apply the volume, then value, then price-deviation limits**, each rejecting outright per RG 241.35(d).
   - **Decision point — compare the deviation by multiplication, not division.** `abs(price - ref) / ref > limit` spuriously rejects an order priced at exactly the limit for a subset of reference prices: reference 402.69 with price 422.8245 (exactly +5% in IEEE-754 double) divides to `0.05000000000000001` and is rejected at a 5% limit it precisely meets. Compare `abs(price - ref) > limit * ref` instead.
6. **Persist every `ComplianceResult`, approved and rejected**, with its `rejection_code`, `order_id` and `checked_at_unix`, for the Part 5.6 audit trail.
   - **Decision point — the result is a point-in-time decision, not a licence to send.** A halt raised after `checked_at_unix` is not reflected in a result already returned. Rule 5.6.3(1)(d) requires suspension to be *immediate*, so re-read the halt state immediately before serialising the message to the venue — inside the same critical section as the send, if you have one.
7. **Change filter parameters only through `replace_config(new_config, authorised_by, reason)`.** It validates the replacement, refuses a blank authoriser or reason, and appends a `FilterParameterChange` recording the previous and replacement values. Rule 5.6.3(1)(a) requires processes to record any change to filters or filter parameters, RG 241.43 says that expressly includes intra-day changes, and RG 241.44 expects administrator-level changes only after authorisation by a qualified person.
   - **Decision point — a parameter change may be a material change.** RG 241.174 lists changes that may be material for Rule 5.6.8, including a change that increases the risk of orders creating a disorderly market or a high order-to-trade ratio. A material change requires review *before* implementation, not after.
8. **On a halt, complete the 5.6.3(1)(e) hand-off.** The manager invokes `cancel_series_callback` with the `AopHaltRecord` and records the outcome — `COMPLETED` with a count, `FAILED` with the error, or `NOT_CONFIGURED`. A raising callback never prevents the halt from applying, and a failure escalates at `CRITICAL` because that is the outcome where messages are still live in the market.
9. **Release a halt only after root-cause review**, via `reset_kill_switch(reason=..., actor=...)` or `release_scoped_halt(...)`. Both refuse a blank reason or actor, return `False` when nothing was released, and audit the refusal. A global reset does *not* release scoped halts — release each one deliberately.

> Full step-by-step procedure: see `references/workflows.md`.
> Rule-by-rule regulatory map with source links: see `references/standards.md`.
> Printable sign-off checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Passive filters that alert instead of blocking.** RG 241.35 lists reject as outcome (d); an "appropriate" filter set that never rejects is not gatekeeping. RG 241.45 adds that ASIC would not accept an AOP system where filters, filter parameters and exception reports could be deactivated.
- **Treating a single global boolean as the kill switch.** Rule 5.6.3(1)(d) requires suspension of AOP *in respect of one or more authorised persons, clients, financial products or markets* as well as in full (RG 241.52). A global-only halt implements part of the rule and forces an all-or-nothing response to a single-source problem.
- **Blocking new orders and calling the kill switch done.** Rule 5.6.3(1)(e)(ii) and (iv) require cancellation of messages in a series that have *already entered* the market once further entry is suspended. Halting the gate leaves resting orders working. If cancellation lives in the OMS, wire it and record the outcome — do not leave the obligation implicit.
- **Mutating filter parameters in place.** An in-place widening bypasses construction-time validation, leaves no record of who widened what, and defeats Rule 5.6.3(1)(a). This is why the config is frozen and `replace_config` is the only path.
- **Trusting a `ComplianceResult` that has gone stale.** Between the check returning APPROVED and the message reaching the venue, the kill switch may have fired. Re-check at the point of send.
- **NaN/Inf inputs bypassing limits.** `NaN > limit` is `False`, so a NaN price silently passes every numeric check. Note also that `isinstance(True, int)` is `True` in Python, so an unguarded `bool` quantity is read as 1 and passes.
- **Dividing to compute price deviation.** Floating-point division rejects some orders that sit exactly on the configured limit, contradicting the documented threshold convention. Multiply instead.
- **Zero or stale reference price.** Guarding the deviation check with `if ref > 0:` turns a data outage into an open gate. Reject the order instead.
- **Resuming AOP without attribution.** An audit entry recording an empty actor satisfies nothing. RG 241.44 expects the administrator-level change to be authorised by a qualified person; refuse the release rather than logging whatever string was passed. Raising a halt is the opposite case — never block a halt for incomplete paperwork.
- **Presenting the configured limits as ASIC thresholds.** Part 5.6 sets none; RG 241.36 leaves "appropriate" to the participant's business. Document the calibration — that documentation is what the Rule 5.6.6 certification and the Rule 5.6.8A annual review consume.
- **Assuming the rule numbering is stable.** ASIC CP 386 (27 August 2025) proposes amending Rule 5.6.3, inserting Rule 5.6.3B (trading algorithm governance, testing and seven-year records) and repealing Rule 5.6.8B. Confirm currency before relying on a citation — see the note in `references/standards.md`.

## Verification

- Valid order inside every limit ⟹ `is_compliant=True`, `rejection_code is None`, `order_id` echoed, `checked_at_unix > 0`.
- **Boundary (must be allowed):** volume exactly `max_order_volume`; value exactly `max_order_value_aud`; price exactly at ±`max_price_deviation_pct` of the reference, **including reference 402.69 with price 422.8245**, which the division form wrongly rejects. One float increment past the deviation limit ⟹ `PRICE_DEVIATION`.
- **Fail-closed (must reject with `NON_FINITE_INPUT`):** NaN, `±Inf`, `True`/`False` and `decimal.Decimal` in `price`, `qty` or `reference_price`.
- **Field sanity:** zero or negative `qty`/`price` ⟹ `INVALID_ORDER_FIELDS`; zero or negative `reference_price` ⟹ `ZERO_REFERENCE_PRICE` and never `ZeroDivisionError`.
- **Scoped halts:** a halt on each of `AUTHORISED_PERSON`, `CLIENT`, `FINANCIAL_PRODUCT`, `MARKET` and `ALGORITHM` blocks a matching order with `AOP_SCOPE_HALTED` and leaves a non-matching order allowed. A scoped halt must not set `is_halted`. A global halt blocks orders carrying unrelated identities with `KILL_SWITCH_ACTIVE`. A blank `scope_value` must raise rather than create a halt that matches every identity-less order. A halt on `"  BHP.AX  "` must block an order for `bhp.ax` and be releasable by `bhp.ax`, while the audit record preserves the operator-entered value.
- **Concurrency:** while an in-flight `cancel_series_callback` is still running, a `halt_blocking` read from another thread must complete rather than block — a slow OMS cancel must not freeze the pre-trade gate for unrelated orders.
- **Release gate:** `reset_kill_switch` with a blank or whitespace actor or reason ⟹ returns `False`, halt remains in force, and a `RESET_REFUSED` entry is appended. With both supplied ⟹ returns `True`. Releasing a halt that is not active ⟹ `False`. A global reset must leave scoped halts in force.
- **Raising a halt is never refused:** `trigger_kill_switch()` and `trigger_scoped_halt(scope, value)` with no reason or actor still apply the halt.
- **Cancellation hand-off:** no callback ⟹ audit entry records `NOT_CONFIGURED`; a callback returning 4 ⟹ `COMPLETED` with `cancelled_message_count == 4` and the `AopHaltRecord` passed through; a callback raising `ConnectionError` ⟹ halt still applied, status `FAILED`, error text captured, `CRITICAL` logged.
- **Parameter control:** assigning to a config field ⟹ `FrozenInstanceError`; assigning `filter.config` ⟹ `AttributeError`; `replace_config` with a blank authoriser or reason ⟹ `ValueError` and no change applied; a successful call records previous and replacement values and takes effect on the next check.
- **Misconfiguration must raise:** any limit zero, negative, NaN or `Inf`; `max_order_volume` non-integer or `bool`; deviation outside `(0.0, 1.0]`; a non-`AsicMarketIntegrityConfig` passed to the filter or to `replace_config`.
- Run `python -m unittest discover -s skills/asic-market-integrity-rules-automated-trading/scripts` and confirm all tests pass.

## Related Skills

- `kill-switch-and-drawdown-circuit-breakers`
- `sec-rule-15c3-5-risk-controls-us`
- `uk-fca-algorithmic-trading-systems-controls`
- `mifid-ii-algo-trading-compliance-eu`
- `australian-securities-exchange-asx-api`
- `wash-trade-and-spoofing-self-detection`
