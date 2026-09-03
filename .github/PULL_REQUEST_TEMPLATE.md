## What does this PR change?

## Checklist

- [ ] Follows the skill directory structure (`SKILL.md` + `references/` + `scripts/` + `assets/`)
- [ ] Frontmatter follows the agentskills.io form: `name`, `description`, `license` at the top level, repo fields (`domain`, `subdomain`, `tags`, `brokers_frameworks`, `version`, `author`) under `metadata:` as strings
- [ ] `description` starts with "Use when …" and fits in 280 characters
- [ ] All seven body sections present, including `When NOT to Use` handing each excluded case to the skill that owns it
- [ ] Reflects a real production failure mode, not generic advice available anywhere
- [ ] `Verification` section describes a concrete, checkable test — not just "it works"
- [ ] `python tools/validate_skills.py` passes, and `agentskills validate skills/<name>` passes on Python 3.11+
- [ ] `index.json` and `.claude-plugin/marketplace.json` regenerated (`python tools/build_index.py && python tools/build_marketplace.py`) if a skill was added, removed, or renamed
- [ ] Any included script has been run at least once against a sandbox/paper environment where applicable

## Title format

Use `Add skill: your-skill-name` for new skills, or a plain descriptive title for fixes.
