# Binary Options — Cited Regulatory and Risk Standards

Every rule in `scripts/binary_options.py` traces to a source below. Where a claim could
not be verified from a primary regulator source it is marked as such rather than stated.

**Scope limit:** this is decision support for engineering a pre-trade gate. It is not
legal advice, it covers a handful of regimes at a coarse level, and it omits most EU
member states entirely. Confirm with counsel for your entity and client base.

**Ruleset last verified against primary sources: 13 August 2026.** This date is mirrored
in `RULESET_LAST_VERIFIED`; the engine warns once evaluations run more than
`RULESET_MAX_AGE` (default 180 days) past it.

## 1. Why this skill treats rules as dated configuration

Binary options regulation has moved repeatedly, and in at least three verified ways that
would break a hardcoded implementation:

- ESMA's EU-wide prohibition was temporary and **lapsed in 2019** (§3).
- ASIC's ban was **extended in 2022** from an initial short term out to 2031 (§5).
- The venue list in the joint CFTC/SEC alert has **changed since publication** (§2).

Accordingly the engine hardcodes as little as possible, carries an explicit verification
date, defaults to deny, and takes venue registration as caller-supplied data.

## 2. United States — CFTC and SEC

**Permitted, but only on a registered venue.**

- Binary options on commodities are lawful for US persons only on a CFTC-registered
  designated contract market (DCM). The joint alert states that entities other than the
  named DCMs "offering binary options that are commodity options transactions are doing
  so illegally."
  Source: [CFTC/SEC Investor Alert: Binary Options and Fraud](https://www.cftc.gov/LearnAndProtect/AdvisoriesAndArticles/fraudadv_binaryoptions.html)
- Where a binary option's return is tied to a company's securities price it is a
  **security**, and "a company may not lawfully offer or sell securities unless the offer
  and sale have been registered with the SEC or an exemption from such registration
  applies." Platforms operating as unregistered broker-dealers or exchanges violate
  federal law. Source: same alert.
- Off-exchange binary options offered to US persons are the CFTC's stated concern, with
  documented refusal to credit accounts, refusal to return funds, identity theft, and
  manipulation of trading software.
  Source: [CFTC — Beware of Off-Exchange Binary Options Trades](https://www.cftc.gov/LearnAndProtect/AdvisoriesAndArticles/beware_of_off_exchange_binary_options.htm)
- The CFTC advises checking registration status before trading, and maintains a
  Registration Deficient (**RED**) List of unregistered foreign entities.
  Sources: [CFTC — Avoid Unregistered Binary Options Trading Platforms](https://www.cftc.gov/LearnAndProtect/AdvisoriesAndArticles/avoid_unregistered_binary_options_platforms.htm);
  [CFTC Press Release 8225-20 — additions to the RED List](https://www.cftc.gov/PressRoom/PressReleases/8225-20)

**Venue names are deliberately not hardcoded.** The joint alert names three DCMs — Cantor
Exchange LP, Chicago Mercantile Exchange Inc., and North American Derivatives Exchange
(Nadex). That list is a point-in-time statement and has moved: Nadex was acquired in 2022
and now does business as Crypto.com | Derivatives North America, retiring the Nadex.com
platform in December 2025. Cantor Exchange's current DCM status **could not be verified**
in this pass. Treat any venue whitelist as dated, owned configuration checked against the
CFTC's current DCM list, not as a literal in code.

Implemented as: the venue gate in `ComplianceEngine.validate_trade` denies anything whose
`venue_status` is not `REGISTERED`, in every jurisdiction — `REG_VENUE_NOT_REGISTERED`
for `UNREGISTERED`, `REG_VENUE_STATUS_UNKNOWN` for the default `UNKNOWN`. `_rule_us_cftc`
therefore has nothing further to deny on: in the US the venue *is* the requirement.

## 3. European Union — the ESMA measure has expired

**This is the most commonly mis-stated fact about binary options.**

- ESMA adopted a **temporary** EU-wide prohibition on the marketing, distribution and
  sale of binary options to retail clients: Decision (EU) 2018/795, in force 2 July 2018,
  under MiFIR product intervention powers, applying to retail clients only.
  Source: [ESMA — agreement to prohibit binary options and restrict CFDs](https://www.esma.europa.eu/press-news/esma-news/esma-agrees-prohibit-binary-options-and-restrict-cfds-protect-retail-investors)
- ESMA **did not renew** it. The prohibition "expired at the end of the day on 1 July
  2019", because "most national competent authorities (NCAs) have taken permanent
  national product intervention measures relating to binary options that are at least as
  stringent as ESMA's measure."
  Source: [ESMA ceases renewal of product intervention measure relating to binary options](https://www.esma.europa.eu/press-news/esma-news/esma-ceases-renewal-product-intervention-measure-relating-binary-options)
- The binding measures are now **national**, adopted by each NCA under **Article 42 of
  MiFIR**, which permits permanent measures. Example: BaFin's General Administrative Act
  under MiFIR Article 42 regarding binary options.
  Source: [BaFin — General Administrative Act pursuant to Article 42 MiFIR regarding binary options](https://www.bafin.de/SharedDocs/Veroeffentlichungen/EN/Aufsichtsrecht/Verfuegung/vf_190701_allgvfg_Binaere_Optionen_en.html)

Implemented as: `_rule_eu_esma` denies retail (every national measure ESMA relied on is at
least as stringent), and returns `REG_JURISDICTION_UNRESOLVED` for non-retail rather than
approving — a single `EU_ESMA` enum member cannot stand in for 27 national measures.
Supply your own per-member-state rules via `ComplianceEngine(jurisdiction_rules=...)`.

## 4. United Kingdom — FCA permanent retail ban

- The FCA permanently prohibited "the sale, marketing and distribution of binary options
  to retail consumers", effective **2 April 2019**.
- The FCA ban is **wider than ESMA's**: it captures all binary options "including
  'securitised binary options'".
- **Retail only.** Firms may apply to limit permissions to offer these products to
  professional clients.
- Applies to activity "in, or from, the UK", including UK branches of third-country
  investment firms.
  Source: [FCA PS19/11 — Product intervention measures for retail binary options](https://www.fca.org.uk/publications/policy-statements/ps19-11-product-intervention-measures-retail-binary-options)
- Handbook rules: **COBS 22.4** — "Prohibition on the retail marketing, distribution and
  sale of derivative contracts of a binary or other fixed outcomes nature". Application
  at COBS 22.4.1R; prohibitions at COBS 22.4.4R, covering investments specified in
  articles 85(4A) and 85(4B) of the Regulated Activities Order, addressed to retail
  clients. "Marketing" includes communicating and/or approving financial promotions.
  Source: [FCA Handbook COBS 22.4](https://www.handbook.fca.org.uk/handbook/COBS/22/4.html)

  Note: **COBS 22.6 is a different prohibition** (cryptoasset derivatives and cryptoasset
  ETNs) and is sometimes miscited for binary options.

Implemented as: `_rule_uk_fca` denies `ClientCategory.RETAIL`.

## 5. Australia — ASIC product intervention order

- ASIC banned the issue and distribution of binary options to retail clients under the
  **ASIC Corporations (Product Intervention Order—Binary Options) Instrument 2021/240**,
  in effect from **3 May 2021**.
  Source: [ASIC 21-064MR — ASIC bans the sale of binary options to retail clients](https://www.asic.gov.au/about-asic/news-centre/find-a-media-release/2021-releases/21-064mr-asic-bans-the-sale-of-binary-options-to-retail-clients/)
- The order was **extended until 1 October 2031**.
  Source: [ASIC 22-243MR — ASIC's binary options ban extended until 2031](https://www.asic.gov.au/about-asic/news-centre/find-a-media-release/2022-releases/22-243mr-asic-s-binary-options-ban-extended-until-2031/)

Implemented as: `_rule_au_asic` denies `ClientCategory.RETAIL`.

## 6. Canada — a differently shaped rule

This regime is included specifically because its rule shape breaks the
retail-vs-professional model that most binary options gates assume.

- **CSA Multilateral Instrument 91-102 Prohibition of Binary Options** prohibits
  advertising, offering, selling or otherwise trading a binary option **with a term to
  maturity of less than 30 days** with or to an **individual**.
- It applies to individuals **including those who are accredited investors** — a
  sophisticated or professionally categorised natural person is *not* exempt.
- Adopted by the CSA members **other than the British Columbia Securities Commission**;
  in force **12 December 2017**.
- The Companion Policy confirms the prohibition extends to offers and solicitations made
  through a website or other electronic means.
  Sources: [OSC — Multilateral Instrument 91-102](https://www.osc.ca/en/securities-law/instruments-rules-policies/9/91-102/multilateral-instrument-91-102-prohibition-binary-options);
  [OSC — CSA Multilateral Notice of MI 91-102 and Companion Policy](https://www.osc.ca/en/securities-law/instruments-rules-policies/9/91-102/csa-multilateral-notice-multilateral-instrument-91-102-prohibition-binary-options-and-related);
  [ASC — Canadian securities regulators announce ban on binary options](https://www.asc.ca/en/news-and-publications/news-releases/2018/10/canadian-securities-regulators-announce-ban-on-binary-options)

Implemented as: `_rule_ca_csa` uses `is_natural_person` and `term_to_maturity`, **not**
`client_category`, and denies when `is_natural_person` is unknown. The British Columbia
carve-out is noted in the citation string; if you trade BC clients, model it separately.

## 7. Event contracts and prediction markets — ESMA, 3 July 2026

- ESMA published a public statement dated **03/07/2026** reminding firms of their
  obligations under the binary options measures given the growth of prediction markets.
- Event contracts are "products whose financial outcome is binary (a fixed payout or no
  payout at all)". Where they qualify as financial instruments they "fall within the
  scope of the existing national product intervention measures on binary options",
  which prohibit marketing, distribution or sale to retail clients.
- ESMA also states that "distribution of event contracts qualifying as financial
  instruments in the EU requires an authorisation as investment firm, even where only
  distributed to non-retail clients."
  Source: [ESMA reminds firms of existing rules and obligations under binary option measures](https://www.esma.europa.eu/press-news/esma-news/esma-reminds-firms-existing-rules-and-obligations-under-binary-option-measures)

Whether a given event contract is a MiFID financial instrument is a case-by-case
classification question turning on its underlying, and requires legal analysis — this
skill does not attempt to automate that determination.

## 8. Israel — noted, not implemented

Israel's Securities Law amendment (passed 23 October 2017) prohibited the binary options
industry, including Israeli-managed platforms offering binary options to clients
**outside** Israel. This extraterritorial shape is materially different again from the
retail-ban model.

Primary Israel Securities Authority sourcing **was not obtained** in this pass — the
available sources were secondary press coverage — so no rule is implemented for it and
the claim is flagged as unverified. Confirm directly with the ISA before relying on it.

## 9. Risk standards for discontinuous payoffs

These are firm policy positions, not regulatory requirements, and are labelled as such in
code:

- **Exposure is measured as full notional.** A binary payoff jumps at the strike, so there
  is no partial-loss regime to net or interpolate against; the worst case is the entire
  amount at risk. Stress and VaR scenarios must assume the discontinuous outcome.
- **Near-expiry concentration cap** (`max_pin_risk_exposure`): aggregate registered
  notional expiring inside `pin_window`. This is explicitly **not** a Greeks-based
  measure — it consumes no spot price and no volatility surface. It exists because
  near-expiry binaries are where discontinuity risk concentrates, and because a limit that
  is merely declared and never evaluated is worse than no limit. For delta/gamma
  behaviour near the strike, see `options-pin-risk-management-at-expiry`.
- **`pin_window` is a policy parameter**, not a regulatory threshold. The default of 24
  hours is a starting point for calibration, not a standard.
- **Default-deny** on unknown jurisdiction, unknown venue status, and unresolvable client
  facts. In a regime this restrictive, an unknown is far more likely to be prohibited
  than permitted.
