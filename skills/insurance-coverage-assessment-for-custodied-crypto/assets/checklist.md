# Pre-Flight Checklist — Custody Insurance Assessment

## Evidence

- [ ] Insurance binder and executed custody agreement obtained (not a marketing page)?
- [ ] For each policy: form, limit, retention, occurrence vs aggregate basis, and named
      insured recorded?

## Tier mapping

- [ ] Confirmed from the **wording** which wallet tier each policy attaches to, rather than
      inferring it from "specie" or "crime"?
- [ ] Any uninsured tier recorded explicitly as a 0.0 limit?
- [ ] Confirmed whether the custodian covers hot wallets at all?

## Dilution and retention

- [ ] Custodian pool size obtained for the cold tier, and its provenance recorded as
      disclosed or estimated?
- [ ] Custodian pool size obtained for the hot tier, or the undiluted assumption accepted
      and documented?
- [ ] Retention confirmed, including whether the custodian absorbs it and whether it applies
      per occurrence?
- [ ] Checked whether the firm's balance is smaller than the retention?
- [ ] Any dedicated limit (loss-payee endorsement) evidenced in writing before setting
      `cold_limit_is_dedicated_to_firm`?

## Structure

- [ ] Confirmed whether hot and cold limits are independent towers or one shared aggregate?
- [ ] Checked whether prior-year claims have eroded an annual aggregate?
- [ ] Confirmed whether the firm is a named insured or loss payee, or holds only a
      contractual claim against the custodian?

## Output

- [ ] Both scenarios reported — isolated-loss upper bound and pooled-loss lower bound?
- [ ] Net uninsured capital reported in USD and percentage?
- [ ] The report's `assumptions` list carried alongside every quoted figure?

## Out of scope — confirm covered elsewhere

- [ ] Policy exclusions documented: smart contract and DeFi exploits, market loss,
      client-side key loss, war and terrorism, regulatory seizure?
- [ ] Slashing exposure assessed separately, including delegator scope and correlated-event
      exclusions?
- [ ] Custodian insolvency and bankruptcy remoteness assessed separately — insurance does not
      cover it, and neither FDIC nor SIPC applies?
- [ ] Review cadence set for the next annual renewal?
