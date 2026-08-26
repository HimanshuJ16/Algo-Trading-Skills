# Standards — india-sebi-algo-trading-tagging-requirements

## What is actually mandated, and by whom

Every row below was checked against the primary source. Where a value is an exchange
decision or a firm decision rather than a SEBI one, the row says so — do not cite this
file as authority for a threshold it calls an exchange or firm setting.

**Jurisdiction: India.** SEBI is the **Securities and Exchange Board of India**, acting
under section 11(1) of the SEBI Act, 1992. These requirements bind recognised stock
exchanges and SEBI-registered stock brokers, and reach algo providers indirectly — SEBI's
2025 circular states plainly that "algo providers shall not be regulated by SEBI" and
instead makes the broker the principal and the algo provider its agent, empanelled with
the exchanges. Nothing here applies to any market outside India, and the retail-algo
implementation standards expressly do not apply to Direct Market Access.

---

## 1. The retail algo framework

### SEBI/HO/MIRSD/MIRSD-PoD/P/CIR/2025/0000013 — 4 February 2025

*Safer participation of retail investors in Algorithmic trading.* Addressed to all
recognised stock exchanges and to stock brokers through them.

| Requirement | Source | What it actually says | Status |
|---|---|---|---|
| Broker is principal, algo provider is agent | **para 5.I(a)** | "brokers shall be the principal while any algo provider or fintech/vendor ... shall act as its agent, while using the API provided by the broker". | Mandatory, India |
| **Unique identifier on API algo orders** | **para 5.I(b)** | "All algo orders originating/flowing through Application Programming Interface (API) extended by brokers to algo providers, shall be tagged with a unique identifier provided by Stock Exchange." | Mandatory, India |
| Registration of self-written retail algos | **para 5.I(c)** | Algos developed by tech-savvy retail investors "shall also be registered with the Exchange, through their broker, **only if they cross the specified order per second threshold**". A registered algo may be used for "self, spouse, dependent children and dependent parents" — not for other investors. | Mandatory, India |
| **Where the OPS threshold comes from** | **footnote 2** | "Specified threshold for categorization as an Algo shall be evolved by the Broker's Industry Standards Forum, under the aegis of the stock exchanges and in consultation with SEBI." **The number 10 is not in this circular.** | Delegated |
| No open APIs; API key + static IP | **para 5.I(d)** | Brokers shall "not permit open APIs and allow access only through a unique vendor client specific API key and static IP whitelisted by the broker to ensure identification and traceability of the algo provider and the end user". | Mandatory, India |
| OAuth only; 2FA | **para 5.I(d)** | "have OAuth (Open Authentication) based authentication only and all other authentication mechanisms shall be discontinued"; "authenticate access to API through two factor authentication". | Mandatory, India |
| Detect and categorise algo orders | **para 5.I(d)** | Brokers shall "ensure that they have systems and procedures in place to detect/identify and categorize all orders above the specified threshold as algo orders". | Mandatory, India |
| Empanelled providers only | **para 5.I(d), 5.III(a)** | Brokers shall "deal with empaneled algo providers only ... as such algo providers are agents of such broker"; any algo provider placing algo orders through a broker's API "shall require to be empaneled with Exchanges". | Mandatory, India |
| Exchange permission per algo | **para 5.II(a)** | "The facility of algo trading shall be provided by the broker only after obtaining requisite permission of the stock exchange for each algo." | Mandatory, India |
| **Tagging and change control** | **para 5.II(b)** | "All algo orders shall be tagged with a unique identifier provided by the Exchange in order to establish audit trail and the broker shall seek approval from the Exchange for any modification or change to the approved algos." | Mandatory, India |
| Grievances and API monitoring | **para 5.II(c)** | Brokers "solely responsible for handling investor grievances related to algo trading and the monitoring of APIs for prohibited activities". | Mandatory, India |
| Exchange kill switch per algo id | **para 5.IV(a)(iii)** | Exchanges "continue to have the ability to use the kill switch for orders emanating from a particular algo id". Footnote 4 describes it as "the last level of defence against any Algorithm malfunction". | Mandatory, India |
| **White box vs black box** | **para 5.V(a)** | Two categories: execution / white box algos where "the logic is disclosed and replicable"; black box algos where "the logic is not known to the user and is not replicable". For black box, the algo provider shall "Register as a Research Analyst and maintain a detailed research report for each such algo", and any change in logic means registering it "as a fresh algo". | Mandatory, India |
| Effective date | **para 7(b)** | "The provisions of this circular shall be applicable with effect from August 01, 2025." Later re-phased — see §4 below. | Mandatory, India |

