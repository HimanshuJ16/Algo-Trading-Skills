# US SEC Regulation SHO — Short Sale Standards

Every citation below was checked against the regulatory text or SEC/FINRA primary sources.
Regulation SHO has been amended repeatedly since 2004 and the settlement cycle it references
changed in 2024; confirm currency before relying on any of it for a live compliance decision.

## 1. Rules matrix (17 CFR 242.200-204)

| Rule | Mandate | What it actually requires | Where this skill enforces it |
| :--- | :--- | :--- | :--- |
| **200(g)** | Order marking | Every sell order in an equity security is marked `long`, `short`, or `short exempt`. `long` only if the seller owns the security *and* it is, or will be by settlement, in the seller's physical possession or control. `short exempt` only where 242.201(c) or (d) is met. | `validate_order_intent()` — marking validated, `ShortExemptReason` required for `SHORT_EXEMPT` |
| **203(b)(1)** | Locate | Before accepting or effecting a short sale the broker-dealer must have borrowed the security, entered into a bona fide arrangement to borrow it, or have reasonable grounds to believe it can be borrowed and delivered when due — **and** have documented compliance under (b)(1)(iii). | `locate_id` verification and reservation |
| **203(b)(2)** | Locate exceptions | (i) a broker-dealer accepting a short sale order from another registered broker-dealer that is itself required to comply; (ii) a sale of a security the person is deemed to own under 242.200, where the broker has been reasonably informed of intent to deliver; (iii) short sales by a market maker in connection with **bona fide market making**. | **Not implemented** — a claimed exception is a documented firm decision |
| **201(b)(1)(i)** | Price test | While in force, prevent execution or display of a short sale order in a covered security at a price **less than or equal to** the current national best bid. | `_check_rule_201()` |
| **201(b)(3)** | Trigger determination | The **listing market** determines whether the price declined 10% or more from the prior day's close and, if so, immediately makes that available under 242.603(b). | `trigger_rule_201_ssr(source=...)`; `evaluate_local_trigger()` is advisory |
| **204** | Close-out of fails | Clearing-participant obligation on settled fails — see §4. | **Out of scope** for a pre-trade gate |

