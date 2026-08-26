# Standards for Per-Venue Market Data Entitlement

Every standard below is traced to a primary source. Where a rule belongs to one
venue or one plan, that is stated — do not universalise it to every exchange
without reading the agreement you actually signed.

## Engineering standards

| Standard | Requirement | Source |
|---|---|---|
| Per-venue non-display entitlement | Non-display rights MUST be held and checked per venue. Fees and reporting requirements vary by data product at Nasdaq, are charged per DCM at CME Group, and are declared per segment at LSE. | Nasdaq *US Equities and Options Data Policies* s.7; CME *Data Licensing Policy Guidelines — Non-Display Use*; LSE *Non-Display and Other Application Usage — Customer Declaration* s.4 |
| Per-activity non-display entitlement | The non-display activity category (trading as principal / facilitating client business / operating a trading platform) MUST be licensed individually. | CME Category A1/A2/A3; LSE Non-Display Declaration s.4 |
| Depth licensed separately | Depth-of-book MUST NOT be served under a top-of-book entitlement. | Nasdaq price schedule ("U.S. NASDAQ DEPTH [TOTALVIEW/ LEVEL 2] NON-DISPLAY PROFESSIONAL [INTERNAL]"); LSE Declaration matrix (Level 1 / Level 2 rows) |
| Display never confers non-display | A display entitlement MUST NOT authorise automated consumption. | Nasdaq *US Equities and Options Data Policies* s.9: "Nasdaq Basic – Display Professional Subscribers ONLY (Non-Display Usage is NOT included)" |
| Professional is the default | Subscribers MUST be treated as Professional unless positively qualified as Non-Professional, and only natural persons may so qualify. | Nasdaq s.5; CTA *Nonprofessional Subscriber Policy* |
| Distributor verifies classification | The distributor MUST verify Non-Professional status rather than accept the subscriber's assertion. | CTA *Nonprofessional Subscriber Policy* |
| Periodic re-verification | Non-Professional status for retired and inactive professionals MUST be re-verified semi-annually. | CTA *Nonprofessional Subscriber Policy* s.D |
| Fail closed on unrecognised usage | A usage type that cannot be classified MUST be refused, not defaulted to display. | Derived: no venue licence covers an undeclared use (Nasdaq GDA s.4(c) requires prior written approval for uses not already provided for) |
| Term enforcement | A request evaluated after the licensed term has ended MUST be denied. | Contractual term; general |
| Decision record retention | Access decisions MUST be persisted durably for at least the audit look-back period (three years under the Nasdaq Global Data Agreement). | Nasdaq GDA s.7(e) |

## Definitions that drive the checks

**Non-Display Usage** — "any method of accessing Exchange Information other than
Display Usage … a means of accessing Nasdaq data that involves automated access or
use by a machine, without access or use of a Display by a natural person or
persons." Non-display is fee-liable regardless of whether the OMS, EMS or trading
infrastructure is virtual, cloud-hosted, in a datacenter, enterprise, or on an
individual's desktop.
Source: Nasdaq, *US Equities and Options Data Policies*, v2.6, s.7.

**Non-Display unit of count** — the greater of (a) the number of Subscribers that
can modify the application in real time, or (b) the number of Devices (usually
servers) that receive and benefit from the Information, including servers running
computations or creating derived data. Multiple cores on one physical device count
once; GPUs and memory attached to an already-counted server are not counted
separately. **This engine does not compute it** — derive it from your
infrastructure inventory.
Source: as above, s.7 and the definitions table.

**Non-Professional Subscriber (Nasdaq)** — "Any natural person who is NOT: (a)
registered or qualified in any capacity with the SEC, the Commodities Futures
Trading Commission, any state securities agency, any securities exchange or
association or any commodities or futures contract market or association; (b)
engaged as an 'investment advisor' as that term is defined in Section 202(a)(11)
of the Investment Advisors Act of 1940 (whether or not registered or qualified
under that Act); or (c) employed by a bank or other organization exempt from
registration under federal or state securities laws to perform functions that
would require registration or qualification if such functions were performed for
an organization not so exempt." "All Subscribers are deemed to be Professional
unless they are qualified as Non-Professional Subscribers." For a Non-Professional
Subscriber, "Information is licensed only for personal use."
Source: as above, s.5.

**Nonprofessional Subscriber (CTA/CQ)** — "any natural person who receives market
data solely for his/her personal, non-business use and who is not a 'Securities
Professional'." An account not registered to a natural person is Professional:
"even though an individual natural person may be receiving market data only for
her personal, non-business use, if the market data is received through an
organization's account, this individual is classified as a Professional
Subscriber." And, on trusts and investment clubs: "the Trust is an organization,
and by definition only natural persons can qualify as Nonprofessionals."
Source: NYSE/CTA, *Nonprofessional Subscriber Policy*, November 2016.

