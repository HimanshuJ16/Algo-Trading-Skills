# Standards for Philippine Stock Exchange API Integration

Source of record: the PSE Revised Trading Rules and the Implementing Guidelines
thereto, as amended by PSE circulars published at `documents.pse.com.ph`.
Transcribed here on **27 August 2026** from:

- **CN-2020-0028** (21 March 2020) — *Amendment of Rule on Static Threshold*,
  amending Article IV Section 7(b) and Implementing Guidelines Item VI.2(b).
  Effective **24 March 2020**.
- **TPA-2022-0036** (15 July 2022) — *Dynamic Threshold Semi-Annual Review*,
  the per-security trade-frequency clusters and their percentages.
- **CN-2025-0046** (15 December 2025) — Consultation Paper, *Proposed Amendments
  to the PSE Board Lot and Rule on Trading during Run-Off/Trading-at-Last*. Its
  "Existing" columns reproduce the Article IV Section 8 board lot table and the
  Rules on DDS Part C Section 1.a table **currently in force**; its "Proposed"
  columns are a consultation draft and are **not** in force.
- **pse.com.ph — Investing at PSE** — trading hours and settlement cycle.

| Metric | Engineering Standard |
|---|---|
| Base Currency | Philippine Peso (PHP) for the main board; USD for Dollar Denominated Securities (DDS), which carry their own board lot table. |
| Lattice Basis | Article IV Section 8: *"The Board Lot and Price Fluctuation of a Security for any Trading Day shall be based on the Security's **Reference Price**."* Both are fixed for the whole day and are **not** derived from the order price. |
| Reference Price | The previous day's Reference or Closing Price, or the Last Adjusted Closing Price (LACP) where a corporate action intervened. |
| Static Threshold | **+50% above / −30% below** the Reference Price. Asymmetric since 24 March 2020. Bounds are inclusive. |
| Dynamic Threshold | Symmetric about the **Last Traded Price**, at a percentage PSE assigns per security by trade-frequency cluster and reviews semi-annually. |
| Board Lot Range | 1,000,000 shares (sub-centavo issues) down to 5 shares (issues at PHP 5,000 and above). |
| Minimum Price | PHP 0.0001 (peso board); USD 0.01 (DDS). |
| Trading Hours | Pre-Open 09:00; Pre-Open No-Cancel 09:15; Market Open 09:30; Market Recess 12:00–13:00; Pre-Close 14:45; Pre-Close No-Cancel 14:48; Run-Off / Trading-at-Last 14:50; Closing VWAP Session 15:00; Market Close 15:15 (PHT). |
| Settlement | T+2. |
| Scope | Order-entry validation for cash equities. Halts, suspensions, the market-wide circuit breaker, session mechanics, the Odd Lot Market, block sales, commissions and taxes are out of scope. |

## Board Lot and Price Fluctuation — Peso-Denominated Securities

PSE Revised Trading Rules, Article IV, Section 8. Both the `From` and the `To`
bound of each band are **inclusive**.

| Price From (PHP) | Price To (PHP) | Tick Size (PHP) | Board Lot |
|---|---|---|---|
| 0.0001 | 0.0099 | 0.0001 | 1,000,000 |
| 0.0100 | 0.0490 | 0.0010 | 100,000 |
| 0.0500 | 0.2490 | 0.0010 | 10,000 |
| 0.2500 | 0.4950 | 0.0050 | 10,000 |
| 0.5000 | 4.9900 | 0.0100 | 1,000 |
| 5.0000 | 9.9900 | 0.0100 | 100 |
| 10.0000 | 19.9800 | 0.0200 | 100 |
| 20.0000 | 49.9500 | 0.0500 | 100 |
| 50.0000 | 99.9500 | 0.0500 | 10 |
| 100.0000 | 199.9000 | 0.1000 | 10 |
| 200.0000 | 499.8000 | 0.2000 | 10 |
| 500.0000 | 999.5000 | 0.5000 | 10 |
| 1,000.0000 | 1,999.0000 | 1.0000 | 5 |
| 2,000.0000 | 4,998.0000 | 2.0000 | 5 |
| 5,000.0000 | UP | 5.0000 | 5 |

The bands tile the tick lattice exactly: one tick added to a band's `To` lands on
the next band's `From` (49.9500 + 0.0500 = 50.0000; 999.5000 + 0.5000 =
1,000.0000; 4,998.0000 + 2.0000 = 5,000.0000). A Reference Price falling strictly
between two bands is therefore off-lattice — usually a sign that an LACP was
adjusted without being re-rounded — and warrants a warning rather than a silent
bucketing.

## Board Lot and Price Fluctuation — Dollar Denominated Securities

