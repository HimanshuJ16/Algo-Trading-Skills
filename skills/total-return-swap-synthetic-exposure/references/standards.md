# Total Return Swap (TRS) — Standards and Sourced Conventions

All amounts below are USD. Rates carry explicit units: `r_pct` is a percentage (5.25 means
5.25%), `s_bps` is basis points (50 means 0.50 percentage points), `T_pct` is a percentage
withholding haircut (15 means 15%).

## 1. Cash flow legs (as implemented in `scripts/trs_exposure.py`)

1. **Total return leg**

   $$\text{Capital Return} = N_{\text{shares}} \times (P_{\text{end}} - P_{\text{start}})$$

   $$\text{Manufactured Dividend} = N_{\text{shares}} \times \sum_{d \,\in\, \text{Dividend Period}} D^{d}_{\text{gross}} \times \left(1 - \frac{T^{d}_{\text{pct}}}{100}\right)$$

   $$\text{Total Return Leg} = \text{Capital Return} + \text{Manufactured Dividend}$$

   The sum runs only over dividends whose **relevant date** falls inside the Dividend
   Period — see §3.

2. **Funding leg** (accrued on the period-reset notional, not the trade-date notional)

   $$N_{\text{period}} = N_{\text{shares}} \times P_{\text{start}}$$

   $$\text{Funding Interest} = N_{\text{period}} \times \frac{r_{\text{pct}} + s_{\text{bps}}/100}{100} \times \tau$$

   where $\tau$ is the day count fraction from §2. A negative all-in rate yields a
   negative accrual: the funding payer is credited.

3. **Net settlement and mark-to-market** — signed for the modelled party

   - Total Return **Receiver** (long synthetic):
     $$\text{Net} = \text{Total Return Leg} - \text{Funding Interest}$$
   - Total Return **Payer** (short synthetic):
     $$\text{Net} = \text{Funding Interest} - \text{Total Return Leg}$$

   The two sides' marks are mirror images. At a fully cash-settled reset the period
   mark-to-market equals the settlement amount; between resets the two diverge.

## 2. Day count conventions

| Currency | Benchmark | Day count | Source |
| :--- | :--- | :--- | :--- |
| USD | SOFR, Effective Fed Funds | `ACT/360` | ARRC, *A User's Guide to SOFR* — Act/360, "consistent with the standard convention in US money markets" |
| EUR | €STR, EURIBOR | `ACT/360` | ECB €STR methodology; euro money-market convention |
| GBP | SONIA | `ACT/365` | Bank of England / sterling money-market convention |
| JPY | TONA | `ACT/365` | Yen money-market convention |

`30/360` (Bond Basis, 2006 ISDA Definitions **Section 4.16(f)**) is a function of the two
calendar dates, not of the number of days between them:

$$\tau_{30/360} = \frac{360(Y_2 - Y_1) + 30(M_2 - M_1) + (D_2 - D_1)}{360}$$

with the adjustments: **if $D_1$ is 31, set $D_1 = 30$; if $D_2$ is 31 and $D_1$ is 30 or
31, set $D_2 = 30$.** Computing it as `actual_days / 360` produces Act/360.

Worked checks (used as test vectors): 31 Jan 2026 → 31 Mar 2026 = 60/360 (Act/360 gives
59/360); 31 Aug 2026 → 28 Feb 2027 = 178/360; 1 Jan 2026 → 1 Jul 2026 = 180/360.

Prime broker financing spreads over the benchmark are bilaterally negotiated and vary with
the client, the borrow and the balance sheet charge. This repository does not publish an
indicative range because no authoritative public source supports one.

## 3. Dividends — 2002 ISDA Equity Derivatives Definitions, Article 10

- **Section 10.1 — Dividend Amount** is the **Record Amount**, the **Ex Amount** or the
  **Paid Amount** "as specified in the related Confirmation". Each is defined as *100% of
  the gross cash dividend per Share*, with eligibility turning on a different date falling
  inside the Dividend Period:
  - *Record Amount* — the record date occurs during the period;
  - *Ex Amount* — the date the shares commenced trading ex-dividend occurs during the period;
  - *Paid Amount* — the issuer paid the dividend during the period.
