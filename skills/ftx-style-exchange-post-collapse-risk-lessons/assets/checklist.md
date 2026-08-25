# Pre-Flight Checklist — Post-Collapse Exchange Counterparty Risk

Venue: ______________________  Legal entity / jurisdiction: ______________________
Audit date: ____________  Auditor: ____________

## Scope and provenance

- [ ] The **legal entity** you contract with is recorded, not just the brand — that entity is the one that goes into insolvency.
- [ ] Audit date recorded, and every conclusion below is labelled with it.
- [ ] Each input tagged *verified by us* / *attested by a third party* / *asserted by the venue*.
- [ ] It is written down that the verdict inherits the weakest of those tags.

## Proof of Reserves

- [ ] Reserve figure is backed by proof of **liabilities**, not a wallet snapshot alone.
- [ ] PoR verified cryptographically (`exchange-proof-of-reserves-verification`), not copied off a dashboard.
- [ ] `proof_of_reserves_ratio` supplied as a **ratio** (1.05 == 105%), not a percentage.
- [ ] Snapshot timestamp obtained and `por_snapshot_age_days` computed — or `None` passed rather than guessed.
- [ ] Snapshot age is inside `max_por_snapshot_age_days`, or the staleness finding is carried into the write-up.
- [ ] It is stated in the write-up that a PoR engagement is not an audit (PCAOB advisory, 2023-03-08).

## Native-token collateral

- [ ] Native-token share measured against **collateral posted at this venue**, as a fraction (0.05 == 5%).
- [ ] Affiliate tokens counted, not just the venue's headline token.
- [ ] Position marked at a **stressed** price, not the screen price.
- [ ] Soft cap (`max_native_token_ratio`) and hard trigger (`hard_native_token_trigger`) both set deliberately — a soft-cap breach alone does not force a withdrawal.

## Settlement and custody

- [ ] `uses_off_exchange_settlement` is True only because **this desk's** collateral is actually held off-venue today.
- [ ] Custodian entity and governing law recorded.
- [ ] Tri-party agreement reviewed for enforceability in the relevant insolvency forum.
- [ ] Settlement cadence recorded, and the unsettled-obligation window sized.
- [ ] Noted that unrealised PnL remains a claim on the venue regardless of OES.

## Attestation

- [ ] `has_independent_attestation` reflects the position **today**, not that a report once existed.
- [ ] Provider is still performing crypto assurance work and the report is still published.
- [ ] Report post-dates any material corporate event at the venue.

## Concentration

- [ ] `nav_exposure_pct` includes collateral, free balance and open position margin.
- [ ] Single-venue cap set (`max_single_venue_nav_pct`).
- [ ] Aggregate exposure across **all** centralised venues reviewed separately — this engine sees one venue at a time.

## Inputs and configuration

- [ ] All fractions passed as fractions; no percentage passed where a fraction is expected.
- [ ] Non-finite, negative and out-of-range inputs raise rather than scoring.
- [ ] Thresholds set from the mandate, and recorded as **risk policy, not regulation**.
- [ ] `derisk_residual_nav_pct` ≤ `max_single_venue_nav_pct`, and `hard_native_token_trigger` ≥ `max_native_token_ratio`.

## Reading the verdict

- [ ] Admissibility decided on `is_derisking_triggered` / `status` — **never** on `recommended_capital_withdrawal_pct`.
- [ ] Understood that 0.0% withdrawal can mean "nothing deployed" or "already inside the residual target", not "safe".
- [ ] Withdrawal percentage read as a share of **venue capital**, not of fund NAV.
- [ ] Disqualification vs trimming distinguished in the write-up.

## De-risking execution

- [ ] Withdrawal destinations confirmed against the whitelist before initiating.
- [ ] Test transaction sent first for a large or first-time destination.
- [ ] Open positions unwound before collateral is pulled.
- [ ] Accepted that a venue in difficulty may throttle or halt withdrawals — the gate's value is in the audits done before the queue forms.

## Cadence

- [ ] Re-audit scheduled per PoR publication, at minimum.
- [ ] Score, findings, inputs and provenance persisted per audit.
- [ ] Series reviewed for drift: falling coverage, growing snapshot age, rising native-token collateral, a changed or vanished attestation provider.

## Limitations recorded in the write-up

- [ ] Stated that this engine verifies nothing and every input is supplied.
- [ ] Stated that the thresholds are internal policy, not regulator-set.
- [ ] Stated whether the venue is in scope of any segregation regime (e.g. MiCA Arts. 70/75 for EU-authorised CASPs) — the engine does not model jurisdiction.
- [ ] Stated that off-exchange settlement reduces rather than removes counterparty exposure.
