# Workflows — HK SFC electronic and algorithmic trading compliance

Paragraph references are to Schedule 7 of the SFC Code of Conduct unless stated
otherwise. Citations and exact wording are in `references/standards.md`.

## 0. Establish scope before building anything

1. Confirm the entity is a licensed corporation or registered institution — Schedule 7
   binds licensed or registered persons, not the strategy or the software.
2. Confirm the venue. The short selling controls here are SEHK's. HKFE futures,
   Northbound Stock Connect and off-exchange business are outside them.
3. Classify the channel: internet trading, DMA, algorithmic trading, or more than one.
   Paragraph 1 applies to all electronic trading; paragraph 2 adds internet-trading and
   DMA requirements; paragraph 3 adds algorithmic-trading requirements. A DMA gateway
   feeding a client's own algorithm is subject to both 2 and 3.
4. Record which responsible officer or executive officer owns the system (§1.1.1(a)) and
   who sits in the governance process from dealing, risk and compliance (§1.1.1(b)).

## 1. Calibrate the thresholds — this is the step that is usually skipped

No Hong Kong rule supplies a number. The SFC's 2016 thematic review found inadequate
analysis behind threshold parameter values, so the calibration record *is* part of the
deliverable.

1. For each control — notional value, order quantity, price band, ADV participation,
   message rate — derive the value from something: the desk's historical order
   distribution, the instrument's liquidity, the firm's capital, the client's credit line.
2. Write down the analysis, the value, the owner and the review date. Store it with the
   §1.3.1(b) risk-management-control documentation.
3. Set the same suite on every channel. DMA flow subject only to a notional credit limit
   was an express finding; different algorithmic systems should carry the same controls
   absent strong justification.
4. Define the exception path *before* you need it: who may override a pre-trade control,
   what approval and notification the override requires, and where the evidence goes.
   Verbal approval alone was a finding.

## 2. Wire the kill switch (§1.2.1)

1. Implement both halves. `trigger_sfc_kill_switch()` stops the gate from admitting new
   orders — §1.2.1(a). The mass-cancel of unexecuted orders resting in the market —
   §1.2.1(b) — is a separate call against the exchange session and must be part of the
   same procedure.
2. Implement scopes below firm level. At minimum firm, algorithm and client; the SFC's
   good-practice list also names exchange connectivity, order, trader and system levels.
3. Require a reason and a named human on both activation and release, and log both at
   CRITICAL. Releasing is a control override.
4. Rehearse it. The circular asks that procedures be formalised so shutdown can be
   executed "within a short period of time"; an untested switch is a document, not a
   control. See `execution-algorithm-kill-switch-integration`.

## 3. Gate the order (per submission)

Evaluate in this order, but evaluate *all* of it — a single-reason rejection loses the
rest of what was wrong.

1. **Kill switches** covering the firm, this algorithm and this client.
2. **Authorisation** — algorithm signed off for production (§1.1.1(d)), this version
   tested before deployment (§3.2.1), operator approved to use the system (§3.1.2).
3. **Firm pre-trade thresholds** (§2.1.1(a), §3.3.1) — notional, quantity, price band
   against the nominal price, ADV participation, message rate, and for a sliced order the
   child's price and quantity against the parent's.
4. **Short selling**, if the order is short — see section 4.
5. **Assemble the record** — every violation, in precedence order, with the real notional
   and deviation figures even when the order was blocked for an unrelated reason.

Two rules govern bad input:

- A defect in the caller's own order — unknown session, side, order type or exemption
  token, empty identifiers, non-positive quantity, non-finite price — raises `ValueError`.
  It is a bug in the strategy, and a silent pass is how a malformed order reaches SEHK.
- Missing external market data — no nominal price, no tick-rule reference price, no ADV
  when the ADV cap is enabled — produces `MISSING_MARKET_DATA` and blocks the order. A
  control that could not be evaluated has not been satisfied.

## 4. Covered short selling — run all five checks