- A "gross cash dividend" is explicitly **before** withholding or deduction of taxes at
  source, and **excludes** Extraordinary Dividends and Excess Dividend Amounts unless the
  Confirmation says otherwise (§10.6, §10.7). Any pass-through below 100% — net of
  withholding, or an agreed percentage — is a Confirmation-level term, not an ISDA default.
- **Section 10.3 — Dividend Period.** The default is the *Second Period*: "from, but
  excluding, one Valuation Date to, and including, the next Valuation Date". The engine
  therefore treats the period as the half-open interval `(start_date, end_date]`.
- **Section 10.4 — Re-investment of Dividends**, if elected, adds the Dividend Amount to
  the Equity Notional Amount for subsequent periods. **Not modelled by this engine.**

## 4. Margin — BCBS-IOSCO uncleared margin requirements

The **standardised initial margin schedule** sets IM as a percentage of notional exposure
by asset class:

| Asset class | IM |
| :--- | :--- |
| **Equity** | **15%** |
| Commodity | 15% |
| FX | 6% |
| Credit 0–2y / 2–5y / 5y+ | 2% / 5% / 10% |
| Interest rate 0–2y / 2–5y / 5y+ | 1% / 2% / 4% |

Net-to-gross adjustment for a netting set: `0.4 × gross IM + 0.6 × NGR × gross IM`.

- **Initial margin** is exchanged on a **gross** basis, held so as to protect the posting
  party, and **cannot be re-hypothecated**. It therefore does **not** offset a variation
  margin call.
- **Variation margin**: "the full amount necessary to fully collateralise the
  mark-to-market" must be exchanged — a **zero threshold** — subject only to the Minimum
  Transfer Amount. The framework caps the MTA at **€500,000** combined across IM and VM
  (EU/EMIR implementation; jurisdictional equivalents differ — Canada's OSFI states
  CAD 750,000). An IM exchange obligation itself only bites above a group-level threshold
  (€50 million in the EU).
- Whether UMR applies at all depends on entity type and aggregate average notional amount.
  Many prime-brokerage equity TRS with buy-side clients are margined under a **bilateral
  house grid** instead. Do not hard-code 15%; take the number from the CSA.

## 5. US withholding on dividend equivalents — IRC §871(m)

- A dividend equivalent paid to a non-US person under a §871(m) transaction is US-source
  income subject to **30%** withholding (IRC §871(a)/§881), **unless reduced by an
  applicable income tax treaty** (15% is a common treaty rate, not the statutory one).
- **IRS Notice 2024-44** (22 May 2024) extended the phase-in previously set by Notice
  2022-37: for transactions **issued before 1 January 2027**, only **delta-one**
  transactions are treated as §871(m) transactions. A TRS that tracks its underlying
  one-for-one is delta-one and is therefore in scope now. Non-delta-one transactions come
  into scope from 1 January 2027 absent a further extension — **verify the current notice
  before relying on this date.**
- The notice also extends the QDD relief and the transition out of the QSL regime through
  the same window.

## 6. Sources

- 2002 ISDA Equity Derivatives Definitions, Article 10 (Dividends), §§10.1–10.7 — <https://www.isda.org/book/2002-isda-equity-derivatives-definitions/>
- 2006 ISDA Definitions, Section 4.16(f), 30/360 (Bond Basis) — <https://www.isda.org/2008/12/22/30-360-day-count-conventions/>
- BCBS-IOSCO, *Margin requirements for non-centrally cleared derivatives* (BCBS d499, 2020) — <https://www.bis.org/bcbs/publ/d499.pdf>
- OSFI, *Margin Requirements for Non-Centrally Cleared Derivatives* Guideline (2020), standardised IM schedule and MTA — <https://www.osfi-bsif.gc.ca/en/guidance/guidance-library/margin-requirements-non-centrally-cleared-derivatives-guideline-2020>
- IRS Notice 2024-44 — <https://www.irs.gov/pub/irs-drop/n-24-44.pdf>
- ARRC, *A User's Guide to SOFR* (2021 update) — <https://www.newyorkfed.org/medialibrary/Microsites/arrc/files/2021/users-guide-to-sofr2021-update.pdf>
