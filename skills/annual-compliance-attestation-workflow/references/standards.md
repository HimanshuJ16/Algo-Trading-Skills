# Standards for Annual Compliance Attestation

Jurisdiction: **United States only.** Every provision below binds either an SEC-registered
investment adviser or a FINRA member broker-dealer. None of it travels to another regime —
see `mifid-ii-algo-trading-compliance-eu` and `uk-fca-algorithmic-trading-systems-controls`.

## Provisions the engine cites

| Regulator | Provision | Requirement (as written) | Retention |
|---|---|---|---|
| **SEC** | Rule 206(4)-7(a) | Adopt and implement written policies and procedures reasonably designed to prevent violation of the Advisers Act and the rules thereunder. | Copy of the policies retained 5 years per Rule 204-2(a)(17)(i). |
| **SEC** | Rule 206(4)-7(b) | "Review, no less frequently than annually, the adequacy of the policies and procedures established pursuant to this section and the effectiveness of their implementation." **No writing requirement appears in the current rule text** — see the vacatur note below. | See 204-2(a)(17)(ii). |
| **SEC** | Rule 206(4)-7(c) | Designate an individual (a supervised person) responsible for administering the policies and procedures — the CCO. Note this is a *designation*, not a standalone "CCO annual report." | — |
| **SEC** | Rule 204-2(a)(17)(ii) | "Any records documenting the investment adviser's annual review of those policies and procedures conducted pursuant to § 275.206(4)-7(b)." | 5 years from the end of the fiscal year of the last entry, the first two "in an appropriate office of the investment adviser" (Rule 204-2(e)(1)). |
| **FINRA** | Rule 3130(b) | The member's CEO certifies annually, per the paragraph (c) text, that the firm has processes to establish, maintain, review, test and modify its written compliance policies and WSPs, and that the CEO "has conducted one or more meetings with the chief compliance officer(s) in the preceding 12 months to discuss such processes." | 3–6 years by record type per 17a-4. |
| **FINRA** | Rule 3130(b), footnote 1 | "Members must ensure that each ensuing annual certification is **effected no later than on the anniversary date of the previous year's certification**." This constrains the *certification*, not the CEO-CCO meeting. | — |
| **FINRA** | Rule 3130(c)(2) | Certification item 2: the CEO "has/have conducted one or more meetings with the chief compliance officer(s) in the preceding 12 months, the subject of which satisfy the obligations set forth in FINRA Rule 3130." | — |
| **FINRA** | Rule 3130(c)(3) | Certification item 3: the processes "are evidenced in a report reviewed by" the CEO, CCO and such other officers as needed. "The final report has been submitted to the Member's board of directors and audit committee **or will be submitted** to the Member's board of directors and audit committee (or equivalent bodies) **at the earlier of their next scheduled meetings or within 45 days of the date of execution of this certification**." | 3–6 years per 17a-4. |
| **FINRA** | Rule 3120(a) | Designated principals "establish, maintain, and enforce a system of supervisory control policies and procedures that: (1) test and verify that the member's supervisory procedures are reasonably designed ... and (2) create additional or amend supervisory procedures where the need is identified by such testing and verification", and "submit to the member's senior management **no less than annually**, a report detailing" that system. | 3–6 years per 17a-4. |
| **FINRA** | Rule 3120(b) | For a member that reported $200 million or more in gross revenue in the preceding calendar year, the report must additionally include a tabulation of customer-complaint and internal-investigation reports made to FINRA and a discussion of the preceding year's compliance efforts. **Not modelled by the engine.** | — |
| **SEC** | Rule 15c3-5(b) | Binds "a broker or dealer with market access, or that provides a customer or any other person with access to an exchange or alternative trading system through use of its market participant identifier or otherwise". A BD without market access is not an addressee — hence `has_market_access`. | — |
| **SEC** | Rule 15c3-5(e)(1) | The broker-dealer "shall review, no less frequently than annually, the business activity of the broker or dealer in connection with market access" and the effectiveness of the controls, under written procedures. | 3–6 years per 17a-4. |
| **SEC** | Rule 15c3-5(e)(2) | The CEO "shall certify" annually "that such risk management controls and supervisory procedures comply with paragraphs (b) and (c) of this section" — an act separate from the (e)(1) review. | 3–6 years per 17a-4. |
| **SEC** | Rule 17a-4(f) | Electronic recordkeeping must satisfy either WORM or the audit-trail alternative (below). | BD records 3–6 years by type. |
| **SEC / FINRA exams** | Quant focus | Firms running algorithmic strategies are expected to document risk controls, code integrity testing and electronic trade surveillance. This is an **examination expectation**, not a numbered rule; the engine gates on it as a firm control. | Per underlying rule. |

## Provisions the engine does NOT model

- **Rule 3130(c)(3), "next scheduled meetings" limb.** The engine has no board calendar, so it
  checks only the 45-day limb. If the board or audit committee meets sooner, that meeting is the
  operative deadline and a passing `REQ_FINRA_3130_C3_BOARD_SUBMISSION` check proves nothing.
