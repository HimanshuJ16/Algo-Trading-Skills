# Pre-Flight / Sign-off Checklist — prime-brokerage-multi-venue-consolidation

Use this before a give-up batch is transmitted to the prime broker.

## Agreements and account

- [ ] **Give-Up Agreement Executed:** A give-up agreement is in force with every
      executing broker in the batch (SIFMA Form 150-style for US equities; FIA
      International Uniform Give-Up Agreement via FIA Tech EGUS for listed futures).
- [ ] **PB Account Eligible:** The account meets the minimum net equity the SEC's 1994
      prime brokerage no-action letter requires, and a breach-and-restore procedure
      exists (five business days to restore).
- [ ] **Account Id Verified:** `pb_account_id` is the account the PB will actually
      claim into — it is stamped on every instruction and is not defaulted.

## Inputs

- [ ] **Unique Execution Ids:** `execution_id` is unique per *fill*, not per order, and
      is stable across a reconnect so a replay is detectable.
- [ ] **Strict Sides:** Upstream parsing emits exactly `BUY`/`SELL`; no side value is
      mapped by "anything not BUY".
- [ ] **Unsigned Quantities:** Quantities are positive; direction is carried by `side`.
- [ ] **Contract Multipliers:** Every derivative fill carries an explicit
      `contract_multiplier` from the contract definition, not assumed from the ticker.
- [ ] **Currencies Tagged:** Every fill carries its ISO 4217 trading currency; no
      symbol appears under two currencies.
- [ ] **Both Fee Legs Present:** `executing_broker_commission` is populated per fill and
      `clearing_fee_per_unit` / `fee_currency` match the PB's fee schedule.

## Idempotency

- [ ] **Cross-Batch Check On:** `enforce_cross_batch_idempotency` is left enabled in
      production.
- [ ] **Reconnect Procedure Written:** Operators reconcile against the PB's claimed list
      before any resubmission; `reset_submitted_execution_ids()` is used only after
      that reconciliation.
- [ ] **Ledger Lifetime Understood:** The ledger lives on the engine instance — a
      process restart clears it, so PB-side reconciliation, not process memory, is the
      authority.

## Timeliness

- [ ] **Cut-Offs Sourced:** Deadlines come from the applicable rulebook or PB SLA (US
      equities: end of trade date per SEC Rule 15c6-2, DTC affirmation cut-off 9:00
      p.m. ET; futures: clearing house rulebook). No constant is hard-coded.
- [ ] **Timezone-Aware:** Both `submitted_at` and every cut-off carry an offset; DST
      transitions are handled by the source of the cut-off, not assumed.
- [ ] **Full Coverage:** A cut-off exists for every trade date in the batch.
- [ ] **Late Batch Escalates:** `CONSOLIDATION_SUCCESSFUL_LATE_GIVEUP` and the WARNING
      log are wired to an operations alert, not just written to the report.

## Output interpretation

- [ ] **No Margin Claim Made:** `notional_offset_pct_by_currency` is reported as a
      netting statistic. Nothing downstream presents it as margin or capital savings.
- [ ] **Gross vs Net Chosen Deliberately:** Cost and impact analysis reads gross;
      financing and residual exposure read net.
- [ ] **Per-Currency Totals Preserved:** Any single-currency roll-up happens downstream
      with a timestamped FX table, not by summing this report's buckets.

## Post-transmission

- [ ] **Claim Reconciliation:** The PB's claimed/affirmed list is compared against
      `giveup_payload` and breaks are escalated before the disaffirmance deadline.
- [ ] **DK Handling:** A disaffirmed trade is recognised as remaining on the executing
      broker's books, and the resulting financing/settlement exposure has an owner.
- [ ] **Audit Trail Stored:** `audit_notes`, the payload and the PB response are
      retained per the firm's recordkeeping policy.
