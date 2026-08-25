# Pre-Flight Checklist

## Registration and scope

- [ ] Is the SMP identifier **issued and registered by the venue** (CME 7-digit SMP ID via FADB, Nasdaq MPID/Group ID, Coinbase account/profile) rather than invented locally?
- [ ] Is the SMP group scoped to the **beneficial owner / commonly-owned accounts**, not to a single strategy — since MAR's wash-trade indicator turns on beneficial interest and CME Rule 534 on common ownership?
- [ ] Has the cost of that scope been accepted — every order sharing the ID is mutually exclusive, so a hedger and a market maker on one ID will pull each other's genuine liquidity?

## Wire encoding

- [ ] Is the value sent on the wire the **venue's own enum** (`O`/`N` on CME tag 8000, `1`/`2`/`3` on FIX tag 2964, `dc`/`co`/`cn`/`cb` on Coinbase `stp`) and never the internal name `CANCEL_RESTING`?
- [ ] Is the SMP ID on the **right tag for the right session** — `7928` on iLink 2, `2362` on iLink 3 and FIX 5.0 SP2?
- [ ] Has `profile.supported_instructions()` been checked before wiring a strategy to an instruction (Globex offers cancel-resting and cancel-aggressing only — no cancel-both, no decrement)?
- [ ] If `DECREMENT_AND_CANCEL` is used, is `profile.decrement_model` the one the venue actually implements — symmetric (Nasdaq) versus taker-cancelled/maker-decremented (Coinbase `dc`)?
- [ ] Is the instruction sent explicitly rather than relying on Globex's absent-tag default, so the choice is visible in the order log?

## Fail-closed behaviour

- [ ] Does an **unrecognised** instruction raise rather than falling back to a default? (A typo that silently becomes "cancel resting" leaves the control looking configured.)
- [ ] Does an instruction the **venue does not offer** raise rather than being downgraded to the nearest supported one?
- [ ] Is a **blank SMP ID** rejected unless `require_smp_id=False` makes routing without SMP an explicit decision?
- [ ] Are two **blank** SMP IDs treated as *not* a group, so unrelated flow is never flagged as a self-collision?
- [ ] Are malformed orders rejected — side outside BUY/SELL, non-positive or non-integer quantity, NaN/infinite/non-positive price, blank client order ID?

## Audit correctness

- [ ] Does the audit report **every** own resting order the aggressor reaches, not just the first? (Globex cancels the resting order**s** at each executable price level.)
- [ ] Are collisions ordered **price-then-time** so the audit trail does not change when the snapshot is rebuilt in a different order?
- [ ] Is `entry_seq` populated from venue time priority, rather than relying on the caller's list order?
- [ ] Are **unpriced (market) orders** audited as crossing every opposing level, rather than raising or being skipped?
- [ ] Are prices venue-tick-aligned and taken from the same source that built the book, so an off-by-one-ULP limit does not report "no collision"?

## Enforcement boundary

- [ ] Is it understood that the **venue**, not this module, performs the cancels — and that no client-side cancel is issued on a predicted collision?
- [ ] Is `is_order_dispatched` consumed as a **prediction against a stale snapshot**, used to block/resize/reprice upstream, never as an instruction to act on the book?
- [ ] Is it understood that `CANCEL_AGGRESSIVE` does **not** mean nothing traded — a sweeping aggressor can fill third-party liquidity before reaching its own order and being pulled?

## Gaps SMP does not close

- [ ] Are orders entered during the **CME Globex pre-open** de-conflicted upstream, given SMP does not prevent self-matches on the opening (RA1614-5, FAQ 17)?
- [ ] Is **cross-venue** self-crossing handled by netting upstream, since SMP scope is one venue and one firm?
- [ ] Is a separate wash-trade/spoofing surveillance control in place, given a Rule 534 violation turns on **intent** and orders that never match can still evidence it?

## Reconciliation

- [ ] Are venue SMP cancels captured (`MsgType=8`, `OrdStatus=4`, `ExecRestatementReason(378)=103` for the resting side on Globex) and processed without error?
- [ ] Is the venue's cancel set diffed against the pre-trade `resting_cl_ord_ids_cancelled`, with a persistent gap investigated as book staleness rather than logged and forgotten?
- [ ] Is the SMP event rate per SMP ID trended per session, so "incidental" self-matching does not quietly become routine?
