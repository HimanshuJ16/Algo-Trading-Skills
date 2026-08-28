# Standards for Regulatory Sandbox Programs for Fintech Testing

## No regulator publishes universal numeric caps

Every framework below sets the test's limits **per applicant**, in the approval letter
or the agreed "boundary conditions" / "testing parameters" document. Any cross-firm
numeric table of client, volume or AUM caps would be fabricated. Register the values
from your own approval; leave the program unregistered otherwise.

| Framework | Regulator | Live customers | How limits are set | Published duration facts |
|---|---|---|---|---|
| FCA Regulatory Sandbox (UK) | Financial Conduct Authority | Yes, under restricted authorisation | FCA "will set a strict limit to the size of the test"; the testing plan must state its own "Testing parameters (duration, customer/transaction limit)", customer safeguards, risk assessment and exit strategy. Applications are accepted on a rolling basis. | No statutory maximum. The FCA records that most stakeholders considered **3–6 months** an appropriate testing duration. |
| MAS FinTech Regulatory Sandbox (SG) | Monetary Authority of Singapore | Yes | Boundary conditions agreed per experiment; they "should clearly define the start and end date", limits on customer type and **number of customers**, "other quantifiable limits such as transaction thresholds or cash holding limits", and quantification of maximum loss. | No fixed maximum for Sandbox / Sandbox Plus. **Sandbox Express** experiments may remain in the sandbox for up to **nine months**. |
| SEBI Regulatory Sandbox (IN) | Securities and Exchange Board of India | Yes — "limited set of real users", SEBI-regulated entities | Two testing stages; SEBI approves the identified user set for Stage-I and a larger set for Stage-II. Stage-II eligibility requires at least **90 days** in Stage-I. | Stage-I and Stage-II **cumulatively must not exceed twelve months**, unless extended. |
| SEBI Innovation Sandbox (IN) | Securities and Exchange Board of India | **No** | Out of scope for this engine. Offline testing "in isolation from the live market" on historical, anonymised datasets shared by exchanges, depositories and QRTAs; "Live data shall not be made available to participants." | n/a — no live clients, AUM or traded volume exist to cap. |

Where a jurisdiction operates an Inter-operable Regulatory Sandbox (IoRS), the testing
phase duration follows "the sandbox framework of the PR concerned" — i.e. the lead
regulator's own framework, not a separate IoRS limit.

## Sources

- FCA, *Default standards for sandbox testing parameters* — duration, number of customers, customer safeguards, disclosure, testing plans (incl. exit strategy). https://www.fca.org.uk/publication/policy/default-standards-for-sandbox-testing-parameters.pdf
- FCA, *Regulatory Sandbox* — rolling applications, restricted authorisation, "small scale, for a limited duration, and with a limited number of consumers". https://www.fca.org.uk/firms/innovation/regulatory-sandbox
- MAS, *FinTech Regulatory Sandbox Guidelines* (Jan 2022) — boundary conditions. https://www.mas.gov.sg/-/media/mas-media-library/development/regulatory-sandbox/sandbox/fintech-regulatory-sandbox-guidelines-jan-2022.pdf
- MAS, *Sandbox Express* — up to nine months. https://www.mas.gov.sg/development/fintech/sandbox-express
- SEBI, *Revised Framework for Regulatory Sandbox*, Circular SEBI/HO/ITD/ITD/CIR/P/2021/575 (14 Jun 2021) — two-stage testing, twelve-month cumulative cap, 90-day Stage-I minimum. https://www.sebi.gov.in/legal/circulars/jun-2021/revised-framework-for-regulatory-sandbox_50521.html
- SEBI, *Framework for Innovation Sandbox*, Circular SEBI/MRD/CSC/CIR/P/2019/64 (20 May 2019) — offline testing, historical anonymised data, no live data. https://www.sebi.gov.in/legal/circulars/may-2019/framework-for-innovation-sandbox_43027.html
- SEBI, *Inter-operable Regulatory Sandbox: FAQs* (Q19, testing phase duration). https://www.sebi.gov.in/sebi_data/faqfiles/sep-2025/1758091557500.pdf

*Verified against the sources above in August 2026. Sandbox frameworks are revised
frequently — re-check the citation before relying on any duration figure.*
