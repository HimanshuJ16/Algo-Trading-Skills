# Pre-Flight / Sign-off Checklist - post-breach-root-cause-analysis-template

Use before signing off a post-breach RCA.

## Evidence and timestamps

- [ ] **Containment confirmed** before the RCA was written - the position stopped changing,
      not merely the ticket closed.
- [ ] **Evidence preserved** ahead of log-retention rollover: order logs, drop copies, risk
      gateway config history, deployment records.
- [ ] **`detected_at` is the moment of reasonable basis to conclude a breach occurred**, not
      the moment of escalation or ticket creation.
- [ ] **Every timestamp is timezone-aware.** The engine rejects naive datetimes rather than
      assuming UTC - a clean run is the evidence.
- [ ] **Clock provenance recorded** in `TimelineEvent.source` for every event, and
      `timeline_clock_sources` on the report has been reviewed: do you trust the ordering
      these clocks imply?

## Financial impact

- [ ] **`financial_loss_usd` and `unauthorized_turnover_usd` supplied as non-negative
      magnitudes** (a 25,000 USD loss is `25000.0`).
- [ ] **Figures reconciled against the books** by whoever owns the P&L. The engine validates
      them; it does not compute or verify them.
- [ ] **`0.0` used deliberately** where there was genuinely no realised loss or no
      unauthorised turnover - not as a placeholder for "unknown".

## Analysis

- [ ] **5-Whys depth meets `min_five_whys_depth`**, and the depth is real: no blank or
      placeholder entries (the engine raises on blanks).
- [ ] **The final "why" names a control that failed, not a person.** If
      `TERMINAL_BLAME_ATTRIBUTION` fired, it has been reviewed by a human - and if it did not
      fire, that is not proof the chain is blameless. The heuristic is a substring match.
- [ ] **Multi-causal contributors captured**, not compressed into one chain for the template's
      convenience.
- [ ] **The chronology is complete enough to be falsifiable** - a reader can check the causal
      story against the logs.

## CAPA

- [ ] **Every action item has a named owner and a due date.** `unassigned_action_items == 0`.
- [ ] **At least one item is typed `PREVENTIVE`**, or the absence has been consciously
      accepted. `has_preventive_action` shows which.
- [ ] **Items are tracked to closure** in the firm's issue tracker. The engine checks they
      exist, never that they were done.

## Compliance

- [ ] **`possible_rule_violation` set explicitly to `True` or `False`.** `None` blocks, and
      correctly so - this determination is not something to leave defaulted.
- [ ] **If `True`: Compliance engaged before finalising.** For a FINRA member, an internal
      conclusion that a violation occurred starts the Rule 4530(b) 30-calendar-day clock.
- [ ] **Applicable deadline identified for this entity and jurisdiction** and, if one applies,
      passed as `rca_due_by`. The engine will not invent a deadline. See
      `references/standards.md` for what binds whom.
- [ ] **No regulatory claim overstated in the document.** SEC Rule 15c3-5 does not mandate an
      incident RCA; FINRA Rule 4511 governs preservation, not creation; Reg SCI applies to SCI
      entities only.
- [ ] **Retention arranged.** The RCA is a discoverable business record - for a FINRA member,
      at least six years under Rule 4511(b) absent a more specific period, in an SEA Rule
      17a-4 compliant format.

## Report review

- [ ] **`validation_findings` read in full**, not just `status`. `status` reports one finding.
- [ ] **`is_valid_rca is True`**, or the remaining gaps are explicitly accepted and recorded.
- [ ] **Engine parameters archived with the report**: `min_five_whys_depth`,
      `require_preventive_action`, and the `rca_due_by` used. A threshold-dependent audit is
      not reproducible without them.
- [ ] **`generated_at` recorded** - the output is deterministic, so this value plus the spec
      reproduces the document exactly.

## Automated testing

- [ ] Run `python -m unittest discover -s skills/post-breach-root-cause-analysis-template/scripts` -
      52 tests, 100% pass rate.

## Sign-off

- RCA author: ___________________________
- Reviewed by (independent of the incident): ___________________________
- Compliance sign-off (if `possible_rule_violation` is `True`): ___________________________
- Date: ___________________________
