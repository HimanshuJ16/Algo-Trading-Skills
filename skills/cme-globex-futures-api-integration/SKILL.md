---
name: cme-globex-futures-api-integration
description: Use when assembling CME Globex futures order entry messages, to validate
  the Operator ID (Tag 50, Rule 576) and Manual Order Indicator (Tag 1028, Rule 536.B.)
  a message is rejected without, apply price banding on the side CME actually constrains,
  keep prices on the product's tick, and compute where Market-with-Protection residual
  quantity will rest.
domain: Market Connectivity
subdomain: Exchange API
tags:
- cme-globex
- ilink3
- futures
- tag50
- tag1028
- mwp
- price-banding
- tick-conformance
brokers_frameworks:
- CME Globex iLink 3
- CME FIX
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when a trading system sends orders to CME Globex — futures and options
on futures such as `ES`, `CL`, `ZB` — and you are assembling the order entry message
yourself rather than handing an order to a vendor OMS that already fills in the
exchange-mandated fields. It covers the four gateway checks a hand-rolled CME client
most often gets wrong:

1. **Operator ID (Tag 50 / iLink 3 `SenderID`)** absent or malformed — CME Rule 576.
2. **Manual Order Indicator (Tag 1028)** absent, or a team/ATS ID used on a manually
   entered order — CME Rule 536.B.
3. **Price banding** implemented as a two-sided check, which rejects perfectly legal
   resting orders that the exchange would accept.
4. **Market with Protection** treated as an unbounded market order, so the residual
   that rests at the protection limit is invisible to position and risk logic.

## When NOT to Use

- **As a session layer.** This module validates and assembles fields. It does not
  encode SBE, run the FIXP session, sequence messages, or recover gaps — see
  `fix-protocol-session-management-across-venues`.
- **For stop and stop-limit orders.** Stop orders on Globex have their own protection
  and trigger semantics; `process_order` rejects any order type other than `LIMIT`
  and `MARKET` rather than silently treating it as a limit.
- **As an idempotency mechanism.** `cl_ord_id` is passed through, not tracked. Whether
  a resubmission after a lost response duplicates an order is out of scope — see
  `order-placement-idempotency`.
- **As a source of contract parameters.** Tick size, Price Band Variation and
  protection points are inputs. They come from CME's published product reference files
  and change; nothing here derives or guesses them.
- **As your only pre-trade risk gate.** These are exchange-conformance checks, not
  capital, exposure or drawdown limits — see `sec-rule-15c3-5-risk-controls-us`.

## Prerequisites

- A CME Globex Firm ID, iLink session, and Operator IDs registered in the Exchange Fee
  System (EFS). The value transmitted must match the registration exactly.
- Knowledge of which Operator IDs are registered to an individual and which to a
  team/ATS — the two are not interchangeable across manual and automated orders.
- Per-symbol `ContractSpec`: tick size (minimum price increment), Price Band Variation,
  and Market-with-Protection points, refreshed from CME's product reference files.
- Python 3.7+. Standard library only — `decimal`, `logging`, `dataclasses`.

## Workflow

1. **Load contract specifications.** Build a `ContractSpec` per symbol. The constructor
   rejects a non-positive tick size or negative band/protection values, because a
   zero-width band silently rejects every order and a zero tick makes every price
   off-tick.

2. **Construct the engine.** Pass `team_operator_ids` if you know which IDs are
   team/ATS registrations — the manual/automated pairing check is skipped when the set
   is empty rather than guessed at. Pass `permitted_operator_id_symbols` if the advisory
   notice in force for your firm differs from the default `_ - : @`.

3. **State Tag 1028 explicitly on every order.** `manual_order_indicator` has no
   default. `True` means a human entered the order; `False` means it was generated or
   routed without direct human interaction — anything an execution algorithm produces
   is `False`. An order that leaves it unset is rejected before transmission, because
   CME rejects the message anyway and a guessed audit-trail value is worse than a
   refusal.

4. **Call `process_order` with the market state the exchange will judge the order
   against.** `reference_price` is the CME Banding Reference Price — the last trade,
   else the best bid/offer, else the settlement price — and is required for a limit
   order. `current_bid` / `current_ask` are needed only for a market order, and only on
   the side the protection limit is computed from.

5. **Handle the rejection by type, not by string.** `OperatorIdError` and
   `ManualOrderIndicatorError` mean a registration or configuration fault: the same order
   will fail again, so do not retry it — escalate. `PriceBandingError` and
   `TickConformanceError` mean the price is wrong for the current market: re-price and
   resubmit. A bare `CmeOrderValidationError` on a market-data argument means the quote
   is unusable, so refresh it before retrying.

6. **Account for the Market-with-Protection residual.** On a market order the returned
   `price` is `None` — a market order carries no Tag 44 — and `protection_price_limit`
   says where unfilled quantity will rest as a limit order. Feed that into position
   tracking. If `protection_limit_outside_band` is set, the residual would rest outside
   the price band; the module flags and logs it rather than rejecting, because banding
   applies to price-based orders and a market order carries no price.

## Common Pitfalls

- **Checking the price band on both sides.** CME rejects buys above BRP + PBV and sells
  below BRP − PBV, and deliberately does *not* stop a bid below the market or an offer
  above it. A symmetric `min <= price <= max` check rejects ordinary deep passive orders
  the exchange would have accepted — a silent loss of legitimate order flow that no
  exchange reject ever tells you about.
- **Trusting `price % tick_size == 0`.** In binary floating point `5000.10 % 0.05` is
  about 0.049999999, so a naive modulo check rejects a valid price for any product whose
  tick is not a power of two. Tick arithmetic here runs in `decimal.Decimal`.
- **Letting protection points off the tick.** If protection points are not a whole number
  of ticks, the computed protection limit is off-tick. This module rounds it *toward* the
  market — down for a buy, up for a sell — so rounding can only tighten protection.
- **Sending a market order as if it were unbounded.** Globex fills a market order only
  inside the protected range and rests the remainder at the limit of that range. A client
  that does not model this ends up holding a resting limit order it never placed.
- **Trimming whitespace off an Operator ID and sending it anyway.** The transmitted value
  must match the EFS registration exactly; a padded ID is a configuration fault, so it is
  rejected rather than normalised.
- **Distinguishing Operator IDs by letter case.** CME Operator IDs are not case sensitive,
  so `DESK_01` and `desk_01` are the same registration. The team/ATS check here compares
  case-insensitively for the same reason.
- **Omitting Tag 1028.** In-scope iLink order entry messages without it, or with an
  invalid value, are rejected — and a team/ATS Operator ID may only submit automated
  messages.

## Verification

- Submit an order with `manual_order_indicator=None` and confirm it is rejected before
  transmission rather than defaulted.
- Submit a limit buy far *below* the reference price and confirm it is **accepted** — the
  regression case a two-sided band check gets wrong — then a limit buy one tick above
  BRP + PBV and confirm `PriceBandingError`.
- Submit a limit price off the product's tick (e.g. 5000.10 on a 0.25-tick product) and
  confirm `TickConformanceError`.
- Submit a market buy and confirm `price is None`, `ord_type == "MARKET"`, and
  `protection_price_limit == best_offer + protection_points`, on-tick.
- Run `python -m unittest discover -s skills/cme-globex-futures-api-integration/scripts`.

## Related Skills

- `cme-group-fix-api-for-futures`
- `fix-protocol-session-management-across-venues`
- `order-placement-idempotency`
- `exchange-self-match-prevention-configuration`
- `sec-rule-15c3-5-risk-controls-us`
