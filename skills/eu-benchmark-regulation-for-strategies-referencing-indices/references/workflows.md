# Workflows for EU Benchmark Regulation (EU BMR)

The order matters. Scope is decided before any register or plan test, because
since 1 January 2026 most indices are out of scope and testing the register first
manufactures violations.

## 1. Build the inventory of index references

- Enumerate every place the firm references an index: fund benchmarks in
  prospectuses and KIIDs, index-linked notes and certificates issued, OTC
  derivative payoffs, consumer or mortgage credit borrowing rates, performance-fee
  hurdles.
- For each, record the **entity** making the reference and its Article 3(1)(17)
  classification, and characterise the activity against the closed Article 3(1)(7)
  list. Entries that are not on that list — a futures hedge, a risk model input, a
  research signal — are recorded as `NOT_A_BMR_USE` and kept in the inventory so
  the exclusion is documented rather than silent.
- Flag each reference as **new** (being added) or **existing**. Article 29(1) and
  Article 29(1b) impose different duties on the two.

## 2. Classify each benchmark against the amended Article 2(1)

- Critical: check the Commission implementing act.
- CTB / PAB: check the label in the administrator's benchmark statement.
- Significant: check the administrator's published regulatory classification and
  any competent-authority designation. Accept that there is no comprehensive public
  list; document the basis for each conclusion.
- Annex II commodity: check whether the benchmark is based on contributed input
  data and whether any of the carve-outs apply.
- Anything else is `OUT_OF_SCOPE`. Record *why*, with a date — this is the
  conclusion a supervisor will probe.
- Separately record any Article 2(2) exemption. The common one is a central-bank
  rate such as €STR.

## 3. Verify ESMA register status, and date the check

- Consult the ESMA register of administrators for every in-scope benchmark whose
  category carries the Article 29(1) register gate: critical, CTB/PAB, Annex II
  commodity.
- Record `register_status_verified_on`. Between 1 January and 30 September 2026 the
  register is being re-cut against the new scope, with out-of-scope administrators
  removed from 1 October 2026, so a check made in 2025 tells you about a register
  that no longer exists in that form.
- Check ESMA's public statement on transitional provisions for third-country
  administrators with recognition or endorsement applications still pending.

## 4. Check for Article 24a(6) public notices

- For each significant benchmark, check whether ESMA or a Member State competent
  authority has published a notice that it does not comply with the BMR.
- Record the publication date, and the end date of any derogation granted to avoid
  serious market disruption.
- For **new** references: a notice in force with no active derogation prohibits the
  addition outright.
- For **existing** references: start the six-month clock. Either replace the
  benchmark with an appropriate alternative before it expires, or publish a
  reasoned statement on the firm's website. Track the deadline as a dated
  remediation item, not a background intention.

## 5. Audit the Article 28(2) written plans

Three separately evidenced limbs:

- **The plan exists** and is robust — it sets out what the firm would actually do
  on material change or cessation, proportionate to the benchmark and the scale of
  its use.
- **An alternative is nominated where feasible and appropriate**, with the reasons
  for its suitability. Where no alternative is appropriate, record that reasoning;
  do not invent a fallback to close a checklist item.
- **The plan is reflected in the fallback provisions** of the financial contracts,
  financial instruments and fund documentation that use the benchmark. A plan that
  never reached the documentation is a separate failure from having no plan.

Also confirm the plans can be produced to the competent authority on request
without undue delay.

## 6. Emit and retain the audit report

- Run `audit_strategy_bmr_compliance` with an explicit `assessment_date`.
- Persist `compliance_status`, `in_scope`, `scope_basis`, every finding with its
  article reference, and any `replacement_deadline`.
- Re-run when: the ESMA register changes, a public notice is published or lifted, a
  benchmark's category changes (including an administrator's opt-in designation), a
  new reference is added, or the firm's entity classification changes.

## 7. Cross-check the UK position separately

If any group entity is UK-regulated, run the UK BMR test independently against the
FCA's UK Benchmarks Register. The UK did not adopt the 2025/914 scope cut, so a
benchmark that left EU scope on 1 January 2026 may remain fully in UK scope.
