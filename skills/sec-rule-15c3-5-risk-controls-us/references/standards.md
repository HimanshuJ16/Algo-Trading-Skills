# Regulatory Coverage — sec-rule-15c3-5-risk-controls-us

Jurisdiction: **United States**. The rule binds a *broker or dealer* with market access.
It does not bind that broker-dealer's non-broker-dealer customers, and it does not travel
to other jurisdictions — see `mifid-ii-algo-trading-compliance-eu` for the EU analogue.

## Clauses cited by this skill

| Instrument | Provision | Requirement (as written) |
|---|---|---|
| 17 CFR 240.15c3-5 | (a)(1) | "Market access" means access to trading on an exchange or ATS as a result of being a member or subscriber, or access to an ATS provided by its broker-dealer operator to a non-broker-dealer. |
| 17 CFR 240.15c3-5 | (a)(2) | "Regulatory requirements" means all federal securities laws, rules and regulations, and SRO rules, applicable in connection with market access. |
| 17 CFR 240.15c3-5 | **(b)** | A broker-dealer with market access, or providing access through its MPID or otherwise, must "establish, document, and maintain a system of risk management controls and supervisory procedures reasonably designed to manage the financial, regulatory, and other risks of this business activity", and preserve the procedures and a written description of the controls per § 240.17a-4(e)(7). Carve-out: a broker-dealer routing on behalf of an exchange or ATS to reach protected quotations under Rule 611 is excepted for those routing services "except with regard to paragraph (c)(1)(ii)". |
| 17 CFR 240.15c3-5 | (c) intro | The controls "shall include the following elements". |
| 17 CFR 240.15c3-5 | (c)(1) intro | Financial controls "reasonably designed to systematically limit the financial exposure of the broker or dealer that could arise as a result of market access". |
| 17 CFR 240.15c3-5 | **(c)(1)(i)** | "Prevent the entry of orders that exceed appropriate pre-set credit or capital thresholds **in the aggregate for each customer and the broker or dealer** and, where appropriate, more finely-tuned by sector, security, or otherwise by rejecting orders if such orders would exceed the applicable credit or capital thresholds". |
| 17 CFR 240.15c3-5 | **(c)(1)(ii)** | "Prevent the entry of erroneous orders, by rejecting orders that exceed appropriate price or size parameters, **on an order-by-order basis or over a short period of time**, or that indicate duplicative orders." |
| 17 CFR 240.15c3-5 | (c)(2) intro | Regulatory controls "reasonably designed to ensure compliance with all regulatory requirements". |
| 17 CFR 240.15c3-5 | **(c)(2)(i)** | "Prevent the entry of orders unless there has been compliance with all regulatory requirements that must be satisfied on a **pre-order entry** basis". |
| 17 CFR 240.15c3-5 | **(c)(2)(ii)** | "Prevent the entry of orders for securities for a broker or dealer, customer, or other person if such person is restricted from trading those securities". |
| 17 CFR 240.15c3-5 | (c)(2)(iii) | Restrict access to trading systems and technology to persons and accounts pre-approved and authorised by the broker-dealer. *Not implemented here.* |
| 17 CFR 240.15c3-5 | (c)(2)(iv) | "Assure that appropriate surveillance personnel receive immediate post-trade execution reports that result from market access." *Not implemented here.* |
| 17 CFR 240.15c3-5 | **(d)** intro | The controls "shall be under the **direct and exclusive control** of the broker or dealer that is subject to paragraph (b)". |
| 17 CFR 240.15c3-5 | (d)(1) | Control over specific **(c)(2)** regulatory controls may be reasonably allocated, by written contract and after a thorough due diligence review, **only to a customer that is a registered broker or dealer**, where that customer has better access to the ultimate customer and its trading information. |
| 17 CFR 240.15c3-5 | (d)(2) | Any such allocation "shall not relieve" the broker-dealer of any obligation under the section, including the overall responsibility for the system of controls. |
| 17 CFR 240.15c3-5 | (e)(1) | Review of the market access business "no less frequently than annually" to assure overall effectiveness, conducted under written procedures, documented, and preserved per § 240.17a-4(e)(7) and (b). |
| 17 CFR 240.15c3-5 | (e)(2) | Annual certification by the **Chief Executive Officer** (or equivalent) that the controls comply with (b) and (c) and that the review was conducted, preserved per § 240.17a-4(b). |
| 17 CFR 242.203 (Reg SHO) | **203(b)(1)** | A broker-dealer may not accept a short sale order in an equity security unless it has (i) borrowed the security or entered a bona-fide arrangement to borrow it, or (ii) reasonable grounds to believe the security can be borrowed for delivery when due, **and** (iii) documented compliance. |
| 17 CFR 242.203 (Reg SHO) | 203(b)(2)(iii) | Exception for "short sales effected by a market maker in connection with bona-fide market making activities in the security for which this exception is claimed". |
| 17 CFR 242.200 (Reg SHO) | 200(g) | Sell orders must be marked long, short or short exempt. Upstream of this gate: a short mis-marked as a long sale never reaches the locate check. |
| FINRA Rule 4320 | — | **Not** the locate rule. "Short Sale Delivery Requirements" for *non-reporting threshold securities*: a fail-to-deliver position persisting 13 consecutive settlement days must be closed out, and until then a short sale order may not be accepted without borrowing or arranging to borrow — a pre-borrow, and stricter than a locate, but scoped to those securities. |

