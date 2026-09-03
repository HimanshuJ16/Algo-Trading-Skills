# Standards for Strategy Committee Governance for Capital Allocation Decisions

## The thresholds in this skill are house defaults, not standards

An earlier version of this file presented the three numbers below in a column
headed **"Mandatory Standard"**, with no source. That heading was wrong. No
regulator, exchange or standards body mandates a committee quorum, a voting rule,
a Chief Risk Officer veto, or a maximum share of fund AUM per strategy. They are
starting values chosen for this engine.

| Rule | Default | What it actually is |
|---|---|---|
| `quorum_percentage` | $\ge 50\%$ of the roster participating | House heuristic. Quorum comes from the fund's own governing documents — the LPA, the committee charter, the IPS. The common charter convention is a **majority** of members; an inclusive $\ge 50\%$ is one seat weaker than that on an even-sized committee. |
| Majority rule | `FOR > AGAINST`, abstentions excluded, plus `min_votes_for` | House heuristic. Presence-based quorum with a majority of votes *cast* is a common convention, not a requirement. On a 4-seat committee at the default settings, two participants and a single FOR vote carry a proposal — raise `min_votes_for` if that is not your charter. |
| `max_single_strategy_aum_pct` | $20\%$ | House heuristic. **Not** UCITS, **not** AIFMD, **not** any regulator's number — see "The 20% number has no regulatory parent" below. |
| CRO veto | On, and an AGAINST from a veto-holder counts as one | A **charter choice**, not a legal power. See "No instrument grants a CRO a veto" below. |

Calibrate these to your fund's mandate and record what you used. The engine embeds
`policy_applied` in every decision for exactly this reason: a committee of one at a
0% quorum emits the same `APPROVED` string as a full board.

## The 20% number has no regulatory parent

The EU concentration limits most often misremembered as the source of a
single-strategy cap are the UCITS "5/10/40" rules in Directive 2009/65/EC
**Article 52**. They limit exposure **per issuing body**, not the share of a fund
allocated to a trading strategy: no more than 5% of assets in transferable
securities or money market instruments issued by the same body, which Member
States may raise to 10% provided the holdings above 5% do not in aggregate exceed
40% of assets. A strategy is not an issuer. Nothing in Article 52 constrains how a
multi-strategy fund splits capital between its own books.

If your fund's own mandate, LPA or IPS sets a per-strategy cap, that number — not
this default — is the one to configure.

## No instrument grants a CRO a veto

What regulation actually requires of the risk function is **independence** and
**escalation**, not a blocking vote:

