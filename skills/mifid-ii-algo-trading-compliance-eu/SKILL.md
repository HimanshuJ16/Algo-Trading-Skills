---
name: mifid-ii-algo-trading-compliance-eu
description: >-
  Use when deploying an algorithmic trading system that executes on an EU trading venue, to account for MiFID II's specific technical and organizational requirements for algorithmic trading firms (RTS 6), which go beyond the general risk-management practices covered elsewhere in this repo
domain: algorithmic-trading
subdomain: regulatory-compliance-global
tags: ["regulatory-compliance-global", "mifid-ii---mifir", "rts-6-(regulatory-technical-standard-on-algorithmic-trading)"]
brokers_frameworks: ["MiFID II / MiFIR", "RTS 6 (Regulatory Technical Standard on algorithmic trading)"]
version: "1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this when a bot will place orders on any EU trading venue, since MiFID II's algorithmic trading requirements (detailed in RTS 6) impose specific, technical obligations beyond general good-engineering-practice risk controls — including pre-trade risk controls, kill-switch functionality, testing requirements before deployment, and self-assessment/documentation obligations that a firm must be able to produce on request. This skill focuses on the engineering-relevant technical requirements; broader firm-level regulatory registration and reporting obligations require legal/compliance review independent of this skill.

## Prerequisites

- Confirmation of whether the trading activity meets MiFID II's definition of "algorithmic trading" (broadly: a system that determines individual parameters of orders with limited or no human intervention) and, separately, whether it meets the higher threshold for "high-frequency algorithmic trading" (HFT), which carries additional obligations — this determination should be made with compliance/legal input, not assumed from the engineering side alone
- An existing kill-switch/circuit-breaker implementation (see `kill-switch-and-drawdown-circuit-breakers`) as the technical foundation this skill extends with EU-specific requirements

## Workflow

1. Confirm the system has a functioning "kill switch" capable of immediately halting all algorithmic order flow and cancelling outstanding orders — RTS 6 explicitly requires this capability, and while `kill-switch-and-drawdown-circuit-breakers` already covers the engineering pattern, verify specifically that the kill switch can be triggered manually by a human at any time, independent of any automated trigger condition, since the regulatory requirement is for human-accessible emergency control, not just automated risk limits.
2. Implement pre-trade risk controls that check, before any order reaches the venue: price collars (rejecting orders too far from current market price), maximum order value, maximum message rate (orders per second), and maximum order volume — these overlap with but are more prescriptive than the general risk-management skills elsewhere in this repo, and RTS 6 expects these specific control categories to be identifiable and testable individually.
3. Maintain a testing and deployment record: RTS 6 expects algorithmic trading systems to be tested in a non-live environment before deployment and after any material change, with records of that testing retained — treat `paper-to-live-promotion-checklist`'s promotion gate as the mechanism that produces this record, and ensure the record itself (not just the practice) is retained and retrievable, since regulators may request evidence of testing having occurred, not just an assertion that it did.
4. Tag algorithmic orders with the required identifiers for market-abuse surveillance — EU venues generally require an algorithm ID and, in some jurisdictions/venues, additional flags identifying the order as algorithmically generated; verify the specific venue's order-tagging requirements rather than assuming a generic "algo flag" satisfies all venues uniformly.
5. Maintain a business continuity plan specifically for the algorithmic trading system (not just general IT disaster recovery) covering how the firm would prevent, detect, and manage disorderly trading conditions caused by the system — the systemd supervision and reconciliation patterns in `systemd-supervision-for-trading-bots` are relevant building blocks, but RTS 6 expects this documented as a specific continuity plan for the algo system, not left implicit in general infrastructure practice.
6. Conduct and document a self-assessment (per RTS 6's Annex I framework) at least annually, and after any material change to the algorithm, covering governance, testing, and risk-control adequacy — this is a compliance/documentation deliverable rather than a code change, but the engineering team should expect to produce evidence (logs, test records, control configurations) to support it.

> Full step-by-step procedure with broker-specific detail: see `references/workflows.md`.
> Broker/framework coverage table for this skill: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- Building a kill switch that only halts new order placement without also cancelling already-resting orders, missing part of RTS 6's expected halt-and-cancel behavior.
- Treating pre-trade risk controls as fully satisfied by the position/drawdown checks in `kill-switch-and-drawdown-circuit-breakers` without adding the more specific price-collar and message-rate checks RTS 6 expects to see as distinct, identifiable controls.
- Running paper-trading validation (per `paper-to-live-promotion-checklist`) without retaining a durable, retrievable testing record — the practice may be sound but unproduceable evidence of it is itself a compliance gap.
- Assuming a single generic "algo order" flag satisfies every EU venue's specific order-tagging requirement, when tagging conventions can vary by venue.
- Treating this as a one-time compliance checkbox rather than an annual (and change-triggered) self-assessment obligation.

## Verification

- Confirm the kill switch can be triggered manually, independent of any automated condition, and that triggering it both halts new orders and cancels existing resting orders, tested in a non-production environment.
- Confirm pre-trade risk controls (price collar, max order value, max message rate, max volume) are each independently testable and produce a rejection when deliberately violated in a test environment.
- Confirm a retrievable testing record exists for the current live algorithm version and that the retention process is repeatable for future versions, not a one-off artifact from the initial deployment.
- Confirm order tagging matches the specific venue(s) the bot trades on, verified against that venue's current rulebook rather than a generic assumption.

## Related Skills

- `kill-switch-and-drawdown-circuit-breakers`
- `paper-to-live-promotion-checklist`
- `systemd-supervision-for-trading-bots`
