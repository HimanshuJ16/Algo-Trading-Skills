# Checklist for ASIC AOP Compliance

Rule references: ASIC Market Integrity Rules (Securities Markets) 2017, Part 5.6, read with
RG 241 (2 August 2022). See `references/standards.md` for the rule map and the currency note.

## Filters — Rule 5.6.1(a), 5.6.3(1)(a)-(b)

- [ ] Pre-trade filters actively **reject** non-compliant orders rather than logging a warning
      (RG 241.35 outcome (d)).
- [ ] Value, volume and price-deviation limits are documented with the *reason* each number was
      chosen. Part 5.6 prescribes no thresholds (RG 241.36); the justification is what the
      Rule 5.6.6 certification rests on.
- [ ] `AsicMarketIntegrityConfig` limits are positive and finite and the deviation is in
      `(0.0, 1.0]` — a non-positive or NaN limit would silently disable a mandatory control.
- [ ] Price-deviation logic uses a valid, real-time reference price; zero, negative and stale
      reference prices are rejected, not divided by.
- [ ] Deviation is compared by multiplication, not division — the division form rejects orders
      sitting exactly on the configured limit (reference 402.69, price 422.8245 at 5%).
- [ ] Non-finite and non-numeric inputs (`NaN`, `±Inf`, `bool`, `Decimal`, strings) in price,
      quantity or reference price are rejected before any limit comparison.
- [ ] Numeric inputs are converted to `int`/`float` at the system boundary, so a `Decimal`
      price never reaches the filter and shows up as a spurious `NON_FINITE_INPUT` rejection.

## Suspension of AOP — Rule 5.6.3(1)(d)

- [ ] A global halt (`trigger_kill_switch`) is wired to an immediate manual override — API
      endpoint or dashboard — reachable by the operations team without a deploy.
- [ ] **Scoped** halts are available and tested for authorised person, client, financial
      product, market and algorithm (RG 241.52 requires suspension "in respect of one or more
      authorised persons, clients, financial products or markets", not only in full).
- [ ] Orders carry the identity fields (`client_id`, `authorised_person_id`, `algorithm_id`,
      `market`) that those halts key on — a halt on an identity orders never carry is inert.
- [ ] Identities are canonicalised upstream. Scope matching strips and case-folds, so a halt
      on `BHP.AX` also catches `bhp.ax`; confirm no two distinct identities differ only by case.
- [ ] Raising a halt is never blocked by missing attribution, and never by a failing
      downstream callback.
- [ ] The halt state is re-read immediately before the message is sent to the venue, not only
      at `run_checks` time — `ComplianceResult` is a point-in-time decision.

## Cancellation of a series — Rule 5.6.3(1)(e)(ii), (iv)

- [ ] `cancel_series_callback` is wired to the OMS bulk-cancel path. Without it, messages
      already in the market are **not** cancelled and every halt records `NOT_CONFIGURED`.
- [ ] A `FAILED` cancellation raises an incident, not just a log line — that is the outcome
      where messages are still live in the market during a breach (RG 241.58).
- [ ] The cancellation has been exercised end-to-end in a test environment against real
      resting orders, not only unit-tested with a stub.

## Direct control and parameter changes — Rule 5.6.3(2), 5.6.3(1)(a)

- [ ] Filters run under the participant's direct control on its own execution path, not in a
      broker's or third party's cloud (RG 241.47-241.51).
- [ ] Filter parameters can only be changed through `replace_config` with a named authoriser
      and a reason; in-place mutation is impossible (the config is frozen).
- [ ] `parameter_audit_log` is persisted, capturing previous and replacement values, actor and
      timestamp — including intra-day changes (RG 241.43).
- [ ] Each parameter change has been assessed against Rule 5.6.8 for materiality **before**
      implementation (RG 241.174), and any temporary widening has a scheduled reversal.

## Recordkeeping and monitoring — Part 5.6, RG 241.81-241.87

- [ ] Every `ComplianceResult` — approved and rejected — is persisted with `rejection_code`,
      `order_id` and `checked_at_unix`.
- [ ] Kill-switch trigger, release and **refused release** events are persisted from
      `audit_log` with timestamp, reason, actor, scope and cancellation outcome.
- [ ] Releasing a halt requires a non-blank actor and reason, the return value is checked, and
      a global reset is known not to release scoped halts.
- [ ] Exception reports derived from this stream are reviewed regularly and at least daily
      (RG 241.87), and historic order/trading patterns are analysed periodically with filters
      adjusted where they are not operating as intended (RG 241.84).

## Certification and review — Rules 5.6.6, 5.6.8, 5.6.8A, 5.6.8B

- [ ] Initial certification completed before AOP use on each market (Rule 5.6.6).
- [ ] Annual review by an appropriately qualified person (Rule 5.6.8A), unless a material
      change review under Rule 5.6.8 occurred in the preceding 12 months (RG 241.185).
- [ ] Annual notification given to ASIC within 10 business days of the 1 November annual
      review date (Rule 5.6.8B, RG 241.188).
- [ ] Rule numbering re-verified against the current compilation — CP 386 (27 August 2025)
      proposes amending Rule 5.6.3, inserting 5.6.3B and repealing 5.6.8B.

## Tests

- [ ] Run `python -m unittest discover -s skills/asic-market-integrity-rules-automated-trading/scripts`
      and confirm all tests pass.

## Sign-off
- Compliance Officer: ___________________________
- Date: ___________________________
