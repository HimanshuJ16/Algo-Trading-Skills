---
name: exchange-self-match-prevention-configuration
description: >-
  Use when several order-generating processes under one beneficial owner quote the same
  instrument on the same venue, configuring native self-match prevention such as the CME
  iLink and FIX identifier and instruction fields.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: market-microstructure-latency
  tags: smp, stp, self-match-prevention, wash-trade-prevention, fix-tag-7928, fix-tag-2362, fix-tag-2964, cme-ilink, order-routing
  brokers_frameworks: "CME Globex iLink 2 / iLink 3; FIX 5.0 SP2 (SelfMatchPreventionID 2362 / SelfMatchPreventionInstruction 2964); Coinbase Exchange STP; Nasdaq INET / OUCH STP; Python Dataclasses"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when a firm runs more than one order-generating process against the same instrument on the same venue under the same beneficial owner — multiple strategies, a market maker quoting both sides, a SOR fanning a parent across child algos, or a hedger unwinding into a book its own maker is still quoting. In that setup the firm's own buy and sell orders will eventually meet in the matching engine, and the resulting print is a self-match.

The venue, not the client, prevents this. Exchanges expose a **Self-Match Prevention (SMP / STP)** identifier plus an instruction saying which side to pull. This module does two things:

1. **Encodes** the SMP identifier and instruction onto the field and value the target venue actually accepts — the tag numbers and enum values differ per venue and are not interchangeable.
2. **Audits** the order against a snapshot of the firm's own resting orders and predicts which of them the venue would cancel, so a self-collision is visible pre-trade rather than discovered in a surveillance report.

## When NOT to Use

- **As the wash-trade control itself.** SMP is a mechanical block on matching. A wash trade under CEA §4c(a) / CME Rule 534 turns on *intent* — whether the party knew or should have known the orders would negate market risk. Orders that never match can still evidence intent. Pair SMP with `wash-trade-and-spoofing-self-detection`.
- **During the pre-open or opening match on CME Globex.** CME's SMP functionality does not prevent self-matches on the opening for orders entered during the pre-open state (MRAN RA1614-5, FAQ 17). Orders resting into the open must be de-conflicted upstream; this module's audit is a same-continuous-session model.
- **As a substitute for the venue flags.** The audit here runs on a local snapshot that is already stale at the matching engine. Never act on it by cancelling locally — see the pitfalls.
- **Across venues.** SMP scope is one venue, one firm (`SelfMatchPreventionID(2362)`: "the same SelfMatchPreventionID … submitted by the same firm"). Two orders on two exchanges never trigger it. Cross-venue self-crossing is a netting problem — see `multi-order-netting-before-routing`.
- **Where the exposure is a real position, not a print.** SMP cancels orders; it does not net positions or reduce fees.
- **As a strategy-level construct.** The wash-trade exposure attaches to the beneficial owner, so the SMP group must be scoped to the legal entity / commonly-owned accounts. Grouping per strategy leaves inter-strategy self-matches unprevented.

## Prerequisites

- A venue-issued SMP identifier registered with the exchange (CME issues a 7-digit SMP ID through firm administration; Nasdaq scopes STP by MPID with an optional port-level Group ID; Coinbase scopes it to the account/profile).
- The target venue profile — `CME_ILINK2`, `CME_ILINK3`, `FIX_LATEST`, `COINBASE_EXCHANGE`, `NASDAQ_INET`, or your own `SmpVenueProfile`.
- An SMP instruction the venue actually offers. Globex offers cancel-resting and cancel-aggressing only; base FIX adds cancel-both; crypto venues add decrement.
- A snapshot of the firm's own working orders on the instrument, ideally with venue time-priority sequence numbers.

## Workflow

1. **Select the venue profile and confirm what it supports.**
   - `CME_ILINK2` → tag `7928` (`SelfMatchPreventionID`) + tag `8000` (`SelfMatchPreventionInstruction`, `O` = cancel oldest/resting, `N` = cancel newest/aggressing).
   - `CME_ILINK3` → the ID moves to tag `2362`; the instruction stays in tag `8000`.
   - `FIX_LATEST` → tag `2362` + tag `2964`, an **integer** enum: `1` cancel aggressive, `2` cancel passive, `3` cancel aggressive and passive.
   - `COINBASE_EXCHANGE` → `stp` = `dc` / `co` / `cn` / `cb`.
   - **Decision point — an instruction the venue does not list is not "close enough" to one it does.** The engine raises rather than downgrading, because a silent `CANCEL_BOTH` → `CANCEL_RESTING` substitution leaves the aggressing side of the intended block live.

2. **Encode the fields.** `encode_smp_fields()` returns the tag *and* the wire value. Never put the internal name (`"CANCEL_RESTING"`) on the wire — tag 8000 is a char and tag 2964 is an int, and either will earn a session-level reject.
   - **Decision point — a blank SMP ID means SMP is off.** The engine refuses a blank ID unless `require_smp_id=False` makes that an explicit choice, because a dropped field silently disables the control on every order that carries it.

3. **Audit against the firm's own book.** `audit_and_apply_smp()` finds every own resting order on the opposite side of the same instrument, carrying the same non-blank SMP ID, at a price the incoming order reaches.
   - Unpriced (market) orders cross every level and therefore collide with **all** of them.
   - Collisions come back in match order: best price for the aggressor first, then venue time priority (`entry_seq`), then input order.
   - **Decision point — two blank SMP IDs are not a group.** Orders without an ID are outside SMP entirely and must never be reported as a self-match.

