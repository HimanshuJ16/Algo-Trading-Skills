# Pre-Flight Checklist

## Before simulating at all
- [ ] Has the broker/venue been checked for a native equivalent of this trigger?
- [ ] Is it accepted that the trigger will NOT fire if this process, its host, or its feed is down?
- [ ] Is there monitoring that notices a dormant trigger whose inputs have gone quiet?

## Condition tree
- [ ] Does every price condition declare its trigger price type (`last` / `bid` / `ask` / `mid`) deliberately?
- [ ] Are Price, Volume, Time and Cross-Asset atomic nodes composed only with validated `AND` / `OR` / `NOT` gates?
- [ ] Are unsupported operators rejected at construction rather than silently evaluating to false?
- [ ] Is every `'=='` comparison given an explicit tolerance band?
- [ ] Are empty composite gates rejected (an empty `AND` is vacuously true)?
- [ ] Are `TimeCondition` targets timezone-aware, and does the whole tick share one pinned clock?

## Data integrity
- [ ] Do missing, non-numeric, non-finite or stale inputs evaluate to UNKNOWN rather than to `False`?
- [ ] Is `max_quote_age_seconds` set on every cross-asset / benchmark leg, sized from that feed's observed cadence?
- [ ] Does each quote carry a timestamp when staleness enforcement is enabled?
- [ ] Has `NOT` over a missing input been tested to confirm it does not fire?

## Firing and lifecycle
- [ ] Is the `DORMANT` → `TRIGGERED` transition performed under the same lock as the evaluation?
- [ ] Has concurrent tick delivery been tested to confirm exactly one child order is released?
- [ ] Do OCO / bracket siblings get cancelled before the next tick is processed?
- [ ] Does cancelling an already-fired trigger report failure rather than silently succeeding?
- [ ] Is the child order payload validated (side, quantity > 0, limit price present) at registration, not at fire time?
- [ ] Does the released payload still pass pre-trade risk control before routing?
