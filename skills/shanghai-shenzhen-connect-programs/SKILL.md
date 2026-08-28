---
name: shanghai-shenzhen-connect-programs
description: >-
  Northbound Stock Connect order gate for SSE and SZSE Securities, enforcing the RMB 52 billion per-channel Daily Quota on its published net-buy formula, SEHK pre-trade checking (the mechanism behind the T+1 no-day-trading rule), per-board lot and order-size limits including the STAR exception, price limits, tick size, and the 28%/26% foreign shareholding suspension.
domain: Global Exchange Connectivity & Cross-Border Trading
subdomain: China Stock Connect & Northbound Trading
tags: ["stock-connect", "shanghai-connect", "shenzhen-connect", "northbound-trading", "daily-quota", "pre-trade-checking", "a-shares", "hkex"]
brokers_frameworks: ["HKEX Stock Connect Rules", "SSE / SZSE Trading Rules", "CCASS / SPSA", "Python Dataclasses", "Python Decimal"]
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when routing Northbound equity orders from Hong Kong or an
international institution into Mainland China A-shares listed on the Shanghai
Stock Exchange (SSE) or Shenzhen Stock Exchange (SZSE) through Stock Connect.

Northbound is not "A-shares with extra steps". It layers a programme-specific
rule set on top of SSE/SZSE trading rules — a per-channel daily quota with its
own accounting identity, a prohibition on day trading enforced through a
market-open position snapshot, per-board lot and size limits, and a foreign
ownership cap that can suspend buying in a single name mid-session. Each of
these rejects orders that would be perfectly valid on any other venue.

The engine is a **client-side pre-submission gate**: it screens an order before
it reaches CSC (the China Stock Connect System) and maintains the Daily Quota
ledger so you know whether further buying is possible at all. Passing the gate
means an order is not obviously invalid; SSE, SZSE and CSC remain authoritative.

## When NOT to Use

- **For Southbound trading.** Different quota (RMB 42 billion), different order
  types (at-auction limit in the pre-opening session, enhanced limit in
  continuous trading), and day trading *is* permitted. None of the logic here
  transfers.
- **As a substitute for CSC's own checks.** SEHK's dynamic price check — a buy
  priced more than a prescribed percentage below the current best bid, 3% at the
  initial phase — needs the live best bid and is applied venue-side. It is not
  implemented here and cannot be inferred from an order in isolation.
- **To decide what is Northbound-eligible.** The eligible-security and sell-only
  lists are published and maintained by SEHK against index membership, market
  cap, turnover and risk-alert status. They are data to ingest, not something
  derivable from a stock code. This engine takes `buy_eligible` as an input.
- **For QFII / RQFII or the CIBM and Bond Connect channels.** The 10% single-
  investor and 30% aggregate foreign shareholding caps are counted across all
  those channels together, but the trading rules are entirely different.
- **As a settlement or custody engine.** Stock settles on T day and money on T or
  T+1; HKSCC runs four Northbound batch settlement runs on T day. That is CCASS
  territory, not this gate's.
- **On multiple threads.** The engine holds mutable per-day quota and position
  state with no locking. Serialise calls, or wrap it.

## Prerequisites

- **Reference data per security, refreshed daily**: previous closing price (it
  anchors both price limits for the whole day), listing board, ETF flag with its
  price-limit percentage, and SEHK's sell-only designation.
- **A market-open shareholding snapshot** per selling participant, or per SPSA
  Investor ID. This is the pre-trade checking baseline — the same snapshot CCASS
  replicates to CSC. Without it no sell order can be validated.
- **The Northbound trading calendar.** Northbound is open only when both the Hong
  Kong and Mainland markets are open; the Mainland calendar alone will have you
  trading on closed days. See `global-exchange-holiday-calendar-handling`.
- **Institutional professional investor status** for any STAR (SSE 688xxx) or
  ChiNext (SZSE 300xxx/301xxx) order. Other investors may not trade them
  Northbound at all.
