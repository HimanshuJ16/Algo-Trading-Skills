# Pre-Flight Checklist — Universe Scale-Up

## Inputs are measured, not assumed
- [ ] `avg_ticks_sec_per_symbol` measured on the current universe, not left at the default.
- [ ] `peak_volatility_multiplier` derived from an observed peak-to-average ratio taken on a
      sub-second window (100 ms / 10 ms), not a one-second average.
- [ ] `bytes_per_tick` measured from real payloads on the feed you actually consume.
- [ ] `memory_mb_per_orderbook` measured by RSS delta or `tracemalloc` at steady-state depth.
- [ ] `db_write_fraction` and `ticks_per_write_io` reflect the real persistence path and its
      batching, not one-IO-per-tick.
- [ ] Provenance recorded for each number above (who measured it, when, on what build).

## Projection is honest about what it does not model
- [ ] `wire_overhead_factor` set from a per-packet framing model (A/B feeds, batching,
      retransmits) — a value of 1.0 charges payload only and under-states wire bandwidth.
- [ ] CPU core headroom sized separately; this skill does not model it.
- [ ] Per-symbol rates checked for skew — a single uniform average understates the hot names.

## Gate configured and read correctly
- [ ] `max_safe_utilization_pct` chosen deliberately and the reason recorded; 80% is a
      heuristic, not a standard, and is too loose for second-averaged inputs.
- [ ] `breached_resources` reviewed in full, not just `status` — there is usually more than one.
- [ ] Non-positive or non-finite inputs rejected rather than silently projected.

## Verified against reality before scaling
- [ ] Captured market data replayed end-to-end at the projected peak message rate.
- [ ] Replay hit the real database, not a cache in front of it.
- [ ] Queue depth, dropped sequence numbers and GC pauses observed during the replay.
- [ ] Instruments added in tranches, with utilization re-checked at each tranche.

## Regulatory (EU/EEA investment firms)
- [ ] Stress test level reconciled against RTS 6 Art. 10 — twice the highest volume observed
      in the previous six months, which is not the same as a multiple of the average.
- [ ] Scope change into new instruments/asset classes logged as a material change and the
      retest evidenced. See `references/standards.md` for applicability by jurisdiction.
