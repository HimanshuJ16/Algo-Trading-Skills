# Standards for Real-Time vs Delayed Data Entitlement Handling

Every standard below is traced to a primary source. Where a rule belongs to one
venue or one jurisdiction, that is stated — the delay boundary in particular is
**not** the same everywhere, and universalising it is the defect this skill exists
to prevent.

## What counts as "delayed" — by venue

| Venue / framework | Real-time boundary | Delayed definition | Source |
|---|---|---|---|
| CME Group | Information made available **within ten (10) minutes** of initial transmission | "made available more than ten (10) minutes, but less than eight (8) hours after initial transmission" | CME Group, *Data Licensing Policy Guidelines — Non-Display Use / Non-Display Licensing FAQ* |
| ICE Futures Europe | "The Exchange defines real-time data as any market data that is **< 10 minutes** old" | "market prices of traded contracts transmitted **more than 10 mins** from publication via the API" (Quote Vendor Agreement) | ICE Futures Europe, *Market Data Policy*, v4, January 2026 |
| Nasdaq | Delay interval configured per product; default 15 minutes | Delayed Data Products carry an eligible Delay Interval; example labels include "Data Delayed 15 minutes" and "Data Delayed 24 hours" | Nasdaq, *Display Requirements Policy* (2022) |
| ESMA terminology (EU) | "Real-time Data should mean market data delivered with a delay of **less than 15 minutes** after publication" | "Delayed Data should mean market data made available **15 minutes** after publication" | ESMA, *Guidelines on the MiFID II/MiFIR obligations on market data*, as reproduced in the ICE Futures Europe Market Data Policy terminology mapping |

**Implication for configuration.** `min_delay_minutes` is the smallest whole-minute
delay that clears the venue's boundary: 15 where the boundary is inclusive
(Nasdaq/ESMA), 11 where the venue requires *more than* ten minutes (CME, ICE).
`max_delay_minutes` encodes the upper bound where one exists — 479 minutes at CME,
beyond which the feed is end-of-day or historical Information under a separate
licence. Confirm both against the agreement you actually signed before relying on
them commercially.

## Engineering standards

| Standard | Requirement | Source |
|---|---|---|
| Venue-specific delay interval | The delay applied before serving a feed as delayed MUST be taken from that venue's published definition, never from a house default. | CME and ICE definitions above |
| No delayed serving without a policy | A venue with no configured delay definition MUST NOT be served delayed data. | Derived: no defensible interval or label exists for it |
| Under-throttled data is real-time | A delay that does not clear the venue's boundary MUST be treated as Real Time Information, not delayed. | CME: real-time is anything within ten minutes; ICE: < 10 minutes old |
| Prominent Delay Message | Delayed data MUST be displayed with a prominent delay message, "at or near the top of the page"; on a ticker it "should be interspersed with the market data at least every 90 seconds". | Nasdaq, *Display Requirements Policy* |
| Fail closed on an unrecognised tier | An entitlement tier outside the recognised set MUST be denied, never served. | Derived: an unclassified tier cannot be shown to be licensed |
| Denials assert no delivery | An audit record for a denied request MUST NOT describe a delay or a stream that was never served. | Derived: audit integrity |
| Delayed is still licensed | Non-Display Use of **Real Time and Delayed** Information MUST be reported on a per-Application basis at CME Group. | CME Group, *Data Licensing Policy Guidelines — Non-Display Use* |
| Decision record retention | Access decisions MUST be persisted durably for at least the audit look-back period (three years under the Nasdaq Global Data Agreement). | Nasdaq Global Data Agreement s.7(e) |

## Definitions and quotations

**Prominent Delay Message (Nasdaq).** "Distributors must display a Prominent Delay
Message on all Delayed Data Products." … "The delay message must prominently appear
on all displays containing Delayed Data, such as at or near the top of the page. In
the case of a ticker, the delay message should be interspersed with the market data
at least every 90 seconds." Nasdaq's example strings: *"Data Delayed 15 minutes"*,
*"Data Delayed 24 hours"*, *"Delayed Data"*, *"Del-15"*, *"Data Delayed until
Midnight CET"*. The requirement covers "all displays of Nasdaq data, including on
wall boards, tickers, mobile devices and audio announcements on voice response
services."
Source: Nasdaq, *Display Requirements Policy*, v2.1 (2022).

