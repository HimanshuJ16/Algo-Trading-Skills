# Standards — finra-algo-trading-registration-requirements

## What is actually mandated, and by whom

Every row below was checked against the primary source. Where a value is a firm
decision rather than a regulatory one, the row says so — do not cite this file as
authority for a threshold it calls a firm policy.

**Jurisdiction: United States. Applies to associated persons of FINRA member
broker-dealers only.** Nothing here binds a non-member proprietary trading firm,
an investment adviser, or an unaffiliated technology vendor.

| Requirement | Source | What it actually says | Status |
|---|---|---|---|
| Securities Trader registration for algo design/development/significant modification | **FINRA Rule 1220(b)(4)(A)(iii)** | Each associated person "primarily responsible for the design, development or significant modification of an algorithmic trading strategy relating to equity, preferred or convertible debt securities", or "responsible for the day-to-day supervision or direction of such activities", must register as a Securities Trader. | Mandatory, US |
| SIE + Series 57 examinations | **FINRA Rule 1220(b)(4)(B)** | A person registering as a Securities Trader on or after 1 Oct 2018 must pass the SIE and the Securities Trader (Series 57) qualification examination. A person registered as a Securities Trader before that date who maintained the registration is considered to have passed the SIE. | Mandatory, US |
| Definition of "algorithmic trading strategy" | **Regulatory Notice 16-21**, "Scope of 'Algorithmic Trading Strategy'" | "an automated system that generates or routes orders (including sending orders for routing and order-related messages, such as cancellations), but does not include an automated system that solely routes orders, in their entirety, to a market center." | Mandatory definition, US |
| Product scope | **Regulatory Notice 16-21** | Covered systems generate or route orders "in any equity security (including options), preferred security or convertible debt security, whether sent to an exchange or handled over the counter." | Mandatory, US |
| Excluded: pure pass-through routers and idea-only engines | **Regulatory Notice 16-21** | "a standard order router that routes retail orders in their entirety to a particular market center for handling and execution is not covered"; an algorithm that "solely generates trading ideas or investment allocations ... but that is not equipped to automatically generate orders or order-related messages" is likewise not covered. | Mandatory, US |
| Definition of "significant modification" | **Regulatory Notice 16-21**, endnote 3 | "generally would be any change to the code of the algorithm that impacts the logic and functioning of the trading strategy employed by the algorithm." A data feed/vendor change generally is not; a change to a benchmark such as an index generally is. | Guidance, US — the firm maps it to its own repository |
| Not every contributor registers | **Regulatory Notice 16-21**, endnotes 4–5 | "FINRA does not intend that the registration requirement apply to every associated person who touches or otherwise is involved in the design or development of a trading algorithm." Integrating the algorithm into the firm's technological infrastructure and testing linkages "would not be required to be performed by a Securities Trader". A junior developer under a lead "presumably is not 'primarily' responsible". | Mandatory scope limit, US |
| Third-party and off-the-shelf algorithms | **Regulatory Notice 16-21**, "Third-Party Algorithms" | Design/development performed solely by a third party does not trigger registration for the firm's own activities, but the associated person **directing** a third party's design, development or significant modification must be a Securities Trader, as must the person making in-house significant modifications. "even where a firm purchases an algorithm off-the-shelf and does not significantly modify the algorithm, the associated person responsible for monitoring or reviewing the performance of the algorithm must be a Securities Trader." | Mandatory, US |
| Effective date | **Regulatory Notice 16-21** | Voluntary registration from 6 Jun 2016; mandatory from **30 January 2017** under NASD Rule 1032(f) (SEC approval: Rel. No. 34-77551, 7 Apr 2016, SR-FINRA-2016-007). Carried into FINRA Rule 1220 on 1 Oct 2018 (Regulatory Notice 17-30). | Historical fact |
| Supervisory assignment of the developer | **FINRA Rule 3110(a)(2), (a)(5)**, as applied in **Regulatory Notice 16-21** | Each registered person is assigned to an appropriately registered representative or principal. A lead developer may be assigned to a Securities Trader Principal, to a Securities Trader, or to more than one registered person "provided that the supervisor responsible for the lead algorithm developer's activities requiring registration as a Securities Trader is registered as a Securities Trader or Securities Trader Principal." | Mandatory, US |
| Securities Trader Principal | **FINRA Rule 1220(a)(7)** | A principal supervising those securities trading activities registers as a Securities Trader Principal: Securities Trader registration **plus** the General Securities Principal (Series 24) examination. | Mandatory, US |
| Continuing education / CE inactive | **FINRA Rule 1240(a)** | The Regulatory Element must be completed annually by 31 December. A person who does not is CE inactive and "shall cease all activities as a registered person and is prohibited from performing any duties and functioning in any capacity requiring registration." After two consecutive years of CE inactivity FINRA administratively terminates the registration. | Mandatory, US |
| Registration lapse and SIE expiry | **FINRA Rule 1210.08** | A person last registered as a representative two or more years before a new application must re-pass the representative examination; the SIE result expires after four years, subject to the Maintaining Qualifications Program under Rule 1240(c). | Mandatory, US |
| Record retention of the audit trail | **FINRA Rule 4511(b)** and **4511(c)** | Books and records for which no retention period is specified elsewhere are preserved for at least six years; records must be kept in a format and media complying with SEA Rule 17a-4. | Mandatory, US |
| Change management around algorithmic strategies | **Regulatory Notice 15-09** | Firms should implement "a change management process that tracks the development of new trading code or material changes to existing code", including review of test results and approval protocols appropriate to the scope of the change. Notice 16-21 endnote 3 applies this **even where a modification is not significant**. | Guidance, US |

