# Pre-Flight Checklist — Options Expiry Cycle Conventions

Sign off before an options chain model, roll scheduler or expiry-day risk
process goes live.

## Contract resolution

- [ ] Is every `(exchange, symbol)` used by the system present in the registry,
      or does the caller declare `asset_class` explicitly?
- [ ] Does an unrecognised symbol raise `UnknownContractError` rather than
      falling back to a default? (Confirm nothing downstream catches and
      substitutes a guess.)
- [ ] Does a registered contract win over a declared `asset_class`, so
      `SPX` + `asset_class="EQUITY"` still resolves European/cash?
- [ ] Does every registry entry carry a primary `source` and a `source_as_of`
      no older than your reference-data refresh cycle?

## Expiry date

- [ ] Is the expiry rule taken from the **venue**, not assumed to be the third
      Friday? (Deribit = last Friday; Cboe VIX = 30-day Wednesday.)
- [ ] Is the third Friday derived arithmetically, never from
      `calendar.monthcalendar()` with its process-global first-weekday
      dependency?
- [ ] Is a `WEEKLY` cycle rejected rather than silently resolved to the monthly
      anchor?
- [ ] Is `QUARTERLY` rejected outside the venue's quarterly months?
- [ ] For CME `ES`, is a `MONTHLY` cycle rejected, so the American-style
      quarterly is never confused with the European-style Third-Friday Monthly?

## Holiday adjustment

- [ ] Is a holiday calendar supplied at all? If not, is
      `report.holiday_calendar_applied == False` surfaced to the operator rather
      than discarded?
- [ ] Is the calendar keyed **per exchange**, so a US calendar cannot adjust a
      Eurex expiry?
- [ ] Are continuously-traded venues (Deribit) excluded from adjustment
      entirely?
- [ ] Does April 2022 resolve to `2022-04-14` and April 2025 to `2025-04-17`
      when the respective Good Friday is in the calendar?

## Last trading day

- [ ] Is `last_trading_date_iso` consumed, not just `expiration_date_iso`?
- [ ] For AM-settled monthlies (`SPX`, `NDX`, `RUT`, `VIX`), does the last
      trading day precede the expiration date?
- [ ] When a holiday rolls an AM-settled expiry back to Thursday, does the last
      trading day move to the Wednesday?
- [ ] Does the position-management logic stop trying to exit an AM-settled
      contract once `dte_to_last_trading_day < 0`?

## DTE and settlement

- [ ] Is `dte_days` signed, with `is_expired` checked before treating a contract
      as live?
- [ ] Are all four settlement types handled downstream — `AM_SETTLED`,
      `PM_SETTLED`, `AUCTION_SETTLED`, `FIXED_TIME_SETTLED` — rather than an
      AM/PM boolean?
- [ ] Are all three delivery types handled — `CASH`, `PHYSICAL`, `FUTURES`?
- [ ] Is early-assignment risk modelled wherever `exercise_style == "AMERICAN"`,
      including CME quarterly `ES`?

## Audit

- [ ] Are `report.warnings` logged or escalated, not dropped?
- [ ] Do persisted records keep `source`, `source_as_of` and
      `holiday_calendar_applied` so a past resolution can be re-justified?
- [ ] Does `python -m unittest discover -s skills/options-chain-expiry-cycle-conventions-by-exchange/scripts`
      pass?
