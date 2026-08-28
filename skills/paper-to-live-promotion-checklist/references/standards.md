# Standards & Regulatory Reference — paper-to-live-promotion-checklist

## The one thing to take from this file

**No regulator prescribes a paper-trading duration, a trade count, a slippage tolerance or
an accuracy band.** Every numeric default in `scripts/promotion_gate.py` is a house
heuristic. What the rules below *do* mandate is that testing happens before deployment,
that it establishes named properties, that a designated person authorises deployment, and
that the whole thing is documented and auditable. Do not cite any of them as authority for
"20 days" or "±15%".

## Verified obligations

| Jurisdiction | Instrument | Provision | What it actually requires | Mandatory? |
|---|---|---|---|---|
| EU / UK | MiFID II RTS 6 — Commission Delegated Regulation (EU) 2017/589 | Art. 5, *General methodology* | Clearly delineated development and testing methodologies established **before** deployment or substantial update, addressing design, performance, recordkeeping and approval; testing must establish that the algorithm does not behave in an unintended manner, complies with the firm's obligations and with the rules of the venues accessed, does not contribute to disorderly trading conditions, continues to work effectively in stressed conditions and can be switched off. A person designated by the firm's senior management shall authorise the deployment or substantial update. The firm retains full responsibility for the testing. | Yes (in-scope firms) |
| EU / UK | RTS 6 (EU) 2017/589 | Art. 6, *Conformance testing* | Conformance testing against the trading venue's / DEA provider's systems. | Yes |
| EU / UK | RTS 6 (EU) 2017/589 | Art. 7, *Testing environments* | Testing undertaken in an environment **separated from the production environment**, used specifically for testing and development. May be the firm's own, or one provided by a venue, DEA provider or vendor. | Yes |
| EU / UK | RTS 6 (EU) 2017/589 | Art. 8, *Controlled deployment of algorithms* | Before deployment, predefined limits on (a) the number of financial instruments traded, (b) the price, value and numbers of orders, (c) the strategy positions, and (d) the number of trading venues to which orders are sent. **This is the regulatory anchor for a reduced-size initial live window** — though the sizing itself is `incremental-capital-deployment-for-new-strategies`. | Yes |
| EU / UK | RTS 6 (EU) 2017/589 | Art. 9, *Annual self-assessment and validation* | Annual self-assessment and validation producing a validation report, drawn up by the risk management function, reviewing the algorithmic trading systems, algorithms and strategies and the governance, accountability and approval framework. | Yes |
| EU / UK | RTS 6 (EU) 2017/589 | Art. 10, *Stress testing* | Test capacity to process **twice** the highest volume of messages and of trades reached in the previous six months. | Yes |
| EU | ESMA, *Supervisory Briefing on Algorithmic Trading in the EU*, ESMA74-1505669079-10311, 26 Feb 2026 | ¶¶25–32 | Confirms RTS 6 Arts. 5–7 as the testing provisions and Art. 10 as stress testing; recognises "the need for proportionality in applying the testing provisions"; requires testing after each **material change / substantial update**, and warns that accumulated minor recalibrations can amount to one. Material-change examples: decision logic, execution behaviour (order types, slicing, routing), scope (new instruments/venues/asset classes), risk controls (thresholds, kill-switch logic, alert triggers), external dependencies (third-party providers, data feeds), and retraining or modifying ML components. | **No** — expressly non-binding, not subject to comply-or-explain |
| India | SEBI, *Master Circular for Stock Exchanges and Clearing Corporations*, Ch. 2 "Trading Software and Technology" | §5.1.2.1 | Exchanges shall provide a simulated test environment; subjecting new software, or existing software that has changed, to that testing facility is **mandatory for market participants before putting it in use**. | Yes |
| India | SEBI, ibid. | §5.1.2.2.1, §5.1.2.2.4 | Exchange mock trading sessions at least once a calendar month; the broker shall undertake User Acceptance Testing of the software. | Yes |
| India | SEBI, ibid. (per Circular SEBI/HO/MRD1/DSAP/CIR/P/2020/234, 24 Nov 2020) | §5.1.2.2.5.4 | All trading members holding approved algorithms — whether or not the algorithm has changed — shall participate in the Simulated Environment on **at least one trading day each calendar month** at every exchange where they are members. Audited and reported in the System Auditor's report. | Yes |
| India | SEBI, ibid. | §7.1.3 | A broker may provide algorithmic trading only upon **prior permission of the stock exchange**, which subjects the broker's systems to initial conformance tests. | Yes |
| India | SEBI, ibid. | §7.1.4 | System audit of the algorithmic trading system **every six months** by a CISA / DISA / CISM / CISSP-certified auditor. | Yes |
| India | SEBI, ibid. | §7.1.9.4–5 | Maintain logs of all trading activities for audit trail, plus records of control parameters, orders, trades and data points from algo-executed trades; inform the exchange of **any modification or change to approved algos**. | Yes |
| India | SEBI Circular SEBI/HO/MIRSD/MIRSD-PoD/P/CIR/2025/132, 30 Sep 2025 | Milestone 3 | Under the retail algo framework (original circular 4 Feb 2025, effective 1 Oct 2025 with a glide path), brokers shall participate in at least one mock session with the new functionality, complete in all aspects, by 3 Jan 2026, and provide **evidence of participation** to the exchange; non-adherence bars onboarding new retail API algo clients from 5 Jan 2026. | Yes |
| US | FINRA Regulatory Notice 15-09, *Guidance on Effective Supervision and Control Practices for Firms Engaging in Algorithmic Trading Strategies* | Testing and reviews | "Testing of algorithmic strategies prior to being put into production is an essential component of effective policies and procedures"; conduct significant testing in a development environment **segregated from production**; supervisory obligations **continue after** the strategy is in production, with controls, monitors, alerts and reconciliation to identify unintended results quickly. | **No** — "suggested effective practices"; the Notice states that implementing them would not in itself necessarily satisfy a firm's obligations under FINRA rules |

