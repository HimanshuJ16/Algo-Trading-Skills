---
name: binary-options-regulatory-and-risk-considerations
description: >-
  Use before researching or deploying a strategy in binary options or fixed-return event
  contracts, where the first question is whether the trade is lawful for that client
  category and jurisdiction rather than whether it has edge.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: multi-asset-derivatives
  tags: regulatory-compliance-global, binary-options, product-intervention, event-contracts, pre-trade-compliance
  brokers_frameworks: "CFTC / SEC (US); FCA Handbook COBS 22.4 (UK); MiFIR Article 42 national measures (EU); ASIC Instrument 2021/240 (AU); CSA Multilateral Instrument 91-102 (CA)"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

# Binary Options Regulatory & Risk Considerations

Binary options are the most heavily restricted retail derivative in most major markets.
For a large share of client/jurisdiction combinations the correct engineering answer is
that the trade cannot be placed at all — so the compliance gate belongs *before* the
alpha work, not after it. This skill supplies that gate plus exposure controls for the
discontinuous payoff.

**This is decision support, not legal advice.** The rules encoded here are partial,
dated, and must be confirmed with counsel for your entity and client base.

## When to Use

- Before any research or backtesting effort on binary options, "digital" options,
  fixed-return options, or one-touch/no-touch products — the first question is legality,
  not edge.
- When building a pre-trade gate that must decide, per order, whether a binary payout is
  permitted for this client category, jurisdiction, and venue.
- When assessing **binary-payout event contracts and prediction markets**. On 3 July 2026
  ESMA confirmed that event contracts which qualify as financial instruments fall within
  the existing national binary options product intervention measures, and that
  distribution in the EU requires investment firm authorisation even to non-retail
  clients. A product is assessed on its function, not its marketing name.
- When sizing exposure to any instrument whose payoff is discontinuous at a strike.

## When NOT to Use

- **As a substitute for legal advice or a licence check.** The rule table here covers a
  handful of regimes at a coarse level and omits most EU member states entirely. It
  cannot tell you whether your entity is authorised.
- **For vanilla options.** Continuous payoffs have none of the pin-discontinuity or
  product-intervention characteristics this skill is built around.
- **For Greeks-based pin risk management.** The near-expiry control here is a notional
  concentration cap that uses no spot price and no volatility model. For delta/gamma
  behaviour near the strike see `options-pin-risk-management-at-expiry`.
- **As a runtime risk control.** This is a pre-trade gate. Continuous drawdown and
  exposure enforcement belongs to `kill-switch-and-drawdown-circuit-breakers`.
- **To settle a conflict between two regimes.** When client, venue, and firm sit in
  different jurisdictions, see `cross-jurisdiction-regulatory-conflict-resolution`.

## Prerequisites

- Python 3.10+ (standard library only).
- Reliable client categorisation (retail / professional / eligible counterparty) **and**
  whether the client is a natural person — these are different tests and some rules key
  on the second one.
- Verified venue registration status with the relevant regulator, refreshed on a
  schedule. In the US, check registration before trading; the CFTC maintains a
  Registration Deficient (RED) List of unregistered foreign entities.
- Timezone-aware expiry timestamps. Term to maturity is a legally material term.
- A named owner for re-verifying the rule table — it carries a `RULESET_LAST_VERIFIED`
  date and warns once stale.

## Workflow

1. **Assemble the trade context.** `TradeContext` validates on construction: notional and
   strike must be finite and positive, expiry must be timezone-aware, jurisdiction and
   venue status must be enum members, and `client_type` is normalised into a
   `ClientCategory`. Anything unrecognised raises `ValueError` rather than proceeding.

2. **Establish the venue before the jurisdiction.** The venue gate runs in *every*
   jurisdiction, not just the US: `UNREGISTERED` denies with
   `REG_VENUE_NOT_REGISTERED`, and `UNKNOWN` — which is the default — denies with
   `REG_VENUE_STATUS_UNKNOWN`. This is deliberate: the dominant documented harm in
   binary options is unregistered offshore platforms soliciting clients, and "we didn't
   record the venue" must not read as "the venue was fine."

3. **Evaluate the jurisdiction rule.** One rule per regime, default-deny for any
   jurisdiction with no rule configured. Each denial returns a reason code and a citation
   so the decision is auditable. The rules differ in *shape*, not just in threshold:
   - **US (CFTC/SEC)** — permitted for any client category, but only on a registered
     venue, which the step 2 gate has already established.
   - **UK (FCA)** — retail prohibited since 2 April 2019; professionals are out of scope
     of the ban.
   - **EU** — retail prohibited under *national* measures. A non-retail EU trade returns
     `REG_JURISDICTION_UNRESOLVED` rather than an approval, because there is no longer an
     EU-wide measure to approve against (see Pitfalls).
   - **Australia (ASIC)** — retail prohibited; extended to 1 October 2031.
   - **Canada (CSA)** — prohibited for **individuals** where term to maturity is under 30
     days, *regardless of accredited or professional status*. If `is_natural_person` is
     unknown the rule denies, because guessing it wrong is a fail-open.

