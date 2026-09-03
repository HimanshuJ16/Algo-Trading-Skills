---
name: options-pin-risk-management-at-expiry
description: >-
  Use on expiration day while the option can still be traded, measuring strike proximity
  and signed moneyness against the exercise-by-exception threshold so a writer knows
  what position they may hold on Monday.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: multi-asset-derivatives
  tags: options-pin-risk, expiry-operations, contrary-exercise, assignment-risk, 0dte-risk, derivatives-risk
  brokers_frameworks: "OCC Rule 805 Exercise-by-Exception; FINRA Rule 2360; Cboe Options; Python Dataclasses"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill on the expiration date of an options position, while the option
can still be traded. Pin risk is the writer's problem of not knowing what
position they will hold when the market reopens, and it exists because three
deadlines are separated in time:

| Deadline | Time | Why it matters |
|---|---|---|
| Trading in the option ceases | 4:00 p.m. ET for standard US equity options | Last moment the position can be closed in the market |
| Holder's final exercise decision | 5:30 p.m. ET (FINRA Rule 2360(b)(23)(A)) | The counterparty is still deciding after you can no longer act |
| Contract expiration time | 11:59 p.m. ET (OCC By-Laws) | Not the operative deadline for anything you can do |

Between 4:00 and 5:30 the underlying keeps trading after hours while the writer
cannot. A holder who watches the stock cross the strike can file a Contrary
Exercise Advice either to **cancel** the automatic exercise of an in-the-money
option or to **exercise** one that would otherwise be abandoned. That is the
whole of pin risk, and it cuts both ways.

## When NOT to Use

- **On cash-settled contracts, as an assignment screen.** SPX, NDX and similar
  settle in cash at the exercise-settlement value. There is no share position to
  be surprised by. The engine reports them with a zero share delta and status
  `CASH_SETTLED_NO_ASSIGNMENT_AMBIGUITY`; their real expiry exposure is
  settlement-value determination — AM-settled contracts settle off the *next*
  morning's opening prints — which belongs to
  `physical-vs-cash-settlement-handling` and
  `options-chain-expiry-cycle-conventions-by-exchange`.
- **As a probability of assignment.** Whether *your* account is assigned depends
  on OCC allocation to your clearing member and then that member's FIFO / random
  / equally-random allocation across its short open interest (FINRA Rule
  2360(b)(23)(C)). None of that is an input here. The output is a directive, not
  a likelihood.
- **Before expiration day, as an early-exercise screen.** Ex-dividend capture and
  carry-driven put exercise are a different problem on a different clock — see
  `early-exercise-assignment-risk-management`.
- **As a clock, session calendar, or broker-cutoff scheduler.**
  `hours_to_trading_close` is a number you supply. The engine does not know the
  venue's session, the holiday calendar, or your broker's exercise cutoff — and
  the broker's cutoff, not the 5:30 p.m. ET regulatory deadline, is what actually
  binds a customer.
- **As reference data.** `contract_multiplier` and `settlement_type` are contract
  terms you must source. A corporate action changes the deliverable: OCC
  publishes an adjustment memo, and an adjusted contract may deliver a non-round
  share count, cash in lieu, or a basket rather than 100 shares.

## Prerequisites

- Per position: `symbol`, `underlying_symbol`, `strike`, `option_type`
  (`CALL`/`PUT`), `position_qty` (signed, non-zero), `spot_price`,
  `hours_to_trading_close`, `contract_multiplier` (100 for standard US equity
  options), `settlement_type` (`PHYSICAL`/`CASH`).
- **`spot_price` should be the official closing price** once it is known. OCC
  applies the exercise-by-exception test to the close, so any moneyness verdict
  from an intraday last price is provisional. Set `price_is_official_close=True`
  when it is final; until then the report carries a
  `PROVISIONAL_PRICE_NOT_OFFICIAL_CLOSE` flag.
- **`hours_to_trading_close`, not hours to expiry.** Expiry is 11:59 p.m. ET,
  about eight hours after the equity option close. A cutoff measured against
  expiry reports hours remaining on a position that can no longer be traded out
  of. Negative values are accepted and mean trading has already closed.
- Pin policy: `pin_distance_pct` (default 1.0%), `pin_cutoff_hours` (default
  2.0), optionally `pin_distance_abs_usd`.

