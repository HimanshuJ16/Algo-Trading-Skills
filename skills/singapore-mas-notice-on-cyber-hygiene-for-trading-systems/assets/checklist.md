# Pre-Flight Checklist — MAS Notice on Cyber Hygiene (Trading Systems)

## Cite the right instrument

- [ ] Entity class determined, and the applicable notice recorded: **FSM-N22** for a capital markets financial institution (CMS licensee, exchange, clearing house, trade repository, CSD); **FSM-N06** for a bank.
- [ ] No reference remaining to **Notice 655** or **CMG-N03** — both cancelled 10 May 2024.
- [ ] Confirmed the paragraph numbering used in the audit file against the reissued notice that binds this entity, not only against the original 4.1–4.6 numbering.

## Calibrate the firm-set numbers

- [ ] `PatchRemediationPolicy` deadlines derived from the firm's own risk assessment, per severity, and the derivation recorded.
- [ ] **No "30-day MAS SLA" anywhere in the control set, the policy or the audit file.** MAS publishes no patching timeframe; para 4.2(a) requires one commensurate with the risk each vulnerability poses.
- [ ] Policy covers every severity label the vulnerability feed actually emits, so no vulnerability goes unmeasured.

## Scope each asset before auditing it

- [ ] Criticality determined for every asset — does its failure cause significant disruption to operations, or materially impact service to customers?
- [ ] Internet customer-information access determined for every asset, **separately** from criticality.
- [ ] Inventory covers all five layers named in para 4.1: operating system, database, application, security appliance, network device.
- [ ] Any asset left with unknown scope is flagged for determination, not left to the conservative default indefinitely.

## Verify each requirement

- [ ] **4.1** — administrative accounts granted on a need-to-use basis, unnecessary accounts removed or disabled, remaining grants reviewed periodically.
- [ ] **4.2(a)** — no available patch is open past the firm's deadline for its severity.
- [ ] **4.2(b)** — every vulnerability with **no available patch** has instituted controls recorded.
- [ ] Compensating controls are **not** being used to excuse an overdue *available* patch — 4.2(b) answers "no patch exists", not "we did not apply one".
- [ ] **4.3(a)** — a written set of security standards exists for every asset.
- [ ] **4.3(b)/(c)** — every asset conforms, or its non-conformity carries instituted controls and a dated exception.
- [ ] Written standards are not described in the audit file as a "CIS benchmark mandate" — the Notice mandates written standards, not a named benchmark.
- [ ] **4.4** — perimeter controls cover third-party-hosted and overseas-hosted network paths, not just the primary datacentre.
- [ ] **4.5** — malware protection implemented, or its unavailability justified in writing and re-assessed after platform or tooling changes.
- [ ] **4.6(a)** — MFA on all administrative accounts of every **critical** system.
- [ ] **4.6(b)** — MFA on **all** accounts (not only administrative) of every system used to access customer information through the internet.

## Verify the gate itself

- [ ] Blank identifiers, unknown severities, negative or boolean patch ages, list-typed vulnerability collections and duplicate vulnerability IDs all raise rather than producing a clean audit.
- [ ] A patch age exactly at the policy deadline passes; one day past it breaches.
- [ ] An asset built from identifiers alone audits **non-compliant** — every control flag fails closed.
- [ ] Unknown criticality audits as critical; unknown internet customer-information access audits as in scope.
- [ ] A requirement that applies to neither MFA limb is reported **not applicable**, never as passed.
- [ ] `remediation_progress_pct` divides by the applicable requirement count, and is not being read anywhere as a compliance grade.
- [ ] Every requirement is evaluated on every asset; all breaches appear in `breaches`, not just the first.
- [ ] Estate results are reviewed per asset, not averaged — one breaching host makes the estate non-compliant.

## Close the attestation gap

- [ ] Each control flag is backed by evidence (configuration scan, identity-provider export, patch-management report), not by a compliance owner's recollection.
- [ ] Third-party and vendor-supplied trading engines audited on their own assets, not assumed compliant by association.
- [ ] Report filed with the IT risk function and the entity's compliance officer, citing the correct notice number.