## Not found — do not claim it

- **No FINRA rule sets a code-review quorum, a four-eyes requirement, or a
  prohibition on self-approval for algorithm changes.** Notice 16-21's own example
  contemplates a single associated person designing, coding and modifying an
  algorithm alone. The `block_self_approval` control in the reference
  implementation is firm supervisory policy reached through Rule 3110(a)(5)
  assignment and the Notice 15-09 approval protocol — it is on by default because
  it is good practice, not because a rule compels it.
- **No FINRA rule requires a CI/CD deployment gate.** Blocking a build is one
  reasonable implementation of the Rule 3110 supervisory system; the rules
  require supervision and registration, not a particular pipeline design.
- **FINRA publishes no list mapping repositories, services or job titles to the
  registration requirement.** "Primarily responsible" is a firm determination
  documented per algorithm.
- **No six-year retention period is specified for this record type in
  particular.** Six years is the Rule 4511(b) default that applies because no
  more specific period does; associated-person records may separately be subject
  to SEA Rule 17a-4(e)(1).

## Primary sources

| Document | Locator |
|---|---|
| FINRA Rule 1220 — Registration Categories | https://www.finra.org/rules-guidance/rulebooks/finra-rules/1220 |
| FINRA Rule 1210 — Registration Requirements (incl. Supplementary Material .08) | https://www.finra.org/rules-guidance/rulebooks/finra-rules/1210 |
| FINRA Rule 1240 — Continuing Education Requirements | https://www.finra.org/rules-guidance/rulebooks/finra-rules/1240 |
| FINRA Rule 3110 — Supervision | https://www.finra.org/rules-guidance/rulebooks/finra-rules/3110 |
| FINRA Rule 4511 — General Requirements (books and records) | https://www.finra.org/rules-guidance/rulebooks/finra-rules/4511 |
| Regulatory Notice 16-21 — Qualification and Registration of Associated Persons Relating to Algorithmic Trading (June 2016) | https://www.finra.org/rules-guidance/notices/16-21 |
| Regulatory Notice 15-09 — Guidance on Effective Supervision and Control Practices for Firms Engaging in Algorithmic Trading Strategies (March 2015) | https://www.finra.org/rules-guidance/notices/15-09 |
| Regulatory Notice 17-30 — SEC Approval of Consolidated Registration Rules (Oct 2018 effective date) | https://www.finra.org/rules-guidance/notices/17-30 |
| SEC Order Approving SR-FINRA-2016-007, Rel. No. 34-77551 (7 Apr 2016), 81 FR 21914 | https://www.sec.gov/files/rules/sro/finra/2016/34-77551.pdf |