- **Rule 3130 Supplementary Material .04** ("Content of Meetings Between Chief Executive Officer
  and Chief Compliance Officer") and .10 (content of the report documenting processes). Note in
  particular that **.04 is not the source of the 45-day board deadline** — that is (c)(3). Any
  tool or manual citing "FINRA 3130.04" for board submission is misciting the rule.
- **Rule 3130 Supplementary Material .09** (members without a board of directors or audit
  committee). The engine requires `board_submission_date` for every broker-dealer, so a member
  with neither body would be **falsely blocked** on `REQ_FINRA_3130_C3_BOARD_SUBMISSION`. Read
  .09 against the member's actual governance structure and record the equivalent body's
  submission date rather than defeating the gate.
- **Rule 3120(b)** gross-revenue-triggered report contents.
- **Whether the reviewed controls are adequate.** The engine records that reviews and
  certifications happened. Adequacy of the 15c3-5 controls themselves is
  `sec-rule-15c3-5-risk-controls-us`.

## SEC Rule 206(4)-7 — the vacated written-documentation amendment

The SEC's August 2023 Private Fund Adviser Rules release amended Rule 206(4)-7(b) to require
that the annual review be **documented in writing**, and the amendment applied to all registered
advisers, not only private fund advisers. On **5 June 2024** the Fifth Circuit vacated the
Private Fund Adviser Rules in their entirety in *National Association of Private Fund Managers
v. SEC*. The SEC's own announcement confirms that "amendments to rules that were in effect
before the Final Rules were adopted have also been vacated", listing § 275.206(4)-7 among them.

Consequence for this skill: **current Rule 206(4)-7(b) contains no writing mandate.** The
engine's `annual_review_documentation_date` gate is therefore attributed to Rule
204-2(a)(17)(ii) — which requires preservation of "any records documenting" the annual review —
together with long-standing examiner expectations. It remains a defensible firm control and a
practical exam-readiness requirement, but a compliance manual must not tell a client that
206(4)-7 obliges a written review.

## SEC Rule 17a-4(f) electronic-recordkeeping audit-trail attributes

Under the 2022 amendments (SEC Release No. 34-96034, adopted 12 October 2022; 87 FR 66412,
3 November 2022; effective 3 January 2023; compliance date for the Rule 17a-4 amendments
3 May 2023, and 3 November 2023 for the Rule 18a-6 amendments), BDs may preserve electronic
records via EITHER:

1. **WORM (Write Once Read Many) storage**, OR
2. **An audit-trail alternative**: an electronic recordkeeping system maintaining a complete
   time-stamped audit trail that permits recreation of an original record if it is altered,
   over-written or erased.

The audit-trail alternative requires the system to preserve the original content, the date and
time of any creation/modification/deletion, the identity of the actor, and the ability to
recreate the original record. Annual attestation evidence archived under either method
satisfies 17a-4.

## SEC 206(4)-7 annual review scope caveat

The annual review is not a single date. Examiners scrutinise nine dimensions:

1. Who conducted the review
2. What was reviewed (scope)
3. When the review was conducted
4. How the review was conducted (methodology)
5. Findings identified
6. Recommendations made
7. Implementation status of prior recommendations
8. Documentation of the review
9. Senior-management sign-off

A review that "happened" but produced no documented findings and no senior-management sign-off
should be treated as a deficiency by the CCO, even though the engine models only the
completion and documentation dates. **A passing engine verdict is necessary but not
sufficient.**

## FINRA Rule 4530 self-reporting

FINRA Rule 4530(b) requires a member to "promptly report to FINRA, but in any event not later
than 30 calendar days, after the member has concluded or reasonably should have concluded" that
the member or an associated person has violated a securities-, insurance-, commodities-,
financial- or investment-related law, rule, regulation or standard of conduct.

Two qualifications matter before treating a missed certification as automatically reportable:

- The trigger is the firm **concluding (or reasonably being able to conclude) that a violation
  occurred**, not the mere discovery of a gap.
- Rule 4530.01 sets the threshold for a firm's own violations: reportable conduct is that which
  "has widespread or potential widespread impact to the member, its customers or the markets",
  or that arises from "a material failure of the member's systems, policies or practices
  involving numerous customers, multiple errors or significant dollar amounts" (see FINRA
  Regulatory Notice 11-32). Not every violation is reportable.

Whether a specific missed deadline clears that threshold is a legal judgment for the CCO and
counsel — the engine does not and cannot make it. Late filings appear on the firm's 4530
Disclosure Timeliness Report Card. See `references/workflows.md` for the recovery path.

## Sources consulted

- 17 CFR 275.206(4)-7 and 17 CFR 275.204-2 (Advisers Act rules), current text.
- 17 CFR 240.15c3-5 (Market Access Rule), current text.
- FINRA Rule 3130 (Annual Certification of Compliance and Supervisory Processes), including
  paragraph (c) certification text, footnote 1 to (b), and Supplementary Material .01–.10.
- FINRA Rule 3120 (Supervisory Control System) and FINRA Rule 4530 with Supplementary Material
  .01; FINRA Regulatory Notice 11-32.
- SEC Release No. 34-96034, *Electronic Recordkeeping Requirements for Broker-Dealers,
  Security-Based Swap Dealers, and Major Security-Based Swap Participants* (87 FR 66412).
- SEC, *Announcement Regarding Private Fund Advisers Rules* (following *National Association of
  Private Fund Managers v. SEC*, 5th Cir., 5 June 2024).
