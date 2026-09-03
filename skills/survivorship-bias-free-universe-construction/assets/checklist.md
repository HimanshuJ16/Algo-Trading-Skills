# Pre-Flight / Sign-off Checklist — survivorship-bias-free-universe-construction

Use this before signing off a backtest whose universe was reconstructed point-in-time.

## Data ingest

- [ ] **Delisting-date convention reconciled:** the vendor field loaded into
      `delisting_date` is the **last date the security traded**, not an index deletion
      effective date, a trading-suspension date, or a Form 25 date. Conversion recorded.
- [ ] **Stable security identifier populated:** `security_id` carries PERMNO / CUSIP /
      SEDOL / FIGI. It is *not* left defaulting to the ticker on a multi-decade universe.
- [ ] **Recycled tickers verified:** every ticker used by more than one issuer resolves
      to the correct issuer on both a pre- and a post-reassignment date.
- [ ] **Terminal value supplied for every delisted name:** exactly one of
      `delisting_settlement_price` or `delisting_return`. (Registration raises otherwise
      — a clean load is the evidence.)
- [ ] **Merger consideration is the deal price, not the last close.** Confirmed on at
      least one name where the two differ.

## Point-in-time queries

- [ ] **Delisted names present:** a universe query on a date before a known delisting
      returns that name. Spot-checked on a name that no longer exists.
- [ ] **Boundary checked:** the name is in the universe on its last trading date and out
      of it on the next session.
- [ ] **No ticker collisions:** `get_active_universe` runs clean across the whole
      backtest window (it raises on overlapping listing windows).

## Settlement

- [ ] **Every open position at a delisting date is settled, not deleted.** Deleting the
      position removes the loss and the audit still passes.
- [ ] **Bankruptcy settlement value justified:** `0.0` is a stated modelling choice, not
      a default. Where the delisting return is genuinely missing, the imputation used
      (−30% NYSE/AMEX, −55% Nasdaq) is recorded and matched to the listing venue.
- [ ] **No imputation over observed data:** where the vendor reports an actual delisting
      return, that value was used.
- [ ] **`last_traded_price` on the same basis as the position quantity** (both adjusted,
      or both raw).
- [ ] **Short positions checked:** at least one short in a wiped-out name settles as a
      gain, not a loss.

## Audit

- [ ] **Denominator understood:** `universe_in_period`, not everything registered.
      `never_live_in_period` reviewed for a registry that spans a wider era than the test.
- [ ] **Attrition threshold set and defended:** `min_expected_attrition_rate` reflects
      the index, era and asset class, and is archived with the report.
- [ ] **Ghost audit run:** `current_static_universe` supplied. `ghost_count` of `None`
      is rendered "not audited" and never read as zero; `0` on a multi-year window is
      treated as evidence of a snapshot-built universe, not a clean bill of health.
- [ ] **Ghost count read as a lower bound:** it compares tickers, so a recycled ticker
      hides its delisted issuer. `delisted_in_period` reviewed alongside it.

## Reporting

- [ ] **No unsourced magnitude claim.** The bias is measured on this universe; no
      headline "inflates Sharpe by X%" figure is quoted.
- [ ] **Backtest labelled as theoretical performance** wherever it leaves the desk
      (GIPS 2020 for Firms 4.C.48; SEC Rule 206(4)-1(d)(6) for US registered advisers),
      with the universe-construction methodology disclosed.
- [ ] **Complementary gates run:** this skill covers tradability and settlement only.
      Index membership (`point-in-time-index-constituent-tracking`), announcement timing
      (`backtest-look-ahead-in-universe-selection`) and corporate actions
      (`corporate-action-adjusted-backtesting`) are separate checks.

## Automated testing

- [ ] Run `python -m unittest discover -s skills/survivorship-bias-free-universe-construction/scripts` — 100% pass
      rate (51 tests).

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Delisting-date convention and imputations used: ___________________________
