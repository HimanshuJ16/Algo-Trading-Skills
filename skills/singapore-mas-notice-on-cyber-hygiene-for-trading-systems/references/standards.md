# Standards for the MAS Notice on Cyber Hygiene (Trading Systems)

All statements below were verified on **2026-08-28**. Every requirement here is
mandatory; none of the *numbers* used to operationalise them are MAS figures,
because MAS publishes none.

## Which notice binds you

MAS issues the same six requirements as a separate Notice to each class of
financial institution. On **10 May 2024** the earlier class notices were
cancelled and reissued under the Financial Services and Markets Act 2022.

| Entity class | Notice in force | Replaced |
|---|---|---|
| Banks in Singapore (full and wholesale, branch and locally incorporated) | **FSM-N06** | Notice 655 (cancelled 10 May 2024) |
| Capital markets financial institutions — including holders of a Capital Markets Services licence, approved exchanges, clearing houses, trade repositories and central securities depositories | **FSM-N22** (amendment effective 20 August 2024) | Notice CMG-N03 (cancelled) |

Other classes (payment services, trust companies, licensed credit bureaus and
others) have their own notices; PSN06, TCA-N06 and CBN03 were the predecessors
in those classes. The Notices are issued under the Financial Services and
Markets Act 2022 — reported as section 29(1) for FSM-N06 — and are legally
binding, not guidance.

**Engineering consequence.** An audit file that cites FSM-N06 at a CMS licensee,
or Notice 655 / CMG-N03 at anyone, cites an instrument that does not bind the
entity. The notice number is a declared input, never a constant.

## What the Notice requires

Paragraph numbers are those of the Notice on Cyber Hygiene as originally issued
(paragraphs 4.1–4.6). The operative text below is the Notice's own wording.

| Para | Requirement (Notice wording) | Scope qualifier |
|---|---|---|
| 4.1 Administrative Accounts | "A relevant entity must ensure that every administrative account in respect of any operating system, database, application, security appliance or network device, is secured to prevent any unauthorised access to or use of such account." | Every system. Covers five layers, not just the OS. |
| 4.2(a) Security Patches | "A relevant entity must ensure that security patches are applied to address vulnerabilities to every system, and apply such security patches within a timeframe that is commensurate with the risks posed by each vulnerability." | **No fixed timeframe.** Risk-commensurate, firm-determined. |
| 4.2(b) Security Patches | "Where no security patch is available to address a vulnerability, the relevant entity must ensure that controls are instituted to reduce any risk posed by such vulnerability to such a system." | Applies **only** where no patch is available. |
| 4.3(a) Security Standards | "A relevant entity must ensure that there is a written set of security standards for every system." | Every system. **Written standards**, not a named benchmark. |
| 4.3(b) Security Standards | "Subject to sub-paragraph (c), a relevant entity must ensure that every system conforms to the set of security standards." | Every system. |
| 4.3(c) Security Standards | "Where the system is unable to conform to the set of security standards, the relevant entity must ensure that controls are instituted to reduce any risk posed by such non-conformity." | The exception path for 4.3(b). |
| 4.4 Network Perimeter Defence | "A relevant entity must implement controls at its network perimeter to restrict all unauthorised network traffic." | Every network perimeter, including third-party and overseas-hosted. |
| 4.5 Malware Protection | "A relevant entity must ensure that one or more malware protection measures are implemented on every system, to mitigate the risk of malware infection, **where such malware protection measures are available and can be implemented**." | Qualified by availability and implementability. |
| 4.6(a) Multi-factor Authentication | "…multi-factor authentication is implemented for … all administrative accounts in respect of any operating system, database, application, security appliance or network device **that is a critical system**." | **Critical systems only.** |
| 4.6(b) Multi-factor Authentication | "…all accounts on any system used by the relevant entity to access customer information through the internet." | **All accounts**, not only administrative ones. Not limited to critical systems. |

### Definitions that decide scope

| Term | Definition |
|---|---|
| Critical system | A system the failure of which will cause significant disruption to the operations of the relevant entity, or materially impact the relevant entity's service to its customers. Includes, but is not limited to, a system processing time-critical transactions or providing essential services to customers. |
| Administrative account | A user account with full privileges and unrestricted access to the system. |
| Multi-factor authentication | The use of two or more factors to verify an account holder's claimed identity. |

## What the Notice does NOT say

| Claim frequently made | Status |
|---|---|
| MAS mandates critical security patches within **30 days** | **Does not exist.** Para 4.2(a) requires a timeframe *commensurate with the risks posed by each vulnerability*. The MAS Technology Risk Management Guidelines are consistent and equally non-numeric: patches implemented "within a timeframe that is commensurate with the criticality of the patches and the FI's IT systems" — and the TRM Guidelines are *guidance*, not a binding Notice. Any fixed figure is the firm's own. |
| MFA is required on **all** administrative access | **Over-broad.** Limb 4.6(a) is scoped to critical systems. The universal-sounding limb is 4.6(b), and it is scoped to systems used to access customer information through the internet — where it reaches every account, not just administrators. |
| The Notice mandates **CIS Benchmarks** | **Misattributed.** Para 4.3 mandates a *written set of security standards* and conformance to it. CIS Benchmarks are a widely used way to author that set; the Notice names no benchmark. |
| Anti-malware is required on every host without exception | **Qualified.** Para 4.5 applies "where such malware protection measures are available and can be implemented". |
| "MAS Notice on Cyber Hygiene" is one document | **No.** It is a family of class-specific notices with different numbers and identical requirements. |

