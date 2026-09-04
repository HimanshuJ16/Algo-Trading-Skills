# Contributing to Algo-Trading-Skills

Contributions are welcome, especially skills that reflect genuine production
experience rather than textbook advice. This project grows through the same
kind of hard-won engineering knowledge it was started with — if you've hit a
real production bug building a trading bot, that's exactly what belongs here.

## Before you start

Read [`docs/skill-anatomy.md`](docs/skill-anatomy.md) — it documents the structure and
frontmatter contract that `tools/validate_skills.py` enforces. Then check
[`docs/ROADMAP_500.md`](docs/ROADMAP_500.md), which lists every skill in the library
grouped by its domain, so you can see what already exists before proposing something
new.

## How the skills are written

The skills are drafted with AI assistance against primary sources — broker documentation,
exchange rulebooks, regulator publications — and then reviewed, corrected and tested by
people. Every claim is expected to trace to a source a reader can check, every helper
ships its own test suite, and the machine-enforced contract in `tools/validate_skills.py`
keeps the structure honest. A contribution written the same way is welcome; a contribution
that pastes unreviewed model output is not. If you cannot verify a broker or regulatory
claim, leave it out rather than guess.

Open an issue first for:
- New category proposals or structural changes to the repo layout
- Larger multi-skill additions

For a single new skill or a fix to an existing one, a PR is fine directly.

## Adding a new skill

1. Create a new directory: `skills/<your-skill-name>/` (kebab-case, matching
   the `name` field you'll put in frontmatter).
2. Populate it following the structure in `docs/skill-anatomy.md`:
   - `SKILL.md` — agentskills.io frontmatter (repo fields under `metadata:`) plus all
     seven required sections (`When to Use`, `When NOT to Use`, `Prerequisites`,
     `Workflow`, `Common Pitfalls`, `Verification`, `Related Skills`)
   - `references/standards.md` — broker/framework coverage table
   - `references/workflows.md` — full technical procedure detail
   - `scripts/` — at least one working helper module plus its `test_*.py` suite
   - `assets/checklist.md` — a sign-off checklist derived from your Verification section
3. Do the research. Verify broker, exchange and regulatory specifics against current,
   authoritative sources (broker docs, exchange rulebooks, regulator publications), and
   cite them in `references/standards.md`. Where no external standard exists, say so and
   label your numbers as configurable defaults rather than inventing an authority.
4. Run the gates locally and fix anything they flag:

```bash
python tools/validate_skills.py                              # structure, frontmatter, cross-refs, packaging
agentskills validate skills/<your-skill-name>                 # the agentskills.io spec itself (pip install skills-ref, Python 3.11+)
python -m unittest discover -s skills/<your-skill-name>/scripts
python tools/build_index.py && python tools/build_marketplace.py   # regenerate the generated files
```

5. Commit the regenerated `index.json` and `.claude-plugin/marketplace.json` alongside
   your skill. Both are generated artifacts — never hand-edit them.
6. Open a PR titled `Add skill: your-skill-name`.

## Quality bar

Before submitting, check your skill against these questions:

- **Would this have actually prevented a real production bug** if an AI agent
  had followed it? Not "is this good practice" in the abstract — did something
  break, in a real or realistic system, because this wasn't done?
- **Is it specific enough to execute, or vague enough to be useless?**
  "Handle errors properly" is not a skill. Naming the exact failure mode and
  the exact step that prevents it is.
- **Does the description say when to trigger?** It must start with "Use when …" and fit
  in 280 characters. An agent picks a skill from its description alone.
- **Does the Verification section describe something checkable?** A reviewer
  or an agent should be able to look at the described test and know whether
  the skill was actually followed, not just take it on faith.
- **Does `## When NOT to Use` hand each excluded case to the skill that owns it?**
  An agent applying a correct playbook to the wrong problem is its own failure mode.
- **Does it avoid overlapping too heavily with an existing skill?** Check
  `index.json` first; if your idea extends an existing skill rather than standing
  alone, consider a PR against that skill instead.

## Improving existing skills

- Fixing an inaccurate procedure, outdated broker behavior, or broken script:
  open a PR directly, referencing what changed and why.
- Bump `metadata.version` in the skill's frontmatter when you change what it says or
  does: patch for a correction, minor when the workflow, helper API or tests gain
  something, major for a rewrite. Then regenerate `index.json`.
- Adding broker coverage: extend `references/standards.md` for the relevant
  skill and update `mappings/broker-api-coverage.md`.
- Reporting without fixing: open an issue using the bug report template.

## How the library is verified

Every push and pull request runs [`.github/workflows/validate-skills.yml`](.github/workflows/validate-skills.yml)
on Python 3.10, 3.12 and 3.13:

1. **Structural and frontmatter validation** — `python tools/validate_skills.py` checks
   the frontmatter contract, the seven body sections, `scripts/` layout, every skill
   cross-reference in skills and repo docs, that documented test commands run from the
   repository root, and that the plugin manifests cover every skill exactly once.
2. **Specification conformance** — the `skills-ref` package runs the agentskills.io
   reference validator against each skill.
3. **Generated files** — `build_index.py --check` and `build_marketplace.py --check`
   fail if `index.json` or `marketplace.json` is stale.
4. **Tests** — the repository suite under `tests/`, then every skill's own unittest
   suite via `tools/run_all_tests.py`, each in an isolated subprocess with a timeout.
5. **Examples** — the three cookbook scripts in `examples/` must run clean.

A regulatory or broker-behaviour claim must be verifiable against an authoritative
source. **A missing claim is better than a wrong or fabricated one.**

## Review process

PRs are reviewed for technical accuracy, adherence to the enforced structure, passing
tests, and the quality bar above. Please be patient — this is a community project
reviewed by maintainers in their spare time.

## Code of Conduct

By participating, you agree to abide by our [Code of Conduct](CODE_OF_CONDUCT.md).
