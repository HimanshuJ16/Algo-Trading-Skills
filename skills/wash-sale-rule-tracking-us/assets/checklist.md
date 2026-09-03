# US Wash Sale (IRC § 1091) Sign-Off Checklist

Jurisdiction: **US federal only.** Sign off per account, per security identifier.

## Scope — establish before running anything
- [ ] **One account per run**: the ledger passed to the engine covers a single
      account. § 1091 applies to the taxpayer, not the account.
- [ ] **Other accounts assessed separately**: other brokers, joint and spouse
      accounts, and any IRA or Roth IRA have been reviewed for replacement
      purchases in the same window.
- [ ] **IRA replacement identified**: any loss washed by a purchase in the
      taxpayer's IRA or Roth IRA is flagged as **permanently disallowed with no
      basis increase** (Rev. Rul. 2008-5) — not as a deferral.
- [ ] **"Substantially identical" mapping decided and documented**: instruments
      the taxpayer treats as substantially identical are mapped to one `symbol`
      before ingestion. § 1091 is not limited to the same CUSIP.
- [ ] **Out-of-scope exposure listed**: options and contracts to acquire
      (Treas. Reg. § 1.1091-1(f)), short sales (§ 1091(e)), and any § 475(f)
      mark-to-market election are handled outside this engine.

## Ledger completeness
- [ ] **Loading window straddles the period**: executions loaded from 30 days
      before the period start to 30 days after the period end, so December losses
      see January purchases and January losses see December purchases.
- [ ] **Every `trade_id` unique**: the engine rejects duplicates; a merge upstream
      would silently corrupt replacement capacity.
- [ ] **`trade_date` is a date, not a datetime**: the ±30 day comparison is on
      whole trade dates.
- [ ] **No unmatched sells**: `evaluate_wash_sales_for_symbol` completes without
      `WashSaleError`. An exception means a missing buy or a short position, not a
      rounding issue — do not suppress it.
- [ ] **Basis is acquisition-complete**: commissions and corporate-action
      adjustments already applied upstream.

## Matching and disallowance
- [ ] **Window is ±30 days inclusive of the disposition date** (61 days total),
      and `window_days` is left at the statutory 30 — a non-statutory value logs a
      warning and does not produce a § 1091 result.
- [ ] **Losses applied in disposition order** and **replacements matched in
      acquisition order** (Treas. Reg. § 1.1091-1(b), (c)).
- [ ] **No replacement share absorbs two losses** (Treas. Reg. § 1.1091-1(e)).
- [ ] **A liquidated position shows no disallowance**: if the whole position was
      sold and not repurchased within 30 days, Box 1g for those dispositions is
      zero and `deferred_loss_in_open_lots_usd` is zero.
- [ ] **Basis carry-forward verified**: for at least one chained case, the later
      sale of a replacement lot uses the § 1091(d)-adjusted basis, and
      `net_allowed_taxable_pnl_usd` is not merely gross P&L plus Box 1g computed
      from purchase price.

## Reporting
- [ ] **Box 1d / 1e / 1g populated from the summary**, with Box 1e carrying the
      **adjusted** basis.
- [ ] **Identity holds**: `net_allowed_taxable_pnl_usd = 1d − 1e + 1g`.
- [ ] **Form 8949 rows** carry adjustment code **W** in column (f) with the
      disallowed loss as a **positive** amount in column (g).
- [ ] **Deferred loss carried forward**: `deferred_loss_in_open_lots_usd` is
      recorded as next period's opening basis adjustment.
- [ ] **Holding period handled elsewhere**: § 1223(3) tacking is applied in the
      STCG/LTCG classification step, not here.
- [ ] **Reconciled to the broker's Form 1099-B**, with every difference explained.
      The broker reports only same-account, same-CUSIP wash sales
      (Treas. Reg. § 1.6045-1(d)(6)(iii)), so a taxpayer figure that is larger is
      expected; a broker figure that is larger means the ledger is incomplete.

## Records
- [ ] **`WashSaleMatch` records retained** for every disallowance: disposition id,
      replacement acquisition id, matched quantity, disallowed amount, and the
      resulting per-share basis.
- [ ] **Retention period set from § 6501, not a rule of thumb**: 3 years from
      filing (§ 6501(a)); 6 years where more than 25% of gross income is omitted
      (§ 6501(e)(1)(A)); unlimited for a false or fraudulent return, a willful
      attempt to evade, or a return never filed (§ 6501(c)(1)–(3)). Basis records
      must survive until the limitation period closes for the year the shares are
      **disposed of**, which for a long-deferred wash sale can be many years after
      the purchase.