A short sale must clear each of these independently:

1. **Cover (SFO s.170).** A presently exercisable and unconditional right to vest the
   securities in the purchaser. In practice a confirmed borrow, a hold notice or blanket
   assurance from the lender, securities already bought, or a physically settled
   convertible instrument. Never waive this on an order-level exemption flag — the
   statutory exemptions in s.3 of the Short Selling and SBL (Miscellaneous) Rules are a
   determination for the firm's legal function, made once and evidenced, not a boolean on
   a message.
2. **Documentary assurance (SFO s.171).** Held before the order is transmitted, conveying
   that it is a short sale and that it is covered. Retain ≥12 months. One assurance may
   cover a series of orders if it is drafted to.
3. **Marking (SFO s.172, Eleventh Schedule Reg (5)(b)).** The order is flagged short on
   input, and whoever passes it on is told it is short.
4. **Eligibility (Rule 563D(1)).** The stock is on SEHK's Designated Securities list *as
   at today*, and the session is one where short selling is permitted. In POS and CAS only
   at-auction limit orders may be input as short selling orders.
5. **Tick rule (Eleventh Schedule Reg (15), Rule 501(G)(3)(d)).** Not below the best
   current ask in CTS, the CAS reference price in CAS, or the POS reference price in POS.

Operational notes:

- Refresh the Designated Securities list on a scheduled job and alarm on staleness; the
  engine cannot tell that your snapshot is old.
- Feed the *right* reference price per session. Passing the last traded price where the
  best ask is required will approve orders SEHK rejects, and reject orders it would accept.
- Exempt participants (market makers, liquidity providers, index-arbitrage and hedging
  participants under Rule 563D(1)) fall outside the Designated Securities and tick
  restrictions. Record which category is claimed so the exemption is auditable.

## 5. Record keeping (§1.3, §3.4)

1. Persist **every** decision — approvals as well as rejections. The Annex asks for order
   placement with time stamping and a unique reference number, compliance validation
   exceptions and erroneous order inputs; approvals are what let you reconstruct that the
   gate was running and what it saw.
2. Persist the **algorithm's own parameters for each order** (§3.4.2) alongside the
   compliance record. This module records the compliance inputs, not the strategy's
   internal state.
3. Retain audit logs and incident reports for **not less than 2 years** (§1.3.2(b)), and
   design/development and risk-control documentation for not less than 2 years after the
   system ceases to be used (§1.3.2(a)). Section 171 assurances follow their own 12-month
   period. The in-memory `audit_trail` satisfies none of this — pass `audit_sink` and write
   to an append-only store. See `record-retention-periods-by-jurisdiction`.
4. Where the system comes from a third party, make arrangements with the provider so the
   §1.3.1 and §3.4.1–3.4.3 records are kept and retained — the obligation stays with the
   licensed person.

## 6. After deployment

1. **Post-trade review (§3.3.2).** Regularly review activity conducted through the system,
   including order instructions, for suspected manipulative or abusive activity and for
   market events or system deficiencies calling for further controls. On identification,
   take immediate steps to stop it (§3.3.3) — the scoped kill switch is that step. See
   `wash-trade-and-spoofing-self-detection`.
2. **Annual review and testing (§3.2.2).** No less than annually, review and test the
   system's ability to handle sizeable volume and the algorithms' ability to execute
   without interfering with a fair and orderly market. Retain the scope and findings
   ≥2 years (§3.4.3).
3. **Retest on every material change (§1.2.2, §3.2.1).** Testing must take account of
   foreseeable extreme market circumstances and of the differing characteristics of
   auction and continuous trading sessions — the auction sessions are where short selling
   order-type rules and reference prices change.
4. **Report material incidents (§1.2.3)** where the system is provided to clients, and keep
   incident reports to the Annex's contents.
5. **Re-calibrate thresholds** on the documented cadence, and re-run the analysis rather
   than reusing last year's number.
