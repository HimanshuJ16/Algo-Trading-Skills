# Pre-Flight Checklist — Single Stock Futures (Where Available)

## Contract and availability
- [ ] Is the contract actually listed on the venue for every date in the study? (No US
      venue listed single stock futures between 18 Sep 2020 and 27 Jul 2026.)
- [ ] Is `settlement_type` read from the **contract's** terms, not assumed from the
      venue? (NSE stock futures are physically settled since the Oct 2019 expiry;
      CME's 2026 contracts are cash-settled; Eurex lists both variants.)
- [ ] Is `lot_size` the current contract multiplier, including any change from a past
      corporate action?
- [ ] Are the spot price, futures price, dividends and margins all in `spec.currency`?

## Pricing model
- [ ] Is the screen comparing against the **band** (`no_arbitrage_upper_bound` /
      `no_arbitrage_lower_bound`), not against `theoretical_fair_value`?
- [ ] Is `short_borrow_rate_annual` the rate actually quotable for this name and holding
      period, rather than the 0.5% placeholder?
- [ ] Is `lending_income_rate_annual` still 0.0 unless the share lending is contracted?
- [ ] Are dividends discrete cash dividends only, with no continuous dividend yield also
      applied? (Subtracting PV(D) *and* applying $q$ double-counts.)
- [ ] Is `excluded_dividends` zero, or explained? A non-zero count usually means ex-dates
      arrived in the wrong unit.
- [ ] Is `risk_free_rate_annual` the funding curve for this currency and tenor, as a
      decimal (0.06, not 6)?

## Signal quality
- [ ] Is `arbitrage_cost_threshold_pct` a **measured** round-trip cost — brokerage,
      exchange and clearing fees, bid-offer on both legs, market impact, funding spread,
      transaction taxes (STT and stamp duty in India), and margin carry to expiry —
      rather than the 0.3% placeholder?
- [ ] Is `gross_edge_pct` (edge beyond the violated band edge) the number being acted on,
      not `mispricing_pct`?
- [ ] For a `REVERSE_CASH_AND_CARRY`: has the borrow been **located** at or below the
      priced rate for the full tenor? (India: SLB; naked short selling is prohibited and
      institutional accounts cannot square off intra-day.)
- [ ] For a `CASH_AND_CARRY`: is funding committed for the full tenor at or below the
      priced rate?

## Corporate actions
- [ ] Is the ex-dividend adjustment **gated** on the ordinary/extraordinary test rather
      than applied unconditionally? (SEBI: no adjustment below 2% of the underlying's
      market value.)
- [ ] Is `underlying_market_price` the closing price on the day before the dividend
      announcement?
- [ ] For a backtest spanning 28 Jun 2022, does it use the 5% threshold before and 2%
      after?
- [ ] Is the adjustment applied to the contract's previous **mark-to-market settlement
      price**, not to the spot?
- [ ] Are non-dividend corporate actions (bonus, split, rights, merger, spin-off) handled
      elsewhere? This module does not model them.

## Settlement and margin
- [ ] Is there an unwind or roll plan before expiry for every physically settled leg, and
      is the position sized against the **full delivery obligation** rather than the
      margin?
- [ ] Are `ssf_margin_pct` and `spot_margin_pct` from the clearing member for NSE, Eurex
      and Euronext, rather than defaulted? (The engine raises `SSFConfigError` if they
      are omitted for those venues — do not work around it with a guess.)
- [ ] Does `margin_basis` in the report name the source of the percentages used?
- [ ] Is `leverage_multiplier` being read as a margin ratio only, with daily variation
      margin on the futures leg funded separately?

## Operational
- [ ] Does every input path raise on NaN/Inf rather than flooring, so a corrupted quote
      cannot produce a signal?
- [ ] Are threshold comparisons on unrounded values, so a 0.2996% edge does not fire a
      0.30% trigger?
- [ ] Is `pricing_model` recorded alongside every stored result?