4. **Read the predicted outcome, do not enact it.**
   - `CANCEL_RESTING`: every reachable own resting order is pulled; the aggressor survives in full and may still trade third-party liquidity.
   - `CANCEL_AGGRESSIVE`: the aggressor is pulled at its first own-book contact; deeper own orders are never reached, so only the trigger is reported.
   - `CANCEL_BOTH`: aggressor and the first resting order both go.
   - `DECREMENT_AND_CANCEL`: model-dependent, and the models disagree — symmetric (Nasdaq: `min(agg, resting)` leaves both sides) versus Coinbase `dc` (the taker always cancels in full and the maker decrements). The engine refuses to simulate decrement for a profile that declares no model.
   - **Decision point — `is_order_dispatched` is a prediction, not an instruction.** The venue performs the cancels. Issuing them locally races the exchange's own SMP cancel and can cancel an order the engine already replaced.

5. **Reconcile against the venue's execution reports.** On Globex a SMP cancel arrives as `MsgType=8`, `OrdStatus=4`, with `ExecRestatementReason(378)=103` ("Cancel Oldest (Resting) due to Self-Match Prevention") for the resting side. Treat a divergence between the pre-trade prediction and the venue's report as a book-staleness defect to investigate, not as noise.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Putting the internal instruction name on the wire.** `SelfMatchPreventionInstruction` is `char` `O`/`N` on Globex and `int` `1`/`2`/`3` in FIX 5.0 SP2. Sending the string `CANCEL_RESTING` is a session-level reject, and the reject arrives after the order was supposed to be working.
- **Silently defaulting an unrecognised instruction.** A typo'd instruction that falls back to "cancel resting" hands the firm the *least* protective outcome for the aggressor while looking configured. Reject the order instead.
- **Treating a missing SMP ID as a group.** Two orders with a blank identifier are two orders with SMP disabled, not two members of the same group. Matching blank-to-blank produces false collisions on unrelated flow; the real risk is the opposite — those orders will happily self-match at the venue.
- **Auditing only the first collision.** Globex cancels the resting order(**s**) at every executable price level. An aggressor sweeping three of the firm's own offers triggers three cancels; reporting one understates the pulled liquidity and the surveillance footprint.
- **Letting input list order decide which order "matched first."** Without price-then-time ordering the reported trigger changes when the book snapshot is rebuilt, and the audit trail stops being reproducible.
- **Assuming SMP covers the open.** CME SMP does not prevent self-matches on the opening for orders entered during the pre-open (RA1614-5). Entering orders into a pre-open that the firm knew or should have known would match is itself the violation.
- **Assuming `CANCEL_AGGRESSIVE` means nothing traded.** SMP acts at the point of match. An aggressor sweeping the book can fill against third-party liquidity at better prices *before* it reaches its own resting order and is pulled. The cancel is of the remainder.
- **Cancelling locally on a predicted collision.** The snapshot is stale by construction, the venue is the enforcer, and a client-side cancel racing the exchange's SMP cancel produces an ambiguous order state — the exact condition `order-placement-idempotency` exists to avoid.
- **Reusing one SMP ID for the whole firm without thinking.** Every order sharing the ID is mutually exclusive. A hedging desk and a market maker on the same ID will pull each other's genuine liquidity all day; too narrow an ID leaves inter-strategy self-matches unprevented. Scope it to the beneficial owner and accept the queue cost.
- **Comparing untick-aligned floats.** A limit price one ULP below the resting price reports "no collision" and routes. Feed venue-tick-aligned prices from the same source that built the book.

## Verification

- Encoding: `ExchangeSelfMatchPreventionEngine(venue="CME_ILINK2").encode_smp_fields("8810123", "CANCEL_RESTING")` returns tag `7928`/`8000` with wire value `O`; `CANCEL_AGGRESSIVE` returns `N`; `CANCEL_BOTH` and `DECREMENT_AND_CANCEL` raise `SmpConfigurationError`. On `FIX_LATEST` the same three instructions return `1`, `2`, `3` on tag `2964`.
- Sweep: with own offers at 185.00, 185.50 and 190.00 (all `SMP_PROP_100`), a BUY 25 @ 186.00 under `CANCEL_RESTING` must report exactly `("R1", "R2")` cancelled, in that order, and `dispatched_qty == 25`.
- Ordering: with own offers listed far-price-first, a crossing BUY must report the *lowest*-priced offer as `colliding_resting_ord_id`, and `entry_seq` must break ties within a price level.
- Negative checks — each must raise `SmpConfigurationError`: an unknown instruction, an instruction the venue does not offer, a blank `smp_id` (unless `require_smp_id=False`), a side other than BUY/SELL, `order_qty <= 0` or non-integer, a NaN/infinite/non-positive price, a blank `cl_ord_id`, a resting order with no price, and `DECREMENT_AND_CANCEL` on a profile with no decrement model.
- Scoping: two orders both carrying a blank SMP ID must report `has_self_collision == False`.
- Market order: an unpriced BUY must collide with every own offer rather than raising.
- Run `python -m unittest discover -s skills/exchange-self-match-prevention-configuration/scripts` and confirm a 100% pass rate.

## Related Skills

- `wash-trade-and-spoofing-self-detection`
- `eu-market-abuse-regulation-mar-surveillance`
- `multi-order-netting-before-routing`
- `order-placement-idempotency`
- `smart-order-routing-across-venues`
- `cme-globex-futures-api-integration`
