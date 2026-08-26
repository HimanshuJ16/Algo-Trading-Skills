# Standards for LSE Millennium Exchange

Every row below is traceable to the source named in it. Where a value is inferred rather
than published, this document says so.

## Exchange specifications

| Item | Requirement | Source |
|---|---|---|
| Instrument identifier on trading messages | "Instruments are identified on trading messages using a unique InstrumentID", specified in Tag 48 `SecurityID` on FIX messages. The TIDM is a display mnemonic; if the ISIN or TIDM changes, the instrument is deleted and re-added. | MIT201 §4.6 Symbology |
| TIDM field | `TIDM STRING(4)` — "The mnemonic code is allocated by the Exchange and used to identify a tradable instrument." Not restricted to A–Z. | MIT401 §2.7 instrument record |
| Trading currency | `Currency STRING(4)` — "the currency in which prices for a tradable instrument must be expressed. A list of all codes is contained in ISO 4217 … except that, for SEAQ compatibility, GBX has been retained." A trading currency change is a reference-data event. | MIT401 §2.7; MIT201 §8 |
| Tick size definition | "The price format or tick size is the minimum valid increment in which order and quote prices can be entered and displayed. Each tick size is a numeric amount, representing a multiple of the unit of currency in which the instrument is quoted." | MIT201 §5.5 |
| Off-tick handling | "If the price of an order/quote is not a multiple of the tick size on entry it will be rejected." | MIT201 §5.5 |
| Static vs dynamic ticks | A static tick is a single fixed value per security until amended by the Exchange; under a dynamic schedule the tick "is determined with reference to the intended price of the incoming order/quote". The regime, sizes and price format codes are published in the Business Parameters document. | MIT201 §5.5, §4.3 |
| Price tick reference data | The Price Tick File carries `Price Tick Table ID`, `Decimals`, `Description`, `Min Value` (lower price band), `Max Value` (upper price band) and `Tick Value`; each instrument record carries its `Price Tick Table ID`, `ADNT` and `Liquid` flag. | MIT401 §2.12, §2.7 |
| Order price field | "This value should be greater than zero and a multiple of the instrument's 'tick'." Applies to `Price` and `Stop price`. | MIT201 §7 order fields |
| Trade reports | "Price format codes have no relevance for the price field of manual trade reports." | MIT201 §5.5 |

Sources: London Stock Exchange, *MIT201 – Guide to the Trading System*, Issue 15.8, effective
19 January 2026; *MIT401 – Guide to Reference Data Services*, Issue 11.17. Both published at
`docs.londonstockexchange.com`.

## Tick size regime — UK RTS 11

The applicable instrument is **Commission Delegated Regulation (EU) 2017/588** ("RTS 11"),
*regulatory technical standards on the tick size regime for shares, depositary receipts and
exchange-traded funds*, assimilated into UK law and maintained in the FCA Handbook technical
standards. It is **not** RTS 28 (Delegated Regulation (EU) 2017/576), which is the
best-execution top-five-venue report.

| Provision | Requirement |
|---|---|
| Article 2(1) | Venues apply to orders in shares and depositary receipts a tick "equal to or greater than" the Annex cell for (a) the liquidity band matching the instrument's average daily number of transactions on the most relevant market in terms of liquidity, and (b) the price range in that band matching the order price. **The Annex is a floor, not the venue's tick.** |
| Article 2(2) | Derogation: where the most relevant market operates only a periodic auction with a trading algorithm run without human intervention, the lowest-ADNT liquidity band applies. |
| Article 2(2A) *(UK only)* | Derogation: for an instrument admitted to trading on a third-country venue, venues **may** apply the tick used by the third-country venue where it was first admitted. Added by FCA instrument FCA 2023/19 Annex C, in force **28 April 2023**. |
| Article 3(1) | The FCA calculates and publishes the ADNT annually, by 1 March; venues apply the resulting band from the first Monday of April. Published through FCA FITRS. |

Sources: FCA Handbook technical standards, *Commission Delegated Regulation (EU) 2017/588*
(Annex tick size table); FCA, *PS23/4 Improving Equity Secondary Markets*, May 2023, Annex C
(amending instrument FCA 2023/19). As of the FCA's June 2026 research note *A closer look at
the UK tick size*, "the FCA does not currently have plans to change the tick size regime".

