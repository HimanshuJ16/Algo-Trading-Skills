# Pre-Flight Checklist — Physical vs Cash Settlement at Expiry

## Contract terms
- [ ] Is `settlement_type` sourced from the **exchange contract specification**, not inferred from a symbol convention or a vendor convenience field? It is the one input nothing downstream can cross-check.
- [ ] Is `multiplier` expressed in **deliverable units per contract** (1,000 barrels for CL, 100 troy ounces for GC, 100 shares for a standard equity option) rather than in dollars per point?
- [ ] For any option: has an OCC adjustment memo changed the deliverable? A contract adjusted for a split, spin-off or merger does not deliver 100 shares.
- [ ] For a physically settled option, is `strike_price` populated? The exercise is funded at the strike, not at spot.

## Clocks
- [ ] Are **both** `business_days_to_first_notice` and `business_days_to_last_trading_day` sourced, and is their **order** understood for this specific contract? First notice day precedes last trading day for COMEX Gold; last trading day precedes the delivery period entirely for NYMEX WTI.
- [ ] Is the clock read for the side actually held — first notice day for a long, last trading day for a short — rather than one date applied to both?
- [ ] Is **your broker's** close-out deadline known and written down? The 2-business-day defaults mirror one broker's published policy. Deadlines differ by broker and by product, and a broker may liquidate without further notification once its deadline passes.
- [ ] Are the counts **business** days, on the contract's own holiday calendar? The engine does not know the calendar.

## Cash-settled positions
- [ ] Is the settlement price the exchange's published final settlement value, with `settlement_price_is_final=True`? A CME equity index contract settles to a Special Opening Quotation built from component opening prints — a level that never traded in the session.
- [ ] Is `PROVISIONAL_SETTLEMENT_PRICE` cleared before any figure is treated as final?
- [ ] Has the funding effect of an **adverse** settlement been checked? "Cash-settled" means no warehouse, not no margin call.

## Physical long positions
- [ ] Is the delivery invoice funded — and funded at the right price basis? Check `delivery_price_basis` reads `STRIKE_PRICE` for an option and `FINAL_SETTLEMENT_PRICE` for a future.
- [ ] Is there somewhere to actually receive the deliverable, or is `has_delivery_facility` set optimistically?
- [ ] If the settlement price is **negative**, is it understood that the cash test now passes trivially and `has_delivery_facility` is the only thing left between the account and the physical commodity? This is how April 2020 happened.
- [ ] Are grade/location differentials, storage, demurrage and load-out charges budgeted separately? The engine's invoice is principal only — a floor, not a total.
- [ ] For an exercised equity option, is the cash available on **T+1** (SEC Rule 15c6-1 as amended, effective 28 May 2024)?

## Physical short positions
- [ ] Is `deliverable_units_available` populated with **registered** warehouse receipts or shipping certificates, in the same units as `multiplier` — not in contracts, and not with a cash figure?
- [ ] Has anyone confirmed the short is not being screened on its cash balance? A zero-cash short holding the certificate is fine; a cash-rich short with no deliverable is in breach.

## Screening
- [ ] Has the **whole expiring book** been run, not only the positions someone remembered?
- [ ] Has `audit_portfolio_settlement` been run against the **book-level** cash balance? Three \$350,000 invoices each pass individually against \$400,000 and need \$1,050,000 together.
- [ ] Are `warnings` reviewed on every position, not just `status`?
- [ ] Are positions **netted by account and contract** before being passed in? Offsetting legs in the same contract net to no delivery obligation; passed gross they produce a phantom breach and a double-counted aggregate invoice.
- [ ] Are flat rows reporting `FLAT_NO_OBLIGATION` rather than being filtered out by hand upstream?

## Action
- [ ] Is every `PHYSICAL_DELIVERY_RISK_BREACH` and `PHYSICAL_DELIVERY_NOT_PROVISIONED` closed or rolled **before** its own binding deadline?
- [ ] Is anything at `PHYSICAL_DELIVERY_DEADLINE_PASSED` routed to **delivery operations** rather than given a close order? Selling the front month after notices are assigned leaves the delivery in place and adds a new short.
- [ ] Is the full report persisted per run, so that after a delivery notice arrives it is possible to say which run last saw the position as compliant and on what clock?
