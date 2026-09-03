# MOEX Order Path — Pre-Flight Checklist

Sign off before any order leaves the process. Facts and citations behind each
item are in `references/standards.md`.

## Sanctions — do this first

- [ ] Sanctions position for this entity, counterparties and activity determined
      with counsel, not inferred from this repository.
- [ ] Screening performed against the regimes that bind us, and the result,
      regimes and date recorded on the session.
- [ ] It is understood that MOEX's central counterparty (NCC) is itself
      OFAC-designated, so the clearing leg is inside the block.
- [ ] Re-screening cadence set deliberately, or `max_screening_age_days` left at
      `None` as a conscious choice; `as_of` supplied when a cadence is set.
- [ ] The gate fails closed: an absent attestation blocks the order.

## Instrument and board

- [ ] Board resolved against ISS `/iss/index.json`, not guessed.
- [ ] Interface confirmed to serve the board — ASTS MFIX covers FX and
      Securities only; FORTS (`RFUD`) needs TWIME SPECTRA / Plaza II.
- [ ] Reference data loaded for the exact Symbol + Board pair.
- [ ] `LOTSIZE`, `MINSTEP`, `DECIMALS` all present; **no defaults assumed**.
- [ ] `CURRENCYID` mapped deliberately (`SUR`, not `RUB`).
- [ ] `source` and `as_of` recorded with the reference data.
- [ ] Instrument confirmed **actually trading** (NUMTRADES / TRADINGSTATUS /
      live book), not merely listed with `STATUS='A'`.

## Quantity

- [ ] Tag 38 carries **lots**, never shares or currency units.
- [ ] Unit-to-lot conversion refuses a remainder rather than rounding.
- [ ] Both lots and resulting units checked against intent before sending.

## Price

- [ ] Price is an exact multiple of `MINSTEP`, checked in `Decimal`.
- [ ] Positivity checked **separately** from step alignment.
- [ ] Off-step prices rejected, not silently moved; any alignment rounds away
      from the market (BUY down, SELL up).
- [ ] A price control applies: Exchange-published `LOWLIMIT`/`HIGHLIMIT` where
      available, otherwise an explicitly declared client-side band.
- [ ] No fixed percentage collar is presented as a MOEX rule.
- [ ] Tag 44 rendered at the instrument's `DECIMALS` and ≤ 10 characters
      including the decimal point.
- [ ] Market orders carry Tag 44 = zero.

## FIX message

- [ ] Board is Tag 336 inside the Tag 386 group, 386 immediately followed by
      336, exactly one element — no invented `BoardID` field.
- [ ] `SecurityExchange` / `MISX` **not** sent as an order field.
- [ ] Client code in `<Parties>`: 448 code, 447 = `D`, 452 = `3`.
- [ ] `Account` ≤ 12 chars, `Symbol` ≤ 12 chars, `TradingSessionID` ≤ 4 chars.
- [ ] No emitted string field contains a FIX delimiter (SOH or `=`).
- [ ] Header and trailer left to the FIX session layer.

## Order identity and recovery

- [ ] `ClOrdID` is caller-generated, unique per order, ≤ 20 characters.
- [ ] `ClOrdID` is **not** derived from the order's own field values.
- [ ] `ClOrdID` does not begin with `#`, so the order stays cancellable.
- [ ] On an ambiguous timeout, order state is resolved through the venue and the
      original `ClOrdID` reused — never a blind resubmit.

## Before promoting to live

- [ ] One validated order exercised against a MOEX test environment; the (336,
      55) pair resolved to a security.
- [ ] `python -m unittest discover -s skills/moscow-exchange-moex-api-integration/scripts` passes 100%.
- [ ] It is understood that `ready_to_send` means "passed local checks", never
      "MOEX accepted the order".
