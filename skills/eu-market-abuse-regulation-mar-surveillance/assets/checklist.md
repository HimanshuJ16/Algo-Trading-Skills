# Pre-Flight / Sign-off Checklist — eu-market-abuse-regulation-mar-surveillance

## Scope
- [ ] The venue and instruments are in EU/EEA MAR scope (not UK MAR — the FCA is not an EU NCA; not crypto-assets — that is MiCA Article 92).
- [ ] The filing entity's role is identified: trading venue operator (MAR Art. 16(1)) or person professionally arranging/executing transactions (MAR Art. 16(2)).

## Input data
- [ ] Order cancellations and modifications are ingested alongside executions — filled trades alone cannot show layering.
- [ ] Each batch contains the full order lifecycle; `unmatched_cancels` is monitored and does not drift upward.
- [ ] `timestamp_ns` is nanoseconds since the Unix epoch, UTC, from clocks synchronised to the applicable standard (RTS 25 / Delegated Regulation (EU) 2017/574 for MiFID II venues and members).
- [ ] Event ids are unique — replayed events are rejected, not silently double-counted.
- [ ] Every event identifies its instrument by ISIN (or at minimum a symbol).

## Beneficial ownership
- [ ] `beneficial_owner_map` maps every sub-account onto its owning entity.
- [ ] Give-up, allocation and internal booking representations that place one account on both sides have been reviewed, so they are not escalated as wash trades.

## Detection parameters
- [ ] The cancel ratio, fast-cancel lifespan and message-rate thresholds have been **calibrated** per venue and instrument liquidity tier, and the rationale recorded — they are library defaults, not regulatory values.
- [ ] The quote-stuffing threshold sits above the quoting rate a designated market maker needs to meet its obligations in the covered names.
- [ ] `require_opposite_side_fill` is set deliberately: `True` for the strict Annex II spoofing shape, `False` to also see pure layering.
- [ ] `min_orders_for_cancel_ratio` is high enough that the ratio carries evidential weight.

## Alert handling
- [ ] Every alert is triaged by a human before escalation — Delegated Regulation (EU) 2016/957 requires an appropriate level of human analysis.
- [ ] No path in the system auto-submits an alert as a STOR.
- [ ] STOR content is transposed into the harmonised template (Annex to Delegated Regulation (EU) 2016/957) and sent through the NCA's own channel, with enrolment completed in advance.
- [ ] Where reasonable suspicion is formed, the report goes **without delay** — reports are never batched up for convenience.

## Record keeping
- [ ] Alerts, `detection_parameters`, triage reasoning and the file / do-not-file decision are retained for **five years** and can be produced to the competent authority on request.
- [ ] Cases analysed but **not** reported are retained too, with the reasons.

## Testing
- [ ] Automated testing: `python -m unittest discover -s skills/eu-market-abuse-regulation-mar-surveillance/scripts` — 100% pass rate.
- [ ] Threshold-boundary behaviour re-verified after any recalibration (at-threshold, just-below, slow-cancel, and burst-straddling-a-second cases).

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
