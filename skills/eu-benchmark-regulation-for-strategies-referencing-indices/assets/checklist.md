# Pre-Flight Checklist

## Scope — settle this before touching the register

- [ ] Is the entity making the reference a **supervised entity** under Article 3(1)(17)?
- [ ] Is the activity one of the five Article 3(1)(7) uses, rather than proprietary trading, hedging or research?
- [ ] Does an Article 2(2) exemption apply — central bank (€STR), CCP settlement price, single reference price, designated spot FX?
- [ ] Is the benchmark critical, significant, an EU CTB/PAB, or a commodity benchmark subject to Annex II? If none of these, it has been out of BMR scope since 1 January 2026.
- [ ] Is the basis for that classification recorded, with a date, given there is no public list of significant benchmarks not under a warning notice?
- [ ] Is `assessment_date` set explicitly, so a historical record is judged against the regime in force on its own date?

## Article 29 — adding versus holding

- [ ] Is this a **new** reference or an **existing** one? The duties differ.
- [ ] For a new reference to a critical, CTB/PAB or Annex II commodity benchmark: is the administrator on the ESMA register?
- [ ] Was the register consulted recently enough, and after 1 January 2026 — not cached from a 2025 check of a register that has since been re-cut?
- [ ] For a significant benchmark: is it the object of an Article 24a(6) public notice, and is any derogation still running?
- [ ] For an existing reference caught by a notice: is the six-month replacement deadline tracked, or a reasoned statement published on the firm's website?
- [ ] Is any reliance on an Article 23b/23c designated statutory replacement documented?

## Article 28(2) — all three limbs, separately evidenced

- [ ] Does a robust written plan exist for material change or cessation of the benchmark?
- [ ] Is an alternative benchmark nominated with reasons — or is the reasoning for concluding none is feasible or appropriate recorded?
- [ ] Is the plan reflected in the **fallback provisions** of the contracts, instruments and fund documentation that use the benchmark?
- [ ] Can the plans be provided to the competent authority on request without undue delay?

## Hygiene

- [ ] Are benchmark ids, categories, entity types and use types drawn from the module's constants rather than free-typed strings?
- [ ] Is every audit report retained with its `assessment_date`, `scope_basis` and full findings list?
- [ ] For any UK-regulated group entity, has the UK BMR position been assessed separately against the FCA register?
