# Pre-Flight / Sign-off Checklist — custody-solution-vendor-due-diligence-checklist

## Artefacts obtained (documents, not vendor summaries)

- [ ] Charter or registration document obtained and the issuing authority independently confirmed.
- [ ] Full SOC 2 Type II report obtained — not a summary, badge, or webpage claim.
- [ ] Insurance binder obtained, confirmed active, and the **named perils and sub-limits** read.
- [ ] **Executed** custody agreement obtained (not a template or term sheet).
- [ ] Audited GAAP financial statements obtained.
- [ ] Independent penetration test attestation obtained.

## Regulatory basis

- [ ] Entity mapped to a specific Rule 206(4)-2(d)(6) category — no reliance on a vendor claiming "SEC Qualified Custodian status", which is not a designation the SEC grants.
- [ ] Counsel has reviewed and confirmed the qualification conclusion.
- [ ] **If a state-chartered trust company custodying crypto:**
  - [ ] State banking authority's authorisation to provide crypto custody verified (re-verify **annually**).
  - [ ] Audited GAAP financial statements reviewed.
  - [ ] Independent internal control report (SOC 1 or SOC 2) reviewed.
  - [ ] Custody agreement prohibits lending, pledging, rehypothecation, or transfer without written consent.
  - [ ] Agreement requires segregation from the custodian's proprietary assets.
  - [ ] Reliance on conditional, revocable staff no-action relief (2025-09-30) recorded in the file.
  - [ ] Client/board risk disclosure and best-interest determination documented (**adviser-side — outside this engine's scope**).

## Asset protection

- [ ] Legal opinion on bankruptcy-remote segregation of client assets on file.
- [ ] Rehypothecation, lending, and pledging expressly prohibited without written consent.

## Security

- [ ] SOC 2 Type II opinion is unqualified.
- [ ] SOC 2 Trust Services Criteria **in scope** confirmed to include Security.
- [ ] SOC 2 report period covers the review window; **bridge letter** obtained for any gap.
- [ ] Key material protected by FIPS-validated modules at **Level 3 or 4**.
- [ ] If the validation cites **FIPS 140-2**: FIPS 140-3 roadmap requested — all remaining 140-2 certificates move to the NIST CMVP Historical List on **2026-09-21**.

## Insurance

- [ ] `assets_under_custody_usd` supplied so coverage is judged as a **ratio**, not an absolute limit.
- [ ] Coverage ratio meets the firm's calibrated floor.
- [ ] Confirmed the policy covers the relevant perils (theft, insider fraud, loss of key material) and understood that market loss, protocol failure, and custodian insolvency are **not** covered.

## Operations and governance

- [ ] Contractual uptime SLA meets the firm's floor.
- [ ] Recovery Time Objective meets the firm's ceiling; BCP/DR plan reviewed.
- [ ] Segregation of duties evidenced between transaction initiation, approval, and key custody.
- [ ] Annual independent penetration testing evidenced.

## Run discipline

- [ ] Threshold policy calibrated and the calibration recorded (engine defaults have **no regulatory basis**).
- [ ] `assessment_date` passed explicitly so the result is reproducible.
- [ ] Every red flag and action item dispositioned — approved, remediated, or accepted with rationale.
- [ ] Automated Testing: run `python -m unittest discover -s skills/custody-solution-vendor-due-diligence-checklist/scripts` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Counsel sign-off (qualification conclusion): ___________________________
- Date: ___________________________
