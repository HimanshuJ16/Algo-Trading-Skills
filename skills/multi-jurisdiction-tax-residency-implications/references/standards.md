# Standards for Multi-Jurisdiction Tax Residency

Every rule below is jurisdiction-specific. Nothing here is a default the engine
applies on its own — each has to be registered, and checked against the text in
force, before it is relied on.

## Corporate residence and the entity tie-breaker

| Item | Position | Source |
|---|---|---|
| Current OECD tie-breaker for entities | Dual-resident non-individuals are referred to the competent authorities, who "shall endeavour to determine by mutual agreement" the single state of residence "having regard to its place of effective management, the place where it is incorporated or otherwise constituted and any other relevant factors". | OECD Model Tax Convention Art. 4(3), as replaced by the 2017 Update, item 18 |
| Consequence of no agreement | "In the absence of such agreement, such person shall not be entitled to any relief or exemption from tax provided by this Convention except to the extent and in such manner as may be agreed upon by the competent authorities." Treaty benefits are denied, not defaulted. | OECD Model Art. 4(3) (2017) |
| MLI equivalent | MLI Art. 4 carries the same rule into Covered Tax Agreements, replacing existing entity tie-breakers of every form. | MLI Explanatory Statement paras. 49–52 |
| MLI is optional | Art. 4 is **not** a minimum standard; a Party may reserve the right not to apply it at all (Art. 4(3)(a)), with further opt-outs at (b)–(d). Whether the MAP rule or the legacy rule governs is treaty-specific. | MLI Explanatory Statement para. 54 |
| Legacy tie-breaker | The pre-BEPS OECD Model and the UN Model break the tie automatically in favour of the place of effective management. Many treaties in force still read this way. | MLI Explanatory Statement para. 52 |
| UK corporate residence | UK-incorporated companies are UK resident; a foreign-incorporated company is UK resident if its central management and control — the highest form of control over its affairs, not day-to-day running — is in the UK. A company treated as solely resident elsewhere under a treaty is not UK resident for domestic purposes. | UK corporate residence rules (incorporation rule plus central management and control) |
| India corporate residence | A company is resident if it is Indian, or if its place of effective management is in India, under Income-tax Act s.6(3)(ii). CBDT Circular 6/2017 sets the guiding principles, including the "active business outside India" test (passive income not more than 50% of total income, and less than 50% of assets in India), under which POEM is presumed outside India where the majority of board meetings are held outside India. Circular 8/2017 disapplies s.6(3)(ii) for companies with turnover or gross receipts of INR 50 crore or less in a financial year. | Income-tax Act s.6(3)(ii); CBDT Circulars 6/2017 and 8/2017 |

## Individual physical presence

| Jurisdiction | Registered test | Not modelled |
|---|---|---|
| United States | Substantial Presence Test: at least 31 days in the current year **and** at least 183 weighted days, counting all days in the current year, one third of the first preceding year and one sixth of the second (IRC s.7701(b)(3)). | Exempt individuals (A/G, J/Q, F/M visa categories), the closer connection exception, Form 8843 obligations. |
| United Kingdom | Automatic UK test at 183 days in the tax year, under the Statutory Residence Test. | Automatic overseas tests (fewer than 16 days for prior residents, fewer than 46 for others, full-time work abroad), the only-home and full-time-work automatic UK tests, and the sufficient ties test, under which four ties make an individual resident on as few as 16–45 days. |
| India | Basic test at **182** days in the tax year (Income-tax Act s.6(1)). | The 60-days-plus-365-days-over-four-preceding-years rule; the 120-day rule for citizens and persons of Indian origin with Indian-sourced income above INR 1.5 million; deemed residence for citizens not liable to tax anywhere; the RNOR/ROR split. |

Because so much sits in the "not modelled" column, a negative result from the
registered test is recorded as *not a clearance*. This is a deliberate design
constraint, not an omission to be quietly filled with an approximation.

## Permanent establishment

| Item | Position | Source |
|---|---|---|
| Definition | A fixed place of business through which the business of the enterprise is wholly or partly carried on. | OECD Model Art. 5(1) |
| Human presence | Human intervention is **not** a requirement for a permanent establishment to exist; a business may be at least partly carried on without personnel. | OECD Commentary on Art. 5, e-commerce clarification |
| Servers | A website is not tangible and cannot itself be a PE. A server *can* be, where the enterprise has it at its own disposal — owns or leases and operates it — and it is sufficiently fixed. A typical ISP hosting arrangement does not put the server at the enterprise's disposal. | OECD Commentary on Art. 5, paras. 42.2–42.4 |
| Decisive question | Whether the functions performed through the equipment exceed the preparatory or auxiliary threshold, decided case by case. | OECD Commentary on Art. 5, paras. 42.7–42.9 |

## Economic substance

| Item | Position | Source |
|---|---|---|
| Cayman Islands | The International Tax Co-operation (Economic Substance) Act, in force from 1 January 2019, brings relevant activities including fund management business into scope, requiring in-scope entities to demonstrate substance or to evidence tax residence outside the Cayman Islands. | Cayman Islands International Tax Co-operation (Economic Substance) Act |

## Scope boundary

Withholding tax rates, creditability, and Foreign Tax Credit limitation are out
of scope here and are handled by
`double-taxation-treaty-considerations-cross-border-trading`. Note in particular
that the US credit limitation under IRC s.904(a) is computed on aggregate
foreign-source **taxable** income for the taxable year, per separate category
under s.904(d), with a one-year carryback and ten-year carryforward under
s.904(c) — none of which can be approximated by a per-payment calculation on
gross income.
