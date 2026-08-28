---
name: physical-vs-cash-settlement-handling
description: >-
  Expiry settlement engine for derivatives: classifies cash-settled versus
  physically delivered contracts, resolves the delivery obligation by position
  direction (a long pays an invoice, a short must produce the deliverable),
  selects the deadline that actually binds that side — first notice day for a
  long, last trading day for a short — prices the delivery invoice at the
  settlement price or the strike, and separates the final variation cashflow
  from lifetime PnL.
domain: Derivatives Settlement & Post-Trade Operations
subdomain: Expiration & Physical Delivery Risk Management
tags: ["settlement", "physical-delivery", "cash-settlement", "first-notice-date", "last-trading-day", "futures-expiration", "derivatives"]
brokers_frameworks: ["CME Group Delivery Rules", "COMEX / NYMEX Contract Specifications", "IBKR Futures Close-Out Policy", "Python Dataclasses"]
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill on a position in an expiring contract, to answer three
questions: **what does this contract oblige the account to do, by when, and can
the account do it?**

The obligations are asymmetric in the position's direction, and that asymmetry
is the whole point of the skill:

| | Long | Short |
|---|---|---|
| Obligation | Take delivery and **pay the invoice** | **Produce the deliverable** (warehouse receipt / shipping certificate) |
| Binding constraint | Cash + somewhere to receive 1,000 barrels | Possession of the registered deliverable |
| Binding deadline | **First notice day** — from which delivery notices are assigned | **Last trading day** — after which it is delivering |

CME Clearing's delivery process is initiated by the short: it registers a
shipping certificate, and CME Clearing ranks open long positions by age and
assigns the oldest long. The long then "makes payment to CME Clearing, and CME
Clearing simultaneously transfers the payment from the long to the short
position holder and transfers the shipping certificate from the short to the
long."

## When NOT to Use

- **As a calendar.** The day counts are business-day numbers you supply. The
  engine does not know exchange holidays, the delivery calendar, or your
  broker's close-out deadline — and the **broker's** deadline, not the
  exchange's first notice day, is what binds a customer account.
- **As reference data.** `settlement_type`, `multiplier` and `strike_price` are
  contract terms you must source. Getting `settlement_type` wrong is the one
  input error this engine cannot detect.
- **As an exercise or assignment model.** Whether an expiring option is
  exercised at all — including a holder's contrary exercise advice against the
  OCC exercise-by-exception default — belongs to
  `options-pin-risk-management-at-expiry`. This skill prices and funds the
  delivery that an exercise *produces*.
- **As a full delivery cost model.** Invoice amounts here are the deliverable's
  principal only. Grade and location differentials, storage, demurrage and
  load-out charges are contract-specific and are not modelled.
- **As a roll optimiser.** It says *close or roll by this date*, not which
  contract month to roll into or how to execute the spread — see
  `futures-contract-roll-automation`.

## Prerequisites

- Contract terms: `symbol`, `settlement_type` (`CASH`/`PHYSICAL`),
  `instrument_kind` (`FUTURE`/`OPTION`), `multiplier` in **deliverable units
  per contract** (1,000 barrels for CL; 100 shares for a standard US equity
  option), `strike_price` for a physically settled option.
- **Both delivery clocks, as separate business-day counts**:
  `business_days_to_first_notice` and `business_days_to_last_trading_day`. They
  are separate fields because they are different dates in a contract-specific
  order — for COMEX Gold, first notice day is the last business day of the
  month *prior* to the delivery month and last trading day is the third-last
  business day *of* it, so first notice day falls weeks earlier; for NYMEX WTI,
  trading terminates before the delivery month opens at all. Negative values
  mean the date has passed. Only the clock binding your side is required.
- Position state: `position_qty` (signed), `entry_price`,
  `account_cash_balance`, `has_delivery_facility`,
  `deliverable_units_available` (short side), `prior_settlement_price`.
- Close-out policy: `SettlementPolicyConfig`. Defaults to 2 business days for
  each side, mirroring IBKR's published futures close-out policy. **That is a
  broker house rule, not a regulatory constant** — set your own broker's.

## Workflow

1. **Classify the contract.** `CASH` or `PHYSICAL`, `FUTURE` or `OPTION`.
   - **Decision point — an unrecognised code is not a default.** A vendor
     string the engine does not understand raises `ValueError` rather than
     falling through to either branch. Treating an unmapped code as physical
     invents a delivery obligation; treating it as cash hides one.
2. **Compute the two cashflows, and report both.**
   - $\text{FinalVariation} = Q \cdot M \cdot (P_{\text{settle}} - P_{\text{prior settle}})$
   - $\text{LifetimePnL} = Q \cdot M \cdot (P_{\text{settle}} - P_{\text{entry}})$
   - **Decision point — these are not the same number.** A futures position is
     marked to market daily, so the money that moves *at* expiry is the final
     variation margin; the move against entry is the lifetime result, most of
     which has already been paid or collected. Without
     `prior_settlement_price` the engine reports lifetime PnL and flags
     `NO_PRIOR_SETTLEMENT_PRICE_LIFETIME_PNL_ONLY` rather than silently
     conflating them.