## Numeric thresholds: what the rule does and does not set

Rule 15c3-5 **prescribes no numeric price or size parameters, and no credit or capital
figures.** (c)(1)(ii) says "appropriate price or size parameters"; (c)(1)(i) says
"appropriate pre-set credit or capital thresholds". The adopting release leaves the
values to the broker-dealer's business model and customer base. Any "5% collar",
"$250,000 notional" or "100 messages per second" in this skill's reference implementation
is an engineering placeholder, not a regulatory threshold.

Two non-numeric obligations in the same clauses are easy to miss and change the design:

- (c)(1)(i) is aggregate **"for each customer *and* the broker or dealer"** — two limbs,
  not one.
- (c)(1)(ii) covers erroneous orders **"on an order-by-order basis *or over a short
  period of time*, or that indicate duplicative orders"** — a purely per-order gate
  implements one third of the clause.

The release also fixes *when* the controls run: they are applied "on an automated,
pre-trade basis, before orders are routed to the exchange or ATS", which "will necessarily
eliminate the practice of broker-dealers providing 'unfiltered' or 'naked' access to any
exchange or ATS". For credit thresholds specifically, compliance must be assessed "on the
basis of exposure from orders entered on an exchange or ATS, rather than relying on a
post-execution, after-the-fact determination".

## Venue-side bands are a separate control

The LULD Plan applies price bands at the venue: for a prior close above $3.00, 5% for
Tier 1 securities (S&P 500, Russell 1000 and selected ETPs) and 10% for Tier 2 (other NMS
securities); 20% for prices between $0.75 and $3.00; and the lesser of $0.15 or 75% below
$0.75, with the bands widened during the opening and closing periods specified in the
Plan's percentage-parameter table. These are enforced by the venue and pause trading; the
15c3-5 collar is enforced by the broker-dealer *before* routing. Neither discharges the
other. The tiering is also the clearest evidence that a single firm-wide collar
percentage is a poor design across a mixed universe.

## Current status (checked August 2026)

The operative text of Rule 15c3-5 is as adopted in Release No. 34-63241 (75 FR 69792,
15 November 2010), effective 14 January 2011 with a compliance date of 14 July 2011. SEC
Release No. 34-105655 (11 June 2026) proposes conforming amendments to Rule 15c3-5 in
connection with rescinding Regulation NMS Rule 611 — which paragraph (b)'s routing
carve-out references. That is a **proposal**; verify its status before relying on the
carve-out as currently drafted.

## Supervisory expectations

FINRA's 2026 Annual Regulatory Oversight Report, Market Access Rule section, records
findings including: pre-trade financial capital and credit limits set at unreasonable
thresholds for the firm's business model, with inadequate documentation of their
reasonableness; excluding certain orders from pre-trade erroneous-order controls based on
order type; inadequate oversight of intra-day changes to credit and capital thresholds,
including obtaining approval before adjusting them and ensuring temporary adjustments
revert; and failure to document the at-least-annual effectiveness review required by
(e)(1). Its effective practices include systemic pre-trade "hard" blocks and, where a
soft control is released, a separate supervisory review that the release rationale was
appropriate.

The SEC staff FAQ on the rule confirms it applies to all orders including market maker
quotes, with no exclusion for them; that it applies to all securities traded on an
exchange or ATS, including security futures; and that a purely manual order flow may be
satisfied by manual pre-order-entry controls.

## Category

`regulatory-compliance-global` — see the top-level `mappings/` directory for how this
category rolls up across the full skill library.

## Sources

- 17 CFR 240.15c3-5 — <https://www.govinfo.gov/content/pkg/CFR-2023-title17-vol4/xml/CFR-2023-title17-vol4-sec240-15c3-5.xml>
- SEC Release No. 34-63241, *Risk Management Controls for Brokers or Dealers with Market Access*, 75 FR 69792 — <https://www.govinfo.gov/content/pkg/FR-2010-11-15/html/2010-28303.htm>
- SEC Division of Trading and Markets, *Responses to Frequently Asked Questions Concerning Rule 15c3-5* — <https://www.sec.gov/rules-regulations/staff-guidance/trading-markets-frequently-asked-questions/divisionsmarketregfaq-0>
- 17 CFR 242.203 (Regulation SHO) — <https://www.law.cornell.edu/cfr/text/17/242.203>
- FINRA Rule 4320, *Short Sale Delivery Requirements* — <https://www.finra.org/rules-guidance/rulebooks/finra-rules/4320>
- FINRA 2026 Annual Regulatory Oversight Report, *Market Access Rule* — <https://www.finra.org/rules-guidance/guidance/reports/2026-finra-annual-regulatory-oversight-report/market-access-rule>
- LULD Plan percentage parameters — <https://www.luldplan.com/>
