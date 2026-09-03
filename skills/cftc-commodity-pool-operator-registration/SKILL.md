---
name: cftc-commodity-pool-operator-registration
description: >-
  Use when a US commodity pool relies on the 17 CFR 4.13(a)(3) de minimis exemption from
  Commodity Pool Operator registration, to test a proposed commodity interest position
  against the initial-margin and net-notional trading tests before it is taken.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: regulatory-compliance-global
  tags: cftc, cpo, de-minimis, margin, futures, compliance
  brokers_frameworks: "CFTC; NFA"
  version: "1.1.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when you operate a US commodity pool (a fund with more than one
participant that trades "commodity interests" — futures, options on futures,
swaps, and retail forex) and rely on the 17 CFR 4.13(a)(3) *de minimis*
exemption from Commodity Pool Operator registration. The engine in
`scripts/` evaluates, before an order is routed, whether the pool would still
satisfy at least one of the two quantitative trading tests in
4.13(a)(3)(ii) once the proposed position is established.

Jurisdiction: United States (CFTC/NFA) only. Nothing here is legal advice;
threshold monitoring is an input to a compliance decision, not the decision.

## When NOT to Use

- **You rely on a different exemption or exclusion.** 4.13(a)(3) is one of
  several paths — e.g. 4.13(a)(1)/(a)(2), the 4.5 exclusion for otherwise
  regulated entities, 4.7 relief, or CFTC Staff Letter 25-50 (a no-action
  position, not a rule) for SEC-registered advisers that file Form PF.
  Breaching the 4.13(a)(3) thresholds does not by itself mean registration is
  required.
- **You are already a registered CPO.** The thresholds are irrelevant; your
  obligations come from Part 4 compliance, not from this gate.
- **As a substitute for the non-quantitative conditions.** This engine tests
  only 4.13(a)(3)(ii). It cannot tell you whether the offering is exempt from
  Securities Act registration (a)(3)(i), whether every participant is an
  accredited investor / QEP / knowledgeable employee (a)(3)(iii), or whether
  the pool is being marketed as a commodity-trading vehicle (a)(3)(iv).
- **Single-managed-account trading.** A managed account for one client is not
  a pool; CTA rules apply instead.

## Prerequisites

- Liquidation value of the pool's portfolio, marked *after* unrealized profits
  and losses — 4.13(a)(3)(ii) requires this explicitly.
- Aggregate initial margin, option premiums, and the required minimum security
  deposit for retail forex transactions (as defined in 17 CFR 5.1(m)) across
  all open commodity interest positions, plus the amount the proposed trade
  would add or release.
- Aggregate notional value of open commodity interest positions computed the
  way 4.13(a)(3)(ii)(B) prescribes — the four instrument-specific formulas are
  in `references/standards.md`; options in particular are **delta-adjusted
  strike** notional, not market-price notional.
- A classification of each instrument as a commodity interest or not, so that
  securities and cash bonds stay out of the numerators while still counting
  toward liquidation value.

## Workflow

1. **Classify the instrument.** If the proposed trade is not a commodity
   interest, it is outside both numerators and the gate passes it through.
2. **Compute the exposure deltas the trade would apply.** Pass them as signed
   values: positive when the trade opens or increases a position, negative
   (the magnitude released) when it closes or offsets one. Do *not* encode
   long/short direction in the sign — under the gross convention this engine
   uses, a new short adds notional exactly like a new long.
3. **Project the aggregates.** `projected = current + delta` for both margin
   and notional. A delta that would push an aggregate below zero means the
   position book and the proposed trade disagree; the engine raises rather
   than guessing, and the boolean wrapper blocks the trade.
4. **Let risk-reducing trades through.** If neither projected aggregate
   exceeds its current value, the trade is allowed unconditionally — including
   when the pool is *already* outside both tests. Blocking an unwind would trap
   the pool in exactly the state that requires registration.
5. **Evaluate the two tests** against the projected aggregates:
   - **Test A (margin)**: projected margin + premiums ≤ 5% of liquidation value.
   - **Test B (notional)**: projected notional ≤ 100% of liquidation value.
   Passing *either* test satisfies 4.13(a)(3)(ii). Only when **both** fail is
   the trade blocked.
6. **Record the decision.** `evaluate_trade()` returns a `ComplianceDecision`
   carrying both ratios, both projected aggregates, which test carried the
   decision, and a reason string — keep it, because the exemption is measured
   "at the time the most recent position was established" and you may have to
   reconstruct that moment later.
7. **Escalate, do not auto-override.** A block means the pool would lose the
   exemption on this trade. The remedies are to size down, unwind, rely on a
   different exemption, or register — all of which are compliance decisions.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Treating the thresholds as the whole exemption.** Passing test A or B
  satisfies only 4.13(a)(3)(ii). The exemption also requires the private-offering
  condition, participant eligibility, the no-marketing condition, a notice filed
  with NFA under 4.13(b), and **annual affirmation of that notice within 60 days
  of calendar year end** through NFA's electronic exemption filing system
  (4.13(b)(4)). An unaffirmed notice lapses regardless of what the numbers say.
- **Blocking the unwind.** A gate that adds `abs(notional)` for every trade
  rejects the very orders that would bring a breaching pool back inside the
  exemption. Reductions must always be permitted.
- **Using static capital as the denominator.** Liquidation value must be taken
  after unrealized profits and losses. A stale NAV silently inflates headroom.
- **Market-price notional for options.** 4.13(a)(3)(ii)(B) computes option
  notional as contracts × contract size × **delta** × **strike price**. Using
  premium or spot-based notional produces a number the rule does not recognize.
- **Forgetting the in-the-money exclusion.** For an option that is in-the-money
  at the time of purchase, the in-the-money amount may be excluded from the 5%
  margin test. Omitting it understates headroom and blocks legitimate trades.
- **Assuming price moves alone break the exemption.** Each test is "determined
  at the time the most recent position was established". Drift caused purely by
  marks does not itself establish a position — but it does change the headroom
  the *next* order will be measured against, so re-evaluate per order rather
  than caching a verdict.
- **Netting more than the rule allows.** Netting is permitted for futures on
  the same underlying commodity across designated contract markets and foreign
  boards of trade, and for swaps cleared on the same DCO — not for arbitrary
  offsetting exposures. This engine defaults to gross, which can reject a trade
  the rule would have allowed; it never allows one the rule forbids.
- **Counting securities in the numerator.** Equities and cash bonds contribute
  to liquidation value (the denominator) but not to the margin or notional
  numerators.

## Verification

- Simulate a pool with a $1,000,000 liquidation value and a flat commodity
  book. A futures order requiring $60,000 initial margin (6%) with $1,100,000
  notional (110%) must be **blocked** — both tests fail. The same order with
  $500,000 notional (50%) must be **allowed** — test B carries it.
- Simulate a pool already in breach ($100,000 margin / $3,000,000 notional
  against $1,000,000 liquidation value) and submit an unwind of `-$20,000`
  margin and `-$1,000,000` notional. It must be **allowed** even though the
  pool still fails both tests afterwards.
- Confirm boundary behaviour: exactly 5.00% margin and exactly 100.00%
  notional both pass; one currency unit above both thresholds is blocked.
- Run `python -m unittest discover -s skills/cftc-commodity-pool-operator-registration/scripts`.

## Related Skills

- `position-limit-reporting-cftc-large-trader`
- `regulatory-capital-requirement-tracking`
- `regulatory-change-monitoring-service-integration`
