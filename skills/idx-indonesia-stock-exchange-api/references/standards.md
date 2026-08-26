# Standards for IDX Integration

Source of record: IDX Peraturan Nomor II-A (Perdagangan Efek Bersifat Ekuitas),
Keputusan Direksi **Kep-00196/BEI/12-2024**, effective **8 April 2025** — the
amendment that made Auto Rejection asymmetric. Tables below are transcribed from
the IDX "Trading Hours and Mechanism" page (verified 25 August 2026).

| Metric | Engineering Standard |
|---|---|
| Base Currency | Indonesian Rupiah (Rp), quoted in whole Rupiah. |
| Ticker Format | IDX equity codes are 4 uppercase letters (e.g. `BBCA`). Rights (`TLKM-R`) and warrants (`TLKM-W`) are separate instruments with separate rules. |
| Market Segments | `RG` Pasar Reguler and `TN` Pasar Tunai trade on the continuous JATS order book; `NG` Pasar Negosiasi is bilaterally negotiated. |
| Tick Size Rules | Order prices MUST conform to IDX Fraksi Harga, selected from the **previous closing price** and fixed for the full trading day. |
| Board Lot Sizing | Pasar Reguler and Pasar Tunai orders MUST be in multiples of 1 Lot ($100$ shares). Pasar Negosiasi has no round-lot requirement. |
| Minimum Price | Rp $50$ on the Main / Development / New Economy boards; Rp $1$ on the Acceleration and Watchlist (Papan Pemantauan Khusus) boards. |
| Order Volume Cap | An order exceeding $50{,}000$ lots **or** $5\%$ of listed shares — whichever is smaller — is auto-rejected on size alone. |
| Auto Rejection Scope | Applies in Pasar Reguler and Pasar Tunai only; it does **not** apply to Pasar Negosiasi. |

## Fraksi Harga (Tick Size) and Jenjang Maksimum

The tick is selected from the previous closing price and applies for one full
trading day (*"Fraksi dan jenjang maksimum perubahan harga di atas berlaku untuk
satu Hari Bursa penuh dan disesuaikan pada Hari Bursa berikutnya jika Harga
Penutupan berada pada rentang harga yang berbeda"*).

| Reference price band | Tick size | Jenjang maksimum perubahan harga |
|---|---|---|
| $< \text{Rp } 200$ | Rp 1 | Rp 10 |
| $\text{Rp } 200 - < \text{Rp } 500$ | Rp 2 | Rp 20 |
| $\text{Rp } 500 - < \text{Rp } 2{,}000$ | Rp 5 | Rp 50 |
| $\text{Rp } 2{,}000 - < \text{Rp } 5{,}000$ | Rp 10 | Rp 100 |
| $\ge \text{Rp } 5{,}000$ | Rp 25 | Rp 250 |

**Jenjang maksimum perubahan harga** (maximum single-step price move) is $10\times$
the tick and applies in Pasar Reguler and Pasar Tunai. The engine surfaces it on
the report as `max_price_step` but does **not** enforce it: IDX's published
summaries do not state unambiguously which reference (last traded price, best
quote, or previous close) the step is measured against. Confirm the measurement
basis with your broker or JATS gateway before enforcing it client-side.

## Auto Rejection (ARA / ARB)

Bands are computed from the Acuan Harga — the previous Pasar Reguler closing
price, the theoretical price after a corporate action, the listing price on a
debut, or an independent appraiser's fair value. Note that the Auto Rejection
bands are **upper-inclusive** (`Rp 50–200`, `>Rp 200–5,000`, `>Rp 5,000`) while
the Fraksi Harga bands are upper-exclusive, so a Rp 200 reference price takes a
Rp 2 tick and a 35% ARA.

### Main, Development and New Economy Boards

| Reference price band | Auto Rejection Atas (upper) | Auto Rejection Bawah (lower) |
|---|---|---|
| $\text{Rp } 50 - \text{Rp } 200$ | $+35\%$ | $-15\%$ |
| $> \text{Rp } 200 - \text{Rp } 5{,}000$ | $+25\%$ | $-15\%$ |
| $> \text{Rp } 5{,}000$ | $+20\%$ | $-15\%$ |

### Acceleration and Watchlist (Papan Pemantauan Khusus) Boards

| Reference price band | Limit (both directions) |
|---|---|
| $\text{Rp } 1 - \text{Rp } 10$ | $\pm \text{Rp } 1$ |
| $> \text{Rp } 10$ | $\pm 10\%$ |

**IPO first trading day**: IDX applies *one* times the percentages above — i.e.
the ordinary limits, not a widened multiple.

**Regime history — these limits change**: symmetric ±20/25/35% before 2020;
emergency asymmetric ARB during the 2020 COVID intervention; symmetry restored
in phases during 2023; asymmetric again (ARB flat 15%) from 8 April 2025.
Re-verify the schedule against IDX before relying on it in production rather
than assuming the constants in `scripts/` are still current.

## Announced Change — Minimum Price Rp 50 → Rp 1

IDX has announced lowering the Pasar Reguler / Pasar Tunai minimum price from
Rp 50 to Rp 1, with member trials on 22 and 29 August 2026 and a target
implementation of 7 September 2026, alongside a re-banding of Auto Rejection
(Rp 1–10 on a Rp 1 absolute basis; 15% ARB for Rp 11–200, Rp 201–5,000 and
above Rp 5,000). This is **announced, not yet in force** as of 25 August 2026,
and is reported by Indonesian financial press rather than confirmed here against
a published Keputusan Direksi. `IdxStockExchangeApiEngine` therefore defaults to
the Rp 50 floor and exposes `minimum_price_ordinary_boards` so the change can be
adopted on the day it takes effect. Verify against IDX before switching.

## Sources

- IDX, *Trading Hours and Mechanism* (tick size, Auto Rejection, board lot, IPO
  first-day rule; cites Kep-00196/BEI/12-2024 effective 8 April 2025):
  <https://www.idx.id/en/products-services/trading-hours-and-mechanism/>
- IDX, *Jam dan Mekanisme Perdagangan* (Indonesian text; cites Peraturan II-A
  Kep-00003/BEI/04-2025): <https://www.idx.id/id/produk-layanan/jam-dan-mekanisme-perdagangan/>
- BCA Sekuritas, *Mekanisme Perdagangan Saham* (confirms the tick is set from the
  previous close and fixed for the day, the Acuan Harga definitions, and that
  Auto Rejection does not apply to Pasar Negosiasi):
  <https://www.bcasekuritas.co.id/help/faq/equities-trading-mechanism>
- IDX Islamic, *Apa itu Auto Rejection?* (volume Auto Rejection: 50,000 lots or
  5% of listed shares, whichever is smaller):
  <https://idxislamic.idx.co.id/whats-on-idx-islamic/berita-dan-artikel/apa-itu-auto-rejection/>
- Bisnis Indonesia, *BEI Akan Turunkan Batas Minimum Harga Saham ke Rp1* (21 Aug
  2026 — announced Rp 1 minimum price and Auto Rejection re-banding; secondary
  source, pending an IDX Keputusan Direksi):
  <https://market.bisnis.com/read/20260821/7/1997936/bei-akan-turunkan-batas-minimum-harga-saham-ke-rp1-batas-ara-dan-arb-ikut-disesuaikan>
