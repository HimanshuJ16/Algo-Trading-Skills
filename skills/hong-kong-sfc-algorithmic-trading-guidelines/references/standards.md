# Standards — hong-kong-sfc-algorithmic-trading-guidelines

## What is actually mandated, and by whom

Every row below was checked against the primary source. Where a value is a firm
decision rather than a regulatory one, the row says so — do not cite this file as
authority for a threshold it calls a firm policy.

**Jurisdiction: Hong Kong SAR.** The conduct requirements bind persons licensed by or
registered with the SFC. The statutory short selling provisions apply to sales "at or
through a recognized stock market" — at present the Stock Exchange of Hong Kong Limited
(SEHK) — and not to off-exchange sales. The Exchange rules bind Exchange Participants.
Nothing here applies to HKFE futures, to Northbound Stock Connect trading, or to any
market outside Hong Kong.

### Code of Conduct — paragraph 18 and Schedule 7

Schedule 7 is titled *"Additional requirements for licensed or registered persons
conducting electronic trading"*. Paragraph 18 of the Code states the general principles;
Schedule 7 sets out the specific requirements. Its own introduction describes the
structure: "Paragraph 1 of this Schedule provides general requirements on electronic
trading. Paragraph 2 provides specific requirements on internet trading and DMA.
Paragraph 3 provides specific requirements on algorithmic trading." **There is no
paragraph 4.**

| Requirement | Source | What it actually says | Status |
|---|---|---|---|
| Named senior ownership of the system | **Schedule 7 §1.1.1(a)** | Written internal policies must ensure "there is at least one responsible officer or executive officer responsible for the overall management and supervision of the electronic trading system". | Mandatory, HK |
| Governance with control-function input | **Schedule 7 §1.1.1(b), (d)** | "a formalised governance process with input from the dealing, risk and compliance functions"; "managerial and supervisory controls that are designed to manage the risks associated with the use of the electronic trading system". | Mandatory, HK |
| Adequately qualified resources | **Schedule 7 §1.1.4** | The firm "should assign adequately qualified staff, expertise, technology and financial resources to the design, development, deployment and operation of the electronic trading system". | Mandatory, HK |
| **Kill switch** | **Schedule 7 §1.2.1** | The system must have "effective controls to enable it, where necessary, to: (a) immediately prevent the system from generating and sending orders to the market; and (b) cancel any unexecuted orders that are in the market." | Mandatory, HK |
| Pre-deployment testing of the system | **Schedule 7 §1.2.2** | The system "and all modifications to the system are tested before deployment and are regularly reviewed". | Mandatory, HK |
| Record keeping — what | **Schedule 7 §1.3.1** | Documentation of design and development (including testing, reviews, modifications), documentation of risk management controls, **audit logs on the activities of its system**, and incident reports for material delays or failures. | Mandatory, HK |
| Record keeping — how long | **Schedule 7 §1.3.2** | Design/development and risk-control documentation "for a period of not less than 2 years after the electronic trading system ceased to be used"; audit logs and incident reports "for a period of not less than 2 years". | Mandatory, HK |
| What an audit log must contain | **Schedule 7, Annex (i)** | At a minimum "order placement / cancellation / modification / execution (with time stamping and the assignment of unique reference number)", login attempts, credit/margin validation exceptions, **compliance validation exceptions**, hierarchical user access, changes to critical system parameters, and **erroneous order inputs** — expressly including "order prices which materially deviated from the market, order sizes exceeding the client's trading limits". | Mandatory, HK |
| Automated pre-trade controls for internet trading and DMA | **Schedule 7 §2.1 and §2.1.1(a)** | Client orders "must" be subject to "appropriate automated pre-trade risk management controls" and regular post-trade monitoring. The controls must be reasonably designed to prevent orders exceeding "appropriate trading and credit thresholds prescribed for each client or proprietary account", limit the firm's financial exposure, "alert the user to the entry of potential erroneous orders and prevent the entry of erroneous orders", and "prevent the entry of orders that are not in compliance with the regulatory requirements". | Mandatory, HK |
| Qualification for algorithmic trading | **Schedule 7 §3.1, §3.1.1, §3.1.2** | Effective policies to ensure persons involved in design and development of, or approved to use, the algorithmic trading system and trading algorithms "are suitably qualified"; those approved to use it must have "a good understanding of ... the operation" and of "the compliance and regulatory issues which may arise". | Mandatory, HK |
| Testing of algorithms | **Schedule 7 §3.2.1, §3.2.2** | Adequate testing before deployment satisfying the firm that the algorithms "will operate as designed", that design took account of "foreseeable extreme market circumstances" and "the characteristics of different trading sessions, such as auction sessions and continuous trading sessions", and that deployment "would not interfere with the operation of a fair and orderly market"; review and testing "regularly, and no less than annually". | Mandatory, HK |
| Risk management for algorithmic trading | **Schedule 7 §3.3.1** | Controls reasonably designed to "monitor and prevent the generation of or passing to the market for execution order instructions ... which may (i) be erroneous; or (ii) interfere with the operation of a fair and orderly market", and to "protect the licensed or registered person and its clients from being exposed to excessive financial risk". | Mandatory, HK |
| Post-trade review | **Schedule 7 §3.3.2, §3.3.3** | Regular post-trade reviews to identify suspected manipulative or abusive activity and system deficiencies; immediate steps to prevent identified activity from continuing. | Mandatory, HK |
| Algorithm records | **Schedule 7 §3.4.1, §3.4.2, §3.4.3** | Design/development and modifications documented in writing with rationale and intended outcome, retained ≥2 years after the algorithms cease to be used; "records of all the parameters which its algorithmic trading system and trading algorithms take into account for each order" retained ≥2 years; records of the §3.2.2 reviews and tests retained ≥2 years. | Mandatory, HK |

