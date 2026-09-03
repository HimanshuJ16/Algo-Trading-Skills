---
name: algorithmic-trading-firm-licensing-thresholds
description: >-
  Screens a trading firm against the quantitative registration triggers that
  actually exist — the 17 CFR 240.15b9-1 conditions for exemption from FINRA
  membership, the Article 19 message-rate test that makes a firm a MiFID II
  high-frequency trader, and the exchange Threshold Orders Per Second above
  which a retail API algorithm must be registered — and reports "cannot
  determine" rather than "compliant" when the measuring input is missing.
domain: regulatory-compliance
subdomain: legal-and-registration
tags:
- compliance
- sec
- finra
- mifid-ii
- hft
- sebi
- registration-thresholds
brokers_frameworks:
- 17 CFR 240.15b9-1 as amended (88 FR 61893, Sept. 7, 2023)
- Securities Exchange Act section 15(b)(8)
- MiFID II Article 4(1)(40) and Article 2(1)(d)(iii)
- Commission Delegated Regulation (EU) 2017/565 Article 19
- SEBI/HO/MIRSD/MIRSD-PoD/P/CIR/2025/0000013 with NSE/INVG/67858
- Python Dataclasses
version: "2.0.0"
author: System
license: MIT
---

## When to Use

Use this skill when auditing a proprietary trading firm, hedge fund or trading
member to check whether its activity has crossed a *quantitative* registration
trigger in the US, the EU or India — the numeric limbs of three specific
regimes, screened against the numbers the regulator or the exchange actually
publishes.

Start here by correcting the misconception this skill exists to prevent.
**There is no single "algo trading firm licence" and no global message-rate
threshold that triggers one.** The three regimes look superficially alike and
are not:

- **US.** Rule 15b9-1 is an exemption from the Exchange Act **section 15(b)(8)
  requirement to join FINRA**, not from broker-dealer registration under
  section 15(a), and *nothing in it turns on order or message rates*. It turns
  on three conditions: exchange membership, carrying no customer accounts, and
  executing solely on an exchange of which the firm is a member.
- **EU.** The MiFID II HFT designation is a **message-rate** test — but on an
  *average*, at 2 and 4 messages per second, not on a peak and not at any
  round number in the dozens. Meeting it removes the own-account dealing
  exemption and forces investment firm authorisation.
- **India.** The SEBI Threshold Orders Per Second registers an **algorithm
  with an exchange**, for a *retail investor's* API-routed flow. It is not
  entity licensing and it does not govern a trading member's own flow.

The output is an auditable `LicensingComplianceReport` with three outcomes,
not two: a crossed threshold (`requires_registration`), an undetermined
question (`manual_review_required`), and neither. Any report that is not
`is_clear` goes to the Chief Compliance Officer and qualified regulatory
counsel before the desk continues operating.

## When NOT to Use

- **As a licensing determination.** The engine screens numeric limbs. Whether
  a firm is a "dealer" or is "engaged in the business" at all, whether an EU
  activity is an investment service, and every qualitative fact besides, sit
  outside it. A clear report is not a legal opinion.
- **As evidence a registration exists.** The engine reads flags the caller
  supplies. It cannot confirm a firm is a FINRA member, holds a MiFID
  authorisation, or has an algorithm registered with an exchange.
- **For an Indian trading member's own algorithms.** TOPS governs retail
  API-routed flow. The exchange algo-approval regime that governs a member's
  own algorithms is not modelled; the `IN` branch says so rather than
  returning a clean report — see `india-sebi-algo-trading-tagging-requirements`.
- **For a US message-rate trigger.** There isn't one in Rule 15b9-1. If you
  need a US order-rate control, that is a pre-trade risk obligation — see
  `sec-rule-15c3-5-risk-controls-us`.
- **Outside US/EU/IN.** Unknown jurisdictions fail closed to manual review.
  Do not carry the 2/4 messages-per-second figures or the 10 OPS TOPS into
  another jurisdiction; see `mas-singapore-algo-trading-guidelines` for a
  worked example of how differently the same-sounding obligations are built.

## Prerequisites

- Python 3.9+ (standard library only).
- A `FirmTradingActivity` snapshot over a documented evaluation window:
  - `jurisdiction`, `is_exchange_member`, `has_customers`.
  - `off_exchange_volume_usd` and `exempt_off_exchange_volume_usd` — the
    latter being the portion falling solely within the Rule 15b9-1(c)
    exceptions, and evidenced, not assumed.
  - `peak_orders_per_second` — the highest order count in any single
    **calendar clock second, per exchange**, which is the basis NSE specifies
    for TOPS. Not a rolling window.
  - For the EU: `avg_messages_per_second_per_instrument` and
    `avg_messages_per_second_all_instruments`, computed per Article 19(1) and
    restricted to liquid instruments per Article 19(2). Leave them `None` if
    unmeasured — `None` and `0.0` mean different things here.
  - `is_retail_api_algo_flow` for the `IN` branch.

