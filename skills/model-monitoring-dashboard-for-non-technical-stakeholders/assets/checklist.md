# Pre-Flight / Sign-off Checklist — model-monitoring-dashboard-for-non-technical-stakeholders

## Bands and configuration

- [ ] Accuracy, model-age and latency bands calibrated to **this** strategy and the
      rationale recorded alongside the model card. The defaults (55%, 14/30 days) are
      library conventions with no external authority.
- [ ] Accuracy floor derived from the strategy's payoff profile
      ($p^{*} = (L + c)/(W + L)$), not assumed to be 50% "break-even".
- [ ] PSI bands understood as the Lewis (1994) rule of thumb — no controlled error
      rate, and power that *decreases* with sample size.
- [ ] Inference-latency budget configured (`latency_green_max_ms`,
      `latency_amber_max_ms`) **or** `monitor_latency=False` set deliberately to
      declare latency out of scope. Confirm no dashboard is running with latency
      neither budgeted nor explicitly excluded.
- [ ] Incoherent threshold configuration confirmed to raise `DashboardConfigError`
      at construction time.

## Input contract

- [ ] Accuracy supplied in **percentage points** (58.5, not 0.585) and computed
      **out-of-sample**.
- [ ] Model age derived from the model registry's recorded training timestamp, not a
      file mtime that a redeploy resets.
- [ ] Feature-drift PSI is the per-feature **maximum**, not the mean.
- [ ] Latency is the **p99**, not the mean.
- [ ] A metric that is unavailable this cycle is passed as `None`, never as `0.0` or
      a last-known value.

## Behaviour

- [ ] Overall status is the **worst** component, verified against a snapshot where
      one component is RED and the rest are GREEN.
- [ ] No component grades GREEN without being measured; unmeasured components report
      `measured=False` and `value=None`.
- [ ] Impossible telemetry (negative age, negative PSI, accuracy outside $[0, 100]$,
      `NaN`/$\pm\infty$) raises `DashboardInputError`.
- [ ] `DashboardInputError` is escalated as a monitoring failure — confirm no caller
      swallows it and renders a blank or stale tile.
- [ ] A missing metric recommends `RESTORE_MODEL_TELEMETRY`, never
      `SCHEDULE_RETRAIN_AND_REVIEW`.
- [ ] The RED headline names the breaching components.

## Process around the dashboard

- [ ] The recommended action is presented as advisory; the runbook names who executes
      a halt and by what mechanism (`kill-switch-and-drawdown-circuit-breakers`).
- [ ] `SCHEDULE_RETRAIN_AND_REVIEW` routes to a change request, not to an automated
      retrain-and-deploy.
- [ ] Escalation and acknowledgement path defined
      (`risk-limit-breach-escalation-matrix`).
- [ ] Reports retained via `to_dict()` as evidence for the RTS 6 Article 9 annual
      self-assessment and validation, where applicable to the entity.
- [ ] Expected RED frequency reviewed — if the staleness band makes
      `HALT_TRADING_IMMEDIATELY` a routine weekly event, recalibrate before the
      recommendation stops being read.
- [ ] The independent risk-control reader has confirmed they can act on the report
      without needing the raw statistics explained to them.

## Automated testing

- [ ] Run `python -m unittest discover -s skills/model-monitoring-dashboard-for-non-technical-stakeholders/scripts`
      — 45 tests, 100% pass rate.
- [ ] Run `python tools/validate_skills.py` — structural and cross-reference
      validation passes.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