### SFC Circular to all Licensed Corporations on Algorithmic Trading — 13 December 2016 (SFO/IS/044/2016)

Findings of a thematic review, with an appendix of FAQs and good practices. This is
supervisory guidance on how the SFC reads Schedule 7, not a separate rule.

| Point | What the circular says | Status |
|---|---|---|
| Areas for improvement | Insufficient control-function input to algorithmic governance; "insufficient pre-trade controls to prevent the generation of algorithmic orders which might adversely affect market integrity"; inadequate third-party due diligence; no written contingency plans specific to algorithmic trading; absence of testing policies. | Guidance, HK |
| Threshold rationale | Deficiencies included "inadequate guidance for the analysis to determine appropriate limits and threshold parameter values for pre-trade controls" and "insufficient documentation of the rationale or analysis behind the limits and threshold parameter values". | Guidance, HK |
| What pre-trade controls missed | Existing controls, "typically limited to order quantity or notional value trading limits", failed to prevent abnormal message volumes within a time interval, "the sending of orders which are outside the stock's reasonable price range", flows deviating from historical patterns, and "trading that deviates from the stock's average daily volume". | Guidance, HK |
| Kill switch granularity | Some firms implemented shutdown "at the exchange connectivity level or the algorithmic engine level only, instead of implementing them at more disaggregated levels (eg, relating to a particular client or algorithmic strategy)". Good practice is controls "at the exchange connectivity, algorithmic, order, trader, system and client levels". | Guidance, HK |
| Overrides | An instance where "pre-trade price limits were overridden when only verbal approval was sought"; overrides "should only be allowed with proper approvals from, and notifications to, designated control functions", with documentation retained and independently reviewed. | Guidance, HK |
| Parent/child and channel parity | "child orders are subject to the same pre-trade and post-trade controls as parent orders"; different algorithmic trading systems subject to the same level of controls absent strong justification; DMA flow subject to the full suite, not only a notional credit limit. | Guidance, HK |
| The enumerated control suite | Price controls, maximum order value, maximum order volume (eg against average daily volume), maximum message limits, child-vs-parent quantity and price checks, pattern deviation checks, DMA cumulative order controls. | Good practice, HK |
| Kill switch speed | "kill switch procedures should also be formalised to ensure that LCs are able to execute the emergency trade flow shutdown process within a short period of time." | Guidance, HK |

