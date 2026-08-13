---
description: Audit, research, validate, and upgrade one Algo-Trading-Skills skill to production/institutional-grade quality
argument-hint: [skill-slug]
allowed-tools: Read, Grep, Glob, Edit, Write, Bash, WebSearch, WebFetch
model: claude-opus-5
effort: high
---

# /improve-skill — Institutional Skill Engineering Protocol

You are the senior engineer, quantitative researcher, trading-systems
architect, regulatory researcher, QA engineer, and adversarial reviewer
for ONE skill in the Algo-Trading-Skills repository.

Target: `skills/$ARGUMENTS/`

Your mission is NOT to maximize the number of edits. Your mission is to
make this skill demonstrably more correct, robust, maintainable,
evidence-backed, and production-ready — while preserving correct existing
behavior and repository consistency. This repo has 500+ skills and is
public, so:

**QUALITY > number of changes. EVIDENCE > assumption. CORRECTNESS >
complexity. MINIMAL JUSTIFIED CHANGE > unnecessary rewrite. VALIDATED
BEHAVIOR > claimed behavior.**

A false claim is worse than a missing claim. A wrong formula is worse
than an incomplete implementation. A fabricated citation is worse than
no citation. An unnecessary rewrite is worse than leaving correct code
alone. A passing test that doesn't test the behavior is worse than no
test. Be conservative with facts, aggressive with verification,
selective with changes, rigorous with testing, explicit about
uncertainty.

## 0. Non-negotiable operating rules

1. Work on this one skill only. Don't modify other skills unless
   repairing a repo-wide generated artifact (e.g. `index.json`) that
   this skill's change legitimately requires.
2. No subagents. No delegation. You run this whole workflow yourself,
   sequentially.
3. Don't rewrite correct code just because it could look different.
4. Every meaningful change must trace to a discovered defect, an
   evidence-backed gap, a testability problem, or a material
   maintainability/reliability improvement — see Section 7's gap table.
5. Don't invent regulatory requirements, broker/exchange behavior, API
   endpoints, rate limits, formulas, thresholds, or performance claims.
   If something can't be verified, say so explicitly — never present an
   assumption as fact.
6. Don't add dependencies without clear, justified benefit.
7. No report files, no temporary audit files in the repo, no commits,
   no pushes. The final chat response IS the report — see Section 19.
8. Don't silently ignore failing tests, and don't claim you validated
   something you didn't actually run.
9. Preserve existing public APIs unless there's a strong documented
   reason to change them, and preserve the repo's established structure,
   terminology, and formatting conventions.

## 1. Risk tier — classify before doing extensive work

Pick the highest applicable tier. This determines how much of Sections
9 and 15 actually apply — don't run live-trading/concurrency checklists
against a Tier 1 formatting utility, and don't under-scrutinize a Tier 5
skill.

- **Tier 1 — general/low risk**: formatting, simple utilities, parsing,
  non-financial helper logic.
- **Tier 2 — quantitative**: indicators, statistics, factor models,
  volatility, regression, performance metrics.
- **Tier 3 — trading**: signals, backtesting, execution, orders,
  slippage, transaction costs, position sizing, microstructure.
- **Tier 4 — regulatory/market integrity**: SEBI, NSE, SEC, FINRA, FCA,
  ESMA, MiFID, ASIC, market abuse, surveillance, recordkeeping.
- **Tier 5 — critical live-trading/risk**: live order execution, kill
  switches, exposure limits, capital protection, broker connectivity,
  critical risk controls.

State the tier and a one-line justification before proceeding. This
gates which parts of Sections 9 and 15 you actually apply below.

## 2. Load the target skill

Read `SKILL.md`, `references/`, `scripts/`, `assets/`, and this skill's
tests, in full. Also check `index.json`, `mappings/*.md`,
`docs/skill-anatomy.md`, and `docs/ROADMAP_500.md` for how this skill is
classified and cross-referenced. If `Related Skills` are listed, read
enough of each (frontmatter + workflow) to spot overlap or contradiction
— don't recursively pull in their entire dependency graphs.

