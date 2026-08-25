# Pre-Flight Checklist — L3 Message-Log Order Book Reconstruction

## Feed mapping

- [ ] Does the source feed have a **separate total-deletion message** (ITCH `D`, LOBSTER 3),
      and is it mapped to `DELETE` rather than to `CANCEL`?
- [ ] Are `CANCEL` and `EXECUTE` quantities **deductions**, not absolute remaining sizes?
- [ ] Is `REPLACE` quantity the **new absolute** displayed total?
- [ ] Does `REPLACE` mint a **new order id**, and is it passed as `new_order_id`?
- [ ] Is the `REPLACE` side **inherited from the original order**, not read off the message?
- [ ] Are hidden-order executions, cross/auction prints and halts (LOBSTER 5/6/7,
      ITCH `P`/`Q`) **excluded** from the message stream fed to the engine?

## Configuration

- [ ] Does `price_scale` match the feed's price precision (10,000 for ITCH `Price (4)` and
      LOBSTER; 100 for a cent-quoted feed)?
- [ ] Has `strict` been chosen deliberately — `True` for a validated production replay,
      `False` for exploratory replay of an imperfect archive?
- [ ] Are prices passed in **currency units**, already divided by the feed's scale — and
      is `max_price` set to catch the case where they are not?
- [ ] Is `top_n_levels` set to the depth the strategy actually consumes?

## Data integrity

- [ ] Are messages ingested in **chronological order**, sorted on the correct key?
- [ ] Was `integrity_violation_count` checked **after** the replay, and is it zero — or has
      a non-zero count been explicitly accounted for before any statistic was reported?
- [ ] Have `UNKNOWN_ORDER` violations at the start of the window been distinguished from
      genuine message loss (a log sliced mid-session leaves orders resting from before it)?
- [ ] Are `OVER_CANCEL` / `OVER_EXECUTE` treated as hard evidence of dropped messages
      rather than as rounding noise?
- [ ] Is transport-layer sequence-gap detection running upstream, rather than relying on
      book mismatches to reveal loss?

## Book validity

- [ ] Are `is_crossed_book` and `is_locked_book` checked before `mid_price` or `spread`
      is consumed downstream?
- [ ] Is it understood that a crossed book yields a **negative** spread, which is still
      populated in the report?
- [ ] Are bids descending and asks ascending, with the BBO at index 0?

## Performance

- [ ] Is the L2 aggregation maintained incrementally rather than rebuilt per snapshot?
- [ ] Are order-id lookups O(1) hash-map operations?
- [ ] For a snapshot-per-message replay, has throughput been measured on a realistic book
      size rather than assumed?