**Non-display activity categories** — LSE's declaration asks customers to classify
non-display "trading based activities" as **Principal** ("trading based activities
as 'principal'"), **Client Facilitation** ("to facilitate customer business") or
**Trading Platforms** ("the operation of trading platforms including but not
restricted to systematic internalisers/multilateral trading facilities"). CME
Group's Category A splits the same way: A1 trading as a principal, A2 facilitating
client business, A3 trading on an alternative venue.
Sources: LSE, *Non-Display and Other Application Usage — Customer Declaration*,
v12.0, s.4; CME Group, *Data Licensing Policy Guidelines — Non-Display Use*.

**Designated Contract Market (DCM)** — CME Group operates CME, CBOT, NYMEX and
COMEX as separate DCMs. Non-display licence fees are charged on a per-DCM basis,
and automated trading using additional DCMs requires additional Category A
licensing. Treat each as its own venue entitlement.
Source: CME Group, *Data Licensing Policy Guidelines — Non-Display Use*.

## Audit and back-fee exposure (why the gate fails closed)

- **CTA/CQ** — "If NYSE finds that the vendor has incorrectly qualified a
  professional subscriber as nonprofessional, the vendor will be liable for
  retroactive fees billed by NYSE for the subscriber at the professional rate."
  (*Nonprofessional Subscriber Policy*, header.)
- **Nasdaq** — under the Global Data Agreement, Nasdaq may audit a Distributor's
  records, reports and systems (s.7(a)); underreported amounts plus interest fall
  due within sixty days, and for a good-faith error liability reaches back three
  years (s.7(e)); a shortfall of 10% or more of reported Reportable Units also
  makes the Distributor liable for Nasdaq's audit, legal and administrative costs
  (s.7(f)).
- **CME Group** — Licensees must declare all Applications using real-time or
  delayed CME Group Information, and must report Applications added or removed in
  the month the change occurs. Fees are assessed for a Non-Display Application if
  it is entitled to CME Group Information for any number of days in a month.
- **LSE** — "an Order Form must be submitted to the relevant Exchange and executed
  for the relevant Licensable Activity, irrespective of whether a Licensable
  Activity has Charges associated with it or not." A zero-fee activity is still a
  declarable one.

## Scope notes and confidence

- The `L1`/`L2`/`L3` ladder is a repo-internal abstraction over venue-specific
  product tiers (Nasdaq TotalView/Level 2, LSE Level 1/Level 2, CME MBP/MBO). It
  is a modelling convenience, **not** a claim that every venue uses those labels.
  Map your venue's products onto it deliberately.
- The rule that non-display consumption cannot run under a Non-Professional
  declaration is derived, not quoted: Nasdaq licenses Non-Professional Information
  "only for personal use" and prices its internal non-display depth entitlement as
  "NON-DISPLAY PROFESSIONAL". *Confidence: medium-high.* Confirm the treatment
  with each venue before relying on it commercially.
- CME Group's policy PDFs are served behind anti-scraping controls; the CME facts
  above were taken from indexed extracts of CME's own published policy documents
  rather than a direct fetch. *Confidence: medium-high — read the current
  guidelines and your executed ILA schedules before encoding fee categories.*
- Eurex, JPX, HKEX and other venues follow comparable per-venue, per-activity
  licensing patterns, but this skill makes no specific claim about their terms.
  Do not assume the CME/LSE/Nasdaq structure transfers unread.

## Sources

- Nasdaq, *US Equities and Options Data Policies*, v2.6 —
  https://www.nasdaqtrader.com/content/AdministrationSupport/Policy/USEquitiesandOptionsDataPolicies.pdf
- Nasdaq, *Data News #2015-9: Clarification for U.S. Non-Display Policy* —
  https://www.nasdaqtrader.com/TraderNews.aspx?id=dn2015-09
- NYSE/CTA, *Nonprofessional Subscriber Policy* —
  https://www.ctaplan.com/publicdocs/ctaplan/Policy_Non-Professional_Subscribers_CTA.pdf
- CME Group, *Data Licensing Policy Guidelines — Non-Display Use / Non-Display Licensing FAQ* —
  https://www.cmegroup.com/market-data/distributor/files/cme-group-data-licensing-policy-guidelines-and-non-display-licensing-faq.pdf
- CME Group, *Information License Agreement Guide* —
  https://www.cmegroup.com/market-data/files/information-license-agreement-ila-guide.pdf
- CME Group, *Designated Contract Markets* —
  https://www.cmegroup.com/company/designated-contract-market.html
- London Stock Exchange, *Non-Display and Other Application Usage — Customer Declaration*, v12.0 —
  https://docs.londonstockexchange.com/sites/default/files/documents/non-display_customer_declaration.pdf
- London Stock Exchange, *Market Data Policy Guidelines* —
  https://docs.londonstockexchange.com/sites/default/files/documents/market-data-policy-guidelines-2025_0.pdf