PSE Rules on Dollar Denominated Securities, Part C, Section 1.a. Prices in USD.
PSE prints the first band as `DOWN | 0.99`; the USD 0.01 tick makes USD 0.01 the
effective minimum.

| Price From (USD) | Price To (USD) | Tick Size (USD) | Board Lot |
|---|---|---|---|
| DOWN | 0.99 | 0.01 | 100 |
| 1.00 | 4.99 | 0.01 | 20 |
| 5.00 | 9.99 | 0.01 | 10 |
| 10.00 | 19.98 | 0.02 | 10 |
| 20.00 | 49.95 | 0.05 | 10 |
| 50.00 | 99.95 | 0.05 | 5 |
| 100.00 | 199.90 | 0.10 | 5 |
| 200.00 | 499.80 | 0.20 | 5 |
| 500.00 | 999.50 | 0.50 | 5 |
| 1,000.00 | UP | 1.00 | 5 |

## Trading Thresholds

### Static Threshold — Article IV Section 7(b)

> *"The upper Static Threshold shall be fifty percent (50%) above the Reference
> Price while the lower Static Threshold shall be thirty percent (30%) below the
> Reference Price."* — CN-2020-0028, effective 24 March 2020.

The lower threshold was **50%** until 23 March 2020. PSE narrowed it during the
COVID-19 volatility episode and it has not been restored. **Any backtest or
replay of a session on or before 23 March 2020 must use the symmetric ±50%
figure**; anything from 24 March 2020 onward must use +50% / −30%.

Both bounds are rounded onto the tick lattice of the **Reference Price's** band —
the ceiling down, the floor up — so that each bound is a placeable price that
stays inside the permitted percentage.

**PSE-published worked example (PLDT, `TEL`).** Reference Price PHP 1,642.00 sits
in the 1,000.0000–1,999.0000 band, so the tick is PHP 1.00 and the lot is 5.

| Bound | Raw | Rounded |
|---|---|---|
| Ceiling | 1,642.00 × 1.50 = **2,463.00** | 2,463.00 (already on the PHP 1.00 tick) |
| Floor | 1,642.00 × 0.70 = **1,149.40** | **1,150.00** (rounded **up**; 1,149.00 would be a fall of 30.02%) |

Note that PHP 2,463.00 is *not* a multiple of the PHP 2.00 tick belonging to the
2,000.0000–4,998.0000 band that PHP 2,463.00 itself falls in. That is a direct
confirmation that the Reference Price — not the price under test — fixes the
day's lattice.

**Edge case at the minimum price.** At a Reference Price of PHP 0.0001 the raw
ceiling is PHP 0.00015, which rounds down to PHP 0.0001, and there is no placeable
price below the minimum. The band collapses to the single price PHP 0.0001. This
is the conservative reading and matches the practical reality that a sub-centavo
issue pinned at the floor cannot move on the tick grid available to it.

### Dynamic Threshold

A second band, measured against the **Last Traded Price** rather than the
Reference Price. PSE classifies each security into a trade-frequency cluster and
reviews the classification semi-annually; the clusters and percentages published
in TPA-2022-0036 were:

| Cluster | Trade Frequency Qualifier (trailing six months) | Dynamic Threshold |
|---|---|---|
| A | Traded 20 times or fewer | 20% |
| B | Traded more than 20 and up to 500 times | 15% |
| C | Traded more than 500 times | 10% |

The percentage is assigned **per security by circular**. It cannot be inferred
from the price, the sector or the index membership, and the cluster list changes
at each review. Read it from the current PSE circular; a validator that guesses
one is worse than a validator that reports the check as not performed.

## Proposed Changes Not Yet In Force

PSE Consultation Paper **CN-2025-0046** (15 December 2025, comment period closed
31 December 2025) proposes, in connection with the migration from **PSEtrade XTS**
to **Nasdaq Eqlipse Trading** scheduled for 2026:

1. **One Lot One Share** — a uniform board lot of 1 share for every security,
   peso- and dollar-denominated alike, with a correspondingly re-banded tick
   table (`Up to 0.099 → 0.001`, `0.10–0.995 → 0.005`, `1–9.99 → 0.01`,
   `10–99.95 → 0.05`, and so on). Trading Participants would be permitted to
   impose a minimum order value instead.
2. **Removal of the Odd Lot Market**, since every lot becomes a round lot.
3. **Run-Off / Trading-at-Last** — incoming orders at the Closing Price would be
   accepted and matched at the Closing Price even where the counterpart passive
   order is better, which PSEtrade XTS currently rejects.

None of this is in force. Treat the tables above as authoritative until PSE
publishes the final rule and its effective date, then inject the replacement
schedule through the engine's `schedules` argument.
