# Standards for Regional Broker Data Residency Constraints

Each row states what the instrument *actually* requires of a deployment, and who
it binds. Several regimes routinely cited as hosting-region mandates impose none.

## Broker / exchange access controls (enforced at the API boundary)

| Constraint | Instrument | What it actually requires |
|---|---|---|
| Static IP for API order placement (India) | SEBI Circular SEBI/HO/MIRSD/MIRSD-PoD/P/CIR/2025/0000013, *Safer participation of retail investors in Algorithmic trading* (4 Feb 2025) | Brokers must not permit open APIs; API access for algo order flow is allowed only through a unique vendor/client-specific API key and a static IP whitelisted by the broker, so the algo provider and end user are identifiable and traceable. Operationalised by NSE circular NSE/INVG/67858 and subsequent implementation standards/FAQs, applicable to all stock brokers from 1 Apr 2026. **This is a network-identity rule, not a location rule.** |
| Zerodha Kite Connect | Zerodha Kite Connect API FAQs; Z-Connect post on the NSE retail-algo circular | Static IP required for **order requests only**; all other endpoints (WebSocket data, order book, positions) remain reachable from any address. IPv4 and IPv6 both accepted. Configured at developer-account level for all apps; up to two IPs (primary + secondary); one change per calendar week. Sharing a registered IP is permitted only with immediate family (spouse, dependent children, dependent parents) — wider sharing risks suspension of the account and all associated API keys. Zerodha states the IP **need not be India-based**, recommends AWS Mumbai for latency, and reported that a "hosted on broker's cloud servers" requirement was revised out of the circular. |
| Upstox Developer API | Upstox Developer API — static IP APIs and algo-trading circular announcement | `GET /user/ip` reads the registered `primary_ip` / optional `secondary_ip`; update endpoint registers them. One primary and one secondary IP per user, changeable once per calendar week. Non-order endpoints (holdings, positions, funds, historical candles) are not restricted by static IP. Once enforcement is active, orders from unregistered addresses are rejected. |
| DEGIRO | — | No official public trading API is published. Any integration relies on an unofficial client; see `degiro-unofficial-api-risk-assessment`. |
| Alpaca | — | No static-IP whitelisting requirement is documented for the trading API. |

## Residency / localisation regimes (bind the entity the rule addresses)

| Regime | Instrument | What it actually requires |
|---|---|---|
| India — securities market | SEBI CSCRF, Data Security standard PR.DS.S2 (Aug 2024) | Data-localisation provisions **kept in abeyance** by SEBI circular of 31 Dec 2024 and not reinstated by the Apr 2025 amendment or the Aug 2025 technical clarifications. Not an in-force mandate; abeyance is not repeal, so track it. Addresses SEBI regulated entities, not their clients. |
| India — payment data | RBI circular DPSS.CO.OD No.2785/06.08.005/2017-2018, *Storage of Payment System Data* (6 Apr 2018) | Payment system data must be stored only in India; foreign processing is permitted but the data must be brought back and deleted abroad. Scope is payment system data and payment system providers — not client-side algo hosting or securities trade records generally. |
| India — personal data | DPDP Act 2023 s.16; DPDP Rules 2025 (notified 14 Nov 2025), Rule 15 | A negative-list model: transfer abroad is permitted except to countries the Central Government restricts by notification, and subject to requirements the Government may specify. The cross-border provisions carry an ~18-month phase-in from 13 Nov 2025 (May 2027), and no restricted-country notification had been issued as of Aug 2026. No general localisation mandate. |
| EU | GDPR Chapter V (Arts. 44–49) | **No localisation requirement.** A third-country transfer requires a Chapter V mechanism (Art. 45 adequacy, Art. 46 SCCs, Art. 49 derogation). |
| EU — market conduct | MiFID II Art. 16(6) | Records retained and made available to the competent authority. No storage-location mandate. |
| EU — operational resilience | DORA, Regulation (EU) 2022/2554, Art. 29 and Art. 30(2)(b) | Art. 29 requires a preliminary ICT concentration-risk assessment before contracting for critical or important functions, including risks from providers/subcontractors established in third countries. Art. 30(2)(b) requires the contract to state "the locations, namely the regions or countries, where the contracted or subcontracted functions and ICT services are to be provided and where data is to be processed, including the storage location", with advance notice of any change of location. This is a **contractual transparency and risk-assessment duty on financial entities**, not a mandate to host in the EU. |
| EU — non-personal data | Regulation (EU) 2018/1807 (free flow of non-personal data) | Member-State data-localisation requirements for non-personal data are prohibited save on public-security grounds — the EU legal current runs against localisation, not toward it. |
| US | SEC Rule 17a-4(a), (b), (j) | Retention (6 years for 17a-4(a) records, first two easily accessible; 3 years for 17a-4(b)) and prompt furnishing of legible, true, complete and current copies. **No residency mandate.** |
| US — non-resident firms | SEC Rule 17a-7 (17 CFR 240.17a-7) | Each **non-resident** registered broker-dealer must keep copies of required books and records at a place within the United States, unless it files a written undertaking acceptable to the Commission to furnish records on demand within 14 days. Binds registered broker-dealers, not their clients. |