**Delayed data is not automatically free (Nasdaq).** "Currently, if a data product
is eligible for delayed pricing, and the data is delayed as per the Delayed Data
Policy, there may not be a charge for the usage of the delayed data, depending upon
the product selected." Nasdaq additionally requires customers to report Delayed
Data Recipients, and prior approval where delayed Information is provided for a fee.
Source: as above; Nasdaq data policies.

**Delayed data obligation (EU only).** Under Article 13(1) of MiFIR, trading
venues must make market data available free of charge 15 minutes after
publication. ESMA's guidelines state the obligation does not distinguish between
user types — professional and retail users alike should be able to access delayed
data free of charge. **Jurisdiction: EU/EEA.** It creates no equivalent obligation
on US or APAC venues, and it does not waive display, reporting or non-display
licensing conditions attached to that data.
Source: MiFIR Art. 13(1); ESMA, *Guidelines on the MiFID II/MiFIR obligations on
market data* (ESMA70-156-4263); ESMA, *MiFID II/MiFIR Review Report No. 1 on prices
for market data*.

**Delayed does not escape non-display licensing (CME).** "Non-Display Use of Real
Time and Delayed Information must be reported on a per Application basis", and all
Non-Display Use must be licensed under an Information License Agreement. Delayed
consumption by an automated Application is therefore a licensable activity, not a
loophole.
Source: CME Group, *Data Licensing Policy Guidelines — Non-Display Use*.

## Scope notes and confidence

- **Blocking execution on a delayed feed is an execution-safety control, not a
  cited regulatory prohibition.** No source consulted here states that a regulator
  forbids trading on delayed prices; CME's per-Application reporting of delayed
  non-display use implies the opposite in licensing terms. The block is justified
  by execution risk and by the fact that a delayed tier usually means the
  real-time licence was never bought. *Confidence: high on the sourcing, and the
  control is deliberately unconditional — see the SKILL's "When NOT to Use" if
  your strategy legitimately runs on delayed data.*
- **The 11 / 479 minute values for CME are this repo's whole-minute encoding** of
  "more than ten (10) minutes, but less than eight (8) hours". They are a
  modelling convention over the venue's wording, not figures CME publishes as
  such. *Confidence: medium-high — verify against your current ILA schedules.*
- **CME policy PDFs are served behind anti-scraping controls**; the CME wording
  quoted above was taken from indexed extracts of CME's own published policy
  documents rather than a direct fetch. *Confidence: medium-high.*
- **Nasdaq's per-product Delay Intervals are not enumerated here.** The policy
  defines the label and cadence obligations; the interval eligible for each
  product comes from that product's own Delayed Data Policy entry.
- Venues not named above (LSE, Euronext, JPX, HKEX, NSE/BSE, TSX) follow
  comparable but *not identical* patterns. This skill makes no claim about their
  delay boundaries. Do not assume the Nasdaq or CME structure transfers unread.

## Sources

- Nasdaq, *Display Requirements Policy* —
  https://www.nasdaqtrader.com/content/AdministrationSupport/Policy/DISPLAYREQUIREMENTSPOLICY.pdf
- Nasdaq, *US Equities and Options Data Policies* —
  https://www.nasdaqtrader.com/content/AdministrationSupport/Policy/USEquitiesandOptionsDataPolicies.pdf
- CME Group, *Data Licensing Policy Guidelines — Non-Display Use / Non-Display Licensing FAQ* —
  https://www.cmegroup.com/market-data/distributor/files/cme-group-data-licensing-policy-guidelines-and-non-display-licensing-faq.pdf
- ICE Futures Europe, *Market Data Policy*, v4, January 2026 —
  https://www.ice.com/publicdocs/futures/ICE_Futures_Europe_Market_Data_Policy.pdf
- ESMA, *Guidelines on the MiFID II/MiFIR obligations on market data* (ESMA70-156-4263) —
  https://www.esma.europa.eu/sites/default/files/library/esma70-156-4263_guidelines_mifid_ii_mifir_obligations_on_market_data.pdf
- ESMA, *MiFID II/MiFIR Review Report No. 1 on prices for market data and the equity consolidated tape* —
  https://www.esma.europa.eu/sites/default/files/library/mifid_ii_mifir_review_report_no_1_on_prices_for_market_data_and_the_equity_ct.pdf
