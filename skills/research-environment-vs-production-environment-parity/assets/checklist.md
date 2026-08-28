# Pre-Flight Checklist — Research → Production Promotion Gate

Sign off before a model, alpha signal or feature pipeline is promoted from research into
the live execution path. Re-run it after any material change to either side.

## Collection (the audit is only as good as this)

- [ ] Both snapshots were read from the **live targets** — the production container or
      host, and the research environment the backtest actually ran in — not transcribed
      from a lockfile or an environment template describing them.
- [ ] The `PRODUCTION` snapshot names which host, image or service it describes, and
      that is recorded with the report.
- [ ] No collection step returned a blank or defaulted value. A field that could not be
      resolved failed the promotion step; nothing was filled in with `"unknown"`.
- [ ] `python_version` is a full `major.minor.patch` release — not `3.11`.
- [ ] `package_versions` and `feature_definitions` are both non-empty, and both sides
      were enumerated from the **same** tracked distribution list, so a one-sided
      install shows up as `NOT_INSTALLED` rather than as a silent omission.
- [ ] `feature_definitions` values are digests of the **deployed artifact**, not entries
      from a hand-maintained registry.
- [ ] `float_precision` states what the pipeline actually computes in, not what the
      language defaults to. It is a specific format, not bare `float`.
- [ ] `env_type` is exactly `RESEARCH` and `PRODUCTION`, passed in that argument order.

## Audit

- [ ] CPython release matches. If only the patch differs, the warning was read and a
      decision recorded — not skipped because it did not block.
- [ ] Every package discrepancy was triaged to one of: production intentionally ahead
      (research revalidated on the new pin), research drifted (rebuilt from the
      lockfile), or neither side under change control (fixed first).
- [ ] No package was removed from `numerically_critical_packages` to silence an alert
      without a recorded reason why it cannot move a number.
- [ ] Declared floating-point precision matches, or the mismatch was resolved — not
      waived because signal diffing happened to pass on today's sample.
- [ ] Every feature is defined on both sides with identical definitions. For any feature
      that was one-sided, what the serving path does with the absent column was
      confirmed, not assumed.

## Shadow diffing

- [ ] Both models were run on **identical** inputs and the outputs paired positionally.
- [ ] The sample includes illiquid instruments, halted and auction sessions, the first
      bars after a corporate action, and newly listed instruments with insufficient
      history — not only mid-session large-cap bars.
- [ ] `signal_diffing_performed` is `True`. A `PARITY_VERIFIED` verdict from a
      static-only audit was not read as signal parity.
- [ ] No sample carried a NaN or infinity. Any that did was treated as a computation
      failure to fix, not a tolerance to widen.
- [ ] `max_signal_rel_diff` and `signal_abs_tol` are the values this desk agreed to, and
      neither was loosened to make this promotion pass.
- [ ] If `discrepancies_truncated` is `True`, the exact counts were read from
      `signal_breach_count` rather than from the length of the list.

## Gate

- [ ] The promotion decision was taken on `is_parity_achieved`, not on a discrepancy
      count or a pass rate.
- [ ] `warning_discrepancies` was read and each warning has an owner or an explicit
      accept.
- [ ] The audit ran in CI **and** again on the production host before the strategy was
      enabled.
- [ ] The report is retained against this promotion, timestamped, for change-control and
      incident reconstruction.
- [ ] A re-audit is scheduled for the next material change on either side — a dependency
      bump, a retrained model, a redefined feature.