Sources: [17 CFR 242.200](https://www.law.cornell.edu/cfr/text/17/242.200),
[242.201](https://www.law.cornell.edu/cfr/text/17/242.201),
[242.203](https://www.law.cornell.edu/cfr/text/17/242.203),
[242.204](https://www.law.cornell.edu/cfr/text/17/242.204).

## 2. Rule 201 — the short sale price test

**Scope.** A *covered security* is "any NMS stock as defined in § 242.600(b)(65)". *Listing
market* carries the meaning of "primary listing exchange" in § 242.600(b)(79). *National best
bid* is § 242.600(b)(60). The price test therefore does not reach non-NMS equities, even though
Rule 200(g) marking and Rule 203(b)(1) locates do.

**Trigger.** 242.201(b)(1)(i): the restriction applies "if the price of that covered security
decreases by 10% or more from the covered security's closing price as determined by the listing
market for the covered security as of the end of regular trading hours on the prior day."

$$P_{\text{trigger}} \le 0.90 \times P_{\text{prior close}}$$

The determination is the **listing market's** under 242.201(b)(3), not the trading firm's. A
firm consumes the SIP Reg SHO price test indicator; the UTP SIP disseminates a dedicated Reg
SHO price test message for this purpose. A locally computed decline is a monitoring signal only.

**Duration.** 242.201(b)(1)(ii): the requirement applies "for the remainder of the day and the
following day when a national best bid for the covered security is calculated and disseminated
on a current and continuing basis pursuant to an effective national market system plan." The
circuit breaker can only be *triggered* during regular trading hours, but once triggered the
restriction applies whenever a national best bid is being disseminated.

**The test.**

- `SHORT`: compliant only where $P_{\text{order}} > \text{NBB}$. At or below the bid is a violation.
- Where no current national best bid exists, the test has no reference point. This engine
  rejects rather than passing, because in practice a zero or absent bid on the order path is a
  data fault, not a genuine absence of quotations.

**Two carve-outs inside 201(b)(1)(iii).**

- **(A) Displayed order.** A trading center may execute a displayed short sale order that was
  priced above the current national best bid *at the time of initial display*, even if the bid
  has since risen. This engine gates order *intents*, not the lifecycle of a resting displayed
  order, so it does not model (A) — a venue-side or OMS-side control does.
- **(B) Short exempt.** A short sale order marked "short exempt" may be executed or displayed
  "without regard to whether the order is at a price that is less than or equal to the current
  national best bid."

## 3. When "short exempt" is permissible

Rule 200(g)(2) permits the marking **only if** 242.201(c) or (d) is satisfied. The engine's
`ShortExemptReason` enumerates exactly these bases:

| Basis | Provision | Note |
| :--- | :--- | :--- |
| Priced above the NBB at submission | 201(c) | Requires written policies reasonably designed to prevent incorrect identification, and regular surveillance of their effectiveness. The engine verifies the claim against the bid rather than trusting it. |
| Seller's delay in delivery | 201(d)(1) | Seller deemed to own the security under 242.200 and intends to deliver once restrictions on delivery are removed |
| Odd lot, market maker | 201(d)(2) | To offset customer odd-lot orders or liquidate an odd-lot position changing the position by no more than a unit of trading |
| Domestic arbitrage | 201(d)(3) | Good faith account of a person entitled to acquire an equivalent number of securities via a convertible or exchangeable security |
| International arbitrage | 201(d)(4) | Profiting from a current price difference between a foreign market and a US market |
| Over-allotment / lay-off | 201(d)(5) | Underwriter or syndicate member in connection with an over-allotment, or a lay-off sale |
| Riskless principal | 201(d)(6) | Customer order received before the offsetting transaction; offset allocated within 60 seconds; supervisory systems producing the records |
| VWAP | 201(d)(7) | Subject to the conditions in the provision, including reporting with a VWAP trade modifier. Not limited to pre-open matched VWAP trades. A broker-dealer relying on it must be able to demonstrate the reliance was reasonable. |

**Bona fide market making is not on this list.** It is a Rule 203(b)(2)(iii) *locate* exception.
Marking an order "short exempt" on a market-making rationale misstates the basis.

**Short exempt does not relieve the locate requirement.** SEC Division of Trading and Markets
staff answer "No" to whether the short exempt marking may be used for an order that qualifies
for an exception from Rule 203's locate requirement, and confirm that an order marked short
exempt under 201(c) must still comply with 203(b)(1). Source:
[Responses to Frequently Asked Questions Concerning Rule 201 of Regulation SHO](https://www.sec.gov/rules-regulations/staff-guidance/trading-markets-frequently-asked-questions-7).

## 4. Rule 204 close-out deadlines (context, not enforced here)

A clearing participant with a fail-to-deliver position must close it out by purchasing or
borrowing securities of like kind and quantity:

| Situation | Deadline (17 CFR 242.204) |
| :--- | :--- |
| General | Beginning of regular trading hours on the settlement day **following** the settlement date — (a) |
| Fail attributable to a **long** sale | Beginning of regular trading hours on the **third** consecutive settlement day following the settlement date — (a)(1) |
| Fail attributable to **bona fide market making** | Beginning of regular trading hours on the **third** consecutive settlement day following the settlement date — (a)(3) |
| Sale of a security the person is deemed to own under 242.200 but has not delivered | Beginning of regular trading hours on the **thirty-fifth consecutive calendar day** following the trade date — (a)(2) |

Failure to close out triggers the 242.204(b) "penalty box": the participant and any
broker-dealer for which it clears may not accept a short sale order in that security without
first borrowing it, or entering into a bona fide arrangement to borrow it, until the fail is
closed out and the purchase clears and settles.

Separately, 242.203(b)(3) requires a participant with a fail-to-deliver position in a
**threshold security** for 13 consecutive settlement days to close it out immediately.

These deadlines are expressed relative to the **settlement date**, which since **28 May 2024**
is T+1 under the amendments to Exchange Act Rule 15c6-1(a). Any material stating a T+3 or T+5
close-out deadline predates both the current rule text and the current settlement cycle.

## 5. Locate documentation and retention

Rule 203(b)(1)(iii) requires the broker-dealer to have **documented compliance** with the locate
requirement. Regulation SHO itself prescribes no retention period. The applicable periods come
from the recordkeeping regime:

- [SEA Rule 17a-4](https://www.law.cornell.edu/cfr/text/17/240.17a-4) sets category-specific
  periods (commonly three or six years, the first two in an easily accessible place) and the
  format and preservation standards.
- [FINRA Rule 4511(b)](https://www.finra.org/rules-guidance/rulebooks/finra-rules/4511):
  members "shall preserve for a period of at least six years those FINRA books and records for
  which there is no specified period under the FINRA rules or applicable Exchange Act rules."
  4511(c) requires the format to comply with SEA Rule 17a-4.

Confirm with compliance which category each artifact falls into rather than assuming a single
number covers locate documentation, order markings, and price test decisions alike.

## 6. Locate reuse

SEC Reg SHO FAQ 4.4 permits reapplying a locate to a subsequent short sale after an intraday
buy-to-cover where the subsequent order is for a quantity no greater than the original locate
and the original locate was good for the entire trading day. FINRA has identified as a
deficiency firms relying on that guidance **without confirming the security is neither a
threshold security nor hard to borrow**, where the reuse is not available. Source:
[FINRA — Regulation SHO, bona fide market making exemptions and reuse of locates](https://www.finra.org/rules-guidance/guidance/reports/2023-finras-examination-and-risk-monitoring-program/regulation-sho).

FINRA has separately flagged firms treating ineligible proprietary trading as bona fide market
making — quoting only at maximum permissible distances from the inside, quoting on one side
only, or quoting only when holding customer orders.

## 7. Locate pool accounting

For a locate record $L_i$ with allocated quantity $Q_{\text{allocated}}$ and a set $R$ of
outstanding, unreleased reservations:

$$Q_{\text{remaining}} = Q_{\text{allocated}} - \sum_{r \in R} Q_r$$

An order of size $Q_{\text{order}}$ may be approved only where

$$Q_{\text{order}} \le Q_{\text{remaining}} \quad \text{and} \quad T_{\text{now}} \le T_{\text{expires}}$$

with $Q_{\text{order}}$ constrained to a positive integer. A reservation released after a
cancel or venue rejection is removed from $R$; the released order ID cannot re-reserve.
