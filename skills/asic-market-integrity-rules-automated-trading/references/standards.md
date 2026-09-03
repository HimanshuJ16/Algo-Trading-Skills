# Standards for ASIC Market Integrity Rules — Automated Order Processing

Regulatory basis: **ASIC Market Integrity Rules (Securities Markets) 2017**
(Federal Register of Legislation [F2017L01474](https://www.legislation.gov.au/F2017L01474/latest)),
Part 5.5 (Participant's trading infrastructure) and Part 5.6 (Automated Order Processing —
Filters, conduct, and infrastructure), read with **ASIC Regulatory Guide RG 241
"Electronic trading"**, issued 2 August 2022
([RG 241 PDF](https://download.asic.gov.au/media/qkgfookw/rg241-published-2-august-2022.pdf)).

RG 241.31: the rules in Part 5.6 are specific to AOP systems and *build on* the general
obligations in Part 5.5, which require organisational and technical resources and trading
management arrangements for the participant's trading infrastructure as a whole. Part 5.6
compliance therefore does not discharge Part 5.5.

## Currency

| Item | Status at last verification (September 2026) |
|---|---|
| ASIC Market Integrity Rules (Securities Markets) 2017 | In force; latest compilation **F2024C01108**, compiled 15 October 2024. |
| RG 241 Electronic trading | Current; issued **2 August 2022**. No superseding version published. |
| CP 386 | **Proposal only, not law.** Consultation Paper 386, *Proposed amendments to the ASIC market integrity rules: Trading systems and automated trading*, published 27 August 2025; comments closed 22 October 2025. Proposes amending Rule 5.6.3, inserting Rule 5.6.3B (trading algorithm governance, pre-use and pre-material-change testing, seven-year records), extending the kill-switch controls to individual trading algorithms, amending Rules 5.6.8 and 5.6.8A and repealing Rule 5.6.8B. |

Re-verify the compilation and RG 241 issue date before relying on any rule number below in a
certification or an audit response. Where CP 386 is finalised, the numbering in this skill and
in `scripts/asic_market_integrity_rules_automated_trading.py` will need revisiting.

## Rule map

| ASIC Rule | Requirement (as explained in RG 241) | Implementation |
|---|---|---|
| **Rule 5.6.1(a)**, **5.6.3(1)(a)-(b)** — appropriate automated filters | A participant using its system for AOP must at all times have appropriate automated filters (RG 241.33). Filters are pre-trade controls, the principal means by which messages are checked so they do not interfere with market efficiency or integrity (RG 241.34). RG 241.39-241.41: filters must minimise the opportunity for *erroneous orders* — an order whose price or volume does not reflect what was intended. | `AsicAopPreTradeFilter.run_checks` hard-gates every order on volume, value and price deviation against a valid reference price. |
| **RG 241.35** — filter outcomes | A filter may (a) pass the message; (b) pass but flag it on an exception report; (c) pass it to a designated trading representative for review; or (d) reject it outright. | This skill implements outcome **(d)**. Firms may layer (b) and (c) on top; a breach must never silently pass. Outcome (c) has no implementation here — see "When NOT to Use" in `SKILL.md`. |
| **Rule 5.6.3(1)(a)** — recording filter changes | Processes to record any change to the filters or filter parameters, expressly including intra-day changes and changes by authorised persons (RG 241.43). The participant should be able to identify which filters are activated at any point in time (RG 241.44). RG 241.45: ASIC "would not accept that an AOP system complies with Rules 5.5.2 and 5.6.3 … where filters, filter parameters and exception reports could be deactivated". | `AsicMarketIntegrityConfig` is frozen and validates its limits at construction; `AsicAopPreTradeFilter.replace_config(new_config, authorised_by, reason)` is the only mutation path and appends a `FilterParameterChange` with the previous and replacement values. |
| **Rule 5.6.3(1)(d)** — suspend, limit or prohibit AOP | Controls, including automated controls, enabling immediate suspension, limitation or prohibition of **all** AOP, AOP in respect of ACOP, **or AOP in respect of one or more authorised persons, clients, financial products or markets** (RG 241.52). RG 241.53 contemplates acting on messages "from a particular source (e.g. a particular authorised person, account or algorithm)". RG 241.54 names termination of the AOP system — a "kill switch" — as one such control. | `AsicKillSwitchManager.trigger_kill_switch` (all AOP) and `trigger_scoped_halt` over `AopHaltScope` values `AUTHORISED_PERSON`, `CLIENT`, `FINANCIAL_PRODUCT`, `MARKET`, `ALGORITHM`. `halt_blocking(order)` is evaluated before any numeric check. |
| **Rule 5.6.3(1)(e)(i)-(iv)** — suspend and cancel a series | Immediate suspension, limitation or prohibition of *further entry* of messages in a series (5.6.3(1)(e)(i), (iii)) **and cancellation of messages in that series that have already entered the market** (5.6.3(1)(e)(ii), (iv)) — RG 241.55. RG 241.56: messages form a series where they share a common user, account or algorithm and occur in close succession. RG 241.58: once a series is identified through the monitoring arrangements, the participant must be able to suspend, limit or prohibit further entry *and cancel any that have already entered*. | Suspension of further entry is enforced by the scoped halt. Cancellation of resting messages is delegated to `cancel_series_callback`, which receives the `AopHaltRecord` and returns a count; the outcome is recorded on the audit entry as `COMPLETED`, `FAILED` or `NOT_CONFIGURED`. **This module holds no order book and cannot cancel resting messages itself** — the callback must be wired for this limb to be satisfied. |
| **Rule 5.6.3(2)** — direct control | The participant must at all times have direct control over its automated filters and the filter parameters (RG 241.47). Direct control is expected at the "administrator" level — over whether a filter is activated or deactivated and over any parameter change — though a user may be given discretion within a defined range (RG 241.48). RG 241.44 expects administrator-level changes to be implemented only after authorisation by a qualified person. RG 241.49-241.51: filters sitting outside the AOP system, or sponsored-access arrangements operating independent of the participant's controls, do not satisfy this. | Filters and config run in-process on the participant's execution path, not in a broker's cloud. `replace_config` refuses a blank `authorised_by` or `reason`; `reset_kill_switch` and `release_scoped_halt` refuse a blank actor or reason and audit the refusal. |
| **Part 5.6 monitoring and recordkeeping** | Monitoring in real time or close to real time (RG 241.81); surveillance capable of identifying breaches in real time or close to it, with steps taken without delay (RG 241.82); regular analysis of historic order and trading patterns, including adjusting filters where they are not operating as intended (RG 241.84); exception reports monitored regularly and at least daily (RG 241.87). | Every `ComplianceResult` carries `rejection_code`, `order_id` and `checked_at_unix`; kill-switch transitions (including refused releases) are appended to `audit_log`; filter parameter changes to `parameter_audit_log`. Feeding these into a surveillance store is the integrator's responsibility — this module performs no pattern detection. |
| **Rules 5.6.6, 5.6.8, 5.6.8A, 5.6.8B** — certification and review | Initial review and certification before using the system for AOP (Rule 5.6.6, with Rules 5.6.4 and 5.6.5); review of material changes before they are implemented (Rule 5.6.8); annual review by an appropriately qualified person where no material change review occurred in the preceding 12 months (Rule 5.6.8A, RG 241.182-241.185); annual notification to ASIC (Rule 5.6.8B). RG 241.188: the annual review date is **1 November each calendar year**, and the annual notification must be given to ASIC **within 10 business days** of it. | Out of scope for this module — it is a runtime control, not a governance workflow. Step 7 of `references/workflows.md` records the obligation so it is not forgotten. |
| **Rules 5.6.11, 5.6.12** — ASIC directions | ASIC may direct a participant to provide further certification (Rule 5.6.11) or to cease, or immediately suspend, limit or prohibit, the conduct of AOP in whole or in part (Rule 5.6.12). | `trigger_kill_switch` / `trigger_scoped_halt` are the mechanism by which such a direction is executed immediately; the audit entry records the actor and reason. |

## Material changes (Rule 5.6.8)

RG 241.174 gives examples of changes that *may* be material and therefore require review
**before** implementation, several of which a filter-parameter change can amount to:

- a change that increases the risk of orders being entered or amended which could create, or
  appear to create, a disorderly market or manipulative trading (RG 241.174(h));
- a change that increases the risk of a high order-to-trade ratio relative to the security,
  underlying market liquidity or current market volatility (RG 241.174(i));
- a series of incremental changes that, considered together, constitute a material change
  against the system at initial certification or the previous annual review (RG 241.174(e)).

RG 241.176: moving from a third-party-provided system to one from a *new* third party is a new
system requiring initial certification under Rule 5.6.6, not a material change.

## No prescribed thresholds

Part 5.6 prescribes no numeric value, volume or price-deviation figure. RG 241.36 states that
there "must be some degree of flexibility in determining what constitutes 'appropriate' filters
for each trading participant", depending on system capabilities and the nature, scale and
complexity of the business. Any limit configured in `AsicMarketIntegrityConfig` is the
participant's own risk policy and must be justified in the documentation supporting the Rule
5.6.6 certification — it is not an ASIC minimum.

## Jurisdiction

RG 241 applies to trading participants of the markets operated by **ASX Limited, Cboe Australia
Pty Limited, National Stock Exchange of Australia Limited and Sydney Stock Exchange Limited**.
(Chi-X Australia, whose venue code CXC is still in wide use, was rebranded Cboe Australia in
2022.) A separate instrument, the **ASIC Market Integrity Rules (Futures Markets) 2017**,
governs futures market participants and is not cited by this skill. These obligations are not
universal — do not apply them to non-Australian venues without confirming the local regulator's
equivalent regime.

## Sources

- ASIC Market Integrity Rules (Securities Markets) 2017 — https://www.legislation.gov.au/F2017L01474/latest
- ASIC Regulatory Guide 241, *Electronic trading* (2 August 2022) — https://www.asic.gov.au/regulatory-resources/find-a-document/regulatory-guides/rg-241-electronic-trading/
- ASIC Consultation Paper 386, *Proposed amendments to the ASIC market integrity rules: Trading systems and automated trading* (27 August 2025) — https://www.asic.gov.au/regulatory-resources/find-a-document/consultations/cp-386-proposed-amendments-to-the-asic-market-integrity-rules-trading-systems-and-automated-trading/