## Workflow

1. **Pin Zone Detection**:
   - $\text{PinDistance}_{\%} = \frac{|S - K|}{S} \times 100\%$; the position is
     in the pin zone when the distance is inside the band **and**
     $\text{HoursToTradingClose} \le \text{PinCutoffHours}$.
   - **Decision point — a percentage band alone scales the wrong way.** 1% of a
     \$5 underlying is \$0.05, too tight to catch any realistic after-hours move;
     1% of a \$600 underlying is \$6.00, wide enough to flag positions that are
     not remotely pinned. Set `pin_distance_abs_usd` to add an absolute band,
     applied with OR. It defaults to `None` so no threshold is invented for you.
2. **Signed Moneyness vs the Exercise-by-Exception Threshold**:
   - $\text{Moneyness}_{\text{call}} = S - K$, $\text{Moneyness}_{\text{put}} = K - S$.
   - In the money by $\ge \$0.01$ per share at the close $\implies$ default
     outcome `AUTO_EXERCISED` under OCC Rule 805; otherwise `EXPIRES_WORTHLESS`.
   - **Decision point — compare on a rounded value.** Binary floating point
     renders an exact one-cent difference as slightly *less* than \$0.01 for most
     strikes ($1234.01 - 1234.00 = 0.009999999999990905$), so a raw comparison
     misclassifies the exact boundary the rule turns on.
3. **Direction-of-Surprise Resolution** — the band is not a symmetric condition:
   - **Short, ITM**: assignment is the default; a holder's contrary advice can
     cancel it, so a writer who pre-hedges by buying stock can end up holding the
     hedge and no assignment $\implies$ `CLOSE_POSITION_BEFORE_EXPIRY`.
   - **Short, OTM**: abandonment is the default, but a holder can still file to
     exercise, leaving an unhedged share position $\implies$
     `CLOSE_POSITION_BEFORE_EXPIRY`. **An OTM short is not safe.**
   - **Long, ITM**: will be exercised into shares unless a do-not-exercise
     instruction is filed. The exposure is unwanted or unfunded delivery
     $\implies$ `CLOSE_POSITION_BEFORE_EXPIRY`, with `dne_eligible` set and the
     intrinsic that a DNE would forfeit quantified.
   - **Long, OTM**: expires worthless by default and the holder elects. There is
     nothing to resolve $\implies$ `NO_ACTION_HOLDER_ELECTS`. A DNE here is a
     no-op on a contract already abandoned by default.
   - **Trading already closed** ($\text{hours} < 0$) $\implies$
     `POST_CLOSE_EXPOSURE_REVIEW`, never a close order that cannot be executed.
4. **Exposure Quantification** — two different numbers, both reported:
   - $\text{ShareDelta} = \pm\, |Q| \times \text{Multiplier}$, signed by delivery
     direction (short call and long put deliver shares away; short put and long
     call receive them).
   - $\text{ShareNotional} = |\text{ShareDelta}| \times S$ — market value of the
     shares that move.
   - $\text{AssignmentCash} = |\text{ShareDelta}| \times K$ — cash actually
     exchanged. **Use this one to size a margin call**: an assigned short put is
     funded at the strike, and spot understates it exactly when the put is ITM.
5. **Portfolio Netting** (`audit_portfolio_pin_risk`): per underlying, report the
   range $[\min, \max]$ of share positions the book can wake up to, and the short
   contracts with no reliably-exercising long leg against them.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Treating an out-of-the-money short as safe.** A Contrary Exercise Advice can
  be filed to exercise an option that exercise-by-exception would abandon. A
  short that closed \$0.05 out of the money is a candidate for assignment, not a
  position that has expired.
- **Pre-hedging an in-the-money short and assuming assignment.** The advice runs
  the other way too: the holder can cancel the automatic exercise. Buying the
  stock to cover an "certain" assignment converts pin risk into an outright long
  position over the weekend.
- **Unwinding one leg of a defined-risk spread.** Closing the long leg while the
  short leg is pinned turns a defined-risk spread into a naked short. So does
  *keeping* a long leg that is far enough out of the money that it will not be
  exercised: it delivers nothing, so it hedges nothing. Only a long leg in the
  money beyond the \$0.01 threshold covers the short's delivery — this is what
  `audit_portfolio_pin_risk` checks, and a per-position audit structurally
  cannot see it.
