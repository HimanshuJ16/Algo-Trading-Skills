# Pre-Flight Checklist — Short Borrow Cost & Availability

## Data integrity

- [ ] Does every shortable universe member have a registered `BorrowStatus` for the date
      being evaluated, with no synthesized default inventory?
- [ ] Is a ticker with no borrow record **rejected** rather than priced as General
      Collateral?
- [ ] Is `utilization_rate` validated to $[0, 1]$ (on-loan / lendable) rather than
      accepted as-is from the vendor?
- [ ] Is borrow data point-in-time, so a historical backtest cannot see a later
      utilization or rate?

## Availability gate

- [ ] Is availability checked before order routing, and is the rejection reason code
      persisted for audit?
- [ ] Does contradictory data (inventory offered at 100% utilization) fail closed?
- [ ] Is availability re-checked at every rebalance, not only at entry?
- [ ] Is the Regulation SHO locate handled separately, by
      `us-reg-sho-short-sale-locate-requirements`, rather than assumed from this check?

## Fee mechanics

- [ ] Is `day_count_basis` 360 for USD/EUR loans (365 only for sterling)?
- [ ] Is the fee accruing on collateral (Margin Percentage × market value), not on bare
      notional?
- [ ] Does accrual run on **calendar** days, inclusive of the open date and exclusive of
      the cover date?
- [ ] Are per-day marks used (`calculate_borrow_cost_schedule`) rather than a single
      entry price, for any position held long enough to drift?
- [ ] Are the marks fed to the schedule **prior-day** settlement prices, so the cost line
      carries no look-ahead?
- [ ] Does the rate reprice per day for holds longer than a few days, rather than being
      frozen at the day-1 quote?

## Rate provenance

- [ ] Is `observed_borrow_rate` supplied wherever a broker or desk quote exists?
- [ ] Is `rate_source` persisted per position-day, so heuristic-priced results can be
      separated from quote-priced ones?
- [ ] Have `gc_rate`, `htb_base_rate`, `max_htb_rate` and `htb_utilization_threshold`
      been recalibrated against this desk's own borrow history, rather than left at the
      shipped placeholders?
- [ ] Is the discontinuity at the HTB threshold excluded from any signal or
      classification cutoff?

## Financing and P&L

- [ ] Is the borrow cost deducted from short P&L per accrual day, not once at exit?
- [ ] Is `short_proceeds_credit_rate` set where proceeds are actually rebated — and left
      unset (rather than guessed) where they are not?
- [ ] Are manufactured dividends and substitute payments accounted for elsewhere, since
      this module does not model them?

## Recall and squeeze

- [ ] Is `assess_recall_risk` run on every open short, with `ELEVATED` and `HIGH` tiers
      escalated to a human?
- [ ] Is there a defined action for a `HIGH` tier — reduce, pre-borrow, or accept — and a
      buy-in contingency if the borrow is recalled and cannot be replaced?
- [ ] Does position sizing account for the fact that an open loan is not term-funded
      (MSLA Sec. 6.1(a))?
