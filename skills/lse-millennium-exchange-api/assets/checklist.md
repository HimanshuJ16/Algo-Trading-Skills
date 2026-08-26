# Pre-Flight Checklist — LSE Millennium Exchange order path

## Reference data

- [ ] Instrument records loaded from the LSE Reference Data Service (MIT401), not hard-coded?
- [ ] `InstrumentID` (FIX Tag 48) held for every instrument, and used as the cache key rather
      than the TIDM?
- [ ] `Currency` read per instrument, with no venue-wide GBX assumption anywhere in the path?
- [ ] Price Tick File loaded per `Price Tick Table ID` (`Min Value` / `Max Value` /
      `Tick Value`), and refreshed on the Exchange's schedule?
- [ ] Liquidity bands sourced from the FCA's published ADNT (FITRS), not from your own trade
      counts, and re-read from the first Monday of April?

## Symbol

- [ ] TIDM validated as `STRING(4)` — length ≤ 4, not "letters only"?
- [ ] `BP.`, `BT.A`, `3IN` and similar mnemonics accepted by the validator?
- [ ] Vendor symbols (`SHEL.L`, `SHEL LN`) rejected before dispatch?
- [ ] Corporate-action handling covers a TIDM change deleting and re-adding the instrument?

## Price

- [ ] Payload currency compared against the instrument's `Currency`, and a GBP price on a GBX
      line rejected rather than sent 100× low?
- [ ] Tick taken from the instrument's price tick table when available?
- [ ] RTS 11 used only as a fallback, keyed on **both** price range and liquidity band?
- [ ] Instrument with neither a tick table nor a band failing closed, not defaulting?
- [ ] Reference-data tick finer than the RTS 11 floor flagged (Article 2(2A) or stale data)?
- [ ] Tick test done in `Decimal`, not floating point?
- [ ] Price positivity checked separately from the modulo test?

## Quantity and value

- [ ] Quantity validated as a positive integer?
- [ ] GBX notional divided by 100 before being reported or compared as pounds?
- [ ] Non-sterling lines left unconverted rather than given an invented FX rate?

## Documentation

- [ ] Tick-size controls documented against **RTS 11** ((EU) 2017/588), not RTS 28?
- [ ] `ready_to_send` understood as "client checks passed", never "the Exchange accepted it"?
