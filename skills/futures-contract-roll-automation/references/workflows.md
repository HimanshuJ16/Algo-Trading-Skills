# Workflows for Futures Contract Roll Automation

## 1. Contract-state assembly

- Build a `FuturesContractState` for the held expiration and the target expiration.
- Express **every** day count in business days, from the same reference session.
- For physically delivered products set `is_physically_delivered=True` and supply
  `days_to_first_notice`. The engine raises rather than assuming the contract is
  cash settled — for many products First Notice Day precedes Last Trading Day.
- Set `contract_multiplier` to the currency value of one full price point (50.0 for
  ES, 5000.0 for CBOT grains). It is used only to express roll cost in currency;
  both legs must carry the same multiplier.

## 2. Delivery-deadline audit

Evaluated before anything else, because it can void the rest of the decision.

| Condition | `delivery_risk_level` | Engine behaviour |
|---|---|---|
| `days_to_expiration < 0` | `PAST_LAST_TRADING_DAY` | `ROLL_TOO_LATE_ESCALATE`, **no spread order**, logged at CRITICAL. |
| Physically delivered, `days_to_first_notice <= 0` | `FIRST_NOTICE_PASSED` | Roll triggered, logged at WARNING. |
| Physically delivered, `0 < days_to_first_notice <= min_days_to_first_notice` | `APPROACHING_FIRST_NOTICE` | Roll triggered, logged at WARNING. |
| Otherwise | `NONE` | Continue to the liquidity audit. |

`days_to_expiration == 0` means today is the final trading session: still tradable,
so a spread order is still built.

## 3. Liquidity-migration audit

- Volume crossover: `next.daily_volume > front.daily_volume` (strict).
- Open-interest crossover: `next.open_interest > front.open_interest` (strict).
- Both are independent and either alone triggers a roll. Equal values are not a
  crossover.
- Each disabled trigger is named explicitly in the hold audit note, so a log line
  never implies a comparison the engine did not make.

Every trigger that fired is returned in `trigger_reasons`, ordered
`FIRST_NOTICE_DAY_THRESHOLD`, `DAYS_TO_EXPIRATION_THRESHOLD`, `VOLUME_CROSSOVER`,
`OPEN_INTEREST_CROSSOVER`. `trigger_reason` is the same list joined with `+`.

## 4. Calendar spread construction

1. **Basis**: `spread_price_diff = P_next - P_front`, independent of any venue
   convention. `CONTANGO` / `BACKWARDATION` / `FLAT` classify its sign.
2. **Legs**: a long rolls `SELL` front / `BUY` next; a short rolls `BUY` front /
   `SELL` next. This never varies by venue.
3. **Spread side and quoted price** — this *does* vary by venue:

   | Convention | `quoted_spread_price` | Long rolls by | Short rolls by |
   |---|---|---|---|
   | `NEARBY_MINUS_DEFERRED` | $P_{\text{front}} - P_{\text{next}}$ | `SELL` the spread | `BUY` the spread |
   | `DEFERRED_MINUS_NEARBY` | $P_{\text{next}} - P_{\text{front}}$ | `BUY` the spread | `SELL` the spread |

   Confirm the product's convention against the venue's own documentation before
   routing; CME uses both across its product lines.
4. **Roll basis cost**: $(P_{\text{next}} - P_{\text{front}}) \times \text{qty}
   \times \text{multiplier}$, negated for a short. Positive is a P&L drag: a long in
   contango pays the basis to stay on the curve, a short in contango receives it.
   Fees and the spread's own bid/ask are excluded.
5. **Symbol**: `spread_symbol` (`FRONT-NEXT`) is a display label. Resolve the
   tradable combination instrument from the venue security definition.

## 5. Execution and audit logging

- Persist the whole `FuturesRollAuditReport`, not just `audit_notes` — the report
  carries the contract snapshots the decision was made from, which is what a later
  reconstruction needs.
- On `ROLL_TOO_LATE_ESCALATE`, stop and escalate. There is no automated recovery:
  the position is heading to settlement or delivery and needs a human decision.
- Reconcile the resulting position after the roll fills. A calendar spread fills as
  a unit at the venue, but position bookkeeping still has to move the exposure from
  one contract to the other.
