---
name: regulatory-custody-requirements-by-jurisdiction
description: >-
  Use when a client-asset or crypto custody arrangement must answer to a named regulator
  and every finding needs its citation, covering the Advisers Act custody rule and its
  regional counterparts.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: crypto-custody-security
  tags: regulatory-custody, sec-custody-rule, mica, fca-cass, mas, qualified-custodian, asset-segregation
  brokers_frameworks: "SEC Rule 206(4)-2; EU MiCA Regulation; UK FCA CASS Rules; MAS Payment Services Act; Python Dataclasses"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when designing or auditing a client-asset or crypto custody
arrangement that has to answer to a named regulator, and you need the answer to
carry its source. It takes a documented setup and returns a report in which every
finding names the instrument it comes from — `17 CFR 275.206(4)-2(a)(4)`,
`MiCA Art. 75(7)`, `FCA Handbook SUP 3.10` — so a custody decision leaves an
evidence trail rather than an assertion.

It is most valuable exactly where the naive one-line summary is wrong. Three
claims that circulate widely and that this engine refuses to make:

- **The EU does not mandate custody insurance.** MiCA Article 75 never mentions
  insurance. Article 67 requires *prudential safeguards*, and Article 67(4) lets
  those be own funds **or** a qualifying insurance policy **or** a comparable
  guarantee. Insurance is a permitted *form* of a capital requirement.
- **MAS does not mandate custody insurance either**, nor an independent
  third-party custodian. A licensed DPT provider may hold the trust account
  itself.
- **The Advisers Act surprise examination has codified exceptions.** Flagging a
  violation against an adviser properly relying on Rule 206(4)-2(b)(3) or (b)(4)
  is a false positive that costs credibility with counsel.

## When NOT to Use

- **As a legal determination.** Whether an entity is a qualified custodian, or
  whether a trust is validly constituted, is a conclusion for counsel. This
  engine produces the evidence for that conversation, not a substitute for it.
- **For regimes it does not model.** US broker-dealer customer protection
  (17 CFR 240.15c3-3), EU custody of financial instruments (MiFID II, AIFMD
  Art. 21), and Singapore capital markets services custody under the Securities
  and Futures Act are all out of scope and are reported as unsupported rather
  than answered with a neighbouring regime's rules.
- **To decide whether a vendor is any good.** Regime conformance is a floor, not
  a diligence programme — see `custody-solution-vendor-due-diligence-checklist`.
- **For self-custody key design.** See `crypto-wallet-key-custody-security` and
  `hot-cold-wallet-split-for-trading-bots`.

## Prerequisites

- The artefacts behind each assertion, not a vendor's summary: the licence or
  charter, the executed custody agreement, the trust deed, the auditor's report
  or client assets report, the prudential capital position.
- The **asset scope** (`SECURITIES` or `CRYPTO`) as well as the jurisdiction.
  Custody rules are not jurisdiction-wide; the same regulator applies different
  rules to different asset classes.
- An explicit `as_of` date. The UK cryptoasset regime commences
  **25 October 2027**; an audit without a date silently changes answer over time.

## Workflow

1. **Resolve the Regime, Not the Country**: Rules are keyed
   `"<JURISDICTION>:<ASSET_SCOPE>"` — `US:SECURITIES`, `US:CRYPTO`, `EU:CRYPTO`,
   `UK:SECURITIES`, `UK:CRYPTO`, `SG:CRYPTO`. A known jurisdiction with an
   unmodelled asset scope returns `UNSUPPORTED_REGIME`, because answering an EU
   securities-custody question with MiCA would be worse than not answering.
2. **Supply Evidence, and Accept That Silence Is Not a Pass**: Every evidence
   attribute is tri-state. `None` means "not evidenced" and produces a violation
   with severity `UNEVIDENCED` — distinct from `MANDATORY`, which means you
   checked and it is breached. A setup you know nothing about is never reported
   compliant.
3. **Apply Codified Exceptions Before Flagging**: The engine checks for an
   exception before evaluating a requirement, and records the exception it
   relied on in `exemptions_applied`. Fee-deduction-only custody and the audited
   pooled-vehicle route both relieve the surprise examination; conditional
   requirements such as the Rule 206(4)-2(a)(6) internal control report only
   engage when the custodian is the adviser or a related person.
4. **Separate Mandatory Rules From Supervisory Guidance**: MAS's 90% cold-storage
   expectation is guidance, so a shortfall lands in `advisories`, not
   `violations`, and does not make the setup non-compliant. Conflating the two
   makes every report equally urgent, which is the same as none of them being.