## Engineering consequences

| Requirement | Engineering standard |
|---|---|
| Patching deadlines | Firm-set, per severity, supplied by the caller. No default — a default would be a fabricated regulatory threshold. A severity the policy does not cover must raise, not pass unmeasured. |
| Deadline comparison | Inclusive: "within N days" is met at exactly N days; N+1 breaches. |
| Patch limb selection | `days_since_patch_released is None` means *no patch available* → 4.2(b). Compensating controls satisfy 4.2(b) only; they never excuse an overdue available patch under 4.2(a). |
| Vulnerability age | Reject negative ages (they compare below every deadline and pass silently) and booleans (`True` is an `int` in Python and would read as 1 day). |
| MFA scoping | Evaluate both limbs independently. A requirement that applies to neither limb is reported **not applicable**, never as passed. |
| Unknown scope | Resolve conservatively — unknown criticality and unknown internet customer-information access both resolve toward the requirement applying, so an absent field can never make a breaching asset look compliant. |
| Control defaults | Every control flag defaults to the non-compliant value. A partially onboarded asset fails closed. |
| Scoring | `is_compliant` is the only figure with regulatory meaning; every requirement is mandatory. Any percentage is an internal remediation-tracking metric and must divide by the **applicable** requirement count, not a flat six. |
| Attestation boundary | The engine records caller assertions. A clean report attests a control; it does not observe a host. |

## Sources

- MAS, Notice FSM-N22 — Cyber Hygiene (capital markets financial institutions; amendment effective 20 August 2024) — https://www.mas.gov.sg/regulation/notices/notice-fsm-n22
- MAS, Notice FSM-N06 — Cyber Hygiene (banks in Singapore; effective 10 May 2024) — https://www.mas.gov.sg/regulation/notices/notice-fsm-n06
- MAS, Notice 655 — Cyber Hygiene [Cancelled 10 May 2024] — https://www.mas.gov.sg/regulation/notices/notice-655
- MAS, Notice CMG-N03 — Cyber Hygiene [Cancelled] — https://www.mas.gov.sg/regulation/notices/notice-cmg-n03
- MAS, *Frequently Asked Questions: Notice on Cyber Hygiene* — https://www.mas.gov.sg/-/media/mas-media-library/regulation/faqs/trpd/faqs---notice-on-cyber-hygiene/faq---notice-on-cyber-hygiene.pdf
- MAS, *Consultation Paper on Notice on Cyber Hygiene* (6 September 2018; contains the draft Notice with the "commensurate with the risks posed by each vulnerability" wording and the "critical system" definition) — https://www.mas.gov.sg/~/media/MAS/News%20and%20Publications/Consultation%20Papers/Consultation%20Paper%20on%20Notice%20on%20Cyber%20Hygiene.pdf
- MAS, *Technology Risk Management Guidelines* (18 January 2021) — https://www.mas.gov.sg/-/media/MAS/Regulations-and-Financial-Stability/Regulatory-and-Supervisory-Framework/Risk-Management/TRM-Guidelines-18-January-2021.pdf
- MAS, Cyber Security regulation landing page — https://www.mas.gov.sg/regulation/cyber-security
- Financial Services and Markets Act 2022 (Singapore) — https://sso.agc.gov.sg/Act/FSMA2022
- Google Cloud, *MAS Notice 655 Cyber Hygiene — Google Cloud Mapping* (January 2023; reproduces paragraphs 4.1–4.6 verbatim) — https://services.google.com/fh/files/misc/mas_cyberhygiene_gcp_compliancemapping.pdf
- Drew & Napier LLC, *Bringing hygiene online — the MAS notice on cyber hygiene* (28 April 2020; confirms the 6 August 2020 effective date and the six measures) — https://www.drewnapier.com/DrewNapier/media/DrewNapier/28-Apr-20-Bringing-hygiene-online-the-MAS-notice-on-cyber-hygiene.pdf
- Rajah & Tann Asia, *Financial Services and Markets Act 2022 Provisions on Technology and Risk Management … Commenced on 10 May 2024* — https://www.rajahtannasia.com/viewpoints/financial-services-and-markets-act-2022-provisions-on-technology-and-risk-management-control-and-resolution-of-financial-institutions-commenced-on-10-may-2024/
- Waystone Compliance, *MAS Requirements on Cyber Hygiene* (corroborates the risk-based, non-numeric patching standard) — https://compliance.waystone.com/mas-requirements-on-cyber-hygiene/

**Verification note.** The `mas.gov.sg` pages listed above are the primary
sources and were located by notice number and title, but the host refuses
automated retrieval — every fetch returns a maintenance page. The requirement
*text* quoted here was read from Google Cloud's clause-by-clause reproduction of
the Notice's paragraphs 4.1–4.6 and from MAS's own consultation paper as
search-indexed, and cross-checked against the Drew & Napier and Waystone
summaries. The paragraph numbering given is that of the Notice as originally
issued; **confirm the numbering, and the current text, in the reissued FSM
notice that binds your entity class** before relying on a citation in a
compliance file.