- Python 3.9+. Standard library only. Prices and quota are `Decimal`; the engine
  refuses `float` prices outright, because `Decimal(0.01)` is not `0.01` and a
  tick-size check would then depend on how the caller spelled the number.

## Workflow

1. **Open the day.** `start_trading_day(opening_positions)` resets the Daily
   Quota to RMB 52 billion per channel, clears yesterday's suspension latch, and
   loads the market-open position map. Unused quota never carries over.

2. **Screen the order's structure** — reference data present, channel matching
   the listing venue, limit order, size, tick, price limit.
   - **Decision point — a missing input rejects.** No registered security, no
     opening position, no classifiable board: reject with an auditable code.
     Every permissive default in a compliance gate eventually fires.
   - **Decision point — the board lot binds buys only.** Odd lots are sellable and
     must be sold in one order. A gate that rejects odd-lot sells permanently
     strands corporate-action remnants.
   - **Decision point — STAR is the board-lot exception, ChiNext is not.** STAR:
     board lot 1 share, 200-share minimum, 100,000 maximum. ChiNext keeps the
     100-share lot with a 300,000 maximum. ChiNext shares STAR's ±20% price limit
     and its professional-investor restriction, which is exactly what makes the
     over-generalisation tempting.

3. **Gate the buy side** — sell-only designation, then foreign shareholding, then
   quota.
   - **Decision point — 28% suspends, 26% resumes.** Asymmetric by rule. A single
     threshold makes buying flap across the boundary.
   - **Decision point — the order that exhausts the quota is accepted.** What the
     rule blocks is the buy arriving *after* the balance is gone. This is why
     HKEX describes the Daily Quota being "exceeded" and why the balance can go
     negative. Rejecting on `balance < notional` refuses orders SEHK would take.
   - **Decision point — exhaustion latches, except at the open.** In a continuous
     auction or the closing call auction it stops Northbound buying for the rest
     of the day, and a later sell trade restoring the balance does not lift it.
     In the opening call auction it does not latch: cancellations are common
     there and SEHK resumes accepting buys if the balance returns positive.

4. **Gate the sell side on pre-trade checking**, never on quota.
   - **Decision point — T+1 is enforced by the market-open position.** Shares
     bought today are absent from that snapshot, so they cannot be sold today by
     construction. The check is against the day's *cumulative* sell quantity, so
     one position cannot be sold twice.
   - Sells are always permitted regardless of quota balance, including while
     buying is suspended.

5. **Account for the lifecycle** through `record_fill` and `cancel_order`, keeping
   the published identity `Daily Quota Balance = Daily Quota – Buy Orders + Sell
   Trades + Adjustments`.
   - **Decision point — consumption at order time, restoration at trade time.**
     Quota is deducted when a buy *order* is accepted and credited when a sell
     *trade* executes. Cancelling a buy releases only its unfilled notional.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Rejecting the buy that exhausts the quota.** The intuitive check —
  "insufficient quota, refuse" — is the wrong rule. SEHK accepts that order and
  refuses the next one, which is why the balance goes negative. A gate that
  cannot represent a negative balance cannot reconcile against SEHK's published
  figure.
- **Crediting quota when a sell order is accepted.** The formula restores on
  "Sell Trades", not sell orders. An unfilled sell that credits quota
  manufactures buying power that was never granted — and if it is then cancelled,
  the error is permanent for the day.
- **Deducting quota when a buy fills rather than when it is submitted.** The
  formula deducts "Buy Orders". Deducting at fill understates consumption all
  day, and a resting unfilled buy order genuinely does hold quota until cancelled.
- **Clamping the balance at RMB 52 billion.** The quota is a *net buy* limit, so a
  net-sell day legitimately credits more than it consumed. Clamping silently
  discards headroom.
- **Lifting the day's suspension because a sell restored the balance.** Once
  exhaustion latches in a continuous or closing call auction, no further buy
  orders are accepted for the remainder of the day, whatever the balance does.