3. **Cash-settled branch.** No deliverable — but not no risk.
   - The final settlement price is frequently a value that never printed in the
     regular session: CME's E-mini S&P 500 settles to a Special Opening
     Quotation built from the component stocks' opening prices on the third
     Friday, regardless of when those stocks open. Until the exchange publishes
     it, the report carries `PROVISIONAL_SETTLEMENT_PRICE`.
   - An adverse settlement is a funding event: if the debit exceeds the balance,
     status is `CASH_SETTLEMENT_FUNDING_SHORTFALL`.
4. **Physical branch — resolve by direction.**
   - **Long** $\implies$ `TAKE_DELIVERY_AND_PAY`, clock = first notice day,
     $V_{\text{invoice}} = |Q| \cdot M \cdot P_{\text{delivery}}$, provisioned
     iff `has_delivery_facility` **and** cash $\ge V_{\text{invoice}}$.
   - **Short** $\implies$ `MAKE_DELIVERY_OF_UNDERLYING`, clock = last trading
     day, units required $= |Q| \cdot M$, provisioned iff
     `has_delivery_facility` **and** `deliverable_units_available` $\ge$ units
     required. The invoice is $0$: a short is *paid*, and cash does not
     discharge a delivery obligation.
   - **Decision point — $P_{\text{delivery}}$ is not always the settlement
     price.** A futures delivery is invoiced off the final settlement price; an
     exercised physically settled **option** delivers against the **strike**.
     Pricing an option's delivery at spot understates the funding requirement
     by exactly the intrinsic value — it is most wrong when the option is
     deepest in the money.
   - **Decision point — a missing clock is not a pass.** If the day count
     binding this side is `None`, the engine raises rather than reporting a
     delivery obligation as compliant on an unknown clock.
5. **Grade against the close-out buffer.**
   - Past the deadline $\implies$ `PHYSICAL_DELIVERY_DEADLINE_PASSED`,
     `ESCALATE_TO_DELIVERY_OPERATIONS`. **A roll no longer avoids the
     obligation** — for a long, notices may already have been assigned.
   - Unprovisioned and inside the buffer $\implies$
     `PHYSICAL_DELIVERY_RISK_BREACH`, `CLOSE_OR_ROLL_BEFORE_DEADLINE`.
   - Unprovisioned with time remaining $\implies$
     `PHYSICAL_DELIVERY_NOT_PROVISIONED` — same action, raised early rather
     than at T-2.
   - A **flat** position reports `FLAT_NO_OBLIGATION` regardless of contract
     terms, never a breach against a zero-unit obligation.
6. **Aggregate across the book** (`audit_portfolio_settlement`). Per-position
   funding checks are individually satisfiable and collectively false: three
   long deliveries each pass `cash >= invoice` against the same balance while
   their sum overdraws it.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Testing a short position for cash.** A short discharges its obligation by
  delivering a registered warehouse receipt or shipping certificate. A
  cash-rich short with no deliverable is in breach; a zero-cash short holding
  the certificate is fine. A `cash >= notional` test applied to both sides gets
  both of those backwards.
- **Treating first notice day as everyone's deadline.** It is the date from
  which a *long* can be assigned. A short is bound by last trading day, which
  for COMEX Gold is weeks *later* and for NYMEX WTI is *earlier* than the
  delivery period. One day count cannot express both.
- **Assuming first notice day follows last trading day.** For COMEX Gold it
  precedes it, so a long can be assigned a delivery notice while the contract
  is still actively trading — the position being liquid is not evidence that
  delivery is still avoidable.
- **Rolling after the deadline.** Selling the front month once notices are
  assigned leaves the delivery obligation in place and adds a new short. Past
  the deadline the problem belongs to delivery operations, not to the trading
  desk.
- **Funding an option exercise at spot.** An exercised call delivers shares
  against the strike. A 10-lot \$100 call settling at \$130 needs \$100,000,
  not the \$130,000 a spot-priced notional suggests — and the resulting stock
  trade settles **T+1** (SEC Rule 15c6-1 as amended, effective 28 May 2024), so
  the cash is due the next business day.
- **Reading "cash-settled" as "no expiry risk".** The settlement value is
  determined by a special procedure, not by the last regular-session trade. A
  position marked at the 4:00 p.m. print and settled at the next morning's
  Special Opening Quotation is marked at the wrong number, and a large adverse
  settlement is a real margin call.
- **Reporting lifetime PnL as the settlement cashflow.** Daily mark-to-market
  means most of it has already moved. Sizing an expiry-day funding need off the
  move against entry price overstates it for a winner and understates it for a
  loser.
- **Treating any non-`CASH` string as physical.** A typo or an unmapped vendor
  code becomes a fabricated delivery obligation — or, worse in the other
  direction, silences a real one.
