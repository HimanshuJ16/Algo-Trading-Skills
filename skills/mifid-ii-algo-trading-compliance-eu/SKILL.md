---
name: mifid-ii-algo-trading-compliance-eu
description: >-
  Use when an algorithmic system executes on an EU trading venue and MiFID II RTS 6
  obligations apply: pre-trade controls, kill functionality that cancels unexecuted
  orders, testing and annual self-assessment beyond ordinary engineering practice.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: regulatory-compliance-global
  tags: regulatory-compliance-global, mifid-ii-mifir, rts-6-algorithmic-trading
  brokers_frameworks: "MiFID II / MiFIR; RTS 6 (Regulatory Technical Standard on algorithmic trading)"
  version: "1.1.0"
  author: algo-trading-skills-contributors
---

## When to Use

Invoke this when a bot will place orders on any EU trading venue, since MiFID II's algorithmic trading requirements (detailed in RTS 6, Commission Delegated Regulation (EU) 2017/589) impose specific, technical obligations beyond general good-engineering-practice risk controls — including pre-trade risk controls, kill-switch functionality, testing requirements before deployment, and self-assessment/documentation obligations that a firm must be able to produce on request. This skill focuses on the engineering-relevant technical requirements; broader firm-level regulatory registration and reporting obligations require legal/compliance review independent of this skill.

## When NOT to Use

- When the trading activity does not meet MiFID II's definition of algorithmic trading, or when the entity is not an investment firm subject to Article 17 of Directive 2014/65/EU — RTS 6 binds the investment firm, and accessing an EU venue through a broker rather than as a member changes the obligation set. Confirm with compliance/legal.
- For UK venues post-Brexit: RTS 6 survives there as assimilated law supervised by the FCA, not as EU law — use `uk-fca-algorithmic-trading-systems-controls`.
- As a substitute for the general engineering risk controls in `kill-switch-and-drawdown-circuit-breakers`. This skill layers EU-specific obligations on top of those; it does not replace them.
- To source numeric risk limits. RTS 6 sets none for the pre-trade controls — see the Prerequisites note below.

## Prerequisites

- Confirmation of whether the trading activity meets MiFID II's definition of "algorithmic trading" (broadly: a system that determines individual parameters of orders with limited or no human intervention) and, separately, whether it meets the higher threshold for "high-frequency algorithmic trading" (HFT), which carries additional obligations — notably RTS 6 Article 28 (record each submitted order immediately after submission, in the Annex II format, retained five years) and RTS 25 clock accuracy (100 microseconds from UTC, 1 microsecond granularity). This determination should be made with compliance/legal input, not assumed from the engineering side alone.
- An existing kill-switch/circuit-breaker implementation (see `kill-switch-and-drawdown-circuit-breakers`) as the technical foundation this skill extends with EU-specific requirements.
- Firm-calibrated values for the four Article 15(1) limits. **RTS 6 prescribes no numeric thresholds.** Article 15(4) requires them to be derived from the firm's capital base, clearing arrangements, trading strategy, risk tolerance and experience. Any "5%" or "10 msgs/sec" figure in this skill's reference code is an illustrative placeholder that must be replaced before live use.

## Workflow

1. Confirm the system has a functioning kill switch — RTS 6 **Article 12** requires the firm to be able to cancel immediately, as an emergency measure, any or all of its unexecuted orders on any or all connected venues. Note what the article actually names: *cancellation*. Halting new order flow comes from the Article 14 business continuity arrangements (14(2)(f) shutdown, 14(3) shutdown without creating disorderly trading conditions); a complete control does both. Verify the switch can be triggered manually by a human at any time, independent of any automated trigger, and that Article 14(2)(e)'s documented usage policy for it exists.
2. Implement the four pre-trade controls **Article 15(1)** enumerates, each independently identifiable and testable: (a) price collars, (b) maximum order values, (c) maximum order volumes, (d) maximum messages limits. Two details in the text are commonly missed and change the design: collars must *differentiate between different financial instruments* (a single global percentage does not satisfy 15(1)(a) across a mixed universe), and the message limit covers *submission, modification or cancellation* (15(1)(d)) — a rate limiter wired only to new orders systematically under-counts amend/cancel storms, which are the usual cause of venue message-rate breaches.
3. Decide what happens after the controls fire. Article 15(3) requires repeated automated execution throttles that disable the trading system after a pre-determined number of repeated executions and keep it disabled *until re-enabled by a designated staff member* — so re-enabling must be a manual, attributed, audited action, never an automatic recovery timer. If the system has an override path for a blocked order, Article 15(6) confines it to temporary use in exceptional circumstances, with risk-management verification and authorisation by a designated individual.
4. Maintain a testing and deployment record: Articles 5–7 require documented testing methodologies, authorisation of each deployment or substantial update by a person designated by senior management, testing in an environment separated from production, and change records showing when a change was made, by whom, approved by whom, and its nature (Art. 5(7)). Treat `paper-to-live-promotion-checklist`'s promotion gate as the mechanism that produces this record, and ensure the record itself is retained and retrievable — regulators may request evidence that testing occurred, not just an assertion that it did.
5. Tag algorithmic orders so each one is attributable. Article 12(3) requires the firm to identify which algorithm and which trader, desk or client is responsible for each order sent to a venue; MiFID II Article 48 puts the matching obligation on the venue, satisfied *by means of flagging from members or participants*, with the record fields specified in RTS 24. Verify the specific venue's tagging requirements against its rulebook rather than assuming a generic "algo flag" satisfies all venues uniformly, and keep the code sets straight — trading capacity is `DEAL`/`MTCH`/`AOTC` (RTS 22 Field 29), and the short selling indicator is a coded value (`SESH`/`SSEX`/`SELL`/`UNDI`, Field 62), not a boolean.
6. Maintain a business continuity plan specifically for the algorithmic trading system (not just general IT disaster recovery) — Article 14 requires it documented in a durable medium, covering adverse scenarios, relocation, staff training, the kill-switch usage policy, shutdown arrangements, and management of outstanding orders and positions, reviewed and tested annually. The supervision and reconciliation patterns in `systemd-supervision-for-trading-bots` are relevant building blocks, but RTS 6 expects this documented as a specific plan for the algo system, not left implicit in general infrastructure practice.
7. Conduct and document the annual self-assessment and validation (**Article 9**, against the Annex I criteria), covering the algorithmic trading systems, the governance and approval framework, business continuity, and overall Article 17 compliance. The validation report is drawn up by the risk management function, audited by internal audit where one exists, and approved by senior management. Article 10 makes stress testing part of it, at a concrete scale: the highest message volume and the highest trade volume of the previous six months, **each multiplied by two**, run without affecting production. Material changes outside the annual cycle are governed by Article 11 (review by a person designated by senior management). This is a compliance/documentation deliverable rather than a code change, but the engineering team should expect to produce the evidence — logs, test records, control configurations — that supports it.

