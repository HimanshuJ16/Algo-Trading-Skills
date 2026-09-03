<!--
An internal working prompt used when revising an existing skill. It is not part of the
skill contract; see docs/skill-anatomy.md for that, and CONTRIBUTING.md for the bar a
change has to clear.
-->

# Production Skill Improvement Workflow

The objective is **not** to rewrite skills.

The objective is to continuously improve every skill until it reaches institutional-grade engineering quality while maintaining complete consistency across the repository.

Every modification must have a measurable engineering benefit.

---

# Phase 1 — Repository Context

## 1. Load the Skill

Locate the target skill.

Load every file belonging to the skill.

- SKILL.md
- references/standards.md and references/workflows.md
- scripts/ (the helper module and its test_*.py suite)
- assets/checklist.md

Treat the skill as one integrated engineering package rather than independent files.

---

## 2. Understand Before Changing

Before making any modifications, build a complete understanding of the skill.

Identify:

- the engineering problem being solved
- intended AI agent behavior
- architecture assumptions
- trading workflow
- dependencies
- failure boundaries
- expected inputs
- expected outputs

Do not begin rewriting until the purpose of the skill is completely understood.

---

# Phase 2 — Institutional Research

Perform targeted research on the specific domain covered by this skill.

Cross-reference against authoritative sources where applicable, such as:

- Exchange documentation
- Broker API documentation
- Regulatory frameworks
- Python best practices
- Distributed systems engineering
- Cloud architecture
- Institutional quantitative engineering
- Market microstructure
- Risk management
- Production DevOps

Research should focus on identifying improvements that materially increase production quality.

Avoid generic information.

Prioritize practical engineering knowledge.

---

# Phase 3 — Gap Analysis

Compare the existing implementation against institutional best practices.

Identify gaps in:

## Documentation

- missing concepts
- ambiguity
- unclear workflows
- incomplete verification
- missing recovery procedures

## Python Code

- architecture
- typing
- modularity
- maintainability
- logging
- validation
- performance
- error handling
- security
- testability

## Engineering

- operational safety
- failure handling
- monitoring
- observability
- configuration
- deployment readiness
- deterministic execution

## AI Agent Experience

Evaluate whether another AI agent can confidently determine:

- when to use the skill
- when not to use it
- prerequisites
- workflow
- decision points
- validation
- recovery
- success criteria

---

# Phase 4 — Improvement Plan

Before modifying any file, produce a prioritized improvement plan.

Classify every recommendation as:

## Critical

Production failures

Incorrect behavior

Security issues

Regulatory issues

Incorrect calculations

Unsafe assumptions

Missing edge-case handling

---

## Recommended

Maintainability

Performance

Documentation

Reliability

Testing

Architecture

---

## Nice to Have

Readability

Examples

Additional references

Minor optimizations

Only implement improvements that provide measurable value.

Avoid cosmetic changes.

---

# Phase 5 — Implementation

Update only the files that genuinely require improvement.

Maintain:

- folder structure
- naming conventions
- writing style
- repository consistency

Never rewrite content solely for stylistic reasons.

Every modification should have a clear technical justification.

---

# Phase 6 — Python Engineering

When modifying scripts:

Ensure they meet production standards.

Review:

- correctness
- typing
- architecture
- validation
- configuration
- structured logging
- exception handling
- deterministic behavior
- memory efficiency
- algorithmic complexity
- concurrency safety
- security
- portability
- maintainability

Avoid introducing unnecessary abstractions.

Prefer simple, explicit, well-tested implementations.

---

# Phase 7 — Documentation Engineering

Upgrade documentation to maximize usefulness for AI coding agents.

Ensure SKILL.md clearly defines:

- purpose
- scope
- when to use
- when not to use
- prerequisites
- workflow
- decision points
- edge cases
- failure modes
- recovery
- verification
- related skills

References should contain:

- implementation guidance
- architecture notes
- institutional best practices
- broker or exchange nuances
- troubleshooting
- additional context

Checklists should contain:

- prerequisites
- validation
- deployment
- rollback
- monitoring
- post-deployment verification

---

# Phase 8 — Test Engineering

Review existing tests.

Expand coverage where necessary.

Verify:

- normal operation
- boundary conditions
- invalid inputs
- edge cases
- mathematical correctness
- regulatory rules
- recovery behavior
- exception handling

Tests should validate behavior rather than implementation details.

---

# Phase 9 — Local Validation

Before finalizing:

- Execute the full test suite.
- Ensure all tests pass.
- Verify formatting and linting.
- Confirm no regressions were introduced.
- Ensure documentation matches the implementation.
- Confirm examples remain accurate.

Do not consider the skill complete unless validation succeeds.

---

# Phase 10 — Repository Consistency Review

Verify the updated skill remains consistent with the rest of the repository.

Review:

- terminology
- writing style
- section organization
- workflow structure
- naming conventions
- documentation depth
- AI agent experience

Do not introduce inconsistencies.

---

# Phase 11 — Engineering Report

Produce a structured report containing:

## Executive Summary

Overall assessment.

## Improvements Made

Summarize all meaningful improvements.

## Critical Issues Resolved

List production-impacting problems fixed.

## File-by-File Changes

Explain modifications made to each file.

## Validation Results

Summarize testing and verification outcomes.

## Remaining Recommendations

List improvements intentionally deferred and explain why.

## Final Scorecard

Rate:

- Production Readiness
- Engineering Quality
- Documentation
- Code Quality
- AI Agent Usability
- Reliability
- Maintainability
- Repository Consistency

Provide concise justification for each score.

---

# Guiding Principles

Always prioritize:

- correctness over cleverness
- production reliability over code brevity
- deterministic behavior over implicit behavior
- maintainability over unnecessary abstraction
- engineering value over cosmetic changes
- consistency over personal preference

If a file is already excellent, explicitly state that no changes are required.

The goal is not to maximize the number of edits.

The goal is to maximize the long-term quality, reliability, and usefulness of the repository.