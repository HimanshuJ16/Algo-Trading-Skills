# Pre-Flight Checklist

## Input data

- [ ] Is the price series **raw/unadjusted**? (Most retail APIs return adjusted closes by
      default — adjusting an adjusted series applies every factor twice.)
- [ ] Are splits, reverse splits and cash dividends logged against the **ex-date**, not
      the declaration, record or pay date?
- [ ] Is `value` the share multiplier for splits (2.0 = 2-for-1, 5.0 on a
      `REVERSE_SPLIT` = 1-for-5) and the per-share cash amount for dividends?
- [ ] Is the bar immediately preceding each dividend ex-date present in the series? That
      close is the factor's denominator.
- [ ] Are spin-offs, mergers, rights issues and returns of capital excluded and handled
      out of band? Only splits and ordinary cash dividends are modelled here.

## Factors

- [ ] Is the dividend factor referenced to the close **preceding** the ex-date, not the
      ex-date close?
- [ ] Is volume adjusted by the **share-count** factor only, so cash dividends leave it
      untouched?
- [ ] Is `caf == 1.0` and `adj_close == raw_close` on the most recent bar?
- [ ] Do ex-dates falling on holidays, halts or calendar gaps still apply?
- [ ] Are cumulative factors kept unrounded, with rounding confined to display?

## Usage

- [ ] Are signal indicators evaluated on the **adjusted** price series?
- [ ] Are order quantity, cash debit/credit, commission and tick rounding computed on the
      **raw** series?
- [ ] Is dividend cash credited explicitly (`cash += shares * D`) from the event log, and
      *not* also implied by treating the adjusted series as total return?
- [ ] Is `as_of` passed for any walk-forward loop, so events that had not gone ex yet are
      hidden from the signal?

## Failure handling

- [ ] Does an unknown `event_type` raise, rather than contributing a factor of 1.0?
- [ ] Does a non-positive split ratio or non-finite value raise before it reaches the CAF
      loop?
- [ ] Is a `CorporateActionError` for a dividend at or above its reference close
      investigated as a data or special-distribution issue, rather than clamped?
- [ ] Has the adjusted series been spot-checked against a second vendor at a known event?
