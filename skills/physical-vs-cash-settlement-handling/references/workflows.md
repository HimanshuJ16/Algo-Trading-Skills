# Workflows — physical-vs-cash-settlement-handling

## 0. Source the contract terms before anything else

`settlement_type` is the one input the engine cannot sanity-check against
anything else. Everything downstream — whether a delivery obligation exists at
all — turns on it. Source it from the exchange's contract specification, not
from a symbol convention or a vendor's convenience field.

Get `multiplier` in **deliverable units per contract**, not in dollars per
point. CL is 1,000 barrels. GC is 100 troy ounces. A standard US equity option
is 100 shares — but an option adjusted for a split, spin-off or merger is not,
and OCC publishes an adjustment memo saying what it became.

Get **both** business-day counts where you have them. They are different dates
and the engine picks between them by direction.

## 1. Classify

```
settlement_type ∈ {CASH, PHYSICAL}   ×   instrument_kind ∈ {FUTURE, OPTION}
```

An unrecognised string in either raises `ValueError`. This is deliberate: a
vendor code the engine does not understand is a reference-data defect. Silently
resolving it to `PHYSICAL` invents a delivery obligation and triggers a
liquidation that was never needed; silently resolving it to `CASH` suppresses a
real one and lets a delivery notice arrive unannounced.

## 2. Compute both cashflows

$$\text{FinalVariation} = Q \cdot M \cdot (P_{\text{settle}} - P_{\text{prior settle}})$$
$$\text{LifetimePnL} = Q \cdot M \cdot (P_{\text{settle}} - P_{\text{entry}})$$

A futures position is marked to market every day. The money that moves *at*
expiry is the final variation margin against the prior settlement price. The
move against the entry price is the position's lifetime result, most of which
has already been paid or collected across the position's life.

Which one you need depends on the question:

- **"How much cash do I need on settlement day?"** → final variation.
- **"What did this trade make?"** → lifetime PnL.

They coincide only when the entry price *is* the prior settlement price. Absent
`prior_settlement_price` the engine reports lifetime PnL in both fields and
raises `NO_PRIOR_SETTLEMENT_PRICE_LIFETIME_PNL_ONLY` rather than pretending it
knows the daily move.

## 3. Cash-settled branch

There is no deliverable. There are still two things to get right.

**The price.** The final settlement value is usually determined by a special
procedure rather than by the last regular-session trade. CME equity index
futures settle to a Special Opening Quotation built from the component stocks'
opening prices on the third Friday, regardless of when each stock opens; a
stock that does not open contributes its last sale. So the settlement value is
a number that never printed as an index level in the session you were watching.
Mark the price `settlement_price_is_final=True` only once the exchange has
published it. Until then the report carries `PROVISIONAL_SETTLEMENT_PRICE` and
every figure derived from it is a forecast.

**The funding.** An adverse settlement is a debit. If it exceeds the account
balance, the status is `CASH_SETTLEMENT_FUNDING_SHORTFALL` and the action is
`FUND_ACCOUNT_BEFORE_SETTLEMENT`. "Cash-settled" means no warehouse, not no
margin call.

## 4. Physical branch — resolve by direction

### 4a. Long: `TAKE_DELIVERY_AND_PAY`

- **Clock**: `business_days_to_first_notice`. First notice day is the date from
  which CME Clearing can assign you a delivery notice. It is not necessarily
  near last trading day — for COMEX Gold it precedes it by weeks, so the
  contract still trading normally is not evidence that delivery is still
  avoidable.
- **Invoice**: $V = |Q| \cdot M \cdot P_{\text{delivery}}$, where
  $P_{\text{delivery}}$ is the final settlement price for a future and the
  **strike** for an exercised physically settled option. Pricing an option
  delivery at spot understates the funding requirement by exactly the intrinsic
  value — it is most wrong precisely when the option is deepest in the money.
  The engine reports which basis it used in `delivery_price_basis`.
- **Provisioned iff** `has_delivery_facility` **and**
  `account_cash_balance >= invoice`.

### 4b. Short: `MAKE_DELIVERY_OF_UNDERLYING`

- **Clock**: `business_days_to_last_trading_day`. A short is not exposed to
  being assigned — it is the side that *initiates* delivery. Its deadline is
  the last date it can trade out.
- **Invoice**: $0$. The short is paid.
- **Units required**: $|Q| \cdot M$, in the deliverable's own units.
- **Provisioned iff** `has_delivery_facility` **and**
  `deliverable_units_available >= units_required`.

A cash balance does not discharge a delivery obligation. What discharges it is
a registered warehouse receipt or shipping certificate. A cash-rich short with
no deliverable is in breach; a zero-cash short holding the certificate is not.
Applying a `cash >= notional` test to both sides gets both cases backwards, and
that is the single most consequential thing this workflow changes.

### 4c. Missing clock

If the day count binding this side is `None`, the engine raises. The
alternative — treating an unknown deadline as a distant one — reports a
delivery obligation as compliant on a clock nobody has read. The *non*-binding
clock may be `None` without complaint.

## 5. Grade against the close-out buffer

| Condition | Status | Action |
|---|---|---|
| `days < 0`, unprovisioned | `PHYSICAL_DELIVERY_DEADLINE_PASSED` | `ESCALATE_TO_DELIVERY_OPERATIONS` |
| `days < 0`, provisioned | `PHYSICAL_DELIVERY_PROVISIONED` | `ESCALATE_TO_DELIVERY_OPERATIONS` |
| `days <= buffer`, unprovisioned | `PHYSICAL_DELIVERY_RISK_BREACH` | `CLOSE_OR_ROLL_BEFORE_DEADLINE` |
| `days > buffer`, unprovisioned | `PHYSICAL_DELIVERY_NOT_PROVISIONED` | `CLOSE_OR_ROLL_BEFORE_DEADLINE` |
| provisioned, before deadline | `PHYSICAL_DELIVERY_PROVISIONED` | `MONITOR` |
| `position_qty == 0` | `FLAT_NO_OBLIGATION` | `NO_ACTION` |

Two distinctions matter here.

**Past the deadline is not a bigger version of "close it".** Once notices can
be assigned, selling the front month does not remove the obligation — it leaves
the delivery in place and opens a new short. That is why the past-deadline path
escalates instead of emitting a close directive, and why a *provisioned*
position past the deadline also escalates: it is now delivery operations' work,
not the desk's.

**`NOT_PROVISIONED` is not a softer breach.** The action is identical. The only
difference is that there is still time to execute it in an orderly way rather
than at the buffer's edge against whatever liquidity is left in an expiring
contract.

## 6. Aggregate across the book

Run the whole expiring book through `audit_portfolio_settlement`. Per-position
funding checks are individually satisfiable and collectively false: three long
CL deliveries of \$350,000 each pass `cash >= invoice` against a \$400,000
balance, and together need \$1,050,000. The aggregate check raises
`AGGREGATE_DELIVERY_INVOICE_EXCEEDS_CASH` when the sum of invoices exceeds the
book-level balance.

## 7. Persist the report

Keep `audit_notes`, `warnings`, `binding_deadline` and
`business_days_to_deadline` per run. When a delivery notice does arrive, the
question asked afterwards is which run last saw the position as compliant and
on what clock.