- **Measuring the cutoff against expiry rather than the close.** OCC expiration
  time is 11:59 p.m. ET. A cutoff measured against it happily reports "8 hours
  remaining" on a position that stopped trading at 4:00 p.m. and can no longer
  be closed.
- **Assuming the regulatory deadline is your deadline.** 5:30 p.m. ET is the
  outer limit under FINRA Rule 2360(b)(23)(A); members may and routinely do set
  earlier cutoffs. Your broker's published cutoff is the one that binds you —
  look it up rather than assuming a number.
- **Reporting assignment exposure at spot.** Delivery moves shares worth
  $|\text{ShareDelta}| \times S$, but the cash that must be funded is
  $|\text{ShareDelta}| \times K$. Sizing a short-put assignment at spot
  understates the funding requirement precisely when the put is in the money.
- **Hard-coding a 100-share multiplier.** Mini contracts and options adjusted for
  a split, spin-off or merger deliver something other than 100 shares. Read the
  deliverable from reference data or the OCC adjustment memo.
- **Running the assignment logic over cash-settled index options.** There is no
  share delivery to be uncertain about; reporting a six-figure "assigned share
  notional" for an SPX position is a number the contract cannot produce.
- **Scoring on a provisional price.** The exercise-by-exception test is applied
  to the official close. A verdict computed at 3:55 p.m. is a forecast.

## Verification

- Instantiate `OptionsPinRiskManagementEngine()`. Audit a short 10-lot \$100 call
  at \$100.50 spot with 1.0h to the close: verify `pin_distance_pct == 0.497512`,
  `status == "HIGH_PIN_RISK_ACTION_REQUIRED"`, `recommended_action ==
  "CLOSE_POSITION_BEFORE_EXPIRY"`, `assigned_share_delta == -1000.0`,
  `assigned_share_notional_usd == 100_500.0` and `assignment_cash_usd ==
  100_000.0`.
- Flip it to a short **put** at \$99.60 spot: verify `assigned_share_delta ==
  +1000.0` and that `assignment_cash_usd` (\$100,000, at the strike) exceeds
  `assigned_share_notional_usd` (\$99,600, at spot).
- Boundary: at strikes 45, 3 and 1234 with spot exactly \$0.01 higher, verify
  `is_itm_at_ex_by_ex_threshold` is true for all three — raw float subtraction
  returns false for each.
- Long 5-lot \$100 call at \$100.02: verify `dne_eligible` is true,
  `intrinsic_forfeited_if_dne_usd == 10.0` against `assignment_cash_usd ==
  50_000.0`. At \$99.98: verify `status == "PIN_ZONE_HOLDER_ELECTION"` and
  `is_pin_risk_high` is false.
- Spread: short 10 × \$100 calls pinned at \$100.20 with long 10 × \$105 calls
  $\implies$ `UNPAIRED_SHORT_PIN_EXPOSURE`, `unpaired_short_shares ==
  {"CALL": 1000.0}`, range $[-1000, 0]$ shares. Replace the long leg with \$95
  calls $\implies$ `PINNED_SHORT_DELIVERY_COVERED`, range $[0, +1000]$.
- Netting is in shares: pair the same pinned short against 10 long \$95 calls
  with `contract_multiplier=10` and verify `unpaired_short_shares == {"CALL":
  900.0}` — matching contract counts alone would call it covered. Verify too
  that a long *call* does not cover a short *put*, and that a long leg which is
  itself inside the pin band is not counted as reliable delivery.
- Negative checks: `NaN`/`inf` spot, `NaN` hours, zero quantity, non-positive
  strike or multiplier, and unrecognised `option_type`/`settlement_type` must
  each raise `ValueError`.
- Run `python -m unittest discover -s skills/options-pin-risk-management-at-expiry/scripts`.

## Related Skills

- `early-exercise-assignment-risk-management`
- `physical-vs-cash-settlement-handling`
- `american-vs-european-style-option-exercise-handling`
- `options-chain-expiry-cycle-conventions-by-exchange`
- `options-greeks-real-time-portfolio-aggregation`