## How this skill maps onto the above

- The **paper-trading period** implemented here is an internal strategy-performance control.
  It is *not* the RTS 6 Art. 6 conformance test, and *not* the SEBI exchange simulated-
  environment/mock-session obligation. Those verify that your system speaks the venue's
  protocol correctly and behaves in an orderly way; they say nothing about whether your
  signal still works. Both are required; neither substitutes for the other.
- The **reduced-size initial live window** in the workflow corresponds to RTS 6 Art. 8's
  predefined deployment limits. RTS 6 requires that such limits exist and be set
  cautiously; it prescribes no figure, and this skill deliberately supplies none.
- The **human sign-off** corresponds to the RTS 6 Art. 5 designated-person authorisation.
  `record_sign_off` produces evidence that the decision was taken; it is not the decision.
- The **"a material change restarts the gate"** rule in the workflow comes from the ESMA
  briefing's material-change guidance and SEBI §7.1.9.5, and applies to changes in risk
  thresholds and kill-switch logic — which are easy to treat as configuration rather than
  as a redeployment.
- **Recordkeeping**: the `policy_applied`, `failed_checks`, `policy_weakened`, `advisories`
  and sign-off fields exist so the promotion decision is reconstructible. Retention periods
  vary by jurisdiction and are out of scope here — see `record-retention-periods-by-jurisdiction`.

## Explicitly not claimed

- That any regulator mandates paper trading, a minimum paper duration, a minimum trade
  count, a slippage tolerance, an accuracy band, a 25% or 10% initial live size, or a 2×
  rollback multiple. None do, on the evidence reviewed.
- That the SEBI Master Circular text cited is the current consolidated edition. SEBI
  reissues master circulars periodically and the retail-algo framework above post-dates the
  chapter cited; re-verify paragraph numbers against the current master circular before
  relying on them in a compliance document.
- Any position on jurisdictions not listed (US SEC Rule 15c3-5, MAS, ASIC, FCA
  supervisory expectations beyond assimilated RTS 6, etc.). See the dedicated skills for
  those — `sec-rule-15c3-5-risk-controls-us`, `mas-singapore-algo-trading-guidelines`,
  `asic-market-integrity-rules-automated-trading`, `uk-fca-algorithmic-trading-systems-controls`.

## Category

`deployment-ops` — see the top-level `mappings/` directory for how this category rolls up
across the full skill library.

## Sources

- Commission Delegated Regulation (EU) 2017/589 (RTS 6) — https://eur-lex.europa.eu/eli/reg_del/2017/589/oj/eng
- ESMA, Supervisory Briefing on Algorithmic Trading in the EU, ESMA74-1505669079-10311 (26 February 2026) — https://www.esma.europa.eu/sites/default/files/2026-02/ESMA74-1505669079-10311_Supervisory_Briefing_on_Algorithmic_Trading_in_the_EU.pdf
- SEBI, Master Circular for Stock Exchanges and Clearing Corporations, Chapter 2: Trading Software and Technology — https://www.sebi.gov.in/sebi_data/commondocs/jul-2021/Chapter%202%20-%20Trading%20Software%20and%20Technology_p.pdf
- SEBI Circular SEBI/HO/MIRSD/MIRSD-PoD/P/CIR/2025/132 (30 September 2025) — https://www.sebi.gov.in/legal/circulars/sep-2025/extension-of-timeline-for-implementation-of-sebi-circular-dated-february-04-2025-on-safer-participation-of-retail-investors-in-algorithmic-trading-_96979.html
- FINRA Regulatory Notice 15-09 — https://www.finra.org/rules-guidance/notices/15-09
