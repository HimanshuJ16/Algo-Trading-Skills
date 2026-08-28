# Workflows — prime-brokerage-multi-venue-consolidation

## 1. Ingest multi-venue fills

- Build one `VenueExecution` per fill from each executing broker's drop copy or fill
  stream. Construction validates: unrecognised `side`, non-positive quantity,
  non-finite or negative numbers, a malformed ISO date, a malformed currency code, a
  non-positive multiplier and blank identifiers all raise here.
- Derivatives carry an explicit `contract_multiplier` read from the contract
  definition. It is never inferred from the ticker: an adjusted option contract can
  deliver a non-standard number of shares, and it is a different instrument from the
  standard one for netting purposes.
- Record the third-party `executing_broker_commission` on the fill. A give-up incurs
  it *in addition to* the PB clearing fee; modelling one leg understates the all-in
  cost of routing away from the PB.

## 2. Enforce give-up idempotency

- The engine keeps a ledger of every `execution_id` it has emitted an instruction for.
- Within a batch, a duplicate always raises. Across batches, it raises while
  `enforce_cross_batch_idempotency` is on (the default).
- **After a give-up queue reconnect:** do not resubmit blindly. Pull the PB's claimed
  list, reconcile it against `submitted_execution_ids()`, and re-send only what is
  genuinely missing. `reset_submitted_execution_ids()` exists for a deliberate,
  reconciled resubmission — for example at a new trade-date boundary — not as a way
  around the exception.
- Registration is atomic: a batch that fails validation for any reason registers
  nothing, so the corrected batch is accepted without a reset.

## 3. Net per instrument

- Netting key is `(symbol, currency)` with a single contract multiplier. Mixed
  currencies under one symbol, or mixed multipliers under one key, raise.
- Per key the engine reports net and gross quantity, buy and sell quantity, gross
  notional, VWAP, residual notional at VWAP, the instrument offset ratio, and signed
  breakdowns by executing broker and by venue.
- Read `gross_quantity` / `gross_notional` for what actually traded and what the fees
  price off; read `net_quantity` for what remains to be financed. `is_internally_offset`
  flags a book whose venues traded against each other.

## 4. Price both fee legs

- PB clearing fee: `quantity × clearing_fee_per_unit`, accumulated in the spec's
  `fee_currency` regardless of the fill currency.
- Executing-broker commissions: accumulated per fill currency.
- Nothing is summed across currencies. If a single base-currency total is needed,
  convert downstream with a timestamped rate table.

## 5. Build the payload and check timeliness

- One give-up instruction per execution, carrying PB account and name, executing
  broker, venue, symbol, side, quantity, price, multiplier, currency, notional, both
  fee legs, trade date, and the `submitted_after_cutoff` flag.
- Timeliness is optional but all-or-nothing: supply `submitted_at` **and**
  `giveup_cutoffs`, both timezone-aware, with a cut-off for every trade date in the
  batch. Any gap raises rather than silently reporting "on time".
- Source cut-offs from the applicable rulebook — for US cash equities, end of trade
  date under SEC Rule 15c6-2 with DTC's 9:00 p.m. ET affirmation cut-off; for listed
  futures, the clearing house's allocation/claim deadlines. Do not carry a constant.
- Late instructions are flagged per row, listed in `late_giveup_execution_ids`, logged
  at WARNING, and set the status to `CONSOLIDATION_SUCCESSFUL_LATE_GIVEUP`. The batch
  is still produced: operations needs the payload *and* the exception.

## 6. Reconcile after transmission

- Transmission is not acceptance. Under the SIFMA Form 150 framework the PB may
  DK/disaffirm, and a disaffirmed trade remains a customer trade on the executing
  broker's books — which is a financing and settlement exposure, not a data error.
- Compare the PB's claimed/affirmed list against `giveup_payload`. Escalate anything
  unclaimed before the PB's disaffirmance deadline passes, and record the outcome in
  the operations log alongside `audit_notes`.

## 7. Audit output

`PBConsolidationReport` carries: per-currency gross notional, residual notional and
notional offset percentage; per-instrument netted positions; PB clearing fees in the
fee currency; executing-broker commissions per currency; the give-up payload; the
trade dates covered; the late list; the status; and `audit_notes` for the log. There
is deliberately no margin or capital-savings field — see `references/standards.md`.
