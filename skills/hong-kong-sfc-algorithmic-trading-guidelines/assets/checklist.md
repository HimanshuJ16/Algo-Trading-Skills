# Pre-Flight / Sign-off Checklist — hong-kong-sfc-algorithmic-trading-guidelines

Jurisdiction: **Hong Kong SAR — SFC-licensed or registered persons trading on SEHK.**
If the venue is HKFE, Northbound Stock Connect or off-exchange, the short selling
controls below do not apply as written; record that determination and use the right regime.

Paragraph references are to Schedule 7 of the SFC Code of Conduct.

## Scope and governance (§1.1)
- [ ] Entity's licensed/registered status confirmed, and the regulated activity covered.
- [ ] At least one responsible officer or executive officer named as accountable for the electronic trading system (§1.1.1(a)).
- [ ] Governance forum has real input from dealing, risk and compliance — not an approval rubber stamp (§1.1.1(b)).
- [ ] Channel classified: internet trading, DMA, algorithmic trading, or a combination — and the paragraph 2 and 3 requirements applied accordingly.
- [ ] Third-party systems covered by due diligence, and by arrangements ensuring the provider keeps the §1.3.1 / §3.4 records.

## Qualification and testing (§3.1, §3.2)
- [ ] Persons designing/developing the algorithms are suitably qualified and trained on the compliance and regulatory issues (§3.1.1) — note Hong Kong has **no** developer registration exam; do not build a gate that pretends otherwise.
- [ ] Persons approved to use the system understand its operation and its regulatory issues (§3.1.2), and are re-briefed after material changes (§3.1.4).
- [ ] Up-to-date operating documentation exists for approved users, covering risk, supervisory and compliance controls (§3.1.5).
- [ ] Pre-deployment testing covers foreseeable extreme market conditions **and** the differing characteristics of auction versus continuous trading sessions (§3.2.1(b)).
- [ ] Review and testing scheduled no less than annually, with scope and findings retained ≥2 years (§3.2.2, §3.4.3).

## Threshold calibration (SFC circular, 13 Dec 2016)
- [ ] Every threshold has a written derivation, an owner and a review date — no value copied from a sample or a skill.
- [ ] Notional value, order quantity, price band, ADV participation and message-rate limits all considered; any control deliberately left off is recorded as a decision with a reason.
- [ ] The same suite applies to DMA flow as to in-house algorithmic flow, not just a notional credit limit.
- [ ] Child orders inherit the parent's controls, and child price/quantity cannot exceed the parent's.
- [ ] Override procedure defined: who approves, who is notified, where the evidence is filed, who reviews it independently. Verbal approval alone is not the procedure.

## Kill switch (§1.2.1)
- [ ] Half (a) implemented: new order generation and submission can be stopped immediately.
- [ ] Half (b) implemented: unexecuted orders resting in the market can be cancelled — a separate call against the exchange session, wired into the same runbook.
- [ ] Scopes below firm level available — at minimum per algorithm and per client.
- [ ] Activation and release both require a named person and a reason, and both are logged at CRITICAL.
- [ ] Shutdown rehearsed against the clock; the procedure names who may trigger it out of hours.

## Pre-trade gate behaviour
- [ ] Thresholds compared **unrounded** — rounding a 5.004% deviation to 5.00% approves the breach.
- [ ] Notional compared in exact arithmetic so an order sitting on the limit is not rejected by floating-point drift.
- [ ] Missing market data blocks the order and is recorded as `MISSING_MARKET_DATA`; it is never defaulted to a passing value such as a 0.0% deviation.
- [ ] A zero or absent nominal price cannot raise an unhandled exception in the order path.
- [ ] Malformed orders from the strategy raise loudly rather than being silently classified as compliant.
- [ ] Every applicable control is evaluated and every breach recorded — not just the first one hit.
- [ ] Blocked orders are filed with their real notional and deviation, not zeroes.

## Covered short selling (SFO ss.170–172; Rule 563D; Eleventh Schedule)
- [ ] Cover under s.170 confirmed before the order is sent — a presently exercisable and unconditional right to vest the securities.
- [ ] Section 171 documentary assurance obtained **before transmission**, referenced on the order, and retained ≥12 months.
- [ ] Order marked "short" on input, and anyone the order is passed to is told it is short (s.172, Reg (5)(b)).
- [ ] Designated Securities list refreshed on a schedule, with a staleness alarm; the check uses the list as at the order's date.
- [ ] Session and order type validated: in POS and CAS only at-auction limit orders may be input as short selling orders.
- [ ] Tick rule enforced against the **correct** reference price — best current ask in CTS, POS reference price in POS, CAS reference price in CAS. Never the last traded price.
- [ ] A missing tick-rule reference price blocks the order rather than approving it.
- [ ] Any Rule 563D(1) exempt status is a documented firm determination, recorded per order as a claimed category, and never used to waive the s.170 cover check on an order flag.

## Records and retention (§1.3, §3.4)
- [ ] Every decision persisted — approvals, rejections and blocked-by-kill-switch alike.
- [ ] Records carry a time stamp and a unique reference number (Annex (i)(a)).
- [ ] Compliance validation exceptions and erroneous order inputs captured, per Annex (i)(d) and (g).
- [ ] The algorithm's own parameters for each order retained alongside the compliance record (§3.4.2).
- [ ] Audit logs and incident reports retained **not less than 2 years** (§1.3.2(b)); design/development and risk-control documentation ≥2 years after the system ceases to be used (§1.3.2(a)).
- [ ] The in-memory `audit_trail` is not the system of record — a durable append-only sink is configured.

## Post-deployment (§1.2.3, §3.3)
- [ ] Post-trade review runs regularly over order instructions and trades for manipulative or abusive activity and for system deficiencies (§3.3.2).
- [ ] A defined path exists from a surveillance hit to an immediate scoped shutdown (§3.3.3).
- [ ] Written contingency plan exists specific to the algorithmic trading system, and is periodically tested (§1.2.6, §1.2.7).
- [ ] Material service interruptions affecting client-facing systems are reported to the SFC promptly (§1.2.3).
