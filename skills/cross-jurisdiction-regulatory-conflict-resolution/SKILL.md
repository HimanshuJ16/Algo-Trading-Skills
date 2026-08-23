---
name: cross-jurisdiction-regulatory-conflict-resolution
description: Pre-trade compliance gate for cross-jurisdiction regulatory conflicts
  (SEC vs MiFIR vs FCA), resolving PFOF, LEI tagging, and short-selling rules under
  Strictest Rule Primacy and recording an auditable decision per order.
domain: Compliance & Legal
subdomain: Cross-Jurisdiction Regulation
tags:
- compliance
- regulatory-conflict
- mifid-ii
- sec
- pfof
- short-selling
- lei
- strictest-rule-primacy
brokers_frameworks:
- MiFID II
- SEC
- FCA
- Python Dataclasses
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in multi-national quantitative trading firms whose orders are simultaneously in scope of more than one regime (e.g. US SEC/FINRA, EU MiFIR/MiFID II, UK FCA, Hong Kong SFC, Japan FSA). Where an entity in one jurisdiction routes to a venue in another, the two rulebooks can disagree — most sharply on Payment for Order Flow (PFOF), pre-trade client identification (LEI / national client ID), and short-selling constraints. This module is the pre-trade gate that resolves those three dimensions under **Strictest Rule Primacy** (obey the most restrictive applicable rule), blocks orders that cannot satisfy it, and emits an audit decision for every order it sees.

**Scope caveat (important):** Strictest Rule Primacy is a conservative *firm policy* heuristic, not a conflict-of-laws determination. It is valid only for prohibition-style rules, where complying with the strictest regime also complies with the others. It cannot resolve a **mandate-vs-prohibition conflict** — where regime A compels an act regime B forbids (blocking-statute situations, disclosure vs secrecy) — and the engine cannot detect such conflicts. Those require legal advice or a regulatory waiver.

## When NOT to Use

- **As a legal conflict-of-laws analysis.** Irreconcilable mandate/prohibition conflicts are out of reach of a MAX/AND/OR resolver; escalate to counsel.
- **As LEI issuance or GLEIF validation.** `is_valid_lei()` is an offline ISO 17442 structural + checksum check. It cannot confirm the LEI is issued, belongs to the client, or is in an active registration status — all of which MiFIR Art. 26 reporting requires (ESMA validation rules require the code to exist in the GLEIF database with an entity status active on the trading date). A GLEIF lookup is still mandatory.
- **As short-selling execution control.** A `PRICE_TEST` result (Reg SHO Rule 201) is a constraint on the *limit price relative to the national best bid*, and a `REPORTING` result is a position-level disclosure duty. Neither can be evaluated from the order payload alone; this engine surfaces them as obligations for downstream systems. Locate/close-out requirements (Reg SHO Rule 203(b)) are not modelled at all.
- **For rule dimensions it does not model.** Research payment arrangements, best-execution policy, position limits, and market-abuse surveillance are out of scope. (Note that research unbundling is no longer a standing UK/EU conflict: the FCA permitted joint payments from 1 Aug 2024 in PS24/9, and the EU Listing Act, in force 4 Dec 2024, re-allowed joint payment for research and execution under MiFID II.)

## Prerequisites

- Trade order payload: `order_id`, `entity_jurisdiction`, `venue_jurisdiction`, `symbol`, `quantity`, `price`, `is_short`, `routed_via_pfof`, `lei_tag`, and — for retail natural-person clients — `is_natural_person_client` / `national_client_id`.
- A `JurisdictionRules` profile per active jurisdiction, maintained by compliance: `is_pfof_allowed`, `is_lei_mandatory`, `short_selling_restriction_level`.
- Agreement on the severity ordering used for short selling (`ShortSellingRestriction`): `NONE(0) < REPORTING(1) < PRICE_TEST(2) < BAN(3)`.

## Workflow

1. **Overlapping Jurisdiction Mapping**:
   - Applicable set $\mathcal{J} = \{\text{Entity Jurisdiction}, \text{Venue Jurisdiction}\}$, codes trimmed and upper-cased, then **sorted** — audit strings must be byte-reproducible for two identical orders, which iterating a Python set does not guarantee.
   - A blank or non-string jurisdiction code raises `ValueError`; an order whose applicable regime is unknown must never reach an APPROVED decision.
2. **Rule Matrix Resolution (Strictest Rule Primacy)**:
   - PFOF: allowed only if allowed in **all** of $\mathcal{J}$ (AND).
   - Client ID: LEI mandatory if mandatory in **any** of $\mathcal{J}$ (OR).
   - Short selling: **MAX** severity across $\mathcal{J}$, i.e. `BAN > PRICE_TEST > REPORTING > NONE`.
   - Decision point: **an unregistered jurisdiction fails closed on every dimension** (PFOF blocked, LEI mandatory, short selling banned) and is named in `decision.unregistered_jurisdictions`. An empty jurisdiction set raises `ValueError` rather than resolving to the most permissive rule set.
