# Pre-Flight / Sign-off Checklist — regulatory-custody-requirements-by-jurisdiction

## Scoping

- [ ] Jurisdiction **and** asset scope (`SECURITIES` / `CRYPTO`) both established — a country alone does not select a custody regime.
- [ ] Confirmed the arrangement falls inside a modelled regime, and not in one of the excluded ones: US broker-dealer customer protection (17 CFR 240.15c3-3), EU custody of financial instruments (MiFID II, AIFMD Art. 21), SG capital markets services custody (Securities and Futures Act).
- [ ] `as_of` date chosen and passed explicitly.

## Evidence obtained (artefacts, not vendor summaries)

- [ ] Licence, authorisation or charter obtained and confirmed with the issuing authority.
- [ ] **Executed** custody agreement obtained.
- [ ] Trust deed or trust account documentation obtained, where the regime requires a trust.
- [ ] Auditor's report obtained — the one the regime actually asks for (surprise examination in the US; SUP 3.10 client assets report in the UK).
- [ ] Prudential capital position or insurance policy obtained, where the regime has a capital test.
- [ ] Every field left as `None` is a field you genuinely have no evidence for — not a shortcut.

## United States (Rule 206(4)-2)

- [ ] Entity mapped to a specific (d)(6) category. No reliance on a vendor claiming "SEC Qualified Custodian status", which is not a designation the SEC grants.
- [ ] Segregation confirmed under (a)(1)(i) or (a)(1)(ii).
- [ ] Annual surprise examination evidenced — **or** an exception identified and recorded:
  - [ ] (b)(3): custody arises solely from advisory fee deduction, or
  - [ ] (b)(4): pool audited annually with GAAP statements distributed within 120 days.
- [ ] If the custodian is the adviser or a related person: (a)(6) internal control report obtained.
- [ ] **If a state-chartered trust company custodying crypto:** all conditions of the 2025-09-30 staff no-action letter verified, and reliance on conditional, revocable relief recorded in the file. Score the conditions with `custody-solution-vendor-due-diligence-checklist`.
- [ ] Counsel has reviewed the qualification conclusion.

## European Union (MiCA)

- [ ] CASP authorisation confirmed — the Art. 143(3) transitional regime closed no later than 2026-07-01, and earlier in several Member States.
- [ ] Segregation of client holdings and clear identification of means of access evidenced (Art. 75(7)).
- [ ] Custody policy obtained (Art. 75(3)).
- [ ] Register of client positions evidenced (Art. 75(2)).
- [ ] Art. 67 higher-of test computed with **both** limbs: Annex IV Class 2 (EUR 125,000) **and** one quarter of the preceding year's fixed overheads. A figure clearing only the floor is not compliance.
- [ ] Understood that insurance is one permitted **form** of the Art. 67 safeguard — not a custody mandate — and, if relied on, meets Art. 67(5) (≥ 1 year initial term, ≥ 90 days cancellation notice, authorised insurer, third-party provider).
- [ ] Art. 75(8) liability cap (market value at the time of loss) understood as the ceiling on recovery.

## United Kingdom (CASS)

- [ ] FCA authorisation for safeguarding and administering investments confirmed. No "qualified custodian" test applied — CASS has no such concept.
- [ ] CASS 6.2 / 6.6 segregation, records and reconciliations evidenced.
- [ ] SUP 3.10 client assets report obtained and delivered to the FCA within four months of the period end; individual rule breaches read, not just the opinion.
- [ ] **Cryptoassets:** determined whether they are *relevant specified investment cryptoassets* (CASS 6 today) or fall under the new regime (CASS 17, commencing **2027-10-25**).
- [ ] Any pre-commencement `UK:CRYPTO` output treated as a **readiness assessment**, not a compliance determination.

## Singapore (Payment Services Act, DPT services)

- [ ] MAS licence for the DPT service confirmed.
- [ ] Customers' assets held in a **trust account on trust** for the customer.
- [ ] Segregation from the provider's own assets, with books and records, evidenced.
- [ ] Daily reconciliation evidenced.
- [ ] Cold storage proportion recorded and compared against the 90% supervisory expectation — treated as **guidance**, not a statutory breach.
- [ ] Not treated as a violation: absence of insurance, and absence of an independent third-party custodian. MAS mandates neither.

## Run discipline

- [ ] `as_of` passed explicitly so the result is reproducible.
- [ ] Every `UNEVIDENCED` finding chased to a `True` or a `False` before sign-off — unresolved missing evidence is a reviewer's remediation item, not the custodian's.
- [ ] Advisory findings dispositioned separately from mandatory violations.
- [ ] `exemptions_applied` reviewed: each recorded exception actually holds on these facts.
- [ ] `references/standards.md` re-verified against primary sources; dated items (UK commencement, MiCA transitionals, the US no-action relief) rechecked.
- [ ] Automated Testing: run `python -m unittest discover -s skills/regulatory-custody-requirements-by-jurisdiction/scripts` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Counsel sign-off (custody qualification conclusion): ___________________________
- `as_of` date audited: ___________________________
- Date: ___________________________
