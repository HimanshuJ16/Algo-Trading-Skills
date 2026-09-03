---
name: eu-benchmark-regulation-for-strategies-referencing-indices
description: >-
  Use when an EU supervised entity references an index in a way Regulation 2016/1011
  regulates, such as issuing an index-linked instrument; decides whether the Benchmarks
  Regulation binds at all before testing the Article 29 use conditions.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: regulatory-compliance-global
  tags: eu-bmr, esma-register, benchmark-regulation, article-28-2-fallback, article-29-use-restriction, significant-benchmark, index-referencing
  brokers_frameworks: "Regulation (EU) 2016/1011; Regulation (EU) 2025/914; ESMA BMR Register; Commission Implementing Regulation (EU) 2016/1368; Python Dataclasses"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when an EU **supervised entity** — a UCITS, an AIFM, an investment
firm, a credit institution, a market operator — references an index in a way the
EU Benchmarks Regulation (Regulation (EU) 2016/1011, "BMR") actually regulates:
issuing an index-linked instrument, determining an amount payable by reference to
an index, or tracking an index to measure a fund's performance, define its asset
allocation, or compute performance fees.

Its first job is to decide whether the BMR binds at all, because since
**1 January 2026** it usually does not. Regulation (EU) 2025/914 cut Article 2(1)
scope down to critical benchmarks, significant benchmarks, EU Climate Transition
and Paris-aligned Benchmarks, and commodity benchmarks subject to Annex II.
Everything else — every non-significant index, every third-country index outside
those categories — is out of scope, and its administrator's absence from the ESMA
register is no longer a reason not to use it. A tool that still tests "is this on
the register?" first will block references an EU fund is entitled to make.

Once the engine concludes the obligations do bind, it applies Article 29 (may this
reference be added, and must an existing one be replaced?) and each limb of
Article 28(2) (written plan, nominated alternative, contractual fallbacks).

## When NOT to Use

- **As a scope determination.** The engine consumes your classification of a
  benchmark as critical / significant / climate / Annex II commodity; it does not
  derive it. Critical benchmarks come from the Commission implementing act and
  CTB/PAB labelling from the benchmark statement, but there is **no public list of
  significant benchmarks** that are not the object of a warning notice. That gap
  is real and unresolved — see `references/standards.md`.
- **As a register client.** It never contacts ESMA. `administrator_on_esma_register`
  and `register_status_verified_on` are assertions about a check a human made.
- **For proprietary trading that is not Article 3(1)(7) "use".** Trading an index
  future, swap or ETF on your own book, or using an index as a research, hedging or
  risk input, is not "use of a benchmark". Modelling it as use manufactures
  obligations that do not exist.
- **For non-supervised entities.** An unregulated proprietary trading firm or a
  family office is outside Article 3(1)(17) and has no Article 28(2) or 29
  obligation, whatever indices it trades.
- **For UK BMR.** The UK onshored regime diverged after Brexit and did not take the
  2025/914 scope cut. A UK supervised entity's Article 29 test is against the FCA's
  UK Benchmarks Register, not ESMA's. This skill models the EU regime only.
- **As spread-adjustment maths.** The engine records that a statutory replacement
  exists under Articles 23b/23c; it computes no spread. Only one EU statutory
  spread is fixed in law (EONIA to €STR, 8.5 bps). Contractual EURIBOR fallbacks
  use industry-published spread adjustments that this skill does not reproduce.

## Prerequisites

- The entity's Article 3(1)(17) classification (`entity_type`), and the Article
  3(1)(7) characterisation of the activity (`use_type`). Get these wrong and every
  downstream answer is wrong in one direction or the other.
- Per benchmark: `category` against the amended Article 2(1), `administrator_name`,
  `administrator_on_esma_register`, and `register_status_verified_on` — the date
  the register was actually consulted, not the date the file was written.
- Any Article 2(2) exemption you have concluded applies (central bank, CCP
  settlement price, single reference price, designated spot FX, …).
- For a significant benchmark: the publication date of any Article 24a(6) public
  notice, and the end date of any derogation granted against it.
- Per usage: whether this is a **new** reference (Article 29(1) prohibition) or an
  **existing** one (Article 29(1b) replacement duty), plus the three Article 28(2)
  booleans — plan exists, alternative nominated, plan reflected in contractual
  fallback provisions.

## Workflow

1. **Gate on the Entity Before Anything Else**: If `entity_type` is
   `NON_SUPERVISED`, stop. The engine returns `OUT_OF_SCOPE_NOT_SUPERVISED_ENTITY`
   with no findings. Articles 28(2) and 29 create obligations for supervised
   entities; they do not regulate indices in the abstract.
2. **Gate on the Use**: If the activity is not one of the five Article 3(1)(7)
   uses, pass `USE_NOT_A_BMR_USE` and stop. An index-arbitrage book trading listed
   futures is the common case here, and the honest answer is that BMR does not
   reach it.
3. **Gate on Article 2(2) Exemptions**: An exempt benchmark leaves BMR scope
   entirely. €STR is the one to get right: the ECB is an exempt central bank, so
   €STR carries no register requirement at all. The engine still raises an
   **advisory** when no written plan exists, because ESMA's Q&A expects supervised
   entities to maintain Article 28(2) plans for central-bank benchmarks anyway.
4. **Gate on Article 2(1) Scope, Against the Assessment Date**: On or after
   1 January 2026 an `OUT_OF_SCOPE` benchmark returns
   `OUT_OF_SCOPE_BENCHMARK`. Before that date the engine applies the wider
   pre-amendment scope, so a 2024 record is judged by 2024's rules. Always pass
   `assessment_date` explicitly; the default of today silently re-dates history.
