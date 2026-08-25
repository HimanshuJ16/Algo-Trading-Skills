# Standards for HKEX Securities-Market Order Validation

All tables and figures below were verified against HKEX primary sources on **2026-08-25**.
Re-verify before each release: the Second Schedule is amended by rule update, and the
Reduction of Minimum Spreads programme has changed Part A twice since 2025.

## Engineering standards

| Area | Engineering standard | Source |
|---|---|---|
| Order entry interface | Orders go to **OCG-C** (Orion Central Gateway – Securities Market), over its Binary or FIX Trading Protocol. An OCG session carries orders and **no** securities market data. | [OCG-C](https://www.hkex.com.hk/Services/Trading/Securities/Infrastructure/OCGC?sc_lang=en) |
| Market data interface | **OMD-C** (Orion Market Data Platform – Securities Market) carries market data and **no** orders. It is the source of the per-security Spread Table Code. | [OMD-C](https://www.hkex.com.hk/Services/Market-Data-Services/Infrastructure/HKEX-Orion-Market-Data-Platform-Securities-Market-OMD-C?sc_lang=en) |
| Stock code format | Zero-padded to 5 digits (`00700`). Codes longer than 5 digits and `00000` are not HKEX security codes and MUST be rejected, not padded. | Second Schedule / HKEX symbology |
| Dual Counter codes | HKD counter `0XXXX`, RMB counter `8XXXX`, normally sharing the last four digits. RMB counter short names end `-R`. Codes `86610`, `86611`, `86639`, `86660`, `86661`, `86663` are allocated to PRC Ministry of Finance bonds and are unavailable to Dual Counter Securities. | [Dual Counter Model FAQ](https://www.hkex.com.hk/-/media/HKEX-Market/Services/Trading/Securities/Overview/Trading-Mechanism/HKDRMB-Dual-Counter/Dual-Counter-Model-FAQ_E.pdf) |
| Minimum spread | Order price MUST be an exact multiple of the minimum spread for the security's **Part** of the Second Schedule. Band boundaries are **upper-inclusive** ("Over X to Y"). | [Second Schedule](https://www.hkex.com.hk/-/media/HKEX-Market/Services/Rules-and-Forms-and-Fees/Rules/SEHK/Securities/Rules/Sch_2_eng.pdf) |
| Spread table selection | Read the security's **Spread Table Code** from the OMD-C Security Definition (11) message. Do not infer it from the ticker or the price. | [Minimum Spreads FAQ Phase 1, Q6](https://www.hkex.com.hk/-/media/HKEX-Market/Services/Trading/Securities/Overview/Trading-Mechanism/Reduction-of-Minimum-Spreads/Reduction-of-Minimum-Spreads-FAQ_E.pdf) |
| Board lot | Quantity MUST be an integral multiple of the security's issuer-set board lot to auto-match. Board lots range from 10 to 100,000 shares; there is no market-wide default. | [Trading Mechanism](https://www.hkex.com.hk/Services/Trading/Securities/Overview/Trading-Mechanism?sc_lang=en) |
| Odd / special lots | Quantity below one board lot is an **odd lot**; above one board lot and not an integral multiple is a **special lot**. Neither is accepted for auto-matching; both use the semi-automatic odd/special lot facility. | [Trading Mechanism](https://www.hkex.com.hk/Services/Trading/Securities/Overview/Trading-Mechanism?sc_lang=en) |
| Maximum order size | "For all trading sessions, the maximum order size for automatch stocks is 3,000 board lots." | [Trading Mechanism](https://www.hkex.com.hk/Services/Trading/Securities/Overview/Trading-Mechanism?sc_lang=en) |
| Price reasonableness (NOT implemented here) | Opening quotation must not deviate more than 24 spreads or the prescribed % (5%, or 3.5% for ETPs) from the previous closing price; and no order may be priced at 9 times or more from the nominal price. Both need market data this module does not take. | [Trading Mechanism](https://www.hkex.com.hk/Services/Trading/Securities/Overview/Trading-Mechanism?sc_lang=en) |

## Second Schedule — Spread Table (applicable to all types of currencies)

Boundaries are upper-inclusive. `500.00` is a Part A `0.200` price; `500.20` is a
`0.500` price.

### Part A — all securities other than those under Parts B, C, D and E

Current text, incorporating Reduction of Minimum Spreads Phase 1 (2025-08-04) and
Phase 2 (2026-08-03). OMD-C **spread table code 01**.

| Price band (currency unit) | Minimum spread |
|---|---|
| From 0.01 to 0.25 | 0.001 |
| Over 0.25 to 10.00 | 0.005 |
| Over 10.00 to 20.00 | 0.010 |
| Over 20.00 to 50.00 | 0.020 |
| Over 50.00 to 100.00 | 0.050 |
| Over 100.00 to 200.00 | 0.100 |
| Over 200.00 to 500.00 | 0.200 |
| Over 500.00 to 1,000.00 | 0.500 |
| Over 1,000.00 to 2,000.00 | 1.000 |
| Over 2,000.00 to 5,000.00 | 2.000 |
| Over 5,000.00 to 9,995.00 | 5.000 |

### Part B — Exchange-authorised securities and all debt securities

| Price band (currency unit) | Minimum spread |
|---|---|
| From 0.50 to 9,999.95 | 0.050 |

### Part C — Exchange Traded Options

Traded per the scale of spreads set out in the **Operational Trading Procedures**, not
the Second Schedule. No table is reproduced here and none is assumed in code —
`SpreadTable.PART_C` raises `SpreadTableUnavailableError`.

### Part D — Exchange Traded Funds (other than those under Part B)

| Price band (currency unit) | Minimum spread |
|---|---|
| From 0.01 to 1.00 | 0.001 |
| Over 1.00 to 5.00 | 0.002 |
| Over 5.00 to 10.00 | 0.005 |
| Over 10.00 to 20.00 | 0.010 |
| Over 20.00 to 100.00 | 0.020 |
| Over 100.00 to 200.00 | 0.050 |
| Over 200.00 to 500.00 | 0.100 |
| Over 500.00 to 1,000.00 | 0.200 |
| Over 1,000.00 to 2,000.00 | 0.500 |
| Over 2,000.00 to 9,999.00 | 1.000 |

### Part E — Structured Products (Derivative Warrants, CBBCs, Inline Warrants)

OMD-C **spread table code 06**, introduced at Phase 1. These are the pre-reduction
Part A bands: Structured Products were carved out of code 01 rather than reduced.

| Price band (currency unit) | Minimum spread |
|---|---|
| From 0.01 to 0.25 | 0.001 |
| Over 0.25 to 0.50 | 0.005 |
| Over 0.50 to 10.00 | 0.010 |
| Over 10.00 to 20.00 | 0.020 |
| Over 20.00 to 100.00 | 0.050 |
| Over 100.00 to 200.00 | 0.100 |
| Over 200.00 to 500.00 | 0.200 |
| Over 500.00 to 1,000.00 | 0.500 |
| Over 1,000.00 to 2,000.00 | 1.000 |
| Over 2,000.00 to 5,000.00 | 2.000 |
| Over 5,000.00 to 9,995.00 | 5.000 |

## OMD-C Spread Table Codes

Published in the Security Definition (11) message of OMD-C and OMD-C MMDH.

| Code | Second Schedule Part | Applies to |
|---|---|---|
| 01 | Part A | All securities except debt securities, Exchange Traded Options, Exchange Traded Products and Structured Products |
| 03, 04, 05 | *not published* | Debt securities, Exchange Traded Options and Exchange Traded Products — HKEX's FAQ states these three codes are in use but **does not publish which numeric code is which**. Resolve from the OMD-C interface specification for your feed; do not guess. |
| 06 | Part E | Structured Products (CBBC, DW, Inline Warrants) — introduced at Phase 1 |

## Reduction of Minimum Spreads — what changed and when

| Phase | Launch | Part A change |
|---|---|---|
| Phase 1 | 2025-08-04 | 10.00–20.00 cut from 0.020 to 0.010; the old 20.00–100.00 band split into 20.00–50.00 at 0.020 and 50.00–100.00 at 0.050. Structured Products moved to new code 06 at the pre-reduction bands. |
| Phase 2 | 2026-08-03 | 0.50–10.00 merged into the 0.25–10.00 band at 0.005 (a 50% cut for the 0.50–10.00 range). |

Scope in both phases is "Applicable Securities": all securities including equities,
REITs and equity warrants, **excluding** Exchange Traded Products, debt securities,
Exchange Traded Options, Inline Warrants and Structured Products. Neither phase changed
the OMD-C or OMD-C MMDH interface.

## Primary sources

- SEHK Rules of the Exchange, **Second Schedule (Spread Table)** —
  <https://www.hkex.com.hk/-/media/HKEX-Market/Services/Rules-and-Forms-and-Fees/Rules/SEHK/Securities/Rules/Sch_2_eng.pdf>
- **Reduction of Minimum Spreads FAQ — Phase 1** (spread table codes; updated 2025-03-24) —
  <https://www.hkex.com.hk/-/media/HKEX-Market/Services/Trading/Securities/Overview/Trading-Mechanism/Reduction-of-Minimum-Spreads/Reduction-of-Minimum-Spreads-FAQ_E.pdf>
- **Reduction of Minimum Spreads FAQ — Phase 2** (version date 2026-05-08) —
  <https://www.hkex.com.hk/-/media/HKEX-Market/Services/Trading/Securities/Overview/Trading-Mechanism/Reduction-of-Minimum-Spreads/Reduction-of-Minimum-Spreads-FAQP2_E.pdf>
- **HKEX Trading Mechanism** (order types, max order size, odd/special lots, price limits) —
  <https://www.hkex.com.hk/Services/Trading/Securities/Overview/Trading-Mechanism?sc_lang=en>
- **HKD-RMB Dual Counter Model FAQ** —
  <https://www.hkex.com.hk/-/media/HKEX-Market/Services/Trading/Securities/Overview/Trading-Mechanism/HKDRMB-Dual-Counter/Dual-Counter-Model-FAQ_E.pdf>
- **Orion Central Gateway – Securities Market (OCG-C)** interface specifications —
  <https://www.hkex.com.hk/Services/Trading/Securities/Infrastructure/OCGC?sc_lang=en>