| Instrument | Jurisdiction & scope | Status | What it requires | Relevance here |
|---|---|---|---|---|
| **AIFMD** — Directive 2011/61/EU, **Art. 15(1)** | EU alternative investment fund managers; assimilated into UK law | Binding | AIFMs must "functionally and hierarchically separate the functions of risk management from the operating units, including from the functions of portfolio management". Competent authorities review the separation under the principle of proportionality, and the AIFM must in any event demonstrate specific safeguards against conflicts of interest allowing the independent performance of risk management. | The real basis for giving the risk seat a distinct standing on the committee. It requires *independence*; it does **not** grant a veto. Whether the risk officer's dissent blocks a capital allocation is a charter decision — which is why the engine exposes it as `veto_holder_against_counts_as_veto` rather than hard-coding it. |
| **Commission Delegated Regulation (EU) 231/2013, Art. 42** | As above | Binding | Sets the conditions under which the separation in AIFMD Art. 15 is regarded as satisfied: risk staff are not supervised by those running the operating units, are not engaged in operating-unit activities, and are remunerated against the objectives of the risk function independently of the operating units' performance. The separation is to be ensured throughout the hierarchy, up to the governing body. | If the person casting the veto reports to the PM whose allocation is being voted on, the veto is decoration. This is the provision that says so. |
| **Commission Delegated Regulation (EU) 231/2013, Art. 39** | As above | Binding | Requires a permanent risk management function with the authority and access needed to do its job, and requires it to give senior management regular updates on the current level of risk and on **actual or foreseeable breaches** of the risk limits set under Art. 44, so that prompt and appropriate action can be taken. | The escalation obligation the `REJECTED_RISK_BREACH` and `POST_DECISION_ALLOCATION_STILL_ABOVE_CAP` records feed. Note the direction: the risk function *reports* a breach; it is not given a unilateral power to stop it. |
| **Commission Delegated Regulation (EU) 231/2013, Art. 44** | As above | Binding | The AIFM must set and implement quantitative and/or qualitative risk limits for each AIF it manages, covering all relevant risks and taking into account the strategies and assets employed, aligned with the risk profile disclosed to investors under AIFMD Art. 23(4)(c). Where only qualitative limits are used, the AIFM must be able to justify that to its competent authority. | This is the obligation `max_single_strategy_aum_pct` is an instance of. Art. 44 requires that limits **exist, are documented and are consistent with what investors were told** — it prescribes no number, and 20% is not in it. |
| **UCITS** — Directive 2009/65/EC, **Art. 52** | EU UCITS funds | Binding | The 5/10/40 issuer-concentration rules, plus separate limits for deposits and counterparty exposure. | Cited here only to correct the common misattribution above: it is a **per-issuer** rule, not a per-strategy allocation cap. |
| **12 CFR 252.33** (Federal Reserve, Regulation YY) | US bank holding companies with total consolidated assets **of \$100 billion or more**. Not funds, not advisers, not proprietary trading firms | Binding rule | An independent risk committee of the board that "approves and periodically reviews the risk-management policies" of global operations, must "[m]eet at least quarterly ... and fully document and maintain records of its proceedings", and receives quarterly reports from a chief risk officer who "must report directly to both the risk committee and chief executive officer" and oversees "establishment of risk limits on an enterprise-wide basis and the monitoring of compliance". | The closest thing in binding US rulemaking to the committee-plus-CRO structure this skill models — and a useful check on it. Even here the CRO's power is to **report and escalate**, not to veto, and the documented-minutes duty is on the committee. **Applicability caveat**: an asset manager or fund cannot be "compliant" or "non-compliant" with Regulation YY. |
| **17 CFR 275.206(4)-7** (Advisers Act compliance rule) | SEC-registered investment advisers | Binding rule | Written policies and procedures reasonably designed to prevent violations of the Advisers Act, reviewed at least annually, with a designated chief compliance officer. | A documented capital-allocation governance process is the kind of policy the rule expects an adviser to have and follow. **Currency note**: the 2023 Private Fund Adviser Rules amendment that would have required the annual review to be **documented in writing** was vacated in full by the Fifth Circuit on 5 June 2024 (*National Association of Private Fund Managers v. SEC*, No. 23-60471), and the SEC published conforming technical amendments in November 2024. Do not cite that written-documentation requirement as current. |
| **17 CFR 275.204-2(e)(1)** (Advisers Act books and records) | SEC-registered investment advisers | Binding rule | Required records must be "maintained and preserved in an easily accessible place for a period of not less than five years from the end of the fiscal year during which the last entry was made", the first two years "in an appropriate office of the investment adviser". | The retention floor for the decision records this engine produces, for advisers in scope. The engine persists nothing itself. |

## What none of the above requires

- A committee **quorum** of any size.
- A **voting rule** — majority, supermajority, or otherwise.
- A **veto** for the Chief Risk Officer or anyone else.
- A **maximum share of fund AUM per strategy**.

Those four are yours to set, in your governing documents, and yours to defend.

## Sourcing note

EUR-Lex and legislation.gov.uk were not retrievable through this review's fetch
tooling, so the EU provisions above are **summarised with article numbers given
for verification**, not reproduced verbatim; the quoted phrases are the ones
carried consistently across the sources listed below. The 12 CFR and 17 CFR
passages in quotation marks were read from the Cornell LII texts linked below.

## Sources

- [Directive 2011/61/EU (AIFMD), Art. 15](https://eur-lex.europa.eu/eli/dir/2011/61/oj/eng) — risk management; functional and hierarchical separation
- [Commission Delegated Regulation (EU) No 231/2013](https://eur-lex.europa.eu/eli/reg_del/2013/231/oj/eng) — Arts. 39, 42, 44
- [Directive 2009/65/EC (UCITS), Art. 52](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX%3A32009L0065) — 5/10/40 issuer concentration
- [12 CFR § 252.33](https://www.law.cornell.edu/cfr/text/12/252.33) — risk committee and chief risk officer requirements
- [17 CFR § 275.204-2](https://www.law.cornell.edu/cfr/text/17/275.204-2) — books and records; (e)(1) retention
- [SEC, Private Fund Advisers](https://www.sec.gov/investment/private-fund-advisers) — status of the vacated 2023 rules
- [Morgan Lewis, "Fifth Circuit Vacates SEC Private Fund Adviser Rules in Full"](https://www.morganlewis.com/pubs/2024/06/fifth-circuit-vacates-sec-private-fund-adviser-rules-in-full) — 5 June 2024, No. 23-60471
