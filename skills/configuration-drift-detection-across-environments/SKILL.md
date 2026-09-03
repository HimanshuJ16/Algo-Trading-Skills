---
name: configuration-drift-detection-across-environments
description: >-
  Use before a configuration tree takes effect somewhere that can send orders, to prove
  it matches the approved baseline; catches missing keys, changed values and silent type
  changes carried over from a debugging session.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: deployment-ops
  tags: configuration-drift, deployment-ops, env-parity, golden-source, pre-trade-gate, risk-control
  brokers_frameworks: ""
  version: "1.1.0"
  author: algo-trading-skills-contributors
---

## When to Use

Invoke this skill when a configuration tree is about to take effect in an environment
that can send orders, and you need positive evidence that it matches the baseline someone
actually approved. The failure it prevents is specific: a risk parameter edited in
staging for a debugging session and carried into production, a `broker_endpoint` still
pointing at sandbox after a promotion, a `max_order_usd` that arrived as the string
`"100000"` and silently disabled a numeric comparison downstream.

Use it in two places, not one: as a CI/CD gate before an artifact is promoted, and again
during process initialization before the trading socket opens. The first catches the
mistake earlier; the second is the one that actually protects live capital, because it
runs on the host and against the file the engine will really read.

## When NOT to Use

Do **not** use it when:

- **The two environments are supposed to differ structurally.** This detector compares
  one tree against one baseline. A research sandbox with a different schema entirely will
  produce noise, not signal — see `research-environment-vs-production-environment-parity`
  for that comparison.
- **You need to know what changed over time in one environment.** This is a two-way
  comparison at a point in time, not a change log. Use
  `audit-logging-for-configuration-changes` for the who/when/why record.
- **You have not decided which config is authoritative.** If two candidate baselines
  exist, the audit result is arbitrary. Designate the Golden Source first
  (`reference-data-golden-source-designation`).
- **What you actually need is approval workflow.** Detecting that a risk limit drifted is
  not the same as governing who may change it; see
  `risk-control-configuration-change-approval-workflow`.
- **The value you care about is not in the config file.** A limit injected at runtime by
  a feature-flag service or mutated by an operator after startup is invisible to this
  audit, which compares configuration to configuration.

## Prerequisites

- A single designated Golden Source configuration tree, version-controlled, loaded as a
  dict.
- The target environment configuration, loaded from the same source the trading process
  will actually read — not a copy prepared for the audit.
- An explicit override policy, split two ways:
  - `allowed_overrides` — connectivity, naming and logging keys that legitimately differ
    (`env_name`, `api_url`, `broker_endpoint`, `log_level`, `port`, `host`, `db_name`).
    Pass an empty set for zero-tolerance auditing.
  - `protected_keys` — risk-control parameters that may never be whitelisted. The
    built-in `DEFAULT_PROTECTED_KEYS` is a starting point keyed on leaf name; extend it
    with the risk parameters your own schema uses.
- A caller that treats `is_compliant is False` as a hard stop, with the authority to fail
  the pipeline or abort startup.

## Workflow

1. **Ingest both trees.** Load `golden_baseline` and `target_config` as dicts. Non-dict
   input raises `TypeError` rather than being audited partially.
2. **Flatten to dot-separated paths.** Nested dicts collapse to `system.api_url`. Two
   distinct keys that flatten to the same path (a literal `"a.b"` alongside a nested
   `{"a": {"b": ...}}`) raise `ValueError` — one branch would otherwise be dropped from
   the audit and real drift could surface as a PASS. An empty nested dict is kept as a
   leaf so a whole missing section is still reported.
3. **Classify every key in the union of both trees:**
   - Key in baseline, **missing** from target → `CRITICAL`. This holds even for
     whitelisted keys: an override may change a value, not delete a setting the engine
     expects to read.
   - Value or type differs on a **protected** key → `CRITICAL`, regardless of the
     whitelist.
   - Value or type differs on a **whitelisted, non-protected** key → `ALLOWED`.
   - Value or type differs otherwise → `CRITICAL`.
   - Key in target, **absent** from baseline → `WARNING`.
4. **Decide.** `critical_drift_count > 0` sets `is_compliant = False`. Block the
   promotion or abort initialization before the socket opens — do not connect first and
   alert afterwards. On a compliant report with warnings, proceed but route the WARNING
   items to human review.
5. **Record.** Persist `report.drift_items` to the deployment audit log alongside the
   authorizing person. For firms in scope of MiFID II RTS 6 this record supports the
   Art. 5(7) and Art. 11 obligations — see `references/standards.md` for what those
   articles do and do not require.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Whitelisting a risk parameter to silence a noisy alert.** Adding `max_position_size`
  to `allowed_overrides` does not fix the drift; it disables the gate on exactly the
  value the gate exists to protect. The detector refuses this at construction time, but
  the same instinct reappears as "let me just widen `protected_keys`'s exceptions" —
  resolve the drift instead.
- **Assuming an empty whitelist means strict.** It does now, and that is a fix, not a
  given: this detector previously treated `allowed_overrides=set()` as falsy and
  substituted the permissive built-in default, turning the strictest possible request
  into the loosest available policy. If you have forked or reimplemented this pattern,
  check that line.
- **Reading a PASS as "the environments are identical."** Extra keys in the target are
  `WARNING` and never block, because the baseline cannot know what a legitimately-added
  key means. A config with an unreviewed extra flag passes this audit.
- **Whitelisting by bare leaf name when you meant one section.** `api_url` in
  `allowed_overrides` whitelists that name *everywhere* in the tree, including under a
  section you never intended to relax. Use the exact path `system.api_url` when the
  override should be scoped.
- **Ignoring type coercion drift.** `max_order_qty` as the string `"100"` instead of the
  integer `100` compares as drift here precisely because it will not compare as a number
  downstream. Do not "fix" a type-mismatch alert by relaxing the comparison.
- **Post-startup auditing.** Running the check after the trading engine is live reports a
  breach rather than preventing one.
- **Auditing a file the engine never opens.** A pipeline that audits `prod_baseline.json`
  from the repo while the host loads a locally-templated config proves nothing about the
  host.

## Verification

- Instantiate `ConfigurationDriftDetector` with `allowed_overrides={"api_url"}`. Feed a
  Golden Source config and a target whose `max_order_usd` is 1,000,000 against a baseline
  of 100,000: the report must show `CRITICAL` severity, the description must name it as a
  protected risk-control parameter, and `is_compliant` must be `False`. Change only
  `api_url` instead: `allowed_override_count` must be 1 and `is_compliant` `True`.
- Confirm the gate cannot be disabled: `ConfigurationDriftDetector(allowed_overrides={"max_position_size"})`
  must raise `ValueError`.
- Confirm zero tolerance is honoured: `ConfigurationDriftDetector(allowed_overrides=set())`
  must flag a `broker_endpoint` change as `CRITICAL`, not `ALLOWED`.
- Run `python -m unittest discover -s skills/configuration-drift-detection-across-environments/scripts`
  and confirm all 28 tests pass.

## Related Skills

- `research-environment-vs-production-environment-parity`
- `environment-parity-dev-staging-production`
- `blue-green-deployment-for-live-strategy-updates`
- `audit-logging-for-configuration-changes`
- `risk-control-configuration-change-approval-workflow`
- `reference-data-golden-source-designation`