5. **Apply Article 29 — and Distinguish Adding From Holding**: A new reference to a
   critical, CTB/PAB or Annex II commodity benchmark requires the administrator on
   the ESMA register. A significant benchmark does **not** carry that register
   gate: new references to it are barred only while it is the object of an Article
   24a(6) public notice. Continuing to hold an existing reference is not itself
   prohibited by Article 29(1).
6. **Run the Article 29(1b) Clock on Existing References**: When a public notice
   lands on a benchmark already in use, the entity has six months from publication
   to replace it, or must publish a reasoned statement on its website explaining
   why it cannot. The engine returns `ACTION_REQUIRED` with the deadline until it
   passes, then `VIOLATION`. A derogation granted to avoid market disruption
   suspends both branches while it runs.
7. **Audit All Three Article 28(2) Limbs, Not Just the First**: A missing plan and
   a plan that never reached the contractual fallback provisions are separate
   violations. A plan that nominates no alternative is an **advisory**, not a
   violation — Article 28(2) requires an alternative only "where feasible and
   appropriate", so record why it is not rather than fabricating one.
8. **Retain the Report**: Persist each `EuBmrAuditReport` with its
   `assessment_date`, `scope_basis` and full `findings` list. The scope conclusion
   is the part a competent authority will question, and it is only defensible if
   the date and basis are on the record.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Applying the Pre-2026 "Everything Must Be On The Register" Rule**: This is the
  defect this version exists to fix. Before 2025/914 applied, Article 29(1) barred
  a supervised entity from using any benchmark whose administrator was not
  registered. Since 1 January 2026 that test only bites on four categories, so
  running it unconditionally blocks perfectly lawful references to non-significant
  and third-country indices.
- **Treating a Register Miss as a Scope Answer**: Administrators on the register at
  end-2025 keep their status until 30 September 2026 and out-of-scope ones are
  removed from 1 October 2026. During that window the register is mid-re-cut:
  presence proves little about scope, and coming absence proves nothing about
  legality. Re-verify rather than caching a 2025 check.
- **Calling Index Trading "Use of a Benchmark"**: Article 3(1)(7) is a closed list.
  Issuance, determining amounts payable, being a party to a *financial contract*
  (which the BMR defines narrowly as a consumer or mortgage credit agreement),
  providing a borrowing rate, and measuring fund performance. Executing an
  index-future hedge is none of them.
- **Treating €STR as a Critical Benchmark Needing Registration**: It is neither.
  The critical-benchmark implementing act lists EURIBOR, EONIA, STIBOR, WIBOR and
  NIBOR; €STR has never been on it, and its administrator — the ECB — is exempt
  under Article 2(2)(a) whatever the list says.
- **Blocking an Existing Position Because Article 29 Blocks New References**:
  Article 29(1) prohibits *adding* a reference. Forcing an immediate unwind of an
  existing one confuses the addition prohibition with the Article 29(1b)
  replacement duty, which has a six-month window and an explain-instead escape.
- **Reporting the First Violation and Stopping**: A benchmark can fail the register
  gate *and* have no contractual fallback provisions. An audit that short-circuits
  understates the remediation and gets re-opened on the second pass.
- **Letting a Typo Become a Regulatory Finding**: A benchmark id that is not in the
  registry is a data error. The engine raises `BmrConfigurationError` rather than
  reporting a violation, because "unauthorised benchmark" against a misspelled id
  is a false positive that costs real remediation effort.
- **Assuming the UK Register Mirrors ESMA's**: A dual-regulated group needs both
  tests. The UK did not adopt the 2025/914 scope cut, so an index that dropped out
  of EU scope on 1 January 2026 can still be fully in scope for a UK entity.

## Verification

- Audit a UCITS tracking `EURO STOXX 50` (significant, STOXX Ltd on the register,
  all three Article 28(2) limbs satisfied) on 2026-08-24 and confirm
  `BMR_COMPLIANT` with an empty `findings` list.
- Audit the same UCITS against an unregistered non-significant proprietary index
  and confirm `OUT_OF_SCOPE_BENCHMARK`, `in_scope is False`, and no violation — the
  pre-2.0 engine returned `UNAUTHORIZED_BENCHMARK_VIOLATION` here.
- Re-run that audit with `assessment_date=date(2025, 6, 30)` and confirm it *is* in
  scope and *does* return the register prohibition; check the boundary flips
  between 2025-12-31 and 2026-01-01.
- Confirm an unregistered administrator blocks a new CTB reference but not an
  existing one, and that a significant benchmark with an unregistered administrator
  and no public notice is compliant.
- Publish a notice on 2026-03-15 for a benchmark already in use: confirm
  `ACTION_REQUIRED` with `replacement_deadline == date(2026, 9, 15)`, still
  `ACTION_REQUIRED` on that date, and `VIOLATION` one day later.
- Set `has_written_fallback_plan=True` but
  `fallback_reflected_in_contractual_terms=False` and confirm a violation; drop
  only `designates_alternative_benchmark` and confirm an advisory instead.
- Reference a benchmark id that is not registered, an entity type of
  `"HEDGE_FUND"`, a category of `"Significant"`, or a duplicate benchmark id, and
  confirm each raises `BmrConfigurationError`.
- Run `python -m unittest discover -s skills/eu-benchmark-regulation-for-strategies-referencing-indices/scripts`
  and confirm a 100% pass rate.

## Related Skills

- `mifid-ii-algo-trading-compliance-eu`
- `eu-market-abuse-regulation-mar-surveillance`
- `esma-double-volume-cap-mechanism`
- `benchmark-selection-for-strategy-evaluation`
- `point-in-time-index-constituent-tracking`
- `regulatory-change-monitoring-service-integration`
- `cross-jurisdiction-regulatory-conflict-resolution`
