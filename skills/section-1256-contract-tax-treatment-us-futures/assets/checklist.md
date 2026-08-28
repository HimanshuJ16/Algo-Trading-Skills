# Pre-Flight Checklist — IRC Section 1256 Contracts / Form 6781 Part I

US federal only. Not tax advice.

## Classification (§ 1256(b))
- [ ] Every position classified as one of the **five** § 1256(b)(1) types, or
      explicitly classified out — never left to be inferred from a symbol?
- [ ] Index options confirmed **broad-based**? A narrow-based security index
      option is an equity option (§ 1256(g)(6)) and does not qualify.
- [ ] Options on **ETF shares** (SPY, QQQ, IWM) treated as equity options — an ETF
      share is stock — and excluded?
- [ ] Single-stock futures and every swap / cap / floor excluded under
      § 1256(b)(2)?
- [ ] `DEALER_EQUITY_OPTION` / `DEALER_SECURITIES_FUTURES_CONTRACT` used only for
      a registered dealer, with § 1402(i) self-employment tax considered?
- [ ] Excluded positions still reported (Form 8949 / Schedule D by actual holding
      period) rather than dropped from the blotter?

## Routed out of Part I
- [ ] Identified § 1256(e) hedges excluded from the mark, treated as **ordinary**,
      and entered in the Form 6781 **line 4** adjustment with a statement?
- [ ] Hedge identification confirmed to exist **before the close of the day** the
      transaction was entered into — not created at year end?
- [ ] Mixed straddle legs routed out? § 1256(a)(4) shelters a straddle from
      § 1092 only when **every** leg is a § 1256 contract.
- [ ] § 988 election (§ 988(a)(1)(B) or § 988(c)(1)(D)) on any currency contract
      identified, with the covered-contract list attached to the return?
- [ ] Confirmed no § 475(f)(2) commodities election is in effect — it disapplies
      § 1256(a) and forfeits 60/40 entirely?

## Mark-to-market (§ 1256(a)(1), (a)(2))
- [ ] Every contract held at the close of the year marked to fair market value on
      the **last business day** — not assumed to be December 31?
- [ ] `is_open_at_year_end` set on every marked position, so no mark is silently
      dropped?
- [ ] Realized and marked amounts describe **disjoint** portions of a partially
      closed position?
- [ ] For every contract carried across a prior year end: the prior year's mark
      removed under § 1256(a)(2)?
- [ ] Only **one** adjustment convention used per trade — a Form 1099-B **box 11**
      figure is already broker-adjusted and must **not** also carry
      `prior_year_end_cumulative_mark_usd`?

## Character and placement
- [ ] Net figure split **40% short-term / 60% long-term**, with no holding period
      computed for any § 1256 contract?
- [ ] Losses split 60/40 on the same terms as gains?
- [ ] Line 8 routed to **Schedule D (Form 1040) line 4**, line 9 to **line 11**?
- [ ] Wash sale matching **not** run over § 1256 contracts?
- [ ] Partnership / S corporation: stopped at line 5, lines 6–9 left blank?

## Loss year (§ 1212(c) → § 1211(b) → § 1212(b))
- [ ] Net section 1256 contracts loss computed as the smaller of **both** box D
      prongs, not just the excess over $3,000?
- [ ] Carryback limited to prior **§ 1256** gains only, taken to the **earliest**
      of the 3 preceding years first?
- [ ] Each carryback year checked against the smaller of its § 1256-only and its
      actual Schedule D **line 16** gain, figured before any carryback?
- [ ] Confirmed the carryback does not increase or create an **NOL** in any
      carryback year?
- [ ] Taxpayer eligible — **not** a corporation, estate or trust?
- [ ] Form 1045 or amended return prepared, with an amended Form 6781 and
      Schedule D, carryback shown on the prior year's **line 1**?
- [ ] Remaining loss applied against other capital gains **before** the $3,000
      ($1,500 MFS) cap?
- [ ] § 1212(b) carryforward reported, not discarded?

## Tax estimate
- [ ] Rates supplied as decimal fractions (`0.37`, not `37.0`)?
- [ ] § 1411 NIIT considered — 3.8% on top above the MAGI threshold, which makes
      the top blended rate 30.6%, not 26.8%?
- [ ] Understood that the 10.2-point advantage is unchanged by NIIT because NIIT
      is character-blind?
- [ ] Self-employment tax applied only to a **dealer's** § 1256 P&L (§ 1402(i))?

## Output
- [ ] `warnings` read end to end — every exclusion, adjustment and unevaluated
      limitation surfaced rather than silently dropped?
- [ ] Excluded totals reconciled back to the source blotter, so nothing
      disappeared between the broker statement and the return?
- [ ] Results reviewed by a qualified US tax professional before filing?
