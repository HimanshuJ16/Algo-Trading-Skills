# Skill Anatomy

Every skill follows a consistent directory structure so agents can rely on the same
loading pattern for all of them:

```
skills/<skill-name>/
├── SKILL.md              ← Skill definition (YAML frontmatter + Markdown body)
├── references/
│   ├── standards.md      ← Broker/framework coverage + regulatory touchpoints
│   └── workflows.md      ← Deep technical procedure reference
├── scripts/
│   ├── <helper>.py       ← Working reference implementation / helper
│   └── test_<helper>.py  ← unittest suite for it
└── assets/
    └── checklist.md      ← Sign-off checklist for the skill's Verification section
```

## Why this split

`SKILL.md` is the agent-facing entry point — short enough to scan quickly, complete
enough to act on. `references/` holds the deeper technical context an agent (or human)
loads only once it's actually implementing the skill, not while deciding whether it
applies. `scripts/` gives a concrete, runnable starting point rather than describing
a pattern in prose alone. `assets/checklist.md` gives a copy-pasteable sign-off
artifact for the specific gates (paper-trading promotion, risk-control testing) where
a checklist is more useful than another paragraph.

## YAML frontmatter

The frontmatter follows the [agentskills.io specification](https://agentskills.io/specification)
exactly. The spec allows only six top-level fields — `name`, `description`, `license`,
`compatibility`, `allowed-tools` and `metadata` — so everything this repository adds
lives under `metadata:` as **string** values (the spec defines `metadata` as a map from
string keys to string values, so lists are joined into one string):

```yaml
---
name: order-placement-idempotency
description: >-
  Use whenever a bot places, modifies, or cancels live orders and must guarantee it
  never double-executes an order due to retries, timeouts, or reconnects
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: broker-integration
  tags: broker-integration, idempotency, client-order-id, order-ledger, retry-safety
  brokers_frameworks: Fyers API v3; Zerodha Kite Connect; Upstox API v2; IBKR API
  version: "2.0.0"
  author: algo-trading-skills-contributors
---
```

| Field | Meaning |
|---|---|
| `name` | kebab-case identifier, at most 64 characters, must match the directory name |
| `description` | the **trigger**: starts with "Use when …", at most 280 characters |
| `license` | `Apache-2.0`, matching the repository LICENSE |
| `metadata.domain` | always `algorithmic-trading` in this repo |
| `metadata.subdomain` | one of the 16 domains listed in [`ROADMAP_500.md`](ROADMAP_500.md) |
| `metadata.tags` | comma-separated keywords for search |
| `metadata.brokers_frameworks` | semicolon-separated broker APIs / frameworks the skill references (semicolons, because vendor names contain commas) |
| `metadata.version` | skill content version, quoted semver; bump patch for a correction, minor when the workflow, helper API or tests gain something, major for a rewrite, then regenerate `index.json` |
| `metadata.author` | `algo-trading-skills-contributors` |

Two constraints on `description` deserve emphasis, because they are what makes a
library this size usable:

- **It must start with `Use when …`.** The description is the only thing an agent sees
  before deciding to open a skill. A noun phrase ("Institutional compliance skill
  for …") describes the subject; a trigger describes the situation the agent is
  currently in, which is what it actually has to match against.
- **It must fit in 280 characters.** The spec permits 1024, but every installed skill's
  description is loaded into the model's context at the start of every session. The
  cap keeps a whole domain's worth of skills affordable to load.

## Markdown body sections

```
## When to Use        — trigger conditions for an agent to invoke this skill
## When NOT to Use    — scope boundaries, each handing off to the skill that owns it
## Prerequisites      — required tools, access, and environment setup
## Workflow           — step-by-step execution guide (full detail also in references/workflows.md)
## Common Pitfalls    — specific, named failure modes this skill prevents
## Verification       — concrete tests that confirm the skill was followed correctly
## Related Skills     — cross-links to other skills in this repo
```

All seven are required, as exact level-2 headings.

Three further conventions are enforced rather than merely suggested:

- The helper and its suite are told apart **by filename**. `tools/run_all_tests.py`
  discovers suites by globbing `test_*.py`, so a helper that itself starts with `test_`
  is collected as if it were a suite. Name the helper after what it implements.
- Every test command quoted in a skill's Markdown must be the repo-root form
  `python -m unittest discover -s skills/<skill-name>/scripts`, and `SKILL.md` must quote
  it at least once so the Verification section is actionable. A path relative to the
  skill directory reads fine and fails when run.
- Every backticked skill slug in `## Related Skills` — and in the repo-level docs —
  must name a real directory under `skills/`.

## Checking your work

```bash
python tools/validate_skills.py                            # this contract, machine-enforced
agentskills validate skills/<skill-name>                    # the agentskills.io spec itself (pip install skills-ref, 3.11+)
python -m unittest discover -s skills/<skill-name>/scripts   # the skill's own suite
```

See [`tools/validate_skills.py`](../tools/validate_skills.py) for the enforced version
of everything above.
