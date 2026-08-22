# Pre-Flight Checklist — Cross-Margin Capital Efficiency

## Account and scope

- [ ] Is portfolio margining actually **enabled** on the account? If the account is on
      isolated or Reg T margin, this estimate under-reports the real requirement outright.
- [ ] Does the account meet the eligibility minimums (FINRA 4210(g): $100k–$500k by
      monitoring capability; IBKR: $110k to open, $100k to maintain)?
- [ ] Are all positions in **one** margining pool? Positions at a second venue are a
      second clearing organisation and do not offset.
- [ ] For a US single-name equity book: has an explicit `0.0` credit rate been set, given
      OCC CPM grants non-index single-stock class groups no offset?

## Input hygiene

- [ ] Has `net_positions_by_symbol` been applied so each instrument appears **once**?
- [ ] Are sleeve-level rows for the same instrument netted rather than spread against
      each other?
- [ ] Are all standalone margins non-negative and finite, with no NaN from a broken feed?
- [ ] Are correlations within [-1, 1] and credit rates within [0, 1]? (Both now raise, but
      confirm the upstream feed rather than relying on the guard.)

## Credit rates

- [ ] Has the broker's or exchange's **published** offset percentage been used wherever
      one exists, in preference to a correlation?
- [ ] Is the correlation matrix dated, with a documented estimation window?
- [ ] Is `correlation_haircut` a deliberate choice? It is this module's conservatism knob,
      **not** a regulatory standard — no primary source prescribes a percentage.
- [ ] Have tail-correlation breakdowns (March 2020, and similar dislocations) informed the
      haircut rather than being assumed away?

## Reading the output

- [ ] Has the `offsets` audit trail been reviewed — how much of the total came from
      `correlation` rather than `published` rates?
- [ ] Is `min_cross_margin_fraction` set to model the floors real engines impose ($0.375 x
      multiplier per contract under TIMS, the SPAN short option minimum)?
- [ ] If `floor_applied` is true, is it understood that the floor and not the model
      produced the number?
- [ ] Is the reported CER within the model's structural ceiling of 2.0, and is no plan
      relying on a 5x or 10x "saving"?

## Before allocating the freed capital

- [ ] Has the estimate been **reconciled against the broker's actual margin figure** on a
      live book?
- [ ] Is released collateral being routed to **uncorrelated** exposure rather than more of
      the same risk that produced the offset?
- [ ] Is there a margin-utilisation circuit breaker in place for the case where the
      correlation breaks and the offset evaporates intraday?