### RTS 11 Annex — tick by price range and liquidity band

Liquidity bands are defined on the average daily number of transactions (ADNT): band 1 is
`0 ≤ ADNT < 10`, band 2 `< 80`, band 3 `< 600`, band 4 `< 2,000`, band 5 `< 9,000`, band 6
`≥ 9,000`. The price is read in the unit the instrument is quoted in — pence for a GBX line.

| Price range | LB1 | LB2 | LB3 | LB4 | LB5 | LB6 |
|---|---|---|---|---|---|---|
| 0 ≤ P < 0.1 | 0.0005 | 0.0002 | 0.0001 | 0.0001 | 0.0001 | 0.0001 |
| 0.1 ≤ P < 0.2 | 0.001 | 0.0005 | 0.0002 | 0.0001 | 0.0001 | 0.0001 |
| 0.2 ≤ P < 0.5 | 0.002 | 0.001 | 0.0005 | 0.0002 | 0.0001 | 0.0001 |
| 0.5 ≤ P < 1 | 0.005 | 0.002 | 0.001 | 0.0005 | 0.0002 | 0.0001 |
| 1 ≤ P < 2 | 0.01 | 0.005 | 0.002 | 0.001 | 0.0005 | 0.0002 |
| 2 ≤ P < 5 | 0.02 | 0.01 | 0.005 | 0.002 | 0.001 | 0.0005 |
| 5 ≤ P < 10 | 0.05 | 0.02 | 0.01 | 0.005 | 0.002 | 0.001 |
| 10 ≤ P < 20 | 0.1 | 0.05 | 0.02 | 0.01 | 0.005 | 0.002 |
| 20 ≤ P < 50 | 0.2 | 0.1 | 0.05 | 0.02 | 0.01 | 0.005 |
| 50 ≤ P < 100 | 0.5 | 0.2 | 0.1 | 0.05 | 0.02 | 0.01 |
| 100 ≤ P < 200 | 1 | 0.5 | 0.2 | 0.1 | 0.05 | 0.02 |
| 200 ≤ P < 500 | 2 | 1 | 0.5 | 0.2 | 0.1 | 0.05 |
| 500 ≤ P < 1,000 | 5 | 2 | 1 | 0.5 | 0.2 | 0.1 |
| 1,000 ≤ P < 2,000 | 10 | 5 | 2 | 1 | 0.5 | 0.2 |
| 2,000 ≤ P < 5,000 | 20 | 10 | 5 | 2 | 1 | 0.5 |
| 5,000 ≤ P < 10,000 | 50 | 20 | 10 | 5 | 2 | 1 |
| 10,000 ≤ P < 20,000 | 100 | 50 | 20 | 10 | 5 | 2 |
| 20,000 ≤ P < 50,000 | 200 | 100 | 50 | 20 | 10 | 5 |
| 50,000 ≤ P | 500 | 200 | 100 | 50 | 20 | 10 |

Scope note: RTS 11 covers shares, depositary receipts and exchange-traded funds. Certificates,
structured products and non-equity instruments are outside it and carry venue-specific tables
only. The engine therefore refuses to apply the share grid to an instrument for which no
liquidity band has been supplied.

## Worked-example instrument data

The catalog shipped in `scripts/lse_millennium_exchange_api.py` is illustrative. Mnemonics and
trading currencies were read from London Stock Exchange published instrument data on
**2026-08-25**:

| TIDM | Instrument | Currency | Observed quote increment |
|---|---|---|---|
| `SHEL` | Shell plc | GBX | 3384.5 / 3385.5 → 0.5 GBX |
| `AZN` | AstraZeneca plc | GBX | 12226 / 12230 → 2 GBX |
| `BT.A` | BT Group plc | GBX | 205.2 / 205.3 → 0.1 GBX |
| `3IN` | 3i Infrastructure plc | GBX | — (mnemonic example: leading digit) |
| `IGLN` | iShares Physical Gold ETC | **USD** | — (currency example: LSE is not GBX-only) |

The liquidity bands in the catalog are **inferred** from those increments against the Annex
above; they are not read from an FCA publication. Replace them with the FCA FITRS ADNT
calculation, and replace the catalog with the Reference Data Service Price Tick File, before
relying on either.
