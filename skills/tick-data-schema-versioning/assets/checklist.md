# Pre-Flight / Sign-off Checklist — tick-data-schema-versioning

Complete before the first producer emits the new schema version.

## Change classification

- [ ] **Compatibility direction named.** The change is classified `BACKWARD`,
      `FORWARD`, `FULL` or breaking, using the Confluent taxonomy in
      `references/standards.md` §1 — not assumed.
- [ ] **Deploy order follows from it.** `BACKWARD` ⟹ consumers first;
      `FORWARD` ⟹ producers first. Written down, and the runbook matches.
- [ ] **Transitivity checked.** If any consumer is more than one version
      behind, a non-transitive compatibility guarantee does not cover it.
- [ ] **No in-place type change.** A field whose type changed was added as a
      new field; the old one is deprecated and its identifier reserved.

## Schema declaration

- [ ] **Every added field is optional with a `None` default.** No `0.0`, `""`,
      `"UNKNOWN"` or other sentinel that a consumer cannot distinguish from a
      real value.
- [ ] **Required fields are genuinely required.** A required field with no
      default breaks reader-side resolution for anyone on an older writer.
- [ ] **Schema registered before its adapters.**

## Adapters

- [ ] **Both directions registered.** Upgrade *and* downgrade, for every
      adjacent version pair in the live window.
- [ ] **No `.get(field, <numeric default>)` in any adapter.** The engine has
      already validated the source body; a defaulted read fabricates ticks.
- [ ] **Symbol untouched.** No case folding, trimming or namespace rewriting
      inside a version hop.
- [ ] **Unknown keys pass through.** A vendor extension survives a multi-hop
      chain — verified on an actual V1 → V3 payload, not assumed.
- [ ] **Known fields the target lacks are parked, not dropped.** A
      V3 → V1 → V3 round trip restores `exchange_id` and both sizes.
- [ ] **Every invented or narrowed value raises a note.** Synthesized,
      precision-reduced, default-applied, suspect.

## Numerics

- [ ] **No `int(seconds * 1e9)` anywhere.** Timestamp scaling goes through
      `Decimal` with round-half-to-even.
- [ ] **No `price > 0` used as a presence or validity test.** Negative outright
      prices are real (WTI, 2020-04-20); presence is a schema question.
- [ ] **Midpoint is `bid + (ask - bid) / 2`,** and a `0.0` bid is treated as a
      price, not as an absent field.
- [ ] **Consumers of an upgraded V1 timestamp know it is not nanosecond
      accurate** (~238 ns quantization at a present-day epoch). No
      tick-to-trade latency measurement is built on one.

## Runtime behaviour

- [ ] **Unversioned payloads are refused,** not defaulted to the oldest version.
- [ ] **Unknown / unreachable versions raise,** and the raw payload is never
      returned in their place.
- [ ] **Every schema error routes to a dead-letter path, not a retry.** These
      failures are deterministic; redelivery blocks the partition forever.
- [ ] **`has_synthesized_values` gates every spread, size and venue
      consumer.** A V1-sourced tick has a zero spread that is not a market.
- [ ] **No per-tick logging above DEBUG.** Warn once per distinct condition;
      `stats()` carries the volume.

## Rollout and retirement

- [ ] **`stats()` exported to monitoring,** with an alert on a hop counter that
      should be falling and is not.
- [ ] **Old version retired only after its hop counter reads zero for a full
      trading cycle** *and* the producer inventory confirms nobody emits it.

## Automated testing

- [ ] Run `python -m unittest discover -s skills/tick-data-schema-versioning/scripts`
      — 56/56 pass.
- [ ] Run `python tools/validate_skills.py` — no errors for this skill.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