The module computes none of these from raw order data; aggregation discipline
(windowing, exchange-versus-ATS classification, Article 19(2) message
exclusions) is a prerequisite, not a feature.

## Workflow

1. **Aggregate, then construct.** Build `FirmTradingActivity`. The constructor
   rejects unsupported jurisdictions, non-finite and negative metrics, `bool`
   masquerading as a numeric quantity, and an exempt off-exchange figure
   larger than the total — a claim that would otherwise net to a negative
   non-exempt volume and hide a condition (c) breach.
2. **Configure only to tighten.** `LicensingThresholdEvaluator` defaults to
   the published figures. Override `sebi_tops_orders_per_second`,
   `mifid_ii_msgs_per_sec_single_instrument`,
   `mifid_ii_msgs_per_sec_all_instruments` or `sec_off_exchange_floor_usd`
   only to screen *more* strictly. Loosening one past the published figure
   puts the firm outside the rule it is screening for. `0` is a valid,
   stricter override and is honoured as such.
3. **Evaluate.** `evaluator.evaluate(activity)` runs every check; nothing
   short-circuits. The customer-account check runs first and dominates
   `rule_id`, but never suppresses the jurisdiction's own violations.
4. **Triage on three outcomes, not two.**
   - `requires_registration` — a modelled threshold was crossed. Throttle,
     disable the offending routing, or stop, and escalate.
   - `manual_review_required` — the evaluator could not conclude. Escalate to
     counsel. **Do not record this as compliant, and do not act on it as
     though a breach were confirmed either.** The distinction matters: an EU
     firm whose Article 19 averages were never computed is an open question,
     not a proven HFT.
   - `is_clear` — neither fired. Still not a legal opinion.
5. **Audit.** Persist the report (`evaluated_at`, `schema_version`,
   `rule_id`, `violations`, `manual_review_items`) alongside the input
   snapshot. Re-run on a documented cadence; ESMA expects a firm to
   self-assess its Article 19 position **at least monthly**.

## Common Pitfalls

- **Reading Rule 15b9-1 as a registration exemption.** It exempts a
  broker-dealer from *joining FINRA* under section 15(b)(8). A firm that
  concludes it need not register as a broker-dealer because it fits 15b9-1 has
  read the wrong statute.
- **Screening EU exposure on an order rate.** Article 19 is measured on an
  *average*, and it counts *messages* — modifications and cancellations
  included. One order per second, cancel-replaced five times, is already above
  the 2 messages/second limb. So `peak_orders_per_second` is not an EU input in
  either direction: a low order rate does not earn a clean report, and a high
  one does not prove a breach. Without both averages the engine returns "cannot
  determine" — where the earlier 50-peak-OPS benchmark returned "compliant" for
  firms comfortably inside the HFT definition.
- **Treating a missing measurement as a zero.** `None` for an Article 19
  average means unmeasured. Passing `0.0` asserts you measured zero traffic,
  and will produce a clean report you cannot defend.
- **Flagging all off-exchange volume.** Rule 15b9-1(c) still permits
  exchange-routed Rule 611 / Options OPP flow and the stock leg of a
  stock-option order. Netting those out is what
  `exempt_off_exchange_volume_usd` is for — but (c)(2) requires written
  policies and procedures preserved for three years, so the engine raises a
  review item whenever the exception is claimed rather than accepting it.
- **Assuming a de minimis allowance survives.** It does not. The 2023
  amendments removed it; the default screening floor is therefore 0.00 USD.
  A higher floor is a firm's own triage threshold and reflects no regulatory
  carve-out.
- **Applying TOPS to a proprietary desk.** It governs a retail investor's
  API-routed algorithm, and requires registering the *algorithm* with each
  exchange through the broker — not licensing the firm.
- **Missing the TOPS boundary.** NSE sets it at "not exceeding 10 orders per
  second per exchange", so registration bites **above** 10, not at 10.
- **Logging user-provided fields verbatim.** Build downstream logging on
  `report` attributes with `%s` placeholders, never on raw call-site strings,
  to avoid log injection through free-form fields.

## Verification

Run `python -m unittest discover -s skills/algorithmic-trading-firm-licensing-thresholds/scripts` (58
tests). The suite asserts each jurisdiction's threshold at and around its
boundary, that the Article 19 limbs fire independently at 2.0 and 4.0
messages/second, that neither a low nor a high order rate can decide the EU
question, that a `0` threshold override is honoured rather than silently
replaced by the class default, that `bool` is rejected wherever a numeric
quantity is expected, that exempt off-exchange volume is netted out — without a
sub-cent floating-point residue reading as a breach — and its evidencing
obligation surfaced, that violations preserve evaluation order
rather than sorted order, and that an unrecognised jurisdiction fails closed to
manual review. Verify the outcome against `assets/checklist.md`.

## Related Skills

- `finra-algo-trading-registration-requirements`
- `sec-rule-15c3-5-risk-controls-us`
- `mifid-ii-algo-trading-compliance-eu`
- `india-sebi-algo-trading-tagging-requirements`
- `mas-singapore-algo-trading-guidelines`