### NSE/INVG/67858 — 5 May 2025 (Annexure: Implementation Standards)

Issued under para 7(a) of the SEBI circular. **This is where the numbers live.** BSE and
MCX issued matching standards; check the venue you actually trade on.

| Requirement | Source | What it actually says | Status |
|---|---|---|---|
| Static IP mandatory for API access | **Annexure A.1** | Clients "must mandatorily provide the stockbroker with a static IP address(es)". A secondary IP is permitted for redundancy (A.2). | Exchange standard |
| Multiple API keys | **Annexure A.3, A.4** | Multiple keys per client are allowed; "the broker will ensure that non-registered algos are run only through one of the predefined API keys. Other API keys can be used only for registered algos." | Exchange standard |
| **Whose static IP** | **Annexure A.5** | Client-generated algos: the client's. Empanelled-provider algos: "the vendor or the client". Broker-generated algos: "the broker or the client". | Exchange standard |
| IP change frequency | **Annexure A.6** | Clients may update mapped static IPs "not more than once a calendar week", with an exception route through the broker. | Exchange standard |
| IP sharing within a family | **Annexure A.7** | A static IP maps to one client at a time; sharing permitted only within a family as defined in SEBI/HO/MIRSD/MIRSD-PoD1/P/CIR/2024/169 (3 Dec 2024), on written or 2FA-validated request. | Exchange standard |
| Daily session logout | **Annexure A.8** | "All API sessions shall be compulsorily logged out every day before the start of the next trading day." | Exchange standard |
| **Threshold Order Per Second** | **Annexure B.2, F** | "The Threshold Order Per Second (TOPS) is initially set at not exceeding 10 orders per second **per exchange/segment** and may be adjusted by the stock exchanges as needed after due notice to the market." Flow below TOPS needs no registration. "The threshold will be applied basis the calendar clock second of the broker server." A broker may set its own lower client-level limit, "not exceeding the current prescribed Threshold Order Per Second". | Exchange standard, adjustable |
| **Generic tag below the threshold** | **Annexure B.3** | Below-threshold algo orders are still identified: "a generic algo ID shall be provided by the Exchange for such Algos". | Exchange standard |
| **Duty to reject above the threshold** | **Annexure B.5** | "If the broker receives orders that exceed the Threshold OPS limit, the broker shall reject/not accept/not process any orders exceeding the OPS limit, in accordance with their policy." | Exchange standard |
| Broker must be able to monitor OPS | **Annexure B.6** | Every broker providing API connectivity "should have the capability to effectively monitor or control the Threshold OPS limits for algos not requiring registration". | Exchange standard |
| Restricted order types / instruments | **Annexure B.4** | Exchanges "may, from time to time, specify restricted order types and/or contracts/securities for client algos. The brokers will ensure that their APIs do not permit such restricted orders". | Exchange standard |
| **Registration above the threshold** | **Annexure C.1, C.2** | To exceed TOPS a client "must register their algorithm with each Exchange where the algorithm is intended to be used"; the exchange issues a registration ID and "The orders will be tagged with the exchange-provided algorithm ID(s)". | Exchange standard |
| Broker-generated algos | **Annexure D.1–D.3** | Each broker algo is registered and gets an exchange-specific algorithm ID; all client orders through it "must include the appropriate exchange algorithm ID"; logic changes are reported and approvals updated. | Exchange standard |
| Provider algos | **Annexure E.1, E.2** | All algo providers empanelled with exchanges and registered with each exchange where their algos trade; "the stock exchanges shall assign a unique algo ID to each such algo. Such unique algo IDs may be used across members once registered." | Exchange standard |
| **Tagging, below and above** | **Annexure G** | "All algo orders (Below and above the threshold) shall be tagged with a unique identifier provided by the Exchange in order to establish audit trail." | Exchange standard |
| Audit trail retention | **Annexure I.a** | Sound audit trail for all IBT / STWT / Client API / Vendor API orders and trades, identifying the actual user and user-id; "The audit trail data should be available for at least 5 years." | Exchange standard |
| Hosting | **Annexure I.h** | "All Retail Algorithms, including those provided by empanelled Algo providers should be hosted on [the broker's] servers." Per the NSE retail-algo FAQ Q5, a tech-savvy client instead hosts their own algo logic at a static IP at their end. | Exchange standard |
| **DMA carve-out** | **Annexure J.1** | "These standards do not apply to trading under Direct Market Access (DMA), which will remain governed by the relevant provisions." | Exchange standard |
| Rogue algo kill | **Annexure J.3** | "The stock exchanges will have the authority to kill any rogue algo(s) in case it is impacting the market." | Exchange standard |