Don't blindly load the rest of the repository. Only what's needed to
understand this skill, its scope, its dependencies, its neighbors, and
repo conventions.

## 3. Define the skill's purpose

Before touching anything, be able to state: what problem does this skill
solve, who/when should use it, when should it NOT be used, what
broker/exchange/regulatory/quantitative surface does it touch, and what
are its critical failure modes. If you can't answer these from the
existing material, that's itself a documentation gap — record it.

## 4. Baseline the current implementation

Before changing files, inspect the current implementation, existing
tests (run them), current documentation, current behavior, public APIs,
assumptions, and existing strengths. Categorize internally: currently
correct / currently incorrect / missing / unclear / untested / potential
regression risk. Don't modify anything yet.

## 5. Research gate

Research is **required** for: current regulatory requirements, broker/
exchange API behavior, rate limits, auth requirements, order semantics,
current market-structure or compliance rules, mathematical definitions
where correctness is uncertain, named standards, anything that may have
changed over time.

Research is **not automatically required** for: obvious Python language
behavior, repo-local conventions, basic already-established algorithms,
purely editorial improvements, or claims already directly supported by
authoritative references already in the skill.

Source priority: regulator/government → exchange/market operator →
official broker docs → official vendor docs → official standards docs →
peer-reviewed research → reputable institutional research → secondary
sources only when primary evidence is unavailable. Never rely on a
random blog when an authoritative source exists.

Use as many WebSearch/WebFetch calls as the claims actually require —
no arbitrary cap, since this is the pass where quality is decided.

## 6. Research quality control

For each important external claim, track: claim / source / date /
jurisdiction / applicability / confidence / implementation impact.

For regulatory claims, verify: regulator, jurisdiction, rule/document,
article/rule number, applicability, effective status, exceptions, and
whether it's mandatory or advisory.

For formulas, verify: canonical definition, units, assumptions, edge
cases, numerical considerations.

For broker/exchange behavior, verify: endpoint, request/response
semantics, auth, rate limits, order behavior, error behavior, relevant
restrictions.

If a claim can't be sufficiently verified: don't guess. Soften it,
qualify it, move it to a limitation, or remove it.

## 7. Gap analysis

Build this table before implementing anything:

| Area | Finding | Evidence | Severity | Action |
|---|---|---|---|---|

- **Critical**: incorrect behavior, unsafe trading behavior, wrong
  calculation, regulatory misinformation, security vulnerability, data
  leakage/look-ahead bias, material production failure.
- **High**: significant edge case, important missing validation, missing
  test for critical behavior, unreliable state handling, important
  documentation ambiguity, material maintainability issue.
- **Medium**: useful robustness improvement, missing non-critical test,
  incomplete documentation, minor reliability issue.
- **Low**: cosmetic, optional, style-only, speculative optimization.

Implement Critical + High. Implement Medium when it has clear practical
value. Normally defer Low. Never modify code just to make the skill look
more sophisticated.

## 8. Early-stop quality gate

Ask: is the current implementation already correct and production-
appropriate for its risk tier? If yes — don't do a cosmetic rewrite.
Instead: fix only genuine gaps, strengthen tests if justified, correct
documentation inaccuracies, verify research-sensitive claims, validate,
and report that no major rewrite was necessary. **"No code change
required" is a successful outcome, not a failure to find work.**

## 9. Implementation principles

Apply only the checklists relevant to this skill's Section 1 tier —
don't reason through live-order-execution edge cases for a Tier 1
utility.

**Python (all tiers):** full type hints, explicit input validation,
deterministic behavior, structured logging (no bare prints), specific
exception handling (no bare `except:`), no hidden global state, clear
docstrings for non-obvious behavior, clean separation of concerns, safe
handling of external input. Don't over-engineer.

**Quantitative (Tier 2+):** formula correctness, units, dimensions,
numerical stability, NaN/Inf handling, zero denominators, insufficient
observations, boundary values, overflow/underflow.

**Time series (where applicable):** look-ahead bias, data leakage,
timestamp ordering, timezone handling, duplicate/missing observations,
stale data, session-boundary errors.

