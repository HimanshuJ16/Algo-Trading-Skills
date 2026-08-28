# Standards — order-to-trade-ratio-fee-penalty-avoidance

## Jurisdiction

There is **no global order-to-trade ratio rule**. Three unrelated regimes are documented
below and they do not agree on the ratio's definition, its granularity, its observation
period, or whether breaching it costs money at all. Do not universalize any of them.

| Regime | Jurisdiction | Nature |
|---|---|---|
| RTS 9 | EU trading venues (and UK venues under the onshored text) | Venue-rule limit; not a fee |
| Eurex Excessive System Usage fee | Eurex (T7) participants | Fee schedule |
| NSE / SEBI algo OTR framework | India | Penal charge + non-monetary sanctions |
| ICE Futures Europe / ICE Endex OTR Guidance | ICE designated products | Flat per-breach charge |

## RTS 9 — the ratio of unexecuted orders to transactions

**Commission Delegated Regulation (EU) 2017/566** of 18 May 2016, supplementing Directive
2014/65/EU (MiFID II) under Art. 48(12)(b), OJ L 87, 31.3.2017, p. 84
([EUR-Lex](https://eur-lex.europa.eu/eli/reg_del/2017/566/oj); UK onshored text at
[legislation.gov.uk](https://www.legislation.gov.uk/eur/2017/566)). Verified against the
published text.

| Provision | What it says | What it means here |
|---|---|---|
| **Art. 1(a)** | "order" includes **all input messages, including messages on submission, modification and cancellation** sent to the trading system, relating to an order **or a quote**, but **excluding** cancellation messages sent subsequent to (i) uncrossing in an auction, (ii) a loss of venue connectivity, (iii) the use of a kill functionality. | Cancels are counted; the three listed classes are not. `OTRInstrumentSession.exempt_cancels`. |
| **Art. 1(b)** | "transaction" means a **totally or partially** executed order. | A partial fill is one transaction. |
| **Art. 1(c)** | "volume" = number of instruments (shares, depositary receipts, ETFs, certificates); nominal value (bonds, structured finance products); number of lots or contracts (derivatives); metric tonnes of CO2 (emission allowances). | `ordered_volume` / `traded_volume` units. There is no universal unit — it is asset-class specific. |
| **Art. 2** | Venues calculate the ratio **for every financial instrument** traded under an electronic continuous auction order book, quote-driven, or hybrid system, per member/participant. | Per-instrument only. Voice systems are out of scope (recital 2). |
| **Art. 3(1)** | At least at the end of every trading session, in **both** of: (a) volume terms `(total volume of orders / total volume of transactions) − 1`; (b) number terms `(total number of orders / total number of transactions) − 1`. | The `− 1`. `OTRConvention.RTS9_UNEXECUTED`. |
| **Art. 3(2)** | The maximum is deemed exceeded if activity **in one specific instrument**, across all phases of the session **including the auctions**, exceeds **either or both** of the two ratios. | Both ratios are evaluated; either alone forces a breach. |
| **Art. 3(3)–(4)** | Orders are counted per the **Annex** methodology; an order type not listed is counted like the most similar listed type. | `RTS9_ANNEX_WEIGHTS`. |
| **Recital 7** | The calculation period "should not be longer than a trading session"; venues may set **shorter** observation periods. | Intra-session windows are permitted, which is what makes pre-emptive throttling meaningful. |

### Annex — counting methodology (rows modelled here)

Each submission, modification and cancellation counts as one single order, **except** as
weighted below.

| Order type | Orders counted |
|---|---|
| Limit; Limit – add; Limit – delete | 1 |
| **Limit – modify** | **2** — "any modifications entails a cancellation and a new insertion" |
| Immediate (Market) | 1 |
| Immediate (FOK, IOC) | 1 (2 if deleted/cancelled) |
| Iceberg / reserve | 1 |
| Stop; Market-to-limit | 1 (plus 1 venue-side update when triggered) |
| **Quote**; Quote – add; Quote – delete | **2** (1 buy side, 1 sell side) |
| **Quote – modify** | **4** |
| One-cancels-the-other; – add; – delete | 2 |
| One-cancels-the-other – modify | 4 |

The Annex covers further types (peg, trailing stop, spread limit, strike match,
order-on-event) whose venue-side updates it marks "potentially unlimited". Those are not
modelled; count them per Art. 3(4) against the closest listed type.

**Zero transactions.** RTS 9 does not define the ratio when the denominator is zero. ICE
Futures Europe and ICE Endex, implementing RTS 9, state: *"No OTR ratios will be
calculated in case the member has not traded for the applicable trading session"*
([Orders to Transactions Ratios guidance, December 2017](https://www.ice.com/publicdocs/futures/IFEU_order_to_transaction_guidance.pdf), §2).
The engine follows that treatment.

## Venue thresholds and charges — verify each one, do not remember

Published limits differ by five orders of magnitude. Anything here is a snapshot of what
the cited document said when checked; read the venue's current schedule before trading.

### ICE Futures Europe / ICE Endex

Per Designated Product, per member, calculated at the end of every trading session, in
both volume and number terms. Two tiers: an **Amber Threshold** (equalled or exceeded in
any Designated Product for 3 consecutive sessions → contact from the Exchange Compliance
Officer) and a **Red Threshold** (equalled or exceeded on any session → **EUR 2,000**
charge by ICE Endex or **GBP 2,000** by ICE Futures Europe). The published IFEU table
lists Amber 9,000,000 / Red 11,250,000 in volume terms and Amber 2,000,000 / Red 2,500,000
in number terms across its market types
([thresholds table](https://www.ice.com/publicdocs/futures/IFEU_order_to_transaction_ratios_table.pdf)).
Member OTR reports are delivered daily as CSV by 06:00 GMT/BST the following business day.

**Consequence for this skill:** the ICE charge is flat per breaching session, not
per-message. Do not model it with `PenaltyTier`; count breach sessions instead.

### Eurex — Excessive System Usage fee

Introduced 1 December 2013. A **daily transaction limit per participant, per product**,
across three limit types (standard orders; all transactions; non-market-data-updating
transactions and order cancellations). The limit is a volume component (aggressive plus
passive) plus a floor, with higher floors available to market makers on performance.

    ESU Fee = [(number of transactions) − (transaction limit)] × fee

with the fee on a sliding scale relative to the participant's own limit. Measured over a
**calendar month**: fewer than four exceedances in the month are treated as accidental and
**not charged**; four or more are systematic and every violation that month is charged
([Eurex — Excessive System Usage Fee](https://www.eurex.com/ex-en/rules-regs/regulations/excessive-system-usage-fee)).
Eurex has recalibrated both the OTR and ESU parameters since introduction; the current
values live in the Price List to the Connection Agreement, not here.

**Consequence for this skill:** the ESU formula is the flat single-tier `PenaltyTier`
form. The monthly four-exceedance waiver and the sliding scale are **not** modelled — an
estimate from this engine is an upper bound on an accidental month.

### NSE (India) — daily algo order-to-trade ratio

NSE circular **NSE/SURV/38122** (Ref. 161/2018, 22 June 2018), implementing para 14 of
SEBI circular **SEBI/HO/MRD/DP/CIR/P/2018/62** (9 April 2018)
([circular](https://archives.nseindia.com/content/circulars/SURV38122.pdf)). Computed **at
member level on a daily basis**, collected monthly, over all algo orders and algo trades.
Extended to the Equity segment from 29 June 2018, alongside Equity and Currency
Derivatives.

| Daily algo order-to-trade ratio | Charge per algo order |
|---|---|
| Less than 50 | Nil |
| 50 to less than 250 (incremental) | 2 paise |
| 250 to less than 500 (incremental) | 10 paise |
| 500 or more (incremental) | 10 paise |

Non-monetary sanctions in the same circular, **not modelled** by the engine: at a ratio of
500 or more the member may not place orders for the first 15 minutes of the next trading
day (risk-reduction mode instead, in Equity and Currency Derivatives); and after more than
ten penalised days in the previous thirty rolling trading days, proprietary trading is
suspended for the first trading hour of the next day.

Exclusions from the count (Annexure I): algo orders entered or modified **within 0.75% of
the LTP**; in the Equity segment, SME and ETF securities and securities with designated
market makers; and orders in the odd lot, auction, block, pre-open, post-close, periodic
call auction and IPO call auction sessions.

**Currency.** NSE ratios are gross messages per trade, not RTS 9 unexecuted ratios — use
`OTRConvention.GROSS_MESSAGES_PER_TRADE`, and note the "incremental basis" wording makes
the slabs progressive brackets, not a single rate applied to the whole excess.

**This framework has moved.** SEBI issued *Revision of Order-to-Trade Ratio (OTR)
framework*, circular **HO/47/11/16(2)2025-MRD-POD2/I/4113/2026**, dated **4 February 2026**
([SEBI](https://www.sebi.gov.in/legal/circulars/feb-2026/revision-of-order-to-trade-ratio-otr-framework_99501.html)).
Contemporaneous reporting describes it as widening the equity-options exemption to orders
within ±40% of the option premium's LTP or ₹20, whichever is higher, exempting Designated
Market Maker orders from the computation, and taking effect from 6 April 2026. The circular
number and date are confirmed against SEBI's own listing; **the operative detail above is
from secondary reporting and was not read from the circular text** — read the circular
before relying on the bands, and re-verify the slab table, which the revision may also have
changed.

## What is deliberately not claimed here

- **No default limit.** `max_count_otr` / `max_volume_otr` have no defaults. The 50:1 figure
  that circulates as a rule of thumb is the floor of NSE's charging slab, not a general
  limit; ICE publishes 2,500,000 in number terms for the same concept.
- **No general per-message rate.** No surveyed venue publishes a universal "€0.01–€0.05 per
  excess message" figure. Read the fee from the venue's schedule.
- **The 80% warning tier is an operational choice.** No consulted venue publishes it. It is
  a safety margin, not a regulatory quantity.
- **Venue coverage beyond the four regimes above was not verified.** Other venues may or may
  not operate an OTR regime; this file lists only what was read from source.