### NSE retail-algo FAQ — 3 November 2025

NSE's own disclaimer states that where the FAQ and the circulars differ, "the Information
in the circulars shall be construed as final and binding". Treat these as operational
clarification, not as the rule.

| Point | What the FAQ says | Status |
|---|---|---|
| Client static IP scope | Q3 and Q6: "Client static IP is required only in case of Tech savvy Investor using API." | Clarification |
| Provider-hosted platform | Q10: where the algo platform is hosted on the trading member's infrastructure, "it would be the static IP of the Trading Member's server". | Clarification |
| **Tagging for client-direct-API and member front-end retail algo** | Q7: "the tagging shall be - first 12 digits would be '444444444444' and the 13th digit would be '0', or '2', or '4'". | Clarification |
| All API orders are algo orders | Q8: "all orders received via API from clients are considered Algo orders and require appropriate tagging including standardised tagging for cases where the OPS is within the threshold of 10 OPS." | Clarification |
| Black box provider must be an RA | Q4: any Research Analyst wishing to deploy a black box algo must first become an Algo Provider; an Algo Provider hosting black box algos of multiple third-party RAs "is not possible". | Clarification |
| Market and IOC orders | Q11: quoting NSE/MSD/67753 §8.1.1.12, "Algo orders with order type as Market Order are not permitted"; and §8.1.2.1, "Immediate Or Cancel (IOC) and Market orders shall not be allowed to be placed using algorithmic trading" in the Commodity segment. | Clarification |

---

## 2. Order identity in the order structure

| Point | Source | What it says | Status |
|---|---|---|---|
| **Algo identity is the 13th NNF digit** | **NSE/CMTR/68802, 30 Jun 2025** (effective 7 Jul 2025) | "Algo shall be identified as per the 13th digit of the 15-digit NNF field in the order structure for every order emanating from the NNF terminals." Extends pre-emptive rejection of algo market orders to the capital market segment, after NSE/CD/67733 (CD) and NSE/COM/68215 (COM). Not applicable to the closing session / post-close in the capital market segment. | Exchange rule |
| NNF field layout | **NSE Consolidated Penalty FAQ** | "the 12 digit NNF terminal id reported for CTCL (i.e. combination of Pin code - 6 digit, Branch Code - 3 digit and Terminal ID - 3 digit)"; the algo/non-algo determination "is based on 13th digit of NNF id as per the order structure". | Exchange rule |
| Algo market order prohibition | **NSE/SURV/55281, 17 Jan 2023** | Reiterates the Market Price Protection check under NSE/CMTR/21793 (28 Sep 2012) — trading members should not place algorithmic orders as market orders — and introduced a penalty structure for market orders emanating from algos. | Exchange rule |
| Tagging-violation penalties | **NSE/SURV/50154, 29 Oct 2021**, revised by **NSE/SURV/57315, 27 Jun 2023** | Penalties for failing to tag algorithmic orders with the Exchange-allocated unique identifier. Instance violation count computed on a rolling 3 months. | Exchange rule |

The specific digit values `0`, `2`, `4` come from the NSE retail-algo FAQ Q7 quoted above,
for the retail client-direct-API and member-front-end case. **Verify against the current
NNF / order-structure protocol for your segment before hard-coding them** — the reference
implementation takes the accepted set as a parameter for exactly this reason.

