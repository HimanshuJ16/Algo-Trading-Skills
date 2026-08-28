# Workflows for IRC Section 1256 Contract Tax Treatment (US Futures)

Scope: US federal income tax, one tax year, Form 6781 Part I. Not tax advice.

## 1. Classify every position before computing anything

§ 1256(b)(1) reaches exactly five things. Classification is a legal
determination the caller asserts — the engine never infers it from a symbol.

| Bucket | Examples | Treatment |
| :--- | :--- | :--- |
| Regulated futures contract | ES, NQ, CL, ZB, GC on CME/CBOT/NYMEX/ICE | Part I, 60/40 |
| Nonequity option (§ 1256(g)(3)) | SPX, NDX, RUT, VIX — broad-based index options | Part I, 60/40 |
| Foreign currency contract (§ 1256(g)(2)) | Interbank forward/futures currency contracts | Part I, 60/40 — but see § 988 below |
| Dealer equity option / dealer securities futures contract | Registered dealer inventory only | Part I, 60/40, plus § 1402(i) SE tax |
| Equity option (§ 1256(g)(6)) | AAPL, TSLA options; **SPY, QQQ, IWM options** | Excluded — Form 8949 / Schedule D by holding period |
| Securities futures contract (§ 1256(b)(2)(A)) | Single-stock futures, non-dealer | Excluded |
| Swap / notional principal contract (§ 1256(b)(2)(B)) | IRS, CDS, equity swap, cap, floor | Excluded |

Two traps worth naming:

- **"Index" is not the test.** § 1256(g)(6) makes an option on a *narrow-based*
  security index an equity option. Only broad-based index options are nonequity
  options.
- **An ETF share is stock.** An option on it is an option to buy or sell stock, so
  it is an equity option even though the fund tracks a broad index.

Excluded positions still belong on the return. Keep them in the blotter with an
excluding `contract_type` so they surface in
`excluded_non_section_1256_pnl_usd` instead of vanishing.

## 2. Route out what Part I does not reach

- **Identified hedges (§ 1256(e)).** Identification must exist "before the close
  of the day on which such transaction was entered into". A properly identified
  hedge is *not* marked to market, and its gain or loss is **ordinary** — it goes
  in the Form 6781 line 4 adjustment with an attached statement, never into the
  60/40 split. A year-end reclassification does not create an identification.
- **Mixed straddle legs.** § 1256(a)(4) turns off § 1092 only where every
  offsetting position is a § 1256 contract. In a mixed straddle the § 1256 loss
  leg must first be reduced by unrecognized gain on the non-§ 1256 leg (line 4),
  or the straddle must be handled under a box A/B/C election in Part II. Route
  these out and resolve them separately.
- **Currency contracts under a § 988 election.** A § 988(a)(1)(B) or
  § 988(c)(1)(D) election changes the characterization; attach the list of covered
  contracts the Form 6781 instructions require. See
  `currency-gain-loss-tax-treatment-for-forex-trading`.
- **A book under a § 475(f)(2) commodities election.** That election disapplies
  § 1256(a) and forfeits 60/40 entirely. See
  `mark-to-market-election-for-active-traders-us`.

## 3. Mark to the last business day

§ 1256(a)(1) marks every contract **held at the close of the taxable year** to
fair market value on the **last business day** — not December 31, which in some
years has no settlement price at all.

Set `is_open_at_year_end=True` and supply `year_end_mark_pnl_usd`. The reference
script **rejects** a non-zero mark on a position flagged closed rather than
ignoring it: an ignored mark understates the year's § 1256 income by its full
amount, and that error is invisible in the totals.

Split a partially closed position across the two fields — `realized_pnl_usd` for
the portion closed during the year, `year_end_mark_pnl_usd` for the portion still
held. They must describe disjoint portions; overlapping them double-counts.

## 4. Make the § 1256(a)(2) adjustment on anything carried across a year end

This is the defect that survives review because the arithmetic looks fine.

A contract opened in year 1, marked at +$30,000 on year 1's last business day,
and closed in year 2 with $50,000 of inception-to-date gain contributes **$20,000**
to year 2 — the $30,000 was already taxed. "Proper adjustment shall be made in the
amount of any gain or loss subsequently realized for gain or loss taken into
account by reason of paragraph (1)."

Two conventions, and mixing them is the failure mode:

- **Broker-adjusted.** Form 1099-B **box 11** already reports the correctly
  adjusted amount for the year. Feed it as `realized_pnl_usd` and leave
  `prior_year_end_cumulative_mark_usd` at `None`.
- **Inception-to-date.** If your own records carry lifetime P&L, supply the prior
  year's mark in `prior_year_end_cumulative_mark_usd` and let the engine subtract
  it.

Setting the field on a box 11 figure removes the prior mark **twice**. The engine
warns whenever the adjustment fires, naming the affected contracts, so the
convention is auditable.

## 5. Split 60/40 and place the figures

`line 5` net § 1256 P&L → `line 6` carryback (positive) → `line 7` → `line 8`
40% short-term → **Schedule D line 4**, `line 9` 60% long-term → **Schedule D
line 11**. Partnerships and S corporations stop at line 5 and leave lines 6–9
blank.

## 6. In a loss year, run the whole waterfall

A net § 1256 loss is deferred, not forfeited. Stopping at the $3,000 cap
understates its value by roughly an order of magnitude and drives the wrong
year-end decision.

1. **§ 1212(c) / box D.** Net section 1256 contracts loss = the smaller of
   (§ 1256 losses over § 1256 gains, plus $3,000 / $1,500 MFS) and (the capital
   loss carryovers to next year with line 6 at zero). Carry it back 3 years
   against prior § 1256 gains only, earliest year first, character preserved
   60/40, and only so far as it does not increase or create an NOL in the
   carryback year. Corporations, estates and trusts cannot elect. File Form 1045
   or an amended return with an amended Form 6781 and Schedule D; on the prior
   year's amended Form 6781 the carryback is entered on **line 1**.
2. **§ 1211(b).** The remainder is allowed against capital gains first, then the
   lower of $3,000 ($1,500 married filing separately) or the excess.
3. **§ 1212(b).** Anything left carries forward indefinitely.

The reference script computes prong 1 and an aggregate prior-gain ceiling, and
warns that the per-year Schedule D line 16 caps, the earliest-year ordering and
the NOL limit still need hand verification against the prior returns.

## 7. Estimate tax honestly

The 60/40 advantage at top rates is `0.60 × (37% − 20%) = 10.2` percentage points
of net gain. The blended **rate** is 26.8% on capital rates alone — but § 1256
gain of a trader in commodities or financial instruments is net investment income
under § 1411(c)(1)(A)(ii), so above the MAGI threshold the real top rate is 30.6%.
NIIT is character-blind, so it raises the bill without touching the saving. Pass
`net_investment_income_tax_rate=0.038` rather than quoting 26.8% as the ceiling.

Self-employment tax applies to a **dealer's** § 1256 P&L under § 1402(i), not to a
non-dealer trader's.

## 8. Read the warnings, then hand it to a professional

Every exclusion, every prior-year adjustment, every limitation the engine did not
evaluate lands in `warnings`. An empty list is the only clean result. A silent
exclusion is the defect that survives review; a logged one does not.
