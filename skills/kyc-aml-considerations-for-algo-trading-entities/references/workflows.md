# Workflows — Algo Trading Entity KYC/AML Onboarding Audit

Deep procedure for `KycAmlEntityComplianceEngine`. Thresholds and citations live
in `standards.md`; this file is the order of operations and the judgement calls.

---

## 0. Establish which side of the obligation you are on

The CDD Rule binds the **covered financial institution**. Before collecting
anything, write down which you are:

| You are | What this engine is for |
|---|---|
| The bank / broker-dealer / FCM onboarding a trading entity | Your CDD control over a legal entity customer |
| The trading fund or prop firm | A pre-flight audit of the file the institution will demand |

Getting this wrong produces a control that documents the wrong party's
obligation. It also changes what "rejection" means: the institution declines an
account; the fund fixes its own structure before applying.

---

## 1. Resolve jurisdictions before anything is compared

Normalise `incorporation_country` and `banking_country` to ISO 3166-1 alpha-2
**first**, and treat an unresolvable value as an error rather than a pass.

Why this comes first: the realistic production failure is not a typo, it is a
**namespace change**. An upstream vendor starts emitting `"IR"` where it used to
emit `"IRAN"`, every comparison against a name-keyed list stops matching, and the
screen keeps returning clean results with no error and no alert. `normalize_country`
raises `KycAmlValidationError` on anything it cannot resolve, so that failure
surfaces as a broken pipeline instead of a silent approval.

Screen **both** jurisdictions. An entity incorporated in a low-risk jurisdiction
that banks through a call-for-action jurisdiction is the exact structure a single
incorporation-only check misses.

---

## 2. Build the ownership picture, then aggregate it

1. Obtain documented ownership — register of members, LP agreement, share
   certificates — not a self-certification form on its own. The engine can only
   evaluate what it is told.
2. Look through each intermediate holding company, trust, and nominee to the
   **natural persons** behind it. An intermediate entity is never a UBO. Where a
   trust holds ≥ 25%, the trustee is the person to identify.
3. **Aggregate each natural person's holdings before applying the threshold.**
   The rule says "directly or indirectly": 15% through Vehicle A plus 15% through
   Vehicle B is one 30% beneficial owner, not two 15% records that each clear the
   screen. The engine aggregates on a normalised name key.

   That key is a **deduplication aid, not entity resolution.** It will not merge
   `"Jane A. Doe"` with `"Doe, Jane Anne"`, and it *will* merge two different
   people who share a name. In production, key on a real identifier — passport
   number, national ID, LEI — and pass a single canonical name per person.
4. Verify identity for every aggregated holder at or above the threshold,
   consistent with your CIP.

---

## 3. Test the residual — what has *not* been declared

Sum the declared ownership. The residual is `100% − declared`.

If the residual exceeds one threshold's worth, **an undisclosed holder could sit
at or above the threshold**, and the assertion "all beneficial owners have been
identified" is unsupported. That is the arithmetic behind
`REJECTED_OWNERSHIP_OPACITY`, and it is why the tolerance defaults to the
ownership threshold rather than to a round policy number.

A genuinely widely-held entity passes this test by declaring enough of the cap
table to demonstrate the dispersion — not by declaring nothing and asserting it.
A declared sum above 100% is a data error and raises.

---

## 4. Identify and verify the control person — independently

One individual with significant responsibility to control, manage, or direct the
entity: CEO, CFO, COO, Managing Member, General Partner, President, Vice
President, Treasurer, or anyone regularly performing similar functions. Record
the **title**, not just the name.

This step is **not conditional on the ownership prong finding nobody**. Run it
every time. The structures where it matters most — master-feeder funds, widely
held partnerships, nominee-layered offshore vehicles — are exactly the ones where
the ownership prong legitimately returns an empty set, and an engine that treats
the control person as a fallback approves them having verified no one.

If the control person also holds equity, declare that equity as a `UboRecord` so
it is counted once; `ControlPerson` deliberately has no ownership field.

---

## 5. Screen, then classify the sanctions outcome

Screening itself happens outside this engine — see
`sanctions-screening-for-counterparties-and-instruments`. Bring back a recorded
result for the **entity name**, every **UBO**, and the **control person**.

Then classify, because the two outcomes have different legal consequences:

| Finding | Meaning | Action |
|---|---|---|
| A blocked person holds a minority stake | Relationship risk | Decline; document |
| Blocked persons hold **≥ 50% in the aggregate** | The entity **is** blocked property under the OFAC 50 Percent Rule, listed or not | **Block the property and report to OFAC.** Quietly declining is not sufficient |

Aggregate across blocked persons — that is what the 2014 revision changed.
Checking each blocked owner against 50% individually will essentially never fire.

An `is_sanctioned=False` in the payload is an **assertion that screening was run
and returned nothing**. It is not a default. If nobody ran the screen, do not
pass `False` — the engine has no way to tell the difference, and it will record
`has_sanctions_hit=False` in an audit file on your behalf.

---

## 6. Tier the jurisdiction risk

| Tier | Outcome | Rationale |
|---|---|---|
| Call for action **with counter-measures** (Iran, DPRK) | Blocking | R.19 counter-measures |
| Call for action, **EDD only** (Myanmar) | `KYC_AML_EDD_REQUIRED` | FATF has not called for counter-measures and asks that humanitarian, NPO and remittance flows not be disrupted |
| **Increased monitoring** (grey list) | `KYC_AML_EDD_REQUIRED` | Risk-based EDD |

Discharge the EDD condition by documenting source of funds; the status then
resolves to approved with the finding still on the record.

**Refresh the lists at every FATF plenary** (roughly February, June, October).
Inject a new `JurisdictionRiskLists` with its `as_of` date; do not edit the
bundled snapshot in place, so the audit file shows which list version produced
which decision. The engine emits `STALE_JURISDICTION_LISTS` once the snapshot
exceeds `max_list_age_days`.

---

## 7. Apply the PEP measures

A PEP hit is an **EDD trigger, not a rejection**. For a foreign PEP, discharge
all of:

- [ ] Senior management approval to establish or continue the relationship.
- [ ] Source of **wealth** and source of **funds** established and documented.
- [ ] Enhanced ongoing monitoring configured — this one lives outside the engine;
      it is a monitoring configuration, not an onboarding field.

Domestic and international-organisation PEPs are risk-based: set `pep_category`
so the engine can tell them apart. Leaving `pep_category` blank while `is_pep` is
`True` makes the engine assume `FOREIGN` — the conservative reading — and record
`PEP_CATEGORY_ASSUMED_FOREIGN` so the assumption is visible rather than implied.

---

## 8. Read the whole report, and file it

`status` is only the highest-precedence blocking finding. The audit record is
`report.findings` — every check that ran, each with its severity and citation —
plus `report.edd_conditions` for what remains outstanding.

Pass `assessment_date` explicitly. A report that cannot be reproduced six months
later during an examination is not an audit trail, and `date.today()` guarantees
it cannot be.

Persist at minimum: `assessment_date`, `screening_lists_as_of`, normalised
jurisdictions, `status`, every finding with its citation, and the underlying
documents. See `record-retention-periods-by-jurisdiction` for how long.

---

## 9. Re-run on cadence

Onboarding is a point-in-time decision against lists and structures that both
move. Re-run when:

- FATF publishes a new plenary statement;
- ownership changes, or a new control person is appointed;
- a screening provider reports a new match on an existing name;
- your own risk-based ongoing CDD schedule falls due;
- facts come to light that call the previously supplied ownership information
  into question — which is the trigger the CDD Rule itself names.