3. **Pre-Trade Compliance Audit**:
   - PFOF routing under a banning regime $\implies$ violation.
   - Client identification: for a **legal entity**, the LEI must pass ISO 17442 structural validation *and* its ISO/IEC 7064 MOD 97-10 check digits; for a **natural person**, an LEI is not the right identifier at all — a national client identifier (RTS 22 Art. 6 / Annex II, CONCAT fallback) is required instead.
   - Short selling: only `BAN` blocks the order. `PRICE_TEST` and `REPORTING` are returned in `required_obligations` for downstream enforcement — approving them silently would drop the obligation.
4. **Audit Decision Generation**:
   - Return `RegulatoryComplianceDecision` (status, resolved rule set, violations, obligations, unregistered jurisdictions, rationale) and append a defensive copy to `engine.audit_trail`, so a recorded REJECTED decision cannot be rewritten by a caller mutating the returned object.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Length-Only LEI Validation**: `len(lei) == 20` accepts `AAAAAAAAAAAAAAAAAAAA` and any typo'd or transposed code. ISO 17442 reserves positions 19-20 for ISO/IEC 7064 MOD 97-10 check digits — a well-formed LEI, read as one integer with A-Z mapped to 10-35, satisfies `value % 97 == 1`. An unchecked tag survives the gate and fails later at the NCA, after the trade.
- **Demanding an LEI from a Natural Person**: an LEI identifies a *legal* entity. A retail client is identified under MiFIR Art. 26 / RTS 22 by a national client identifier (or CONCAT), so a gate that requires a 20-character LEI universally rejects every legitimate retail order.
- **Ranking a Disclosure Duty Above a Price Test**: a net short position *reporting* obligation does not stop a trade; a Reg SHO Rule 201 price test does. Encoding reporting as the more severe level makes MAX resolution silently discard the price-test obligation when both regimes apply.
- **Fail-Open on an Unconfigured Jurisdiction**: an unknown jurisdiction code must resolve to the strictest value on *every* dimension. A fallback that is maximally strict for PFOF and LEI but only mid-severity for short selling permits shorts into a regime nobody has assessed.
- **Non-Deterministic Audit Records**: building the rationale string by joining an unordered set means the same order can produce different audit text on different runs — worthless for reconciliation and for demonstrating consistent treatment to a regulator.
- **Applying Local Rules Only**: a US entity routing to an EU venue is in scope of MiFIR Art. 39a (PFOF banned EU-wide since the last transitional exemption, Germany's, expired 30 June 2026) and MiFIR Art. 26 LEI reporting, regardless of the SEC's more permissive Rule 606 disclosure regime.
- **Ignoring Entity Extraterritoriality**: a UK subsidiary trading US equities is still subject to FCA conduct rules — including the FCA's long-standing treatment of PFOF as an inducement incompatible with COBS 2.3 and best execution (FSA FG12/13).
- **Treating "Strictest" as a Legal Answer**: strictest-rule primacy is a safe default for prohibitions only. It cannot arbitrate a rule that *mandates* what another jurisdiction *forbids*, and quietly returning APPROVED in that situation is worse than escalating.

## Verification

- Instantiate `CrossJurisdictionRegulatoryConflictEngine` with `US_SEC` (PFOF allowed, LEI optional, `PRICE_TEST`) and `EU_MIFID_II` (PFOF banned, LEI mandatory, `REPORTING`). Submit a US-entity / EU-venue order with `routed_via_pfof=True` and no LEI: expect `is_approved=False` with exactly two violations (PFOF, LEI).
- Submit the same order with a structurally valid LEI and no PFOF: expect approval plus `LEI_TAGGING_REQUIRED` in `required_obligations`.
- Pass a 20-character string with wrong check digits (e.g. `5493001KJTIIGC8Y1S12`, MOD 97 remainder 4): expect rejection. Transposing the first two characters of a valid LEI must also fail.
- Resolve `{US_SEC, EU_MIFID_II}` and verify the short level is `PRICE_TEST`, not `REPORTING`, and that a short order returns `SHORT_SELL_PRICE_TEST_APPLIES`.
- Submit an order with an unregistered venue jurisdiction: expect PFOF blocked, LEI mandatory, short selling banned, and the code listed in `unregistered_jurisdictions`. Call `resolve_strictest_rules(set())` and expect `ValueError`.
- Submit a natural-person order with `lei_tag=None` and a national client ID: expect approval; remove the national ID and expect a `CLIENT ID VIOLATION`.
- Evaluate the same order with entity and venue jurisdictions swapped and verify `applied_rules_summary` is identical.
- Mutate a decision returned by `engine.audit_trail` and verify the engine's own record is unchanged.
- Run `python -m unittest discover -s skills/cross-jurisdiction-regulatory-conflict-resolution/scripts`.

## Related Skills

- `cross-border-data-transfer-restrictions-for-trade-data`
- `best-execution-record-keeping-global`
- `us-reg-sho-short-sale-locate-requirements`
- `eu-short-selling-regulation-disclosure-thresholds`