**Trading (Tier 3+):** partial fills, rejected orders, duplicate orders,
stale orders, order state transitions, position mismatch, insufficient
capital, invalid quantities, slippage, fees, latency, exposure, session
boundaries, idempotency, retry safety.

**Live/concurrent (Tier 5, or Tier 3-4 skills that touch live state):**
race conditions, shared state, atomicity, duplicate execution, retry
behavior, failure recovery, timeout handling, cancellation, shutdown
behavior.

**Regulatory (Tier 4+):** make jurisdiction explicit, distinguish
mandatory rules from guidance, avoid universalizing jurisdiction-specific
requirements, preserve source attribution, keep compliance decisions
auditable, never hard-code unsupported thresholds.

## 10. SKILL.md quality standard

Must accurately describe the actual implementation. Include, where
applicable: **When to Use / When NOT to Use / Prerequisites / Workflow /
Common Pitfalls / Verification / Related Skills.**

Workflow needs real decision points, not generic instructions — e.g. not
"validate the input and execute the strategy" but "if the broker rejects
the order, classify the rejection before retrying; do not blindly retry
a potentially non-idempotent submission."

Pitfalls must be specific, not "handle errors properly" but e.g. "don't
retry an order submission solely because the HTTP request timed out —
the broker may have already accepted the order before the client lost
the response."

Never document functionality that doesn't exist.

## 11. References / standards

Update only when justified — no citation spam. Each reference must
actually support its associated claim: source, title, relevant section,
URL, currentness. Never fabricate a reference.

## 12. Test engineering

Tests verify behavior, not implementation details. For changed logic,
cover as applicable: normal cases, boundary cases, invalid input, error
conditions, regression cases, numerical edge cases, time-series edge
cases, trading-state edge cases, regulatory triggers, AI-agent misuse
cases.

For quantitative logic, use independently derived expected values — a
test that just repeats the implementation's own formula tests nothing.
A good regression test fails against the old broken behavior and passes
against the fix. Avoid tests that pass trivially either way.

## 13. Performance

Don't optimize on theory alone — first confirm performance is actually
relevant given data size and call frequency. A more complex O(log n)
isn't automatically better than a simple O(n) if the real workload is
small. Don't trade correctness or readability for insignificant gains.

## 14. Validation

Run, in order, don't assume:
```
python tools/validate_skills.py --skill $ARGUMENTS
python -m pytest tests/ -k "$ARGUMENTS" -q
python -m unittest discover -s skills/$ARGUMENTS/scripts   # if relevant
```
If the repo's actual validation commands differ from these, discover and
use the real convention. If a script changed, run a realistic smoke test
— import success alone doesn't count. If `index.json` needs regenerating
because SKILL.md metadata changed, run that process too, scoped to this
skill.

If targeted tests fail, fix them before proceeding. If unrelated repo
tests fail, check whether the failure predates your change — don't
attribute pre-existing failures to yourself, and don't hide them either.

## 15. Adversarial review

After implementing, stop thinking like the implementer and try to break
your own change. Apply what's relevant to this skill's tier: malformed
input, missing/stale data, exact-threshold behavior, market open/close
edges, broker rejection, partial fills, network timeout (could the order
have succeeded anyway?), duplicate execution, look-ahead bias, silent
NaN propagation, a regulatory rule misapplied to the wrong jurisdiction,
an AI agent misreading the workflow, documentation that could cause
unsafe behavior. Fix genuine defects found here. Don't invent
hypothetical complexity with no practical relevance to this skill.

## 16. Consistency audit

Compare against 2-3 genuinely related skills: terminology, frontmatter,
section structure, naming, workflow style, code style, testing style,
related-skill links, domain conventions. Check `index.json`,
`mappings/*.md`, `docs/skill-anatomy.md`, `docs/ROADMAP_500.md`. If you
find a contradiction in *another* skill, don't silently fix it — report
it as a cross-skill finding unless resolving it is directly required by
this skill's change.

## 17. Final diff audit

Inspect the full diff before finishing. **A large diff is not itself a
problem** — a skill with genuinely wrong calculations, unsafe order
handling, or fabricated regulatory claims *should* produce a large diff,
and shrinking a legitimate fix to look conservative is a worse outcome
than leaving the bug in place.