### Securities and Futures Ordinance (Cap. 571) — short selling

| Requirement | Source | What it actually says | Status |
|---|---|---|---|
| Naked short selling prohibited | **SFO s.170(1)** | Criminal offence to sell securities at or through a recognized stock market unless at the time of sale the seller (or the principal, where selling as agent) has, or "believes and has reasonable grounds to believe" it has, "a presently exercisable and unconditional right to vest the securities in the purchaser of them". | Mandatory, HK — criminal |
| Penalty | **SFO s.170** | Maximum HK$100,000 fine and 2 years' imprisonment. | Mandatory, HK |
| Defences and exemptions | **SFO s.170(3)(a), (e)** | A genuine, reasonably grounded belief is a defence (s.170(3)(a)); s.3 of the Securities and Futures (Short Selling and Securities Borrowing and Lending) (Miscellaneous) Rules disapplies s.170(1) for HKMA-appointed market makers and for SEHK/HKFE market makers and liquidity providers conducting jobbing business. | Mandatory, HK |
| Identify and assure the order | **SFO s.171** | The seller must, at the time of placing the order, identify it as a "short selling order" and confirm the sale is covered. The intermediary must obtain the confirmation **before transmitting the order to SEHK**. | Mandatory, HK |
| Documentary assurance form and retention | **SFO s.171(6)**, SFC Guidance Note §3.7.2, §3.10 | The assurance must be a document — written confirmation, tape recording or electronic document — conveying that "it is a short sale" and "it has been covered", provided no later than when the order is placed; retained **for at least 12 months** from the date of the transaction. One assurance may cover a series of orders if it is sufficient to cover them and the period. | Mandatory, HK |
| Mark the order short | **SFO s.172** | An exchange participant receiving a short selling order for input into SEHK's system must mark it "short" in the form SEHK prescribes. | Mandatory, HK |

### SEHK Rules of the Exchange — short selling

| Requirement | Source | What it actually says | Status |
|---|---|---|---|
| Designated Securities, permitted sessions, permitted order types | **Rule 563D(1)** | Outside the enumerated exempt categories, "short selling shall be limited to transactions in (a) Designated Securities which are also POS securities effected on the Exchange during the Pre-opening Session, (b) Designated Securities effected on the Exchange during the Continuous Trading Session and (c) Designated Securities which are also CAS securities effected on the Exchange during the Closing Auction Session. For the avoidance of doubt, only at-auction limit orders may be input into the System as short selling orders during the Pre-opening Session and the Closing Auction Session." | Mandatory, HK |
| Exempt categories | **Rule 563D(1)** | Securities Market Maker, Dual Counter Market Maker, Structured Product Liquidity Provider, Designated Index Arbitrage, Stock Futures Hedging, Structured Product Hedging and Options Hedging short selling by the corresponding participants. | Mandatory, HK |
| Flag the order to whoever inputs it | **Eleventh Schedule Reg (5)** | An Exchange Participant which knows or is informed that an order to sell is a short sale must inform whoever inputs it that it is a short selling order, and "when inputting the order into the System, indicate ... that the order is a short selling order". | Mandatory, HK |
| **Tick rule** | **Eleventh Schedule Reg (15)** | "A short sale of a Designated Security shall not be made on the Exchange below the best current ask price (during the Continuous Trading Session) or the CAS reference price (during the Closing Auction Session) except where the Designated Security is a Market Making Security approved by the Commission to be excluded from the application of this Regulation." | Mandatory, HK |
| Tick rule in the Pre-opening Session | **Rule 501(G)(3)(d)** | During the no-cancellation and random matching periods, short selling orders input under Rule 563D(1) must be "at or above the POS reference price". | Mandatory, HK |
| Designated Securities list | SEHK, *Designated Securities Eligible for Short Selling* | Maintained and revised by the Exchange; the SFC Guidance Note records that it "is usually reviewed and updated by SEHK quarterly". | Mandatory reference data, HK |

### SEHK order price limits (not modelled by this skill's price band)

