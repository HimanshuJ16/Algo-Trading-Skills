# Standards for Singapore Exchange SGX API Integration

All figures below were reconciled on **2026-08-28**. Every row names the source it came
from. SGX contract specifications change by circular; treat this file as a dated
snapshot, not as a security master.

## 1. Which engine an order goes to

| Market | Engine | Order entry | Market data | Notes |
|---|---|---|---|---|
| Derivatives (futures, options) | **Titan-DT** (Nasdaq Genium INET) | Titan OUCH, FIX order entry, OMnet/Genium API | Titan ITCH + GLIMPSE (MoldUDP64) | Live since the 2016 Titan migration |
| Securities (stocks, REITs, warrants, ETFs) | **Reach-ST** | SGX-ST member connectivity | — | SGX RegCo is replacing Reach-ST with **Iris-ST** in **H2 2027** |

Sources:
- [SGX Titan ITCH & GLIMPSE protocol specifications](https://api2.sgx.com/sites/default/files/2018-09/Titan%20ITCH%20&%20GLIMPSE%20Protocol%20Specifications.pdf)
- [SGX Titan ITCH/OUCH conformance test scope](https://api2.sgx.com/sites/default/files/2018-09/Titan%20Conformance%20Test%20Scope%20for%20ITCH%20OUCH.pdf)
- [Rajah & Tann — SGX RegCo consults on the Iris-ST trading engine (replaces Reach-ST, H2 2027)](https://www.rajahtannasia.com/viewpoints/sgx-regco-consults-on-details-of-new-trading-engine-iris-st-for-singapore-stock-market-with-new-and-enhanced-trading-functionalities/)

## 2. Titan-DT contract specifications (as shipped in `SGX_DERIVATIVES_CONTRACTS`)

| Code | Contract | Currency | Contract size | Outright tick | Other published increments | Source |
|---|---|---|---|---|---|---|
| `CN` | SGX FTSE China A50 Index Futures | USD | US$1 x index | **1 index point** (US$1) | not verified | [KGI Securities (SGX member) contract specification](https://www.kgieworld.sg/futures/sgx-FTSE-China-A50-index-futures-contract-specifications) |
| `NK` | SGX Nikkei 225 Index Futures | JPY | JPY 500 x index | **5 index points** (JPY 2,500) | calendar spread 1 point (JPY 500); T@IC 0.25 point (JPY 125, ticker `NKTI`) | [KGI Securities (SGX member) contract specification](https://www.kgieworld.sg/futures/sgx-nikkei-225-index-futures-and-options-contract-specifications) |
| `TWN` | SGX FTSE Taiwan Index Futures | USD | US$40 x index | **0.25 index point** (US$10) | strategy 0.25 (US$10); NLT 0.01 (US$0.40); T@IC 0.05 (US$2, ticker `TWNTI`) | [SGX FTSE Taiwan Index Futures and Options suite](https://www.sgx.com/derivatives/products/twnfc) |
| `FEF` | SGX Iron Ore CFR China (62% Fe Fines) Index Futures | USD | 100 metric tonnes, quoted US$/tonne | **US$0.01/tonne** (US$1 per contract) | not verified | [SGX contract specification (archived PDF)](https://api2.sgx.com/sites/default/files/2018-12/SGX%20TSI%20Iron%20Ore%20CFR%20China%20(62%25%20FE%20Fines)%20Index%20Futures.pdf) |

"Not verified" means no increment for that trade type was found in a source that could
be checked — the module raises `TickSizeUnavailableError` rather than reusing the
outright tick.

## 3. Specification changes that invalidate older tables

| Date | Change | Why it matters | Source |
|---|---|---|---|
| 5 Oct 2020 | `CN` minimum price fluctuation cut from **2.5 index points to 1** | SGX's own 2018 A50 specification PDF, still hosted on api2.sgx.com, shows 2.5. Any table derived from it rejects half the legal price grid. | [Trading Technologies platform notice, "SGX Change in Tick Size for FTSE China A50 Index Futures"](https://tradingtechnologies.com/support-updates/cme-new-product-addition-sgx-change-in-tick-size-for-ftse-china-a50-index-futures-fia-industry-wide-testing-and-more/); current spec per KGI above |
| 20 Jul 2020 | SGX listed **FTSE Taiwan (`TWN`)** after the MSCI Taiwan contract left SGX | The retired `TW` contract (US$100 x index, 0.1 point tick) is not tradeable on SGX. Its archived specification is still indexed. | [SGX FTSE Taiwan Index Futures and Options suite](https://www.sgx.com/derivatives/products/twnfc); [archived SGX MSCI Taiwan specification](https://api2.sgx.com/sites/default/files/2018-11/EN%20-%20TW%20Futures%20Contract%20Specifications.pdf) |
| 22 Jun 2026 | Mini Nikkei became Micro Nikkei **under the same code `NS`**: JPY 100 x index / 1-point tick became JPY 10 x index / 2.5-point tick; open positions converted 1:10 | The strongest argument for `verified_on` provenance on every spec: the code did not change, so nothing invalidated a code-keyed cache. Sourced from a broker reproduction of the SGX notice only, which is why `NS` is **not** in the shipped table. | [Yuanta Futures (HK) — SGX contract specification adjustment notice, 16 Jun 2026](https://www.yuantafutures.com.hk/en/Uploads/file/20260616/1781617014989124.pdf) |
| 1 Sep 2025 | One-time price adjustment to SGX iron ore futures in connection with the Platts IODEX move to a 61% Fe basis, affecting expiries from Jan 2026 | The `FEF` contract name and underlying basis have moved even though the code has not. Confirm the current contract name and basis against SGX circulars. | [Phillip Nova announcement](https://www.phillipnova.com.sg/announcement/sgx-iron-ore-index-futures-price-adjustments/); [S&P Global Platts iron ore specifications guide](https://www.spglobal.com/content/dam/spglobal/ci/en/documents/platts/en/our-methodology/methodology-specifications/metals/iron-ore-specifications.pdf) |

## 4. SGX-ST minimum bid size (Reach-ST securities), SGD-denominated

Governing document: **SGX-ST Regulatory Notice 8.5.2 — Minimum Bid Size**. The table
below is reproduced from two SGX member brokers' published market-information pages,
which agree exactly; reconcile against the Regulatory Notice text before production use.

| Security class | Price range (S$) | Minimum bid size (S$) |
|---|---|---|
| Stocks (excluding preference shares), REITs, business trusts, company warrants | below 0.20 | 0.001 |
| | 0.20 – 0.995 | 0.005 |
| | 1.00 and above | 0.01 |
| Structured warrants | below 0.20 | 0.001 |
| | 0.20 – 1.995 | 0.005 |
| | 2.00 and above | 0.01 |
| Bonds, debentures, loan stocks, preference shares | all | 0.001 |
| ETFs and ETNs | all | 0.01 **or** 0.001, as determined by SGX-ST per instrument |
| Daily Leverage Certificates | — | own scale; not modelled here |

The published upper edges (`0.995`, `1.995`) are the last on-tick price in the band, not
an exclusive bound: the module bands on the inclusive lower bound, so a price of
`0.9975` is off tick rather than being promoted into the band above. The
S$1.00–S$1.99 band was widened from S$0.005 to S$0.01 on **13 November 2017**; tables
predating that are still in circulation.

Sources:
- [Phillip Securities (POEMS) — minimum bid size FAQ](https://www.poems.com.sg/faq/trading/general/what-is-the-minimum-bid-size-for-trading/)
- [OCBC Securities — Singapore market information](https://www.iocbc.com/help-and-support/market-information/singapore)

## 5. Adjacent SGX rules this module deliberately does not enforce

| Rule | Where it lives | Why not here |
|---|---|---|
| Forced Order Range (±30 bids for most SGD securities), Force Key | SGX-ST Practice Note 8.6 / Regulatory Notice 11.4.2(g) | Needs the reference price; covered by `mas-singapore-algo-trading-guidelines` |
| SGX-ST circuit breaker band | SGX-ST Rule 8.14 and Regulatory Notice 8.14.1 | Needs the five-minute-lagged reference price |
| Board lot / minimum quantity | SGX-ST; standard lot 100 units, **price-tiered from 5 October 2026** (10 units above S$10, 1 unit above S$100 for specified instruments) | Per-security reference data; covered by `minimum-fill-size-and-lot-rounding-logic` |
| Daily price limits (A50: ±10%, ±15% with cooling-off) | SGX Futures Trading Rules / contract specifications | Needs the previous settlement price |
| Margin | SGX-DC | Not derivable from contract size |

Board lot source: [SGX news release, 1 July 2026 — custody structure enhancements from July, board lot reduction from 5 October 2026](https://links.sgx.com/FileOpen/20260701%20SGX%20custody%20structure%20enhancements%20to%20take%20effect%20from%20July%20board%20lot%20reduction%20in%20October.ashx?App=Announcement&FileID=894992).

## 6. Foreign-currency counters

From **15 July 2026** SGX no longer requires the minimum bid sizes of HKD-, RMB- and
JPY-denominated securities and futures contracts to be aligned with their home markets;
those bid sizes are set at the exchange's discretion. The SGD scale in section 4 must
not be assumed to carry over to a foreign-currency counter, and
`validate_securities_order` refuses a non-SGD currency for that reason. Source:
[Rajah & Tann — SGX RegCo consultation on board lot size reduction and removal of foreign currency bid size alignment](https://www.rajahtannasia.com/viewpoints/sgx-regco-consultation-on-reduction-of-board-lot-size-etc/).

## 7. Verification limits

- SGX's own product pages (`sgx.com`) and rulebook (`rulebook.sgx.com`) could not be
  retrieved directly during this reconciliation; the derivatives figures come from SGX
  product-page search results and SGX member brokers' published contract
  specifications, and the securities figures from two SGX member brokers. Where only a
  secondary source exists, the row says so.
- Trading hours, expiry calendars, position limits and give-up/allocation rules are out
  of scope for this skill and are not reproduced here.
