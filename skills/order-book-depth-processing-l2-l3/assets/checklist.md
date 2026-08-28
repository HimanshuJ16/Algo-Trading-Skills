# Pre-Flight / Sign-off Checklist — order-book-depth-processing-l2-l3

Use this before considering the skill's implementation complete.

## Ingress validation

- [ ] **Non-finite rejection:** NaN and $\pm\infty$ prices and sizes raise at ingress. Confirm
      by injecting a NaN price and checking the crossed-book guard did **not** report a
      clean book.
- [ ] **Price domain:** zero and negative prices raise.
- [ ] **Zero vs negative quantity:** an L2 quantity of exactly $0$ removes the level; a
      *negative* quantity raises rather than removing it.
- [ ] **Side vocabulary:** the venue's own tokens (`B`/`S` on ITCH, `buy`/`sell` on
      Coinbase) route to the correct book, and an unrecognised token raises instead of
      defaulting to a side.
- [ ] **Depth level count:** `depth_levels` of $0$, a negative, a float, a bool or a string
      raises — never returns a plausible number.
- [ ] **Batch atomicity:** a batch containing one bad field leaves the book unchanged.

## Book state

- [ ] **Thread-safety:** every bid/ask mutation and every read spanning both sides holds
      the same mutex.
- [ ] **No mutable public state:** `bids`, `asks` and `l3_orders` reject assignment from
      outside; strategy code consumes `get_snapshot()`, not the live views.
- [ ] **Crossed/locked guard:** `is_crossed` is `True` when best bid $\ge$ best ask, and the
      update that caused it returns `False` and increments `CROSSED_BOOK`.
- [ ] **Crossed guard applicability:** confirmed this is a single-venue continuous book —
      not a consolidated multi-venue book and not an auction call phase, both of which
      cross legitimately.
- [ ] **Recovery path:** a crossed book triggers `reset()` plus a fresh snapshot, and the
      offending tick is **not** silently dropped.

## L3 lifecycle

- [ ] **Aggregation:** adds and cancels re-aggregate price level volumes; the last order
      leaving a level removes the level.
- [ ] **Duplicate ids:** a repeated order id is refused and counted, leaving the level at
      its original size.
- [ ] **Unknown ids:** cancel, execute and modify against an unknown id are counted as
      `UNKNOWN_ORDER`, not silently ignored.
- [ ] **Execute vs modify:** executions deduct, modifies set an absolute size, and the two
      are not interchanged.
- [ ] **Over-execution:** flagged as `OVER_EXECUTE`, not clamped to zero.
- [ ] **No leaks:** the order map is empty after every resting order is cancelled or filled.

## Metrics

- [ ] **Weighted mid-price:** bid price carries the *ask* volume; result falls strictly
      inside the spread on an uncrossed book. Verified against a hand calculation, not
      against the implementation's own formula.
- [ ] **No epsilon-clamped denominators:** verified on a small-volume book — $1\text{e-}6$
      versus $3\text{e-}6$ must give a weighted mid of $100.25$ and an imbalance of $-0.5$,
      not $40.1$ and $-0.2$.
- [ ] **Imbalance bounds:** the ratio stays within $[-1.0, +1.0]$ and is $0$ for a
      symmetric book.
- [ ] **Aggregation disclosed:** the level count actually aggregated on *each* side and
      both aggregate volumes are reported with the ratio, so the consumer knows the weighted
      mid is a touch quantity, the imbalance is not, and whether a thin side made the two
      sides' depth unequal.
- [ ] **Crossed metrics gated:** consumers check `is_crossed` before using any field; the
      crossed case returns real values, not a neutral placeholder.

## Session audit

- [ ] **Integrity counters:** `integrity_violation_count == 0` before any statistic derived
      from the session is used. "No exception raised" is not the same check.
- [ ] **Automated testing:** run
      `python -m unittest discover -s skills/order-book-depth-processing-l2-l3/scripts`
      and confirm 42/42 pass.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Venue / feed level tested (L2 or L3, which venue): ___________________________
- Environment tested (replay/sandbox/live): ___________________________
