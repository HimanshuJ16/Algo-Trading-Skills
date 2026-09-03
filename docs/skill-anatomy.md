# Skill Anatomy

Every skill follows a consistent directory structure so agents can rely on the same
loading pattern for all 504 (and any future) skills:

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
    └── checklist.md       ← Printable pre-flight / sign-off checklist
```

## Why this split

`SKILL.md` is the agent-facing entry point — short enough to scan quickly, complete
enough to act on. `references/` holds the deeper technical context an agent (or human)
loads only once it's actually implementing the skill, not while deciding whether it
applies. `scripts/` gives a concrete, runnable starting point rather than describing
a pattern in prose alone. `assets/checklist.md` gives a copy-pasteable sign-off
artifact for the specific gates (paper-trading promotion, risk-control testing) where
a checklist is more useful than another paragraph.

## YAML frontmatter (real example)

```yaml
---
name: order-placement-idempotency
description: >-
  Use whenever a bot places, modifies, or cancels live orders and must
  guarantee it never double-executes an order due to retries, timeouts,
  or reconnects
domain: algorithmic-trading
subdomain: broker-integration
tags: ["broker-integration", "fyers-api-v3", "zerodha-kite-connect", "icici-breeze-api"]
brokers_frameworks: ["Fyers API v3", "Zerodha Kite Connect", "ICICI Breeze API", "Upstox API v2", "Alpaca Trading API", "IBKR API"]
version: "1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---
```

Frontmatter fields:

| Field | Meaning |
|---|---|
| `name` | kebab-case identifier, must match the directory name |
| `description` | keyword-rich, written for agent discovery (what triggers this skill) |
| `domain` | always `algorithmic-trading` in this repo |
| `subdomain` | one of the six categories (`broker-integration`, `real-time-architecture`, `backtesting-methodology`, `financial-ml`, `risk-management`, `deployment-ops`) |
| `tags` | subdomain + up to 3 broker/framework tags, for keyword search |
| `brokers_frameworks` | full list of broker APIs / frameworks the skill references |
| `version` | skill content version, bump on material rewrites |
| `author` | attribution |
| `license` | `Apache-2.0`, matching the repo license |

## Markdown body sections

```
## When to Use        — trigger conditions for an agent to invoke this skill
## When NOT to Use     — scope boundaries, each handing off to the skill that owns it
## Prerequisites       — required tools, access, and environment setup
## Workflow            — step-by-step execution guide (full detail also in references/workflows.md)
## Common Pitfalls     — specific, named failure modes this skill prevents
## Verification        — concrete tests that confirm the skill was followed correctly
## Related Skills      — cross-links to other skills in this repo
```

Two conventions in the tree above are enforced rather than merely suggested:

- The helper and its suite are told apart **by filename**. `tools/run_all_tests.py`
  discovers suites by globbing `test_*.py`, so a helper that itself starts with `test_`
  is collected as if it were a suite. Name the helper after what it implements.
- Every test command quoted in a skill's Markdown must be the repo-root form
  `python -m unittest discover -s skills/<skill-name>/scripts`, and `SKILL.md` must quote
  it at least once so the Verification section is actionable. A path relative to the
  skill directory reads fine and fails when run.

See `tools/validate_skills.py` for the machine-enforced version of this contract.