4. **Apply risk limits.** `check_limits` enforces the per-trade notional cap, an optional
   aggregate book cap, and a near-expiry ("pin") concentration cap. Exposure is measured
   as full notional: with a discontinuous payoff there is no partial-loss regime to net
   against, so the worst case is the whole amount at risk.

5. **Book approved exposure.** `process_order` registers approved trades against the risk
   book, keyed by `asset_id`. Re-submitting the same id replaces rather than
   double-counts, so a retry after an ambiguous response does not inflate exposure. Pass
   `register=False` to evaluate without booking.

6. **Persist the decision record.** Every call returns `status`, `message`,
   `reason_code`, `citation`, and `asset_id`, and logs the decision. Retain rejections —
   they are the evidence that the gate was operating.

7. **Re-verify the rules on a schedule.** Pass `ruleset_last_verified` when you check the
   table against primary sources. Past `ruleset_max_age` (default 180 days) every
   evaluation logs a warning.

> Cited primary sources for every rule: see `references/standards.md`.
> Step-by-step integration and re-verification procedure: see `references/workflows.md`.
> Sign-off checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Citing ESMA's EU-wide ban as current.** Decision (EU) 2018/795 was *temporary*. It
  expired at the end of 1 July 2019 and ESMA did not renew it, because national
  authorities had adopted permanent measures under MiFIR Article 42. Code or documents
  asserting "ESMA bans binary options for EU retail" are describing a measure that lapsed
  years ago; the binding rule is the member state's.
- **Comparing a free-text client type with `==`.** `client_type == "RETAIL"` approves a
  client recorded as `"Retail"`. A retail ban defeated by capitalisation is the single
  cheapest regulatory failure to introduce and the hardest to notice in testing.
- **Assuming "professional" is always the exemption.** Canada's MI 91-102 keys on
  *individual*, explicitly including accredited investors. A professional-categorised
  natural person is still prohibited from short-dated binaries there.
- **Treating a NaN as a small number.** `float('nan') > limit` is `False`, so a NaN
  notional passes a naive cap check and books as unlimited risk.
- **Approving on jurisdiction alone while ignoring the venue.** A client in a permissive
  jurisdiction trading on an unregistered offshore platform is the exact fact pattern in
  the CFTC/SEC investor alerts. Jurisdiction and venue registration are independent
  checks.
- **Hardcoding a venue whitelist.** The joint CFTC/SEC alert named three DCMs; that list
  has since moved — Nadex now does business as Crypto.com | Derivatives North America and
  retired the Nadex.com platform in December 2025. Whitelists must be dated, sourced
  configuration with an owner, never literals in a compliance module.
- **Assuming an unrecognised jurisdiction is permissive.** Default-deny, always.
- **Netting binary exposure like a linear payoff.** The payoff jumps at the strike, so
  stress and VaR must assume the full discontinuous loss rather than interpolating.
- **Letting "prediction market" or "event contract" branding bypass the analysis.** ESMA's
  3 July 2026 statement is explicit that binary-payout event contracts which are financial
  instruments sit inside the existing binary options measures.
- **Naive expiry timestamps.** Comparing naive and aware datetimes raises `TypeError`, and
  silently assuming UTC produces a wrong answer to a legally material question.

## Verification

- Run the unit suite:
  `python -m unittest discover -s skills/binary-options-regulatory-and-risk-considerations/scripts`.
- Submit a UK retail trade with `client_type="Retail"` and confirm
  `REG_UK_RETAIL_PROHIBITED` — not an approval.
- Submit a trade with `venue_status=VenueStatus.UNKNOWN` and confirm
  `REG_VENUE_STATUS_UNKNOWN`; repeat with `UNREGISTERED` for
  `REG_VENUE_NOT_REGISTERED`. Confirm both hold outside the US too.
- Submit a Canadian trade for a professional-categorised natural person expiring in 29
  days and confirm `REG_CA_SHORT_TERM_PROHIBITED`; repeat at exactly 30 days and confirm
  it is permitted.
- Submit an EU professional trade and confirm `REG_JURISDICTION_UNRESOLVED` rather than
  an approval.
- Submit `notional=float('nan')` and confirm `TradeContext` raises `ValueError`.
- Construct a `ComplianceEngine` with `ruleset_last_verified` over a year old and confirm
  a staleness warning is logged.
- Register near-expiry trades past `max_pin_risk_exposure` and confirm
  `RISK_PIN_EXPOSURE`; confirm the same notional outside `pin_window` is permitted.
- Confirm every rejection carries a non-empty `citation`.

## Related Skills

- `mifid-ii-algo-trading-compliance-eu`
- `uk-fca-algorithmic-trading-systems-controls`
- `asic-market-integrity-rules-automated-trading`
- `cross-jurisdiction-regulatory-conflict-resolution`
- `regulatory-change-monitoring-service-integration`
- `options-pin-risk-management-at-expiry`
- `kill-switch-and-drawdown-circuit-breakers`
- `record-retention-periods-by-jurisdiction`