- **Checking funding one position at a time.** Three CL deliveries at \$350,000
  each pass individually against a \$400,000 balance and need \$1,050,000
  together.
- **Feeding gross legs instead of the net position.** A long and a short in the
  same contract and the same account net to a flat position and no delivery
  obligation. Passed as two rows, they report a funded delivery *and* a
  deliverable requirement, and the aggregate invoice double-counts. Net by
  account and contract before calling.
- **Rejecting a negative settlement price.** NYMEX WTI settled at **-\$37.63**
  on 20 April 2020, driven by longs who could not take delivery at Cushing —
  the exact failure this skill screens for. Prices here must be finite but may
  be negative or zero; a validator that demands positive prices refuses the one
  event it most needs to model. Note what a negative invoice means: the long is
  *paid* to take delivery, so the cash test passes trivially and
  `has_delivery_facility` becomes the only thing standing between the account
  and the barrels.
- **Trusting the 2-day default buffer.** It mirrors one broker's published
  policy. Deadlines differ by broker and by product, and brokers may liquidate
  without further notification once they pass. Look yours up.

## Verification

- Instantiate `PhysicalVsCashSettlementHandlingEngine()`.
- **Cash**: 10 ES (multiplier 50) entered at 5000, prior settle 5040, settling
  at 5050 $\implies$ `lifetime_pnl == 25_000.0` but
  `final_variation_cashflow == 5_000.0`, `delivery_obligation == "NONE"`,
  `binding_deadline == "NONE"`. Short 10 from a 4900 prior settle into 5050
  with \$50,000 cash $\implies$ `CASH_SETTLEMENT_FUNDING_SHORTFALL` on a
  \$75,000 debit, flagged `INSUFFICIENT_CASH_FOR_SETTLEMENT_DEBIT` (**not**
  the delivery-invoice flag — a cash-settled contract has no invoice).
- **Long physical**: 5 CL (multiplier 1,000) at \$70 $\implies$
  `delivery_invoice_usd == 350_000.0`, `binding_deadline ==
  "FIRST_NOTICE_DATE"`, `deliverable_units_required == 0.0`.
- **Short physical**: the same 5 CL short with \$0 cash but 5,000 barrels
  available $\implies$ `MAKE_DELIVERY_OF_UNDERLYING`, `delivery_invoice_usd ==
  0.0`, `binding_deadline == "LAST_TRADING_DAY"`, **not** a breach. Flip it to
  \$10m cash and 0 barrels $\implies$ `PHYSICAL_DELIVERY_RISK_BREACH`.
- **Split clocks**: first notice in 1 day, last trading day in 20. The long
  reports `PHYSICAL_DELIVERY_RISK_BREACH`; the short, same contract, same
  provisioning, reports `PHYSICAL_DELIVERY_NOT_PROVISIONED`.
- **Boundary**: with the default 2-day buffer, first notice in 2 days breaches
  and in 3 days does not; at -1 days, `PHYSICAL_DELIVERY_DEADLINE_PASSED` and
  `ESCALATE_TO_DELIVERY_OPERATIONS`.
- **Option**: 10 physically settled \$100 calls (multiplier 100) settling at
  \$130 against \$110,000 cash $\implies$ `delivery_price_basis ==
  "STRIKE_PRICE"`, `delivery_invoice_usd == 100_000.0`, provisioned. A
  spot-priced invoice would have been \$130,000 and would have failed.
- **Flat**: `position_qty=0` on a physical contract with no delivery facility
  and first notice in 1 day $\implies$ `FLAT_NO_OBLIGATION`, `NO_ACTION`.
- **Negative price**: long 1 CL entered at \$20 settling at **-\$37.63**
  $\implies$ accepted, `lifetime_pnl == -57_630.0`,
  `delivery_invoice_usd == -37_630.0`, no cash warning — but
  `NO_DELIVERY_FACILITY` still yields `PHYSICAL_DELIVERY_RISK_BREACH`.
- **Negative checks**: unrecognised `settlement_type`, a physically settled
  option with no `strike_price`, a missing binding day count, fractional day
  counts, non-finite (`NaN`/`±inf`) prices, non-positive multiplier or strike,
  and negative close-out buffers must each raise `ValueError`. Negative and
  zero *prices* must **not**.
- **Portfolio**: three long CL deliveries against a \$400,000 balance $\implies$
  every position `PHYSICAL_DELIVERY_PROVISIONED` but
  `aggregate_delivery_invoice_usd == 1_050_000.0` and
  `AGGREGATE_DELIVERY_INVOICE_EXCEEDS_CASH`.
- Run `python -m unittest discover -s skills/physical-vs-cash-settlement-handling/scripts`.

## Related Skills

- `options-pin-risk-management-at-expiry`
- `futures-contract-roll-automation`
- `early-exercise-assignment-risk-management`
- `american-vs-european-style-option-exercise-handling`
- `margin-utilization-circuit-breaker`
