# Contributing to Algo-Trading-Skills

Contributions are welcome, especially skills that reflect genuine production
experience rather than textbook advice. This project grows through the same
kind of hard-won engineering knowledge it was started with — if you've hit a
real production bug building a trading bot, that's exactly what belongs here.

## Before you start

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

## Review process

PRs are reviewed for: technical accuracy, adherence to the structure enforced
by `tools/validate_skills.py`, and whether the skill meets the quality bar
above. Please be patient — this is a community project reviewed by
maintainers in their spare time.

## Code of Conduct

By participating, you agree to abide by our [Code of Conduct](CODE_OF_CONDUCT.md).