> Full step-by-step procedure with per-article detail: see `references/workflows.md`.
> Article-by-article regulatory map with source links: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- Letting a signed quantity defeat the Article 15(1)(b)/(c) caps: where "sell" is encoded as a negative quantity, `price * quantity` is negative and slips under any positive maximum-order-value cap, and a bare `quantity <= max_volume` comparison passes too — both controls go silently inert for every sell order. Compare on absolute notional, and require a positive finite size.
- Building a kill switch that only halts new order placement without also cancelling already-resting orders, missing the part of RTS 6 Article 12(1) that the article actually names.
- Reporting a successful kill when the venue mass-cancel failed. The venue gateway is exactly what tends to be unavailable during the incident that triggered the kill; the halt must be applied before the cancel is attempted, and a failed cancel must surface loudly rather than being swallowed.
- Allowing a halted system to re-enable itself. Article 15(3) requires it to stay disabled until re-enabled by a designated staff member, so an automatic recovery timer or an unattributed reset breaches it.
- Treating pre-trade risk controls as fully satisfied by the position/drawdown checks in `kill-switch-and-drawdown-circuit-breakers` without adding the more specific price-collar and message-rate checks Article 15(1) expects to see as distinct, identifiable controls.
- Applying one global price collar percentage across a mixed instrument universe, when Article 15(1)(a) requires differentiation between instruments — and applying a *percentage* collar at all to instruments that legitimately trade at or below zero (power, gas, some commodity futures), where relative deviation is undefined at a zero reference and inverts in sign below it.
- Presenting a 5% collar or a 10 msgs/sec cap as an RTS 6 requirement. Article 15 sets no numeric thresholds; hard-coding one and citing the regulation for it is a documentation defect that survives into the self-assessment.
- Running paper-trading validation (per `paper-to-live-promotion-checklist`) without retaining a durable, retrievable testing record — the practice may be sound but unproduceable evidence of it is itself a compliance gap. The same applies to a pre-trade audit trail that exists only in process memory.
- Assuming a single generic "algo order" flag satisfies every EU venue's specific order-tagging requirement, when tagging conventions vary by venue, and assuming a wall-clock timestamp satisfies RTS 25 — clock traceability is an infrastructure property, not a code property.
- Citing the wrong article. Kill functionality is Article 12, not Article 18 (security and limits to access); pre-trade controls are Article 15, not Article 13 (automated market-manipulation surveillance). A self-assessment that maps controls to the wrong articles invites exactly the scrutiny it is meant to satisfy.
- Treating this as a one-time compliance checkbox rather than an annual (and change-triggered) self-assessment obligation.

## Verification

- Confirm the kill switch can be triggered manually, independent of any automated condition, that triggering it both halts new orders and cancels existing resting orders, and that a *failing* cancellation still halts order flow and is reported as a failure — tested in a non-production environment with the venue gateway deliberately unavailable.
- Confirm re-enabling after a halt requires an identified operator and produces an audit record, and that no automatic path clears the halt (Art. 15(3)).
- Confirm each of the four Article 15(1) controls (price collar, max order value, max order volume, max message rate) is independently testable and produces a rejection when deliberately violated, including with a negative/zero/non-finite quantity and with a reference price of zero.
- Confirm the message-rate control counts amend and cancel messages, not only new orders (Art. 15(1)(d)).
- Confirm collar parameters differ per instrument where the traded universe is mixed (Art. 15(1)(a)), and that the configured values trace to a documented calibration rather than to this skill's placeholder defaults.
- Confirm every pre-trade decision and every kill/reset event reaches durable storage, not just an in-memory buffer, and that a failure to write is alarmed. For HFT firms, confirm the Article 28 five-year retention and Annex II format.
- Confirm a retrievable testing record exists for the current live algorithm version and that the retention process is repeatable for future versions, not a one-off artifact from the initial deployment.
- Confirm order tagging matches the specific venue(s) the bot trades on, verified against that venue's current rulebook, and that code sets match RTS 22 (`DEAL`/`MTCH`/`AOTC`; `SESH`/`SSEX`/`SELL`/`UNDI`).
- Run `python -m unittest discover -s skills/mifid-ii-algo-trading-compliance-eu/scripts` and confirm all tests pass.

## Related Skills

- `kill-switch-and-drawdown-circuit-breakers`
- `paper-to-live-promotion-checklist`
- `systemd-supervision-for-trading-bots`
- `uk-fca-algorithmic-trading-systems-controls`
- `eu-market-abuse-regulation-mar-surveillance`
- `clock-synchronization-ptp-for-trading-hosts`
