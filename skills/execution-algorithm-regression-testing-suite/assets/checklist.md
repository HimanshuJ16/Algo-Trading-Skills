# Pre-Flight Checklist

## Suite configuration
- [ ] Are standardized market scenario backtests configured in the CI/CD pipeline?
- [ ] Is `required_scenario_names` set to the scenario kinds this desk must cover, so a
      suite missing a stress scenario is rejected rather than silently approved?
- [ ] Have the thresholds ($\Delta \text{IS}_{\text{max}}$, min fill ratio, max
      participation) been calibrated and signed off for these instruments — rather than
      inherited from the library defaults and cited as if they were regulatory limits?
- [ ] Is `*_is_bps` signed as a **cost** (larger = worse) in the replay harness?

## Replay integrity
- [ ] Do baseline and candidate replay identical scenario inputs?
- [ ] Does the replay run in an environment separated from production (RTS 6 Art. 7(1))?
- [ ] Does every scenario carry a unique `scenario_id`, and does the harness report
      scenarios the candidate failed to complete rather than dropping them?
- [ ] Is `baseline_fill_rate` strictly positive for every scenario?

## Gate behaviour
- [ ] Does the pipeline branch on `cicd_gate_status` only — never on
      `scenarios_failed_count`, `avg_is_degradation_bps`, or absence of an exception?
- [ ] Does a raised `ValueError`/`TypeError` from the harness fail the build rather than
      being caught and ignored?
- [ ] Is a non-finite metric confirmed to reject the build (fail-closed), not abstain?
- [ ] Is `worst_is_degradation_bps` reviewed, not just the average?

## Audit trail
- [ ] Is the full `RegressionTestSuiteAuditReport` persisted against the build version,
      with the scenario inputs retained alongside it?
- [ ] Is the release record free of claims that a passing gate evidences MiFID II RTS 6
      conformance or SEC Rule 15c3-5 compliance?

## Scope boundaries acknowledged
- [ ] Is disorderly-trading testing (RTS 6 Art. 5(4)(d)) covered elsewhere — e.g. a dynamic
      testing environment with multiple algorithms in a shared order book?
- [ ] Is the RTS 6 Art. 10 annual stress test (previous six months' peak messaging and
      trade volumes $\times\,2$) run separately from this per-build gate?
- [ ] Is deployment after a PASS staged/controlled (RTS 6 Art. 8) rather than
      full-population?
