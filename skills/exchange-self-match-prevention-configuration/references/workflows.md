# Workflows for Exchange Self-Match Prevention (SMP / STP)

## 0. Inputs and their provenance

- `smp_id` — issued and registered by the venue, not chosen by the developer. CME generates a 7-digit SMP ID through firm administration (FADB); Nasdaq derives scope from the MPID with an optional port-level Group ID; Coinbase uses the account/profile. An unregistered value earns a session-level reject.
- **Scope decision, made once per firm** — every order sharing an SMP ID is mutually exclusive. Because MAR's wash-trade indicator turns on *beneficial interest* and CME Rule 534 on commonly-owned accounts, the group must cover the legal entity, not one strategy. The cost is real: a hedger and a market maker on the same ID will pull each other's genuine liquidity. Decide it deliberately and record the rationale.
- `smp_instruction` — must be one the venue offers. Confirm against `references/standards.md` before assuming portability.
- `resting_orders` — the firm's own working orders on the instrument, from the order manager, not the public book. Carry the venue's time-priority sequence in `entry_seq`; without it the audit falls back to input order, which is only as reliable as the caller's sorting.
- Prices must be venue-tick-aligned and drawn from the same source that built the book. Comparison is exact; a float one ULP off silently reports no collision.

## 1. Venue profile selection

1. Pick a profile from `SMP_VENUE_PROFILES` (`CME_ILINK2`, `CME_ILINK3`, `FIX_LATEST`, `COINBASE_EXCHANGE`, `NASDAQ_INET`) or construct an `SmpVenueProfile` for a venue not shipped here.
2. Read `profile.supported_instructions()` before wiring a strategy to an instruction. Globex offers cancel-resting and cancel-aggressing only.
3. If the strategy needs `DECREMENT_AND_CANCEL`, check `profile.decrement_model`:
   - `SYMMETRIC` — `min(aggressor, resting)` is removed from both; the smaller side cancels in full; the aggressor walks the book until exhausted (Nasdaq).
   - `CANCEL_AGGRESSOR_DECREMENT_RESTING` — the taker cancels in full, the maker decrements by the taker's size (Coinbase `dc`).
   - `None` — the engine refuses to simulate it. Supply a profile whose model you have confirmed against the venue rather than assuming one.
4. Set the engine default deliberately. `default_smp_instruction=None` makes the instruction mandatory on every request, which is the right setting when several strategies with different appetites share the engine.

## 2. Field encoding

```
fields = engine.encode_smp_fields(smp_id, instruction)
# -> SmpWireFields(venue, smp_id_tag, smp_id_wire_value,
#                  smp_instruction_tag, smp_instruction_wire_value)
```

Attach `smp_instruction_wire_value`, never the internal name. Tag `8000` is a `char` (`O`/`N`); tag `2964` is an `int` (`1`/`2`/`3`); Coinbase `stp` is a two-letter code. On Globex, omitting tag 8000 entirely is legal and defaults to cancelling the resting order(s) — but relying on that default hides the choice from anyone reading the order log later.

## 3. Pre-trade collision audit

`audit_and_apply_smp(req, resting_orders)` selects a resting order when **all** of the following hold:

1. same `symbol`;
2. opposite `side`;
3. both `smp_id` values non-blank and equal after stripping — a blank ID means SMP is off for that order and is never a group member;
4. the aggressor's price reaches the resting price (`BUY: price >= resting`, `SELL: price <= resting`), with an unpriced aggressor reaching every level.

Selected orders are returned in match order — best price for the aggressor first (lowest own offer for a buy, highest own bid for a sell), then `entry_seq`, then input index. This ordering is what makes the audit reproducible across snapshot rebuilds.

Malformed input raises `SmpConfigurationError` rather than being coerced: an unknown side, a non-positive or non-integer quantity, a non-finite or non-positive price, a blank client order ID or symbol, a non-`RestingBookOrder` entry, or a blank SMP ID while `require_smp_id` is set.

## 4. Reading the predicted outcome

| Instruction | Resting orders | Aggressor | Reported |
|---|---|---|---|
| `CANCEL_RESTING` | every reachable own order cancelled in full | survives in full | all collisions |
| `CANCEL_AGGRESSIVE` | untouched | cancelled | first contact only |
| `CANCEL_BOTH` | first contact cancelled in full | cancelled | first contact only |
| `DECREMENT_AND_CANCEL` (symmetric) | decremented in match order until the aggressor is exhausted | remainder survives | each decremented order |
| `DECREMENT_AND_CANCEL` (Coinbase) | first contact decremented by the taker size | cancelled in full | first contact only |

`CANCEL_AGGRESSIVE` and `CANCEL_BOTH` report only the first contact because the aggressor is pulled there and never reaches the rest of the firm's book.

Two properties worth asserting downstream:

- Under the symmetric decrement model, `sum(aggressor_qty_cancelled) + dispatched_qty == order_qty`.
- `resting_cl_ord_ids_cancelled` is exactly the set of collisions with `resting_qty_cancelled > 0`.

## 5. Routing and enforcement boundary

The venue enforces SMP. This module produces the fields that turn it on and a prediction of what the venue will do.

- **Do not** cancel locally on a predicted collision. The snapshot is stale by construction; a client cancel racing the exchange's SMP cancel leaves an ambiguous order state — precisely the failure mode `order-placement-idempotency` addresses.
- **Do** use the prediction as a pre-trade risk gate: block, resize, or reprice the order upstream if the firm's policy is to avoid pulling its own resting liquidity at all.
- **Do** note that `CANCEL_AGGRESSIVE` does not mean nothing traded. SMP acts at the point of match, so a sweeping aggressor can fill against third-party liquidity at better prices before reaching its own order and being pulled. The cancel is of the remainder.

## 6. Post-trade reconciliation

1. Capture the venue's SMP cancels. On Globex: `MsgType=8`, `OrdStatus=4`, `ExecRestatementReason(378)=103` ("Cancel Oldest (Resting) due to Self-Match Prevention") on the resting side; the aggressing-side cancel arrives on the corresponding execution report with tag 8000 echoed back.
2. Diff the venue's cancels against `resting_cl_ord_ids_cancelled` from the pre-trade audit. A persistent gap is a book-staleness or scope defect, not noise.
3. Count SMP events per SMP ID per session and trend them. A rising rate means the strategies sharing an ID are increasingly fighting each other — an execution-quality problem and, past the "incidental" threshold in CME RA1614-5, a surveillance one.
4. Handle the gaps SMP does not cover:
   - **Pre-open / opening match on Globex** — SMP does not apply to orders entered during the pre-open. De-conflict those upstream.
   - **Cross-venue** — SMP is per venue, per firm. Two orders on two exchanges will match. Net upstream (`multi-order-netting-before-routing`).
   - **Intent** — orders that never match can still evidence a wash-trade intent. SMP is a mechanical block, not a compliance defence.