| Rule | What it says |
|---|---|
| **Rule 505A** | "a buy order or a sell order shall not be made at a price that deviates 9 times or more from the nominal price, if available." |
| **Rules 506A / 507A** | In the Continuous Trading Session, limit and enhanced limit order prices are bounded by "the lower of twenty-four spreads or 5% ... below the current bid price" (and the mirror for sells), measured against the **current bid/ask**, with 3.5% substituted for ETFs. |
| **Rule 501(G)(2)** | POS order input period: specified price must not deviate more than 15% from the POS reference price. |
| **Rule 501L(5), (6)** | CAS order input period: specified price must not deviate more than 5% from the CAS reference price; tighter constraints apply in the no-cancellation and random closing periods. |

These are Exchange validations applied to the order as submitted. The firm's own price
band is a separate, earlier erroneous-order control with a different reference price.

## Not found — do not claim it

- **Neither the SFC nor SEHK prescribes a maximum order value or a firm price-deviation
  percentage for a licensed corporation's pre-trade controls.** Schedule 7 §2.1.1(a) and
  §3.3.1 are written as outcomes ("reasonably designed to..."). The HKD 10,000,000 and
  5.0% defaults in `scripts/` are placeholders; the SFC's expectation is documented
  analysis behind whatever values you choose.
- **Hong Kong has no algorithm-developer registration or certification regime**
  comparable to FINRA's Series 57. Schedule 7 §3.1 requires that the people involved be
  *suitably qualified*, and §1.1.1(a) requires a responsible officer or executive officer
  accountable for the system. Firm-level and individual licensing (Type 1 regulated
  activity, responsible officers, the Manager-In-Charge regime) are separate questions.
- **No SFC rule requires an algorithm to be pre-approved, registered or tagged with the
  Commission or the Exchange before use.** Schedule 7 requires testing, records and
  controls — not a filing.
- **"Schedule 7 paragraph 4" does not exist**, and the kill switch is not in paragraph 3.
  It is §1.2.1, in the general electronic-trading requirements that apply to every
  electronic trading system, algorithmic or not.
- **The 2-year retention in Schedule 7 §1.3.2 is not a general Hong Kong retention
  period.** Section 171 documentary assurances have a 12-month statutory retention, and
  other records (client agreements, accounting records) have their own periods under the
  Securities and Futures (Keeping of Records) Rules.
- **The tick rule is not "no short sale below the last traded price".** It is measured
  against the best current ask in the Continuous Trading Session and against the session
  reference price in the auction sessions.

## Primary sources

| Document | Locator |
|---|---|
| Code of Conduct for Persons Licensed by or Registered with the SFC (paragraph 18, Schedule 7 and its Annex) | https://www.sfc.hk/en/Rules-and-standards/Codes-and-guidelines/Codes |
| SFC Circular to all Licensed Corporations on Algorithmic Trading, 13 Dec 2016 (SFO/IS/044/2016), with Appendix of observations, FAQs and good practices | https://apps.sfc.hk/edistributionWeb/gateway/EN/circular/doc?refNo=16EC67 |
| Securities and Futures Ordinance (Cap. 571), ss.170–172 | https://www.elegislation.gov.hk/hk/cap571 |
| SFC Guidance Note on Short Selling Reporting and Stock Lending Record Keeping Requirements | https://www.sfc.hk/en/Rules-and-standards/Codes-and-guidelines/Guidance-notes |
| SEHK Rules of the Exchange, Chapter 5 (Trading) — Rules 501, 501L, 505A, 506A, 507A, 563D | https://www.hkex.com.hk/-/media/HKEX-Market/Services/Rules-and-Forms-and-Fees/Rules/SEHK/Securities/Rules/Chap_5_eng.pdf |
| SEHK Rules of the Exchange, Eleventh Schedule — Short Selling Regulations | https://www.hkex.com.hk/-/media/HKEX-Market/Services/Trading/Securities/Overview/Regulated-Short-Selling/sch-11_eng.pdf |
| HKEX Regulated Short Selling overview and Designated Securities list | https://www.hkex.com.hk/Services/Trading/Securities/Overview/Regulated-Short-Selling |
