# Global Regulatory & Compliance Touchpoints Index

The skills in this repository encode quantitative infrastructure engineering practices, not legal, tax, or compliance advice. This index provides a cross-cutting pointer map of where trading system implementations intersect with regulatory mandates, exchange rules, and tax frameworks across global jurisdictions.

> **Disclaimer**: Nothing in this repository constitutes financial, legal, tax, or regulatory compliance advice. Regulatory requirements governing algorithmic trading vary by jurisdiction, entity structure, and market venue, and change over time. Consult qualified legal, tax, and compliance professionals in your jurisdiction before deploying live systems.

---

## 1. US Regulatory Regimes (SEC & FINRA)
| Regulation / Rule | Relevant Skills | Key Mandates & Infrastructure Requirements |
|---|---|---|
| **SEC Rule 15c3-5** | `sec-rule-15c3-5-risk-controls-us`, `kill-switch-and-drawdown-circuit-breakers` | Mandatory pre-trade risk controls: single-order notional caps, maximum position limits, credit limits, restricted security lists, non-bypassable kill switch. |
| **SEC Reg NMS Rule 611** | `us-reg-nms-order-protection-rule-compliance`, `smart-order-routing-across-venues` | Order Protection Rule: prevent trade-throughs on Protected Quotes, Intermarket Sweep Orders (ISO) routing. |
| **SEC Rule 2a-5 (17 CFR 270.2a-5)** | `multi-source-price-reconciliation-tie-breaking`, `vendor-outage-fallback-data-source-hierarchy` | Fair value determination for registered investment companies: oversight of pricing service providers, including approving, monitoring and evaluating each provider and initiating price challenges as appropriate; a market quotation is not readily available if it is not reliable. Applies to registered funds and their valuation designees, not to a proprietary firm's feed handler. |
| **SEC Reg SHO (Rule 203/204)** | `us-reg-sho-short-sale-locate-requirements`, `short-selling-borrow-cost-and-availability-modeling` | Pre-short locate verification (`locate_id`), threshold security tracking, mandatory buy-in timelines on fails-to-deliver. |
| **FINRA Rule 4210 (PDT)** | `pattern-day-trader-rule-compliance-us` | Pattern Day Trader rule: $25,000 minimum equity requirement, 4x day trading buying power enforcement, PDT flag tracking. |
| **FINRA Rule 5210 / 5220** | `wash-trade-and-spoofing-self-detection` | Prohibition of self-trades and disruptive quoting practices: pre-trade self-cross prevention, intent audit logs. |
| **CFTC 17 CFR 1.35(b)(5)** | `multi-account-same-strategy-fan-out`, `cftc-commodity-pool-operator-registration` | Bunched orders and post-execution allocation by eligible account managers: allocations must be fair and equitable with no account consistently favored, made as soon as practicable and reported to the FCM before end of the execution day, using a methodology objective enough to permit independent verification, with records reconstructing placement through per-account allocation. |
| **SEC Advisers Act Sec. 206 / Rule 206(4)-7** | `multi-account-same-strategy-fan-out`, `best-execution-record-keeping-global` | Written compliance policies covering trading practices, including allocation of aggregated trades among clients (adopting release IA-2204). No allocation method is mandated by rule; preferential allocation (cherry-picking) is charged under the Sec. 206 antifraud provisions. |
| **IRS Tax Code (1099-B, Sec 475/1256)** | `wash-sale-rule-tracking-us`, `section-475-mark-to-market-election-accounting`, `section-1256-contracts-tax-accounting` | 30-day wash sale tracking across accounts, Mark-to-Market election accounting, 60/40 tax treatment for futures. |

---