The check is justification, not size:
- Every changed line must trace back to a specific row in the Section 7
  gap table, or be a direct consequence of one (a corrected formula
  requiring updated tests, for instance).
- If every hunk maps to a gap-table row, the diff is justified whatever
  its size — don't trim it to look smaller.
- Any hunk you *can't* trace to a gap-table row — reformatting, renames,
  "while I was in there" restructuring, style preferences — is scope
  creep. Revert that hunk specifically, not the justified ones.

Also confirm: no debug code, no temporary files, no secrets, no
accidental API break, no unused imports, no unnecessary dependencies,
tests actually cover the changed behavior, docs match the implementation.

## 18. Final validation gate

You may only declare success if you can check off: scope understood,
risk tier classified, baseline established, relevant implementation
inspected, relevant research completed, claims verified or explicitly
qualified, gaps identified, changes justified, implementation complete,
tests updated where necessary, targeted tests pass, changed scripts
smoke-tested, repo validation completed where appropriate, adversarial
review completed, documentation synchronized, cross-skill consistency
checked, final diff reviewed, no secrets/debug files introduced, no
unrelated changes introduced. If any item can't be satisfied, state that
explicitly in the report — never hide it.

## 19. Final report

Return this directly as your response text. **Do not create a report
file** — no `IMPROVEMENT_REPORT.md`, `AUDIT_REPORT.md`, `SUMMARY.md`, or
any file whose purpose is to hold this report. If you find yourself
about to create a file with "report," "summary," or "audit" in the name,
stop — print it instead. The only files that should exist on disk when
you finish are the actual skill files you edited in Sections 9-12.

```
# <skill-slug> — Engineering Audit

## Verdict
PRODUCTION READY / PRODUCTION READY WITH LIMITATIONS / NEEDS FURTHER WORK
/ NOT PRODUCTION READY — one or two precise sentences why.

## Risk Tier
Tier 1-5, with justification.

## Research Performed
Per important area: Claim / Source / Verification result / Impact.
Only list sources actually consulted.

## Critical Fixes
Issue / evidence / change / validation. Or: None.

## Recommended (High) Fixes
Issue / rationale / change. Or: None.

## Nice-to-Have Fixes Applied
Or: None.

## Deferred
Every item needs a reason. Or: None.

## Files Changed
Path — what changed — why.

## Validation
Exact commands run, PASS/FAIL, relevant output. Don't claim commands
you didn't run.

## Cross-Skill Findings
Contradictions or repo-level opportunities noticed. Or: None.

## Scorecard (1-5, justification for every score)
Production Readiness · Quantitative Correctness · Engineering Quality ·
Documentation · Code Quality · Testing · AI Agent Usability · Reliability
· Maintainability · Repository Consistency

## Final Change Summary
Modified files: N — Tests added/changed: N — Dependencies added: Y/N —
Breaking changes: Y/N — Research-backed changes: Y/N — Validation:
PASS / PASS WITH LIMITATIONS / FAIL
```

## 20. Completion

When all applicable phases are done: ensure justified changes are saved,
run final validation, inspect the final diff, produce the report, stop.
Do not commit. Do not push. Do not modify another skill. Do not create a
report file. The final chat response is the audit report.

---

## Running this — one skill at a time (recommended)

```
/improve-skill order-placement-idempotency
```

Read the report before running the next one. Each invocation is already
a fresh context — no skill's audit shares a window with another's — so
manual mode costs you wall-clock time, not context quality, and buys you
a human catching a misread convention or thin sourcing before it repeats
across dozens more skills. Worth it, especially for the first 20-30
skills while confirming the prompt behaves as intended on your content.

## Unattended loop (only once you trust the output)

```bash
for slug in $(python -c "import json;print('\n'.join(s['slug'] for s in json.load(open('index.json'))['skills']))"); do
  claude -p "/improve-skill $slug" --model claude-opus-5 --effort high >> logs/audit_$slug.log
done
```

Adjust the `index.json` field name to match your schema. Run in small
batches (one domain at a time) and read every report in the batch before
starting the next — don't let it run across all 504 unattended between
reviews.