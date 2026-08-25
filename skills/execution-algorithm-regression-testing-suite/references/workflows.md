# Workflows for Execution Algo Regression Testing Suite

1. **Scenario Suite Ingestion**:
   - Ingest the standardised historical market test scenarios. The default required set is
     `NORMAL_VOLATILITY`, `VOLATILITY_SHOCK`, `LIQUIDITY_CRUNCH`; override
     `required_scenario_names` to match your own taxonomy, and treat the list as a
     fail-closed contract rather than documentation.
   - Assign each scenario a unique `scenario_id`. Duplicates are rejected: a report line
     that cannot be traced back to the replay that produced it is not an audit trail.

2. **Dual-Version Replay**:
   - Replay the baseline production build and the candidate build over identical scenario
     inputs, in an environment separated from production (RTS 6 Art. 7(1)).
   - Record per scenario: Implementation Shortfall in bps (signed as a **cost** — larger is
     worse), fill rate as filled/ordered quantity in $[0, 1]$, and peak participation as a
     fraction of market volume.
   - If the candidate fails to complete a scenario, record what it produced. Never drop the
     scenario — coverage enforcement exists precisely to stop a suite from shrinking to the
     scenarios the candidate happened to survive.

3. **Harness Validation**:
   - `run_regression_suite` raises `ValueError`/`TypeError` for an empty suite, blank or
     duplicate ids, rates outside $[0, 1]$, a `baseline_fill_rate` of zero, and non-numeric
     metrics. These are harness defects; a mis-wired harness must not emit a verdict.
   - A **non-finite** Implementation Shortfall is handled differently: it is recorded as a
     scenario failure with an explicit reason, because it is a symptom of the candidate
     build and belongs in the report rather than in a stack trace.

4. **Metric Comparison** (per scenario, exact comparison, rounded display):
   - $\Delta \text{IS} = \text{IS}_{\text{cand}} - \text{IS}_{\text{base}}$ against
     `max_allowed_is_degradation_bps`.
   - $\text{FillRate}_{\text{cand}} / \text{FillRate}_{\text{base}}$ against
     `min_allowed_fill_ratio`.
   - Peak participation against `max_allowed_participation_rate`.
   - All three are evaluated for every scenario, so a single report lists every reason a
     build was rejected rather than only the first.

5. **Coverage Evaluation**:
   - Scenario names are matched case-insensitively after trimming.
   - A missing required kind sets `coverage_satisfied = False`, populates
     `missing_required_scenarios`, and rejects the build even when
     `scenarios_failed_count == 0`.

6. **CI/CD Gate Action**:
   - Branch on `cicd_gate_status` only. Never infer approval from `scenarios_failed_count`,
     from `avg_is_degradation_bps`, or from the absence of an exception.
   - `FAIL_REGRESSION_REJECTED` blocks the release; `PASS_REGRESSION_APPROVED` permits the
     controlled, staged deployment required by RTS 6 Art. 8 — it does not authorise a
     full-population rollout by itself.

7. **Audit Retention**:
   - Persist the full `RegressionTestSuiteAuditReport` against the build version, including
     the per-scenario `is_degradation_bps` and `fill_rate_ratio`,
     `worst_is_degradation_bps`, and `missing_required_scenarios`.
   - Retain the scenario inputs alongside it. A venue requiring pre-deployment testing
     certification also requires the member to explain the means used (RTS 7 Art. 10), and
     a boolean verdict does not explain anything.
