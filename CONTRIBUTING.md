# Contributing to Algo-Trading-Skills

Contributions are welcome, especially skills that reflect genuine production
experience rather than textbook advice. This project grows through the same
kind of hard-won engineering knowledge it was started with — if you've hit a
real production bug building a trading bot, that's exactly what belongs here.

## Before you start

Check [`docs/ROADMAP_500.md`](docs/ROADMAP_500.md) first — it's a 502-entry backlog
of titled, scoped skills covering global brokers, exchanges, regulatory regimes,
execution algorithms, custody, and more. Most contributions should pick up an
existing `[planned]` entry there rather than proposing something entirely new,
since the scoping work (deciding it's worth a skill, picking a category, writing
a one-line description) is already done. When you build one out:

1. Do the research — verify the broker/exchange/regulatory specifics against
   current, authoritative sources (broker docs, exchange rulebooks, regulator
   publications). Roadmap entries were scoped from general domain knowledge, not
   verified against live documentation, so this step matters.
2. Follow the full structure in `docs/skill-anatomy.md` when building it out.
3. Flip the entry's status from `planned` to `built` in `index.json` and remove
   its `[planned]` line from `docs/ROADMAP_500.md` once it's built and validated.

Open an issue first for:
- New category proposals or structural changes to the repo layout
- Larger multi-skill additions

For a single new skill or a fix to an existing one, a PR is fine directly.

## Adding a new skill

1. Create a new directory: `skills/<your-skill-name>/` (kebab-case, matching
   the `name` field you'll put in frontmatter).
2. Populate it following the structure documented in `docs/skill-anatomy.md`:
   - `SKILL.md` — required frontmatter fields + all six required sections
     (`When to Use`, `Prerequisites`, `Workflow`, `Common Pitfalls`,
     `Verification`, `Related Skills`)
   - `references/standards.md` — broker/framework coverage table
   - `references/workflows.md` — full technical procedure detail
   - `scripts/` — at least one working reference implementation or helper
   - `assets/checklist.md` — a sign-off checklist derived from your
     Verification section
3. Add an entry for your skill to `index.json`.
4. Run `python tools/validate_skills.py` locally and fix anything it flags
   before opening a PR.
5. Open a PR titled `Add skill: your-skill-name`.

## Quality bar

Before submitting, check your skill against these questions:

- **Would this have actually prevented a real production bug** if an AI agent
  had followed it? Not "is this good practice" in the abstract — did something
  break, in a real or realistic system, because this wasn't done?
- **Is it specific enough to execute, or vague enough to be useless?**
  "Handle errors properly" is not a skill. Naming the exact failure mode and
  the exact step that prevents it is.
- **Does the Verification section describe something checkable?** A reviewer
  or an agent should be able to look at the described test and know whether
  the skill was actually followed, not just take it on faith.
- **Does it avoid overlapping too heavily with an existing skill?** Check
  `index.json` and the category README-equivalents first; if your idea
  extends an existing skill rather than standing alone, consider a PR against
  that skill instead.

## Improving existing skills

- Fixing an inaccurate procedure, outdated broker behavior, or broken script:
  open a PR directly, referencing what changed and why.
- Adding broker coverage: extend `references/standards.md` for the relevant
  skill and update `mappings/broker-api-coverage.md`.
- Reporting without fixing: open an issue using the bug report template.

## Initial 504 Skills Verification Process

All 504 skills included in the initial release underwent a multi-tier verification process before publication:

1. **Domain & API Specification Verification**: Every skill's technical procedure was cross-referenced against authoritative broker API documentation (Fyers v3, Zerodha Kite Connect, ICICI Breeze, Upstox v2, Alpaca, IBKR TWS/Gateway), exchange rulebooks (CME Globex, Eurex, HKEX, SGX, ASX, JPX, CBOE, LSE), and regulatory publications (US SEC 15c3-5/Reg NMS/Reg SHO, EU MiFID II/RTS 6/MAR, UK FCA SYSC 25, SEBI Algo Circulars, MAS, ASIC, ISDA).
2. **Automated Structural & Schema Validation**: Verified via `python tools/validate_skills.py`, ensuring 100% compliance with frontmatter schemas, required sections, file hierarchy, `scripts/` layout (a helper module plus a `test_*.py` suite, never a helper named `test_*.py`), documented test commands runnable from the repository root, and `index.json` consistency.
3. **Executable Unit Test Suites**: Executed via `python tools/run_all_tests.py`, running over 20,300 unit tests across 504 test files in `skills/*/scripts/test_*.py` using Python's `unittest` framework.
4. **CI/CD Continuous Enforcement**: Every pull request and push automatically executes structural validation and unit test suites via GitHub Actions (`.github/workflows/validate-skills.yml`).

## Review process

PRs are reviewed for: technical accuracy, adherence to the structure enforced
by `tools/validate_skills.py`, passing unit tests in `tools/run_all_tests.py`, and whether the skill meets the quality bar
above. Please be patient — this is a community project reviewed by
maintainers in their spare time.

## Code of Conduct

By participating, you agree to abide by our [Code of Conduct](CODE_OF_CONDUCT.md).

