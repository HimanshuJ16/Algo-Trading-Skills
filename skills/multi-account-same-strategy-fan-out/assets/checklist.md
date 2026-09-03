# Pre-Flight / Sign-off Checklist — multi-account-same-strategy-fan-out

Use this before considering the skill's implementation complete.

## Allocation correctness

- [ ] **Sum Invariance:** Confirm per-account quantities sum to the master quantity exactly. Test the cases naive rounding fails: 3 equal accounts × 10 shares → `{4,3,3}` = 10 (not 9); 4 equal accounts × 6 shares → `{2,2,1,1}` = 6 (not 8).
- [ ] **No Per-Account `round()`:** Confirm no code path computes `round(Q * w_i)` independently per account. Python's `round()` is round-half-to-even, so equally-entitled accounts receive different quantities.
- [ ] **Deterministic Tie-Break:** Confirm the remainder tie-break is reproducible from the recorded bases and does not depend on dict insertion order.
- [ ] **Basis Snapshot:** Confirm NAVs/weights are snapshotted once per batch, not read live during apportionment.

## Minimum-quantity policy

- [ ] **Floor Excludes, Never Inflates:** Confirm no `max(min_order_qty, share)`. An account entitled to less than the floor is dropped and its shares re-apportioned.
- [ ] **Redistribution Loop:** Confirm dropping a below-floor account triggers re-apportionment across survivors, repeated until stable.
- [ ] **Shortfall Surfaced:** Confirm that when every account falls below the floor, nothing is allocated, `is_fully_allocated` is false, and the caller is warned — the shortfall is *not* traded.
- [ ] **No Phantom Shorts:** Confirm a SELL fan-out never issues an order to an account whose entitlement is zero.

## Order identity and idempotency

- [ ] **Deterministic Client Order IDs:** Confirm re-running a batch with the same `batch_id` reproduces byte-identical IDs, so an ambiguous-timeout retry cannot double-execute.
- [ ] **Uniqueness Within and Across Batches:** Confirm IDs are unique across all sub-accounts in a batch and do not collide between batches or after a process restart.
- [ ] **Thread Safety:** Confirm no unlocked shared counter backs ID generation, and that 32 concurrent fan-outs produce no duplicate IDs.
- [ ] **Venue ClOrdID Limits:** Confirm the generated ID length and character set are accepted by every target broker/venue.

## Input validation

- [ ] **Direction in `action`, Not Sign:** Confirm negative or zero master quantities are rejected rather than fanned out.
- [ ] **Order Type Coherence:** Confirm a LIMIT order requires a positive finite limit price and a MARKET order rejects one.
- [ ] **Registry Integrity:** Confirm duplicate account registration raises rather than silently overwriting a NAV, and that non-finite or non-positive NAVs are rejected.
- [ ] **Exit Basis:** Confirm position unwinds allocate on held quantity (`EXPLICIT_WEIGHT`), not NAV, and that a missing weight raises rather than falling back to NAV.

## Allocation audit trail

- [ ] **Per-Order Basis Retained:** Confirm `allocation_basis`, `allocation_weight`, `exact_quantity` and `received_remainder_share` are persisted alongside fills, so the split can be independently re-derived (17 CFR 1.35(b)(5)(iv)(C), (b)(5)(v) for in-scope US futures activity).
- [ ] **Remainder Skew Monitored:** Confirm `received_remainder_share` is tracked over time; stable NAVs send the leftover share to the same accounts every batch (17 CFR 1.35(b)(5)(iv)(B)).
- [ ] **Bunched-Order Alternative Considered:** Confirm a decision was recorded on whether a bunched order with average pricing (CME Rule 553; IBKR `faGroup`/`faMethod`) is available and preferable to N separate per-account orders.
- [ ] **Allocation Policy Documented:** Confirm the method, the floor policy, and the tie-break are written into the firm's allocation policy and disclosed to clients where required.

## Testing

- [ ] **Automated Testing:** Run `python -m unittest discover -s skills/multi-account-same-strategy-fan-out/scripts` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