---

## 3. Order-to-Trade Ratio — a separate, older framework

The OTR framework is an **economic disincentive levied on the trading member**, computed
per segment per trading day over algo orders and algo trades. It is not a per-order
pre-trade control, and it predates the retail-algo framework by thirteen years.

| Requirement | Source | What it actually says | Status |
|---|---|---|---|
| Framework established | **CIR/MRD/DP/09/2012, 30 Mar 2012** | Broad guidelines on algorithmic trading; directed exchanges to implement "a framework of economic disincentives for high daily order-to-trade ratio of orders placed from trading algorithms". | Mandatory, India |
| Rates doubled; repeat-offence suspension | **CIR/MRD/DP/16/2013, 21 May 2013** | Exchanges directed "to double the existing rates of 'charges to be levied per algo orders'". Para 5 added suspension of proprietary trading rights "for the first trading hour on the next trading day" where a member was penalised "on more than ten occasions in the previous thirty trading days". Also required six-monthly system audit of algo trading systems by a CISA/DISA/CISM/CISSP-certified auditor (para 2.1, 2.2). Effective 27 May 2013. | Mandatory, India |
| **±0.75% LTP exemption; cash segment brought in** | **SEBI/HO/MRD/DP/CIR/P/2018/62, 9 Apr 2018, para 14** | "orders placed within ±0.75% of the LTP shall be exempted from the framework for imposing penalty for high OTR"; the framework was extended to orders in the cash segment and orders under liquidity enhancement schemes. | Mandatory, India |
| **Slabs to 2,000; cooling-off rule** | **SEBI/HO/MRD1/DSAP/CIR/P/2020/107, 24 Jun 2020** | Exchanges may introduce additional slabs up to an OTR of 2000 (from the then-existing 500), and beyond 2000 with "deterrent incremental penalty, which stock exchanges may decide jointly". And: **"On the third instance of OTR being 2000 or more, in last 30 days (rolling basis), the concerned member shall not be permitted to place any orders for the first 15 minutes on the next trading day."** Applies to recognised stock exchanges excluding commodity derivatives exchanges and IFSC exchanges. | Mandatory, India |
| **DMM and option-band exemptions** | **HO/47/11/16(2)2025-MRD-POD2/I/4113/2026, 4 Feb 2026**, effective **6 Apr 2026** | Algorithmic orders placed by Designated Market Makers for market making activity are excluded from OTR computation; an equity-option premium band was added to the exempt range alongside the cash-segment ±0.75% band. | Mandatory, India |

**Slab rates and boundaries are set by each exchange, not by SEBI**, and have been revised
repeatedly. The reference implementation therefore ships 500 as a *penalty slab floor*
(the level that was the top slab before 2020) and 2000 as the *cooling-off level* — both as
overridable defaults, and neither as a claim about what your exchange currently charges.

Related but distinct exchange penalties that a high message count also attracts, and that
this skill does **not** model: quote stuffing (levied where 20 lakh or more order messages
are observed with a trade count of 10 or less, at algo-id + NEAT user-id level per
segment), excessive order modifications without a change in price or quantity, and high
algo order messages in symbols/contracts with nil or low trade count. Exchange-rejected
orders are not counted in order messages. See the NSE Consolidated Penalty FAQ and
NSE/SURV/57315.

---

## 4. Implementation timeline

| Date | Instrument | What changed |
|---|---|---|
| 4 Feb 2025 | SEBI/HO/MIRSD/MIRSD-PoD/P/CIR/2025/0000013 | Framework issued; implementation standards due 1 Apr 2025, provisions applicable 1 Aug 2025. |
| 1 Apr 2025 | SEBI extension circular (Apr 2025) | Timeline for formulation of the ISF implementation standards extended. |
| 5 May 2025 | NSE/INVG/67858 | NSE implementation standards published. |
| 22 Jul 2025 | NSE/INVG/69255 | Detailed operational modalities (incl. Annexure I para 14: strategies run on broker servers). |
| 19 Sep 2025 | NSE/INVG/70309 | Corrigendum / update, including algo provider empanelment criteria (para 4.2). |
| 30 Sep 2025 | SEBI/HO/MIRSD/MIRSD-PoD/P/CIR/2025/132 | Implementation re-phased onto a glide path with milestones; brokers ready from 1 Oct 2025. |
| 3 Nov 2025 | NSE retail-algo FAQ | Operational clarifications. |
| **1 Apr 2026** | Same glide path | **Full implementation mandatory for all stock brokers.** |
| 6 Apr 2026 | HO/47/11/16(2)2025-MRD-POD2/I/4113/2026 | Revised OTR exemptions take effect. |

