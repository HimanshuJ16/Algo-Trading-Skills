![Algo-Trading-Skills banner](assets/banner.svg)

# Algo-Trading-Skills

### A structured algorithmic trading skills library for AI coding agents

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg?style=flat-square)](LICENSE)
[![Skills Built](https://img.shields.io/badge/skills_built-504-brightgreen?style=flat-square)](#whats-inside--16-categories)
[![Roadmap](https://img.shields.io/badge/roadmap-504_built-brightgreen?style=flat-square)](docs/ROADMAP_500.md)
[![Domains](https://img.shields.io/badge/domains-16-9cf?style=flat-square)](#whats-inside--16-categories)
[![Platforms](https://img.shields.io/badge/platforms-6%2B-blueviolet?style=flat-square)](#compatible-platforms)
[![agentskills.io](https://img.shields.io/badge/standard-agentskills.io-ff6600?style=flat-square)](https://agentskills.io)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg?style=flat-square)](CONTRIBUTING.md)

**504 production-grade algorithmic trading skills built across a global research roadmap spanning 16 engineering domains · broker/exchange coverage spanning India (Fyers, Zerodha Kite, ICICI Breeze, Upstox), US (Alpaca, IBKR, Schwab, TradeStation), global crypto (Binance, Coinbase, Kraken, Deribit), forex (OANDA, MT5), and dozens more global venues · agentskills.io standard**

> **Status note:** All 504 skills across all 16 domains are now **100% physically built, verified, and indexed**. Every skill contains full `SKILL.md` frontmatter + markdown sections, working Python engine scripts with `dataclasses`, `unittest` test suites, workflows, standards, and sign-off checklists, passing `tools/validate_skills.py`.

[Get Started](#quick-start) · [What's Inside](#whats-inside--16-categories) · [Skill Anatomy](#skill-anatomy) · [Platforms](#compatible-platforms) · [Contributing](#contributing)

---

> **Community Project.** This is an independent, community-created skills library. Not affiliated with Anthropic PBC or any broker named in this repo.
>
> **Engineering guidance, not financial advice.** These skills encode production engineering practices for trading infrastructure. They do not guarantee the profitability of any strategy and do not eliminate the risk of loss in live trading. See the [Disclaimer](#disclaimer).

## Give any AI agent the instincts of a senior trading-infrastructure engineer

An AI coding agent can write a WebSocket client, a backtest loop, or an order-placement function that looks completely correct — right library calls, clean structure, plausible logic — and still fail in production for reasons that have nothing to do with code quality: a broker invalidates a token overnight in a way its docs don't mention, a backtest silently uses a bar's own close to predict its own direction, a risk limit lives inside the same function it's supposed to constrain, a WebSocket callback blocks the read loop during exactly the volatility spike a strategy exists to catch.

**Your AI agent doesn't know these failure modes — unless you give it these skills.**

This repo contains **504 structured skills** spanning **16 engineering domains** of algorithmic trading infrastructure, each following the [agentskills.io](https://agentskills.io) open standard: dual-broker authentication, ML signal classifiers with walk-forward validation, producer-consumer tick pipelines, correlation-aware position sizing, systemd-supervised deployment, multi-asset derivatives, execution algorithms, crypto custody, multi-strategy portfolio management, market microstructure, alternative data, and global tax accounting.

## Why this exists

Cybersecurity has [Anthropic-Cybersecurity-Skills](https://github.com/mukul975/Anthropic-Cybersecurity-Skills), an 800+ skill library mapped to MITRE ATT&CK and NIST, giving AI agents the structured decision-making a senior security analyst follows. Algorithmic trading has had no comparable resource — existing repos give you broker SDKs, indicator libraries, or strategy templates, but none give an agent the practitioner playbook for *when* to use a technique, what to check first, how to execute it step by step, and how to verify it actually worked.

This is a comprehensive resource for trading: 504 production-ready skills that are each deep enough to prevent a specific, named class of production bug. Quality at scale — every skill here answers yes to: *would this have actually prevented a real production bug, and is it specific enough for an agent to follow step-by-step rather than nod along with generic advice?*

## What's inside — 16 categories

The library covers 16 core engineering domains spanning domestic and global markets — crypto exchanges, forex brokers, multi-currency and multi-timezone data handling, regulatory compliance, multi-asset derivatives, execution algorithms, custody/security, cross-strategy portfolio management, market microstructure, alternative-data research, and tax/accounting.

| Domain | Built | Total Tracked | Key coverage |
|---|---|---|---|
| [`broker-integration`](skills/) | **20** | 20 | Headless auth (REST + Selenium), token lifecycle via live probing, order idempotency, per-broker rate limiting, borrow cost modeling, cost budgeting |
| [`real-time-architecture`](skills/) | **30** | 30 | Producer-consumer tick pipelines, burst-safe buffering, explicit backpressure policy, WebSocket reconnection without duplicate subscriptions |
| [`backtesting-methodology`](skills/) | **35** | 35 | Lookahead bias elimination, walk-forward validation, realistic slippage/fee/latency simulation, synthetic data generation, standardized tearsheets |
| [`financial-ml`](skills/) | **38** | 38 | Leakage-free feature engineering, offline-train/online-infer deployment, triple barrier labeler, sample weighting, model staleness detection |
| [`risk-management`](skills/) | **39** | 39 | Kill switches and drawdown circuit breakers, correlation-aware exposure limits, Kupiec test VaR backtesting, tail risk hedging, risk escalation matrices |
| [`deployment-ops`](skills/) | **30** | 30 | systemd process supervision, paper-to-live promotion checklist, IaC for trading hosts, canary releases, chaos engineering, secrets vault |
| [`global-market-integration`](skills/) | **44** | 44 | Crypto exchange APIs (Binance/Coinbase/Kraken/Deribit/Bybit/OKX), FX (OANDA/MT5), CME Globex, Eurex, HKEX, SGX, ASX, JPX, CBOE, LSE, Xetra |
| [`regulatory-compliance-global`](skills/) | **38** | 38 | US SEC Rule 15c3-5, PDT, FINRA, EU MiFID II/RTS 6/MAR, UK FCA, ASIC, MAS, India SEBI, Canada IIROC, Hong Kong SFC, Japan FSA |
| [`multi-asset-derivatives`](skills/) | **28** | 28 | SPAN margin calculation, futures contract roll automation, real-time Greeks aggregation, perpetual futures funding rates, variance swaps, CDS, quanto options |
| [`execution-algorithms`](skills/) | **33** | 33 | TWAP/VWAP order slicing, POV execution, implementation shortfall minimization, iceberg detection, smart order routing (SOR), dark pool routing, auctions |
| [`data-management-global`](skills/) | **37** | 37 | Global exchange holiday calendars, DST transition handling, multi-timezone session scheduling, multi-currency P&L, ISIN/CUSIP/SEDOL cross-referencing |
| [`crypto-custody-security`](skills/) | **29** | 29 | Wallet key custody, hot-cold split, withdrawal whitelisting, multi-sig approval, HSM integration, Shamir secret sharing, MPC custody |
| [`portfolio-multi-strategy`](skills/) | **30** | 30 | Cross-strategy correlation monitoring, performance-based capital reallocation, strategy retirement criteria, risk parity allocation, meta-strategy signal arbitration |
| [`market-microstructure-latency`](skills/) | **24** | 24 | Colocation latency budgets, PTP clock sync, tick-to-trade measurement, order book signals, adverse selection measurement, FPGA/microwave evaluation |
| [`quant-research-alt-data`](skills/) | **20** | 20 | Satellite imagery signals, credit card transaction data, web-scraped sentiment, supply chain networks, Google Trends, social media bot filtering, transcript NLP |
| [`tax-accounting-reporting-global`](skills/) | **16** | 16 | US wash sale tracking, FIFO vs specific-lot accounting, Section 475 MTM election, crypto tax lot tracking, 1099-B reconciliation, Section 1256 futures tax |

Full indexed list with build status: [`index.json`](index.json). Full 504-entry roadmap with one-line scope for every skill: [`docs/ROADMAP_500.md`](docs/ROADMAP_500.md).

### Built Skills Overview

All 504 skills are fully documented and executable. Key highlight categories:

- **Broker Integration**: `headless-broker-auth-patterns`, `token-lifecycle-live-probing`, `order-placement-idempotency`, `multi-broker-rate-limit-handling`, `short-selling-borrow-cost-and-availability-modeling`, `backtest-infrastructure-cost-budgeting`
- **Real-Time Architecture**: `producer-consumer-tick-pipeline`, `tick-buffering-burst-handling`, `backpressure-drop-degrade-policy`, `websocket-reconnect-without-duplicate-subscriptions`
- **Backtesting Methodology**: `lookahead-bias-elimination`, `walk-forward-validation-setup`, `execution-realistic-simulation`, `synthetic-data-generation-for-backtest-augmentation`, `backtest-reporting-standardized-tearsheet`
- **Financial ML**: `feature-engineering-without-leakage`, `offline-train-online-infer-deployment`, `model-staleness-detection`, `synthetic-labels-from-triple-barrier-method`, `sample-weighting-for-overlapping-labels`
- **Risk Management**: `kill-switch-and-drawdown-circuit-breakers`, `correlation-aware-exposure-limits`, `tail-risk-hedging-with-options`, `real-time-var-backtesting-kupiec-test`, `risk-limit-breach-escalation-matrix`
- **Global Markets & Derivatives**: `crypto-exchange-api-integration`, `forex-broker-integration-oanda-mt5`, `options-margin-span-calculation-global`, `cme-globex-futures-api-integration`, `eurex-market-data-and-order-api`
- **Execution & Data**: `execution-algo-twap-vwap-slicing`, `participation-of-volume-pov-execution`, `implementation-shortfall-minimization`, `smart-order-routing-across-venues`, `daylight-saving-time-transition-handling`

See `docs/architecture.md` for how these fit together as a system, and `mappings/broker-api-coverage.md` / `mappings/regulatory-coverage.md` for cross-cutting broker and regulatory touchpoints.

## Quick start

```bash
git clone https://github.com/<your-org>/algo-trading-skills.git
cd algo-trading-skills
python tools/validate_skills.py   # verifies every skill's structure and frontmatter (504/504 pass)
```

Point your agent at the `skills/` directory (see [Compatible platforms](#compatible-platforms) below for the exact wiring per tool), and it can discover and load skills by scanning `SKILL.md` frontmatter.

## How AI agents use these skills

Each skill costs roughly 30–50 tokens to scan (frontmatter only) and 500–1,500 tokens to fully load (complete workflow in `SKILL.md`, more in `references/` if needed). This progressive-disclosure structure — mirrored from the anatomy below — lets an agent search all 504 built skills without blowing its context window.

```
User prompt: "My Fyers bot's live orders keep getting placed twice after a timeout"

Agent's internal process:

  1. Scans skill frontmatters (~30-50 tokens each)
     → identifies order-placement-idempotency and token-lifecycle-live-probing
       as the relevant matches

  2. Loads skills/order-placement-idempotency/SKILL.md in full
     → follows the Workflow section: classify timeout as ambiguous (not
       failed), reconcile against the broker order book before any retry

  3. Loads references/workflows.md for the full procedure and
     scripts/order_ledger.py for a working starting point

  4. Validates the fix using the Verification section
     → confirms a simulated timeout no longer produces a duplicate order
```

## Skill anatomy

Every skill follows a consistent directory structure:

```
skills/order-placement-idempotency/
├── SKILL.md              ← Skill definition (YAML frontmatter + Markdown body)
├── references/
│   ├── standards.md      ← Broker/framework coverage + regulatory touchpoints
│   └── workflows.md      ← Deep technical procedure reference
├── scripts/
│   └── order_ledger.py   ← Working helper script
└── assets/
    └── checklist.md      ← Sign-off checklist
```

Full anatomy and frontmatter field reference: [`docs/skill-anatomy.md`](docs/skill-anatomy.md).

### YAML frontmatter (real example)

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

### Markdown body sections

```
## When to Use          Trigger conditions — when should an AI agent activate this skill?
## Prerequisites        Required tools, access, and environment setup.
## Workflow             Step-by-step execution guide with specific decision points.
## Common Pitfalls      Named, specific failure modes this skill prevents.
## Verification         How to confirm the skill was executed successfully.
## Related Skills       Cross-links to other skills in this repo.
```

`tools/validate_skills.py` enforces this structure in CI (see `.github/workflows/validate-skills.yml`).

## Compatible platforms

**AI coding agents** Claude Code · Cursor · GitHub Copilot · OpenAI Codex CLI · Gemini CLI · Windsurf · Cline · Continue

**Agent frameworks** Any framework that can read plain Markdown + YAML frontmatter from a filesystem path (LangChain, CrewAI, custom MCP-based agents, etc.)

Installation differs slightly by platform:

- **Claude Code** — copy or symlink `skills/*` into `.claude/skills/` (project-level) or `~/.claude/skills/` (global), or install via the `.claude-plugin/plugin.json` manifest if your setup supports plugin-style discovery.
- **Cursor** — point Cursor's rules/skills configuration at the cloned `skills/` directory.
- **GitHub Copilot** — reference skill files as context, or copy into `.github/copilot-instructions/` if supported.
- **Gemini CLI / other agentskills.io-compatible tools** — load directly from a cloned copy of this repo per your tool's skills-directory convention.

The format is deliberately plain (YAML frontmatter + Markdown, no platform-specific syntax) so manual copy-paste into any agent's context window always works as a fallback.

## Architecture context

The skills assume (and are easiest to apply within) a system shaped like a producer-consumer tick pipeline feeding a strategy/ML engine, gated by an independent risk module, behind idempotent order placement, supervised by systemd — see [`docs/architecture.md`](docs/architecture.md) for the full diagram and how each skill maps onto it.

## Contributing

This project grows through contributions that reflect genuine production experience. See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the process. Every PR is reviewed against the quality bar in `CONTRIBUTING.md` and the structural checks in `tools/validate_skills.py`.

## Community

- [Issues](../../issues) — bug reports and skill proposals (templates provided)
- [Pull requests](../../pulls) — see `CONTRIBUTING.md` before opening
- [Security policy](SECURITY.md) — responsible disclosure for guidance or script vulnerabilities

## Citation

If you use this project in research or documentation, see [`CITATION.cff`](CITATION.cff).

## License

Licensed under the [Apache License 2.0](LICENSE) — free to use, modify, and distribute in personal and commercial projects.

## Disclaimer

These skills encode engineering practices for building trading infrastructure. They are **not financial advice**, do not guarantee the profitability of any strategy, and following them does not eliminate the risk of loss in live trading. Always validate thoroughly in paper trading (see `skills/paper-to-live-promotion-checklist/SKILL.md`) before committing real capital, and confirm the regulatory requirements applicable to algorithmic trading in your jurisdiction independently (see `mappings/regulatory-coverage.md`).

---

Community project. Not affiliated with Anthropic PBC or any broker referenced in this repository.
