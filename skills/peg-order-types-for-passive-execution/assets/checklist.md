# Pre-Flight Checklist — Pegged Orders for Passive Execution

## Specification
- [ ] Is `side` validated against a known set, so an unrecognised value is rejected rather than defaulting to the sell branch?
- [ ] Is `peg_type` one of Primary / Midpoint / Market, and is the reference price the *same* side for Primary and the *opposite* side for Market?
- [ ] Is the offset sign convention documented at the boundary — aggressive-positive here, signed-side-independent in FIX `PegOffsetValue(211)` — and negated for SELL when emitting FIX?
- [ ] Are prices carried as `Decimal`, never binary floats?

## Market state
- [ ] Does the quote's symbol match the order's symbol?
- [ ] Are NaN and infinite quotes rejected explicitly? (`nan <= 0` is `False`.)
- [ ] Is a crossed book (`bid > ask`) suspended rather than priced?
- [ ] Is a locked book (`bid == ask`) still priced, with the midpoint equal to the locking price?
- [ ] On suspension, is the price `None` — with no silent fallback to a last-known-good quote?

## Protective bounds
- [ ] Is the pegged price non-marketable, or is marketability a deliberate, recorded choice?
- [ ] Is a `limit_cap` set — the only bound that survives every other setting being off?
- [ ] Are LULD bands applied, clamping a buy to the upper band and a sell to the lower band?
- [ ] For a short sale under an active Rule 201 price test, is the price strictly above the NBB (one minimum increment above)?
- [ ] Does the report name which bound produced the final price?

## Tick lattice
- [ ] Is `tick_size` set per instrument — `$0.01` at or above `$1.00`, `$0.0001` below?
- [ ] Is quantization the **last** step, after every clamp?
- [ ] Is the rounding direction chosen deliberately, and are bounds re-applied after aggressive rounding?
- [ ] Are sub-penny prices confined to **non-displayed** Midpoint pegs?

## Repricing
- [ ] Is a minimum-move threshold in ticks enforced before every cancel/replace?
- [ ] Is a replace suppressed when the latest evaluation is suspended?
- [ ] Is the message rate measured against the venue's order-to-trade budget?

## Operations
- [ ] Is a logging handler configured, so suspension warnings are actually visible?
- [ ] Are pegged orders cancelled around trading halts rather than carried on a pre-halt NBBO?
- [ ] Is the full `PegOrderReport` persisted for audit, not just the price?