5. **Date the Audit**: Pass `as_of`. For a regime that is made but not yet in
   force, the status is `PRE_COMMENCEMENT_READINESS` and the findings are
   readiness gaps — `is_compliant` still reflects whether they are closed, so a
   forward-looking audit cannot read as a clean bill of health.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Exporting "Qualified Custodian" to Jurisdictions That Have No Such Concept**:
  it is an Advisers Act term. CASS asks whether the firm is *authorised* for
  safeguarding and administering investments; MiCA asks whether it is an
  *authorised CASP*; MAS asks whether it is a *licensed* DPT service provider.
  Modelling the UK as requiring a "qualified custodian" produces breaches that do
  not exist and misses the ones that do.
- **Treating a Vendor's Insurance Certificate as an EU or SG Compliance Item**:
  it is neither. Under MiCA it can go toward the Article 67 safeguard, which is
  a *higher-of* test — Annex IV Class 2 (EUR 125,000 for custody and
  administration) versus one quarter of last year's fixed overheads. A CASP with
  EUR 4m of fixed overheads needs EUR 1m, and a EUR 125,000 policy is not close.
- **Reporting a Surprise-Examination Violation Against an Exempt Adviser**: an
  adviser with custody solely from fee deduction, or one whose pool is audited
  and distributes GAAP statements within 120 days, is relieved by the rule itself.
- **Treating a State Trust Charter as Settled US Crypto Qualification**: the
  2025-09-30 staff no-action letter is conditional, fact-specific and revocable,
  and it did not hold that state trust companies satisfy the Advisers Act "bank"
  definition. Its conditions — annual re-verification of state authorisation,
  audited GAAP financials, a recent SOC report, a custody agreement barring
  rehypothecation — must all be evidenced, and the engine demands that only when
  the state-trust route is actually being relied on.
- **Auditing UK Crypto Custody Against CASS 17 Today**: safeguarding qualifying
  cryptoassets does not become a regulated activity until 25 October 2027. Before
  then the output is a readiness assessment. Firms safeguarding cryptoassets that
  *are* specified investments sit under CASS 6, and belong in `UK:SECURITIES`.
- **Reading a Blank Field as a Pass**: this is the failure mode that turns a
  compliance tool into a liability. `None` is reported, never assumed.

## Verification

- Audit a fully evidenced `US:SECURITIES` setup with a qualified custodian,
  segregation, and a surprise examination, and confirm `CUSTODY_COMPLIANT` with
  no violations.
- Set `is_asset_segregated=None` and confirm a
  `CLIENT_ASSET_SEGREGATION_NOT_EVIDENCED` violation at severity `UNEVIDENCED` —
  not a pass — while `is_asset_segregated=False` yields `CLIENT_ASSET_SEGREGATION`
  at severity `MANDATORY`.
- Set `has_annual_audit=False` with `custody_solely_for_fee_deduction=True` and
  confirm `CUSTODY_COMPLIANT` with Rule 206(4)-2(b)(3) recorded in
  `exemptions_applied`.
- Audit an `EU:CRYPTO` setup with `has_insurance_coverage=False` and adequate own
  funds, and confirm `CUSTODY_COMPLIANT` with **no** `MISSING_INSURANCE` finding.
- Set `prudential_safeguard_eur=200_000` against
  `fixed_overheads_prior_year_eur=1_000_000` and confirm a `PRUDENTIAL_SAFEGUARDS`
  violation — the Annex IV floor is cleared but the overheads limb is not.
- Audit an `SG:CRYPTO` self-custody setup on a statutory trust with no insurance
  and confirm `CUSTODY_COMPLIANT`, then drop `cold_storage_pct` to 50.0 and
  confirm the finding lands in `advisories` and leaves `is_compliant` true.
- Audit `UK:CRYPTO` with `as_of` before 25 October 2027 and confirm
  `PRE_COMMENCEMENT_READINESS`, an empty `violations` list, and
  `is_compliant == False` while gaps remain open.
- Submit `custody_type="QUALIFED_CUSTODIAN"` and confirm `CustodyRegimeError`
  rather than a fabricated unqualified-custodian violation.
- Run `python -m unittest discover -s skills/regulatory-custody-requirements-by-jurisdiction/scripts`
  and confirm a 100% pass rate.

## Related Skills

- `custody-solution-vendor-due-diligence-checklist`
- `custodial-vs-non-custodial-tradeoff-assessment`
- `record-retention-periods-by-jurisdiction`
- `cross-jurisdiction-regulatory-conflict-resolution`
