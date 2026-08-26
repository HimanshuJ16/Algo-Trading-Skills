# IBKR Global Routing — Pre-Flight Checklist

Sign off before the first live order to a new market, and again whenever the venue table,
symbol source or routing configuration changes.

## Contract identity

- [ ] `secType` is one of the values in the TWS API Contract reference (`STK`, `OPT`, `FUT`,
      `IND`, `FOP`, `CASH`, `BAG`, `WAR`, `BOND`, `CMDTY`, `NEWS`, `FUND`).
- [ ] `currency` is a 3-letter ISO-4217-style code, taken from the instrument's line — not
      inferred from the country the venue sits in.
- [ ] The symbol is passed **exactly as IBKR lists it**. No zero-padding, stripping or
      reformatting to match a market-data vendor's display convention.
- [ ] Hong Kong: the symbol is the numeric code as IBKR holds it (IBKR's own SEHK sample is
      `symbol = "1"`), not the padded display form, and it was confirmed with
      `reqContractDetails` rather than assumed.
- [ ] Stock Connect (`SEHKNTL`, `SEHKSZSE`): 6-digit mainland code, CNH.
- [ ] HKEX derivatives (`HKFE`): alphabetic product symbol (`HSI`), and no equity code rule
      is being applied to it.
- [ ] Forex (`CASH`): `symbol` is the base currency, `currency` the quote currency, and the
      two differ.

## Destination

- [ ] `exchange` is present and is either `SMART` or a venue code.
- [ ] `routing_mode` and `exchange` agree — no config labelled `DIRECT_EXCHANGE` that still
      carries `exchange='SMART'`.
- [ ] If a rebate preference is intended: it is configured at the **account/TWS** level under
      the Cost Plus commission structure, and nobody believes an order field is carrying it.
- [ ] If the destination is a direct venue: that venue appears in
      `ContractDetails.validExchanges` for this contract.
- [ ] If the destination is `SMART`: `ContractDetails.aggGroup != -1` for this contract.

## primaryExchange

- [ ] `primary_exchange` is **not** `SMART`.
- [ ] Venue names containing a period are trimmed to the part before it (`ENEXT`, not
      `ENEXT.BE`).
- [ ] A smart-routed stock whose symbol/currency pair could match more than one listing
      carries a `primary_exchange` hint.
- [ ] No smart-routed option, future or forex contract is being *rejected* for lacking one —
      IBKR does not require it there.

## Order fields

- [ ] `action` is `BUY` or `SELL` (or a deliberate, permissioned `SSHORT`/`SLONG`).
- [ ] `quantity` is finite and strictly positive; direction is carried by `action`, never by a
      negative size.
- [ ] Fractional quantities are only used where the instrument and account permit them, and
      the size field is typed to carry them (`Decimal`, not `int`).
- [ ] Limit-bearing order types (`LMT`, `STP LMT`, `LIT`, `LOC`, `REL`, `TRAIL LIMIT`) carry a
      finite, positive `lmt_price`.
- [ ] No `lmt_price` is set on an order type that ignores it.

## Before submitting

- [ ] `audit_and_route_order` returned `IBKR_ROUTING_VALIDATED`.
- [ ] `report.warnings` was **read**, not just the status — an unchecked currency and a
      non-wire routing label both surface only there.
- [ ] `reqContractDetails` returned **exactly one** match. Multiple matches were resolved by
      correcting the contract, not by picking one.
- [ ] The reported currency matches the one being sent.
- [ ] The order is submitted on the returned `conId`.
- [ ] Error 200 is handled as "fix the parameters", never as "retry" or "try another venue".

## Ongoing

- [ ] The local venue registry is understood to be a screen, not a source of truth: unknown
      venues warn and pass, and nobody has converted that into a rejection.
- [ ] Venue and symbol assumptions are re-verified against `reqContractDetails` after any
      IBKR exchange-code change, market addition or instrument re-listing.