Confirm the current position against SEBI and your exchange before relying on any date
here: this framework has been re-phased more than once.

## Primary sources

- SEBI, *Safer participation of retail investors in Algorithmic trading*,
  SEBI/HO/MIRSD/MIRSD-PoD/P/CIR/2025/0000013, 4 February 2025 —
  https://www.sebi.gov.in/legal/circulars/feb-2025/safer-participation-of-retail-investors-in-algorithmic-trading_91614.html
- SEBI, *Extension of timeline for implementation …*,
  SEBI/HO/MIRSD/MIRSD-PoD/P/CIR/2025/132, 30 September 2025 —
  https://www.sebi.gov.in/legal/circulars/sep-2025/extension-of-timeline-for-implementation-of-sebi-circular-dated-february-04-2025-on-safer-participation-of-retail-investors-in-algorithmic-trading-_96979.html
- NSE, *Safer participation of retail investors in Algorithmic trading* (Implementation
  Standards), NSE/INVG/67858, 5 May 2025 —
  https://nsearchives.nseindia.com/content/circulars/INVG67858.pdf
- NSE, *FAQs — Safer participation of Retail investors in Algorithmic trading*,
  3 November 2025 —
  https://nsearchives.nseindia.com/web/sites/default/files/inline-files/FAQ_Retail%20Algo_03112025_NSE.pdf
- NSE, *Algo Market Order pre-emptive cancellation*, NSE/CMTR/68802, 30 June 2025 —
  https://nsearchives.nseindia.com/content/circulars/CMTR68802.pdf
- NSE, *FAQ for Consolidated Penalty Structure* (NSE/SURV/57315) —
  https://nsearchives.nseindia.com/web/sites/default/files/inline-files/FAQ%20for%20Consolidated%20Penalty%20Structure.pdf
- SEBI, *Broad guidelines on Algorithmic Trading*, CIR/MRD/DP/16/2013, 21 May 2013 —
  https://www.sebi.gov.in/sebi_data/attachdocs/1369137134098.pdf
- SEBI, *Guidelines for Order-to-trade ratio (OTR) for Algorithmic Trading*,
  SEBI/HO/MRD1/DSAP/CIR/P/2020/107, 24 June 2020 —
  https://www.sebi.gov.in/legal/circulars/jun-2020/guidelines-for-order-to-trade-ratio-otr-for-algorithmic-trading_46925.html
- SEBI, *Revision of Order-to-Trade Ratio (OTR) framework*,
  HO/47/11/16(2)2025-MRD-POD2/I/4113/2026, 4 February 2026 —
  https://www.sebi.gov.in/legal/circulars/feb-2026/revision-of-order-to-trade-ratio-otr-framework_99501.html
- SEBI, *Measures to strengthen Algorithmic Trading and Co-location / Proximity Hosting
  framework*, SEBI/HO/MRD/DP/CIR/P/2018/62, 9 April 2018 (para 14, OTR).
- SEBI, *Broad Guidelines on Algorithmic Trading*, CIR/MRD/DP/09/2012, 30 March 2012 —
  https://www.sebi.gov.in/legal/circulars/mar-2012/broad-guidelines-on-algorithmic-trading_22471.html

Two sources below were read only through secondary reproduction because the SEBI page
renders its body via script and the PDF was not retrievable: the full text of the 2018
para 14 and of the 4 February 2026 OTR revision. The circular numbers, dates and the
±0.75% / DMM / effective-date facts above are consistent across the reproductions
consulted, but re-read both primaries before relying on any further detail from them.