## 2. European Union & United Kingdom (ESMA & FCA)
| Regulation / Directive | Relevant Skills | Key Mandates & Infrastructure Requirements |
|---|---|---|
| **EU MiFID II Article 48 & RTS 6** | `mifid-ii-algo-trading-compliance-eu`, `hardware-timestamping-vs-software-timestamping-accuracy` | Systems resilience: mandatory kill switches, pre-trade controls, OTR limits, microsecond clock synchronization (RTS 25). |
| **EU Market Abuse Reg (MAR)** | `eu-market-abuse-regulation-mar-surveillance`, `wash-trade-and-spoofing-self-detection` | Market abuse surveillance: automated detection of spoofing, layering, wash trading, momentum ignition, and insider trading. |
| **EU CRR Article 105(8) (Reg. (EU) No 575/2013)** | `multi-source-price-reconciliation-tie-breaking`, `reference-data-golden-source-designation` | Prudent valuation: independent price verification in addition to daily marking to market or to model, performed by a person or unit independent from those who benefit from the trading book, at least monthly or more frequently depending on the market; valuation adjustments where independent pricing sources are unavailable or more subjective. A periodic valuation control for CRR-scope firms, not a tick-level feed requirement. |
| **UK FCA SYSC 25** | `uk-fca-algorithmic-trading-systems-controls`, `kill-switch-and-drawdown-circuit-breakers` | Systems and Controls (SYSC) requirements for algorithmic trading: kill switch architecture, stress testing, change management. |
| **UK Senior Managers Regime (SM&CR)** | `uk-senior-managers-regime-algo-accountability` | Senior management accountability: clear lines of responsibility for trading algorithms, key person governance, algorithmic sign-off. |

---

## 3. Asia-Pacific & Global Regulatory Regimes
| Regulator / Authority | Relevant Skills | Jurisdiction & Mandate |
|---|---|---|
| **India SEBI** | `india-sebi-algo-trading-tagging-requirements`, `order-to-trade-ratio-fee-penalty-avoidance` | Unique Algo ID order tagging, broker pre-approval of algorithms, Order-to-Trade Ratio (OTR) penalty avoidance on NSE/BSE. |
| **Singapore MAS / SGX** | `singapore-mas-notice-on-cyber-hygiene-for-trading-systems`, `mas-singapore-algo-trading-guidelines` | MAS Notice on Cyber Hygiene: multi-factor authentication, security patch management, vulnerability assessment, access control. SFA licensing and SGX Approved Trader registration (no MAS algorithm registration exists), Clearing Member pre-execution limits, Forced Order Range and Force Key, SGX-ST circuit breaker band. |
| **Australia ASIC** | `asic-market-integrity-rules-automated-trading`, `australian-securities-exchange-asx-api` | ASIC Market Integrity Rules (MIR): automated order processing (AOP) certification, pre-trade filters, order cancellation authority. |
| **Switzerland FINMA** | `swiss-finma-algorithmic-trading-expectations` | FINMA guidelines: risk management, algorithm auditability, emergency kill switches, business continuity planning. |
| **Canada IIROC** | `iiroc-electronic-trading-rules-compliance` | IIROC Dealer Member Rules: pre-trade risk controls, marketplace participant obligations, short sale marking. |
| **Hong Kong SFC** | `hong-kong-sfc-algorithmic-trading-guidelines`, `shanghai-shenzhen-connect-programs` | SFC Code of Conduct: algorithm design, testing, risk controls, Stock Connect Northbound trading rules. |
| **Japan FSA** | `japan-fsa-high-speed-trading-registration` | FSA High-Speed Trading (HST) registration: risk control measures, system capacity management, regulatory reporting. |

---

## 4. Institutional Derivatives & Custody Standards
| Framework / Standard | Relevant Skills | Operational Scope |
|---|---|---|
| **ISDA Master Agreement** | `total-return-swap-synthetic-exposure`, `variance-swap-and-volatility-derivative-pricing` | OTC derivative documentation, collateral management, netting agreements, credit support annex (CSA) tracking. |
| **CME SPAN Margin System** | `options-margin-span-calculation-global`, `options-greeks-real-time-portfolio-aggregation` | Standard Portfolio Analysis of Risk: portfolio-based margining, intra-day margin call calculation, Greeks aggregation. |
| **MAS / ISO Custody Security** | `hardware-security-module-hsm-for-signing-keys`, `multi-signature-approval-for-large-transfers` | Digital asset custody: HSM PKCS#11 key management, multi-signature approval thresholds, withdrawal velocity limits. |
