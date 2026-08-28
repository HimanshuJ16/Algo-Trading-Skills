# Deep Workflow Reference — order-book-depth-processing-l2-l3

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Full Procedure

### 1. Choose one feed level per processor instance

L2 updates *set* a price level's absolute volume; L3 events *accumulate* into it. One
instance driven by both produces an aggregate that means nothing. Shard by symbol and by
feed level.

### 2. Validate at ingress, before the lock

Validation is pure and needs no lock, so run it first and reject the whole batch on any
bad field — a half-applied update is worse than a rejected one.

| Field | Rule | Why |
|---|---|---|
| price | finite and $> 0$ | `nan >= x` is `False`, so one NaN price disables the crossed-book guard while every derived metric returns NaN |
| L2 quantity | finite and $\ge 0$; exactly $0$ deletes the level | Venues signal removal with an explicit zero. `qty <= 0` as a delete turns corruption into silent liquidity removal |
| L3 size | finite and $> 0$ | A zero-size resting order is not a book state; a negative one is corruption |
| side | in `{BUY, B, BID}` or `{SELL, S, ASK}` | Defaulting the unrecognised case to one book routes ITCH `B` and Coinbase `buy` onto the offer side |
| order id | non-empty string | An empty key collides across orders |
| depth levels | `int`, $\ge 1$ | `0` raises inside the metrics path; `-1` silently drops the last level through negative slicing |

### 3. Mutate under one mutex

Wrap every bid and ask mutation in the same `threading.Lock`. Bids updated on thread A
while asks are updated on thread B produce transient crossed states indistinguishable
from feed corruption. The lock must also cover any read that spans both sides —
`get_snapshot` and `compute_metrics` take it for exactly that reason.

Do not expose the underlying dicts. A public mutable `bids` lets a caller write to the
book without the lock, at which point the mutex is decorative. Expose read-only views and
an immutable snapshot.

### 4. Apply L2 price-level updates

```
for (price, qty) in updates:
    if qty == 0:  remove level
    else:         level[price] = qty     # absolute, never +=
```

### 5. Apply L3 order events

| Event | Method | Effect on the order | Effect on the level |
|---|---|---|---|
| Add (ITCH `A`/`F`, Coinbase `open`) | `add_l3_order` | create; duplicate id → `DUPLICATE_ORDER_ID`, rejected | `+= size` |
| Execute (ITCH `E`/`C`, Coinbase `match`) | `execute_l3_order` | `-= filled`; remove at zero | `-= filled` |
| Partial cancel (ITCH `X`) | `execute_l3_order` | same deduction semantics | `-= cancelled` |
| Size change (Coinbase `change`) | `modify_l3_order` | **set** to the new absolute size | `+= (new - old)` |
| Delete / full cancel (ITCH `D`, Coinbase `done`) | `cancel_l3_order` | remove | `-= remaining` |

Three rules make the difference between a book that diverges loudly and one that diverges
silently:

- **A duplicate order id is a missed message.** Reference numbers are session-unique.
  Adding twice strands the surplus permanently, because the eventual cancel deducts once.
- **An unknown order id is a divergence.** Every execute / modify / cancel against an id
  that was never added means the add was lost. Count it.
- **An over-execution is a divergence.** More filled than resting means the book was
  already wrong. Deduct what is there, remove the order, and count `OVER_EXECUTE` — do
  not clamp silently.

A price change is *not* a modify: it loses queue priority, so it is a cancel followed by
an add under a new identity. ITCH `U` Replace does exactly this, and the reference number
changes with it — see `nasdaq-totalview-itch-feed-parsing`.

### 6. Classify a crossed or locked book

Best bid $\ge$ best ask covers *locked* (equal) and *crossed* (bid above ask). Before
treating either as corruption, confirm the guard applies:

- **One venue, continuous trading** → corruption. A resting bid at or above a resting ask
  cannot survive the matching engine.
- **Auction call phase** → normal. The uncross exists to "prevent a 'crossed' order book";
  during the call the book crosses and the venue publishes an indicative price and
  imbalance instead (Deutsche Börse Xetra, *Continuous trading with auctions*).
- **Consolidated multi-venue book** → a real market state, not corruption.

For the corruption case, the recovery is **flag, keep, reset, re-snapshot** — never *drop
the tick*. The crossing update is usually the symptom; the message that was actually lost
came earlier. Discarding the update that exposed the gap restores a book that looks fine
and is still wrong, with nothing downstream able to detect it. `reset()` clears the book
and deliberately keeps the violation counters, so recovering from a divergence does not
erase the record that it happened.

### 7. Compute the metrics

```
P_wmid = (V_ask_top * P_bid + V_bid_top * P_ask) / (V_bid_top + V_ask_top)
I      = (V_bid_N - V_ask_N) / (V_bid_N + V_ask_N)
```

The weighted mid uses **top-of-book** volumes; the imbalance aggregates up to $N$ levels
per side. Report the level count *actually* aggregated on each side together with both
aggregate volumes: a thin side contributes fewer levels than requested, and an imbalance
mixing two bid levels with five ask levels is a different statistic from one over five and
five. That asymmetry is real depth information — surface it rather than collapsing it to a
single number.

Guard the denominator by validating inputs, not by clamping. Volumes validated strictly
positive at ingress make the denominator provably positive for a non-empty book; an empty
book raises. Use `math.fsum` for the level sums — a few hundred float additions per tick
accumulate rounding that a compensated sum removes for free.

Return real metrics even when the book is crossed, with the flag set. A fabricated neutral
`imbalance_ratio = 0.0` is indistinguishable from a genuinely balanced book to a caller
that forgot to check `is_crossed`.

### 8. Audit before trusting the session

`integrity_violation_count == 0` is the condition for a clean replay. No exception raised
means only that no update was malformed.

## Failure Modes Observed in Production

- **Epsilon-clamped denominators.** `max(V_bid + V_ask, 1e-5)` reads as a
  division-by-zero guard and behaves as a rescaling whenever total volume is small. A
  \$100/\$101 book with $1\text{e-}6$ and $3\text{e-}6$ resting returns a weighted mid of
  $40.10$ and an imbalance of $-0.20$ instead of $100.25$ and $-0.50$ — a price outside
  the spread, with no exception and no NaN. Crypto books quoted in fractions of a coin hit
  this constantly; equity books in round lots never do, which is why it survives testing.
- **NaN prices disabling the crossed guard.** Every NaN comparison is `False`, so the
  guard reports *not crossed* on exactly the corrupt book it exists to catch.
- **Unrecognised side tokens.** `bids if side == "BUY" else asks` silently inverts the
  book for any feed that does not use that exact literal — which is most of them.
- **Un-synchronised race conditions.** Bid and ask queues mutated on separate threads
  without a shared lock, producing false crossed books that mask the real ones.
- **Dangling L3 order state.** Order ids never purged on cancel or fill leak across a
  multi-day session and, worse, let a later duplicate reference pass the uniqueness check.
- **Float price keys splitting a level.** Two decimal strings for the same tick can parse
  to adjacent doubles and become two levels that never merge and never fully delete. Where
  the venue publishes integer ticks, key on the integer.

## Production Implementation Reference

- Reference code: `scripts/depth_processor.py` — `L2L3DepthProcessor`, `DepthMetrics`,
  `BookSnapshot`, `DepthProcessorError`.
- Automated unit tests: `scripts/test_depth_processor.py` (42 tests, including explicit
  regressions for the epsilon clamp, NaN ingress, side routing, duplicate order ids,
  over-execution and negative `depth_levels`).
