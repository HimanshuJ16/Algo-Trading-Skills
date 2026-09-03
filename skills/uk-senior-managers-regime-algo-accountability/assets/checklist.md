# UK FCA SM&CR Algorithmic Governance Checklist

## Firm classification (do this first)
- [ ] **SM&CR tier confirmed**: limited scope, core, enhanced, banking, or Solvency II insurance. Every item below depends on it.
- [ ] **SMF availability checked**: SMF4, SMF18, and SMF24 are appointable only by enhanced scope and dual-regulated firms (SUP 10C). A core firm has allocated algorithmic trading responsibility to an SMF it actually holds (typically SMF1 or SMF16).
- [ ] **SYSC 25 scope determined**: a management responsibilities map is mandatory only for banking, Solvency II insurance, and enhanced scope firms. Core and limited scope firms are not claiming a mandatory MRM they do not owe.

## SMF allocation
- [ ] **Named holder per algorithm**: every production algorithm maps to a specific SMF holder, not a committee or a job title.
- [ ] **FCA IRNs verified**: each Senior Manager's Individual Reference Number checked against the FCA Register. The engine checks presence only — the FCA publishes no fixed IRN format.
- [ ] **Statement of Responsibilities updated**: algorithmic trading duties appear in the holder's SoR, and in the MRM where SYSC 25 applies.
- [ ] **Handover documented**: any change of holder has a handover certificate (SYSC 25.9), not a silent register overwrite.

## Certification function (SYSC 27.8.23R)
- [ ] **Scoped correctly**: certified staff are those who approve deployment or amendment of an algorithm, or have significant responsibility for monitoring or deciding its ongoing compliance — not everyone who writes or tests code.
- [ ] **F&P assessment complete**: honesty/integrity/reputation, competence and capability, and financial soundness, with SYSC 22 regulatory references covering the six-year look-back.
- [ ] **Certificate validity tracked**: 12-month statutory maximum from issue (FSMA s.63F, SYSC 27.2), with shorter firm-issued windows recorded via `certificate_validity_months`.
- [ ] **Expiry enforced in the deployment gate**: an expired certificate blocks approval rather than merely raising a reminder.
- [ ] **Mid-period role changes assessed**: an employee moving into a new certification function is assessed before starting, not at the next annual review.

## Deployment sign-off
- [ ] **Version-bound**: the sign-off names the exact version being deployed; an amendment does not inherit its predecessor's approval.
- [ ] **RTS 6 evidence recorded**: pre-trade controls on order entry (Article 15), kill functionality drill (Article 12), and stress testing (Article 10), each with a date.
- [ ] **Reasonable-steps notes are substantive**: which controls were reviewed, which stress scenarios ran and their outcomes, when the kill drill took place. "Approved" and "Signed off" are rejected, and a length check does not establish sufficiency.
- [ ] **Non-approvals recorded as such**: REJECTED and PENDING_SIGN_OFF decisions are stored and logged without reading as approval.
- [ ] **No fabricated thresholds**: any millisecond kill-switch target or documentation-length rule is labelled an internal standard. RTS 6 Article 12 requires only "immediately".

## Audit and reporting
- [ ] **Deterministic dating**: `verify_algo_deployment_readiness()` and `generate_mrm_report()` are called with an explicit `as_of` date wherever results are compared, replayed, or archived.
- [ ] **Report fields reviewed**: `unassigned_algos` and `uncertified_dev_algos` are checked, not just the compliant count.
- [ ] **Retained with the annual self-assessment**: governance reports archived alongside the RTS 6 Article 9 self-assessment and validation, per the firm's retention schedule.
- [ ] **Substance tested, not just paperwork**: sign-off procedures reviewed against the FCA's August 2025 multi-firm review findings on outdated policies and superficial compliance involvement.
