# UK FCA SM&CR Algorithmic Governance Standards

All citations below were checked against primary FCA / legislation sources. Verify currency before relying on them for a live compliance decision: the FCA is progressively transferring assimilated MiFID organisational requirements into the Handbook (see Handbook Notice 134 and the Markets in Financial Instruments (Transfer of MiFID Organisational Regulation) Instrument 2025).

## 1. Senior Management Functions for Algorithmic Trading

Availability of an SMF depends on the firm's SM&CR classification. A core or limited scope firm cannot appoint SMF4, SMF18, or SMF24; allocating algorithmic trading responsibility to one of those at such a firm leaves the responsibility unallocated in fact.

| SMF | Title | Availability | Typical algo-trading scope |
| :--- | :--- | :--- | :--- |
| **SMF1** | Chief Executive | Core and enhanced | Executive ownership of business units running algorithmic strategies |
| **SMF16** | Compliance Oversight | Core and enhanced | Compliance monitoring, market abuse surveillance, regulatory reporting |
| **SMF4** | Chief Risk | Enhanced / dual-regulated only (SUP 10C.6A.4R) | Second-line risk oversight; market and credit limit frameworks |
| **SMF24** | Chief Operations | Enhanced / dual-regulated only (SUP 10C.6B.2R) | First-line internal operations, technology, and IT resilience |
| **SMF18** | Other Overall Responsibility | Enhanced only | Residual allocation so no area of the firm is unowned |

Source: [FCA Handbook SUP 10C](https://handbook.fca.org.uk/handbook/sup10c); [FCA Guide for solo-regulated firms (PS19/20)](https://www.fca.org.uk/publication/policy/ps19-20.pdf).

## 2. Certification Function — scope and validity

**Scope.** [SYSC 27.8.23R](https://www.handbook.fca.org.uk/handbook/SYSC/27/8.html) makes the following an FCA certification function:

- approving the deployment of a trading algorithm or part of one, an amendment to one, or a combination of algorithms; and
- having significant responsibility for the management of monitoring whether an algorithm is or remains compliant with the firm's obligations, and deciding whether it is or remains compliant.

"Obligations" covers both regulatory obligations and the rules of connected trading venues. Writing, testing, or researching algorithm code does not by itself bring an employee into the certification regime — the approval, monitoring, or compliance-decision responsibility does. Over-certifying staff is a common and costly scoping error.

**Validity.** A certificate is valid for a maximum of 12 months from the day it is issued and cannot be drafted to last longer; a firm may issue one for a shorter period (FSMA s.63F; [SYSC 27.2](https://handbook.fca.org.uk/handbook/sysc27/sysc27s2)). Where an employee moves to a new certification function mid-period, the firm must assess F&P before they start it rather than waiting for the annual reassessment.

**F&P assessment dimensions.** Honesty, integrity and reputation; competence and capability; financial soundness. Regulatory references must be obtained and given under [SYSC 22](https://handbook.fca.org.uk/handbook/sysc22), which carries a six-year look-back on the matters at SYSC 22.2.2R(1)–(3); older information must still be disclosed where the firm assesses it as relevant to F&P.

## 3. Management Responsibilities Map

[SYSC 25.1.1R](https://www.handbook.fca.org.uk/handbook/SYSC/25/?view=chapter) requires a management responsibilities map from SMCR banking firms, SMCR insurance firms that are Solvency II firms, and enhanced scope SMCR firms only. Core and limited scope firms are not required to maintain one — producing an equivalent internal record is good practice, but presenting it as a mandatory MRM misstates the obligation.

## 4. Duty of Responsibility and "reasonable steps"

There is no FCA finalised guidance numbered FG18/9. The authoritative sources are:

- [FCA PS17/9](https://www.fca.org.uk/publication/policy/ps17-09.pdf) — Guidance on the duty of responsibility.
- [DEPP 6.2.9-A to 6.2.9-E](https://handbook.fca.org.uk/handbook/depp6) — the non-exhaustive list of considerations the FCA weighs in deciding whether a Senior Manager's steps were reasonable in all the circumstances, including whether they took reasonable steps to implement adequate systems and controls.

Evidence a Senior Manager should be able to produce for an algorithm deployment:

1. Formal review of the strategy specification, its pre-trade controls, and expected market impact.
2. Review of stress test results and the outcome of a kill functionality drill, with dates.
3. An explicit written sign-off bound to the algorithm version deployed.
4. Post-deployment monitoring and periodic governance review.

No numeric latency, throughput, or documentation-length threshold is prescribed by the FCA for any of the above. RTS 6 Article 12 requires only that the firm can cancel unexecuted orders "immediately". Any millisecond target a firm adopts is an internal standard and must be labelled as such.

## 5. Underlying algorithmic trading requirements

| Provision | Requirement |
| :--- | :--- |
| [MAR 7A](https://handbook.fca.org.uk/handbook/MAR/7A/) | FCA Handbook rules implementing MiFID article 17: systems and controls, business continuity, market making agreements, immediate notification to the FCA, and order record keeping |
| UK-assimilated RTS 6 Article 9 | Annual self-assessment and validation, reviewed by senior management |
| UK-assimilated RTS 6 Article 10 | Stress testing of algorithmic trading systems and controls |
| UK-assimilated RTS 6 Article 12 | Kill functionality — immediate cancellation of any or all unexecuted orders |
| UK-assimilated RTS 6 Article 13 | Automated surveillance to detect market manipulation |
| UK-assimilated RTS 6 Article 14 | Business continuity arrangements |
| UK-assimilated RTS 6 Article 15 | Pre-trade controls on order entry — price collars, maximum order value and volume, message limits |
| UK-assimilated RTS 6 Article 16 | Real-time monitoring |

Text: [Commission Delegated Regulation (EU) 2017/589](https://eur-lex.europa.eu/eli/reg_del/2017/589/oj/eng); UK version at [legislation.gov.uk](https://www.legislation.gov.uk/eur/2017/589).

## 6. Current FCA supervisory expectations

- [Algorithmic Trading Compliance in Wholesale Markets](https://www.fca.org.uk/publications/multi-firm-reviews/algorithmic-trading-compliance-wholesale-markets) — FCA multi-firm review, February 2018. Five focus areas including governance and oversight, and market conduct. Note this is a multi-firm review, not finalised guidance.
- [Algorithmic trading controls: high-level observations](https://www.fca.org.uk/publications/multi-firm-reviews/algorithmic-trading-controls-high-level-observations) — FCA multi-firm review, 21 August 2025. Found governance quality uneven; better-performing firms required SMF approval of algorithm changes and ran documented kill-switch drills, while many annual self-assessments were outdated or omitted areas such as IT outsourcing and training. Independent external validation of the self-assessment was identified as good practice.