- **Enforcing T+1 by comparing a purchase date to today.** It looks like the rule
  and enforces something much weaker: it says nothing about how many shares of a
  *previously* held position have already been sold today, so the same position
  can be sold repeatedly. The real check is cumulative sell quantity against the
  market-open position — and if the purchase date is an optional field, an order
  that simply omits it skips the check entirely.
- **Applying the 100-share board lot to STAR, or the STAR exception to ChiNext.**
  A valid 250-share STAR buy is a round lot; a 250-share ChiNext buy is not.
- **Applying the board lot to sell orders.** Odd-lot sells are explicitly
  permitted and are the only way to unwind a corporate-action remnant.
- **Sending a market order.** Only limit orders are accepted Northbound,
  throughout the day. An order router that silently converts a market order to a
  marketable limit has changed the client's instruction.
- **Calling the currency CNH.** HKEX says RMB throughout and never CNH: prices,
  quota and money settlement are RMB. Offshore RMB is where a Hong Kong investor
  *sources* the currency, which is an FX exposure, not a settlement denomination.
- **Using binary floats for prices.** `1700.005` is not a valid RMB 0.01 tick, but
  float arithmetic will not reliably tell you so, and quota accounting runs at
  RMB 10¹⁰ magnitude where the error is not academic.
- **Trading on a Mainland trading day that is a Hong Kong holiday.** Northbound
  needs both markets open. The Mainland calendar alone produces orders on days
  the channel is shut.
- **Routing an SSE symbol over Shenzhen Connect.** Each channel holds its own
  separate RMB 52 billion quota, so a mis-routed order debits the wrong pool and
  corrupts both balances.

## Verification

- **Quota identity**: a 100-share buy of `600519.SH` at RMB 1,700 deducts exactly
  RMB 170,000 from Shanghai Connect and leaves Shenzhen Connect untouched.
  Filling it deducts nothing further. Cancelling after a 400-share fill of a
  1,000-share order releases only the 600 unfilled shares' notional.
- **Order/trade asymmetry**: an accepted sell order moves the balance not at all;
  its fill credits the filled notional. A net-sell day must leave the balance
  *above* RMB 52 billion.
- **Exhaustion**: the order taking the balance below zero is accepted and sets the
  suspension; the next is rejected `QUOTA_EXHAUSTED`. A sell trade restoring the
  balance must not lift it. In the opening call auction the same exhaustion must
  *not* latch, and cancelling the exhausting order must let buying resume.
- **Pre-trade checking**: buy and fill 100 shares, then attempt to sell them the
  same day → `PRE_TRADE_CHECK_FAILED`. From a 1,000-share opening position,
  selling 600 then 500 must reject while 600 then 400 must both pass. A sell with
  no recorded opening position must reject.
- **Board rules**: a 250-share STAR buy passes; 199 fails; a 150-share ChiNext or
  Main Board buy fails; a 137-share odd-lot *sell* passes; 300,100 shares of
  ChiNext fails on size.
- **Price and type**: a market order rejects; RMB 1,700.005 rejects on tick; the
  Main Board band off a 1,700.00 close is exactly [1530.00, 1870.00] and 1870.00
  itself is accepted; STAR and ChiNext are ±20%.
- **Foreign ownership**: 28% suspends, 27% stays suspended, 26% resumes, and
  selling works throughout.
- **Fail-closed**: an unregistered security, a float price, a string side, a
  fractional or non-positive quantity, a NaN price, and a duplicate live order id
  must each reject or raise — never pass.
- Run `python -m unittest discover -s skills/shanghai-shenzhen-connect-programs/scripts`
  and confirm 50/50 pass.

## Related Skills

- `hong-kong-exchange-hkex-orion-api`
- `minimum-fill-size-and-lot-rounding-logic`
- `exchange-tick-size-regime-tracking`
- `global-exchange-holiday-calendar-handling`
- `multi-currency-pnl-and-fx-conversion`
- `us-reg-sho-short-sale-locate-requirements`
- `pattern-day-trader-rule-compliance-us`
