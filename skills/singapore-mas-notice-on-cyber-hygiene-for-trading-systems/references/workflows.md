# Workflows for the MAS Notice on Cyber Hygiene (Trading Systems)

Every step runs on every asset. Nothing short-circuits: an asset can breach
several requirements at once, and remediation needs the full list. `is_compliant`
is the only figure with regulatory meaning — every requirement in the Notice is
mandatory.

## 0. Reject structurally invalid input

Raise before auditing. These are caller bugs, not compliance outcomes, and must
never be reported as a clean audit.

- Blank `system_id`, `system_name` or `asset_type`.
- A vulnerability with a blank `vulnerability_id` or a severity outside the
  firm's taxonomy.
- A negative `days_since_patch_released` — it compares below every deadline and
  would pass silently.
- A boolean `days_since_patch_released` — `True` is an `int` in Python and would
  read as one day.
- `open_vulnerabilities` supplied as a list rather than a tuple, or containing
  anything but `OpenVulnerability` instances. An audited asset that the caller
  can mutate after the audit is not an audit record.
- Duplicate `vulnerability_id` values in one asset.
- An `entity_class` outside `CYBER_HYGIENE_NOTICE_BY_ENTITY_CLASS`.
- A missing or wrongly typed `PatchRemediationPolicy`.

## 1. Inventory and scope the estate

- Inventory every trading system host: order routers, market data gateways,
  execution engines, trade and reference databases, colocated hosts, and the
  network devices and security appliances in their path. Paragraph 4.1 names
  five layers — operating system, database, application, security appliance,
  network device — and the estate must cover all of them.
- For each asset, determine **criticality**: does its failure cause significant
  disruption to the entity's operations, or materially impact service to
  customers? An order router and a matching-engine gateway almost always
  qualify; a research host may not.
- For each asset, determine whether it is **used to access customer information
  through the internet**. This is a separate question from criticality and
  brings a different MFA limb into play.
- Leave either determination as `None` only when it is genuinely unknown. The
  engine then audits conservatively and records a warning.

## 2. Fix the applicable notice

Resolve `entity_class` to its notice number:

- `CAPITAL_MARKETS` → **FSM-N22** (successor to CMG-N03).
- `BANK` → **FSM-N06** (successor to Notice 655).

Both predecessors were cancelled on 10 May 2024. The requirements are identical
across classes; the citation is not. Stamp the resolved notice on every report.

## 3. Administrative accounts — paragraph 4.1

Every administrative account on the asset must be secured against unauthorised
access or use. Grant on a need-to-use basis, disable or remove accounts no
longer required, and review the remaining grants periodically. Not attested
$\implies$ breach of 4.1.

## 4. Security patches — paragraph 4.2

The two limbs answer different questions and must not be blurred.

**4.2(a) — a patch exists.** `days_since_patch_released` is an integer. Compare
against the firm's deadline for that severity:

- The deadline is **inclusive**. "Within 7 days" is met at exactly 7; day 8
  breaches.
- Over the deadline $\implies$ breach of 4.2(a).
- Compensating controls recorded on an overdue *available* patch do **not**
  clear the breach. They are recorded as a warning that explicitly says so,
  because this is the single most common way firms talk themselves out of
  patching.
- A severity absent from the policy **raises**. Failing closed is correct: an
  unmeasured vulnerability must never be reported as measured and passing.

**4.2(b) — no patch exists.** `days_since_patch_released is None`.

- Controls instituted to reduce the risk $\implies$ compliant, carried as a
  warning to be re-tested when a patch ships.
- No controls $\implies$ breach of 4.2(b).

**Setting the deadlines.** `PatchRemediationPolicy` is mandatory and has no
default. MAS fixes no figure; the firm must derive its own from the risk each
severity poses to *its* systems, and record the derivation. A flat number
carried across every severity is almost never risk-commensurate.

## 5. Security standards — paragraph 4.3

Three limbs, evaluated in order:

- No written set of security standards $\implies$ breach of 4.3(a), and stop.
  Conformance under 4.3(b) is unevaluable when there is nothing to conform to;
  reporting a second breach would be double-counting an unanswerable question.
- Standards exist and the asset conforms $\implies$ pass.
- Standards exist, the asset does not conform, controls instituted $\implies$
  compliant under 4.3(c), carried as a warning for the firm's exception cycle.
- Standards exist, the asset does not conform, no controls $\implies$ breach of
  4.3(b).

CIS Benchmarks are a common and reasonable basis for authoring the written set.
They are not what the Notice mandates, and the remediation text says so.

## 6. Network perimeter defence — paragraph 4.4

Controls at the network perimeter restricting all unauthorised traffic. The
requirement follows the traffic: perimeters at third-party hosting providers and
overseas-hosted networks are in scope. Not attested $\implies$ breach of 4.4.

## 7. Malware protection — paragraph 4.5

The requirement is qualified: "where such malware protection measures are
available and can be implemented".

- Implemented $\implies$ pass.
- Not implemented, with a non-blank justification (a sealed vendor appliance, an
  FPGA feed handler with no agent support) $\implies$ compliant, carried as a
  warning to be re-assessed whenever the platform or its tooling changes.
- Not implemented, with no justification or a blank one $\implies$ breach of 4.5.

Recording the justification is the point. Silently passing an asset that cannot
run malware protection loses the audit trail for the exception.

## 8. Multi-factor authentication — paragraph 4.6

Two independent limbs. Evaluate both; either can breach alone, and both can
breach together.

- **4.6(a)** — the asset is a **critical system**: MFA on all administrative
  accounts across its OS, database, application, security appliance and network
  device layers. Not a critical system $\implies$ this limb does not apply.
- **4.6(b)** — the asset is used to access **customer information through the
  internet**: MFA on **all** accounts on it, not only administrative ones. This
  limb is not restricted to critical systems, so a non-critical asset can be in
  scope through it alone.
- Neither limb applies $\implies$ the requirement is reported in
  `not_applicable_requirements`. It is not reported as passed, because a
  requirement that never ran was never met.
- Unknown scope resolves conservatively to in-scope, with a warning naming the
  missing field.

## 9. Report and escalate

- Output `MASCyberHygieneAuditReport`: `is_compliant`, `status`, the full
  `breaches` tuple with each breach pinned to its Notice paragraph,
  `failed_requirements`, `applicable_requirements`,
  `not_applicable_requirements`, `warnings`, deduplicated
  `mandatory_remediations`, and `remediation_progress_pct`.
- `remediation_progress_pct` divides by the **applicable** requirement count, so
  an asset the MFA requirement never reached is not marked down for it. It is an
  internal tracking metric with no regulatory meaning — every requirement is
  mandatory, so any breach makes the asset non-compliant.
- For an estate, `audit_estate` returns one report per asset, deliberately
  unaggregated: a breach on one host must not be averaged away against clean
  hosts.
- File the report set with the IT risk function and the entity's compliance
  officer, citing the notice number the engine stamped — FSM-N22 for a capital
  markets firm, FSM-N06 for a bank.
- Remember the boundary: the report attests that controls were asserted. Pair it
  with configuration scanning and the firm's own evidence before treating it as
  proof of compliance.