## Sources

- SEBI, *Safer participation of retail investors in Algorithmic trading*, Circular SEBI/HO/MIRSD/MIRSD-PoD/P/CIR/2025/0000013 (4 Feb 2025) — https://www.sebi.gov.in/legal/circulars/feb-2025/safer-participation-of-retail-investors-in-algorithmic-trading_91614.html
- NSE, Circular NSE/INVG/67858 (implementation standards for API-based algo trading) — https://nsearchives.nseindia.com/content/circulars/INVG67858.pdf
- Zerodha, *Kite Connect API FAQs* (static IP for order requests; two IPs; one change per calendar week; family-only sharing) — https://support.zerodha.com/category/trading-and-markets/general-kite/kite-api/articles/kite-connect-api-faqs
- Zerodha Z-Connect, *A comprehensive overview of NSE's circular on the new retail algo trading framework* (IP need not be India-based; AWS Mumbai recommended for latency) — https://zerodha.com/z-connect/general/a-comprehensive-overview-of-nses-circular-on-the-new-retail-algo-trading-framework
- Upstox Developer API, *Static IP Management APIs* — https://upstox.com/developer/api-documentation/announcements/static-ip-apis/
- Upstox Developer API, *Algo Registration & Static IP Requirement* — https://upstox.com/developer/api-documentation/announcements/algo-trading-circular/
- SEBI, *Cybersecurity and Cyber Resilience Framework (CSCRF)* and subsequent clarifications (PR.DS.S2 kept in abeyance) — https://www.sebi.gov.in/legal/circulars/aug-2025/technical-clarifications-to-cybersecurity-and-cyber-resilience-framework-cscrf-for-sebi-regulated-entities-res-_96329.html
- RBI, *Storage of Payment System Data* — FAQs — https://www.rbi.org.in/commonman/english/scripts/FAQs.aspx?Id=2995
- DPDP Rules 2025, Rule 15 (transfer of personal data outside India) — https://www.dpdpa.com/dpdparules/rule15.html
- Regulation (EU) 2022/2554 (DORA), Arts. 29–30 — https://eur-lex.europa.eu/eli/reg/2022/2554/oj/eng
- Regulation (EU) 2018/1807 (free flow of non-personal data), Art. 4 — https://eur-lex.europa.eu/eli/reg/2018/1807/oj/eng
- 17 CFR § 240.17a-7, *Records of non-resident brokers and dealers* — https://www.law.cornell.edu/cfr/text/17/240.17a-7
- 17 CFR § 240.17a-4, *Records to be preserved by certain exchange members, brokers and dealers* — https://www.law.cornell.edu/cfr/text/17/240.17a-4
