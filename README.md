![Algo-Trading-Skills banner](assets/banner.png)

# Algo-Trading-Skills

### An open-source algorithmic trading skills library for AI agents

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg?style=flat-square)](LICENSE)
[![Validate & Test Skills](https://github.com/HimanshuJ16/Algo-Trading-Skills/actions/workflows/validate-skills.yml/badge.svg)](https://github.com/HimanshuJ16/Algo-Trading-Skills/actions/workflows/validate-skills.yml)
[![Skills](https://img.shields.io/badge/skills-501-brightgreen?style=flat-square)](#whats-inside--16-categories)
[![Tests](https://img.shields.io/badge/tests-20%2C289_passing-brightgreen?style=flat-square)](#what-verified-means-here)
[![Domains](https://img.shields.io/badge/domains-16-9cf?style=flat-square)](#whats-inside--16-categories)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue?style=flat-square)](#quick-start)
[![agentskills.io](https://img.shields.io/badge/standard-agentskills.io-ff6600?style=flat-square)](https://agentskills.io)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg?style=flat-square)](CONTRIBUTING.md)

**501 algorithmic trading skills · 16 engineering domains · 5 regulatory & exchange frameworks · 501 working reference implementations backed by 20,289 unit tests · agentskills.io standard · Works with Claude Code, GitHub Copilot, Codex CLI, Cursor, Gemini CLI and any tool that reads `SKILL.md` · Apache 2.0**

[Get Started](#quick-start) · [What's Inside](#whats-inside--16-categories) · [How It's Verified](#what-verified-means-here) · [Frameworks & Standards](#five-regulatory--exchange-frameworks-one-skill-library) · [Platforms](#compatible-platforms--zero-config-auto-discovery) · [Contributing](#contributing)

---

> ⚠️ **Community Project** — This is an independent, community-created project. Not affiliated with Anthropic PBC or any broker, exchange, or vendor referenced in this repository.
> 
> 📈 **Engineering Guidance, Not Financial, Legal, or Compliance Advice** — Authorized & lawful use only. These skills encode production engineering practices for trading infrastructure. They do not constitute financial, legal, tax, or regulatory compliance advice, do not guarantee strategy profitability, and do not eliminate the risk of capital loss in live trading. Only deploy against paper accounts or live environments where risk limits are strictly enforced. Consult qualified legal, tax, and compliance professionals in your jurisdiction before deploying live trading systems. See [SECURITY.md](SECURITY.md) and [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

---

## Give any AI agent the trading-infrastructure instincts of a senior quant engineer

An AI coding agent can write a WebSocket client, a backtest loop, or an order-placement function that looks completely correct — right library calls, clean structure, plausible logic — and still fail catastrophically in production for reasons that have nothing to do with code syntax: a broker invalidates a token overnight in a way its docs don't mention, a backtest silently uses a bar's own close to predict its own direction, a risk limit lives inside the same function it's supposed to constrain, or a WebSocket callback blocks the read loop during exactly the volatility spike a strategy exists to catch.

**Your AI agent doesn't know these failure modes — unless you give it these skills.**

This repo contains **501 structured skills** spanning **16 engineering domains**, each following the [agentskills.io](https://agentskills.io) open standard. The library maps across key financial regulatory & exchange frameworks — SEC Rule 15c3-5, Reg NMS / Reg SHO, FINRA, EU MiFID II / RTS 6 / MAR, UK FCA, ASIC, SEBI, and ISDA OTC derivative standards. Clone it, point your agent at it, and your next trading system deployment gets expert-level quant infrastructure guidance in seconds.

Every skill also states **where it stops**. A `## When NOT to Use` section on all 501 skills names the cases the skill does not cover and hands each one to the skill that does — because an agent applying a correct playbook to the wrong problem is its own failure mode, and it is the one a keyword match is most likely to cause.

---

## Five regulatory & exchange frameworks, one skill library

Each skill maps to the industry standards, exchange protocols, and regulatory mandates that fit its subject:

| Framework / Standard | Scope | What It Maps | Key Mapped Skills |
|---|---|---|---|
| **US SEC / FINRA** | SEC Rule 15c3-5, Reg NMS Rule 611, Reg SHO, PDT Rule 4210, Form 1099-B | Pre-trade risk controls, order protection, short sale locates, pattern day trading, tax lot reconciliation | `us-reg-nms-order-protection-rule-compliance`, `us-reg-sho-short-sale-locate-requirements`, `sec-rule-15c3-5-risk-controls-us`, `wash-sale-rule-tracking-us` |
| **EU MiFID II / RTS 6 / MAR** | MiFID II Article 48, RTS 6 organizational requirements, MAR market abuse surveillance | System resilience, kill switches, OTR limits, wash trade & spoofing self-detection, double volume caps | `mifid-ii-algo-trading-compliance-eu`, `wash-trade-and-spoofing-self-detection`, `eu-market-abuse-regulation-mar-surveillance` |
| **UK FCA & Senior Managers Regime** | FCA SYSC 25, MIFIDPRU, Senior Managers & Certification Regime (SM&CR) | Algorithmic trading system controls, algorithmic accountability, key person governance | `uk-fca-algorithmic-trading-systems-controls`, `uk-senior-managers-regime-algo-accountability` |
| **Global Regulatory (ASIC, SEBI, MAS, IIROC)** | ASIC MIR, SEBI Algo Circulars, MAS Cyber Hygiene, IIROC Electronic Trading | Regional exchange order tagging, circuit breakers, risk-gate dependencies, kill switches | `asic-market-integrity-rules-automated-trading`, `india-sebi-algo-trading-tagging-requirements`, `mas-singapore-algo-trading-guidelines` |
| **ISDA & OTC Derivatives** | ISDA Master Agreement, SPAN Margin, Options Greeks, Variance Swaps | Collateral management, cross-margining, delta hedging, synthetic TRS exposure, volatility derivatives | `options-margin-span-calculation-global`, `total-return-swap-synthetic-exposure`, `variance-swap-and-volatility-derivative-pricing` |

### Example — Each skill maps directly to regulatory mandates, broker APIs, and institutional standards:

| Skill | Primary Regulatory / Exchange Standard | Broker / Platform Touchpoints | Operational Safety Target |
|---|---|---|---|
| [`order-placement-idempotency`](skills/order-placement-idempotency/) | SEC Rule 15c3-5 / MiFID II RTS 6 | Fyers, Zerodha, Breeze, Upstox, Alpaca, IBKR | Zero duplicate executions on reconnection |
| [`kill-switch-and-drawdown-circuit-breakers`](skills/kill-switch-and-drawdown-circuit-breakers/) | SEC Rule 15c3-5 (Pre-Trade Controls) | Universal / Broker-Agnostic | Instant capital protection on drawdown breach |
| [`wash-trade-and-spoofing-self-detection`](skills/wash-trade-and-spoofing-self-detection/) | EU MAR / FINRA Rule 5210 | Exchange Order Feeds | Pre-trade self-cross prevention & cancellation audit |
| [`us-reg-sho-short-sale-locate-requirements`](skills/us-reg-sho-short-sale-locate-requirements/) | US SEC Reg SHO Rule 203(b)(1) | US Equity Brokers / Prime Brokers | Mandatory pre-short locate verification & buy-in tracking |
| [`withdrawal-velocity-limits-and-anomaly-detection`](skills/withdrawal-velocity-limits-and-anomaly-detection/) | MAS Cyber Hygiene / Custody Risk | Coinbase, Fireblocks, BitGo, HSM | Automated hot wallet freeze on velocity breach |

---

## Quick start

```bash
# Option 1: Claude Code — install one domain, not the whole library
/plugin marketplace add HimanshuJ16/Algo-Trading-Skills
/plugin install algo-trading-risk-management
/plugin install algo-trading-broker-integration

# Option 2: skills CLI — pick the skills you want interactively
npx skills add HimanshuJ16/Algo-Trading-Skills

# Option 3: git clone, then run the gates yourself
git clone https://github.com/HimanshuJ16/Algo-Trading-Skills.git
cd Algo-Trading-Skills
pip install -r requirements-dev.txt

python tools/validate_skills.py    # structure, frontmatter, cross-references & packaging
python tools/run_all_tests.py      # every skill's unit test suite, isolated per subprocess
```

**Install one domain, not all of them.** Claude Code loads the name and description of
every skill in an installed plugin into the model's context at the start of every
session. The marketplace therefore ships **one plugin per engineering domain**
(`algo-trading-risk-management`, `algo-trading-execution-algorithms`, …), each a few
thousand tokens. An `algo-trading-skills-all` plugin exists for completeness, but it
costs tens of thousands of tokens per session — reach for it only if you know you want
that.

Run one skill's suite on its own — the same command every skill quotes in its own Verification section:

```bash
python -m unittest discover -s skills/order-placement-idempotency/scripts
```

Works immediately with Claude Code, GitHub Copilot, OpenAI Codex CLI, Cursor, Gemini CLI, and any agentskills.io-compatible platform.

---

## Why this exists

The quantitative trading and financial software engineering domain requires deep practitioner knowledge across market microstructure, exchange protocols, and risk engineering. AI agents can help build and scale trading infrastructure — but only if they have structured practitioner playbooks to work from. Today's generic LLMs can write Python code and API wrappers, but they lack the operational context that separates code which works in a notebook from code that survives a live market.

Existing trading libraries give you broker SDKs, indicator formulas, or naive strategy backtests. None of them give an AI agent the structured decision-making workflow a senior quant infrastructure engineer follows: when to use each technique, when *not* to, what prerequisites to check, how to execute step-by-step, and how to verify results in production. That is the gap this project fills.

**Algo-Trading-Skills** is not a collection of toy scripts. It is an AI-native knowledge base built from the ground up for the [agentskills.io](https://agentskills.io) standard — YAML frontmatter for sub-second discovery, structured Markdown for step-by-step execution, and reference files for deep technical context. Every skill encodes real practitioner workflows, not generic LLM summaries.

---

## What "verified" means here

Skill libraries are easy to generate and hard to trust. Everything in this table is re-checked
by CI on every push and pull request, on Python 3.10, 3.12 and 3.13, so the claims stay true or
the build goes red.

| | |
|---|---|
| **501 working reference implementations** | ~292,000 lines of Python under `skills/*/scripts/`. Not pseudocode — importable modules with validated inputs and named exceptions. 466 of the 501 import nothing outside the Python standard library; the 35 that do reach mostly for numpy or pandas, so the whole suite still runs on `requirements-dev.txt` alone. |
| **501 unit test suites · 20,289 tests** | ~228,000 lines of tests. Each suite runs in its own subprocess with a timeout, so no skill can leak module state into another or hang the build. A skill whose own reference implementation fails its own tests fails the build. |
| **A machine-enforced contract** | [`tools/validate_skills.py`](tools/validate_skills.py) checks the frontmatter contract, the seven required body sections, `scripts/` layout, every skill cross-reference in both skills and repo docs, that every documented test command runs from the repository root, and that the plugin manifests cover every skill exactly once. |
| **The specification, not our reading of it** | CI also runs [`skills-ref`](https://github.com/agentskills/agentskills/tree/main/skills-ref), the official agentskills.io reference validator, against every skill. It needs Python 3.11+, so that job runs on the newer interpreters while the library itself stays 3.10-compatible. |
| **Descriptions that say when to trigger** | Every description starts with "Use when …" and fits in 280 characters — enforced, because it is the only thing an agent reads before choosing a skill, and it costs context on every session. |
| **Generated files can't drift** | `index.json` and the plugin marketplace are generated and carry no timestamp; `--check` modes fail CI if either is stale. |
| **Verification you can paste** | Every skill quotes a runnable command in its `## Verification` section, alongside the concrete assertions to check by hand. |
| **Sourced, or explicitly unsourced** | 458 of 501 `references/standards.md` cite at least one primary source — the rule text, the exchange notice, the vendor spec. Where no external standard exists, the file says so and labels its numbers as configurable defaults rather than inventing an authority for them. |
| **Stated scope boundaries** | `## When NOT to Use` on every skill, naming the excluded cases and handing each to the skill that owns it. |
| **Examples that use the real code** | The three walkthroughs in `examples/` import the actual skill helpers rather than re-implementing them, and CI runs all three. |

The CI workflow is [`.github/workflows/validate-skills.yml`](.github/workflows/validate-skills.yml).

---

## What's inside — 16 categories

The library covers 16 core engineering domains spanning domestic and global markets — crypto exchanges, forex brokers, multi-currency and multi-timezone data handling, regulatory compliance, multi-asset derivatives, execution algorithms, custody/security, cross-strategy portfolio management, market microstructure, alternative-data research, and tax/accounting.

| Domain | Skills | Key capabilities |
|---|---|---|
| [`broker-integration`](docs/ROADMAP_500.md) | **36** | Headless auth (REST + Selenium), token lifecycle via live probing, order idempotency, per-broker rate limiting, borrow cost modeling, cost budgeting |
| [`real-time-architecture`](docs/ROADMAP_500.md) | **31** | Producer-consumer tick pipelines, burst-safe buffering, explicit backpressure policy, WebSocket subscription reconciliation after a reconnect |
| [`backtesting-methodology`](docs/ROADMAP_500.md) | **31** | Lookahead bias elimination, walk-forward validation, realistic slippage/fee/latency simulation, synthetic data generation, standardized tearsheets |
| [`financial-ml`](docs/ROADMAP_500.md) | **38** | Leakage-free feature engineering, offline-train/online-infer deployment, triple barrier labeler, sample weighting, model staleness detection |
| [`risk-management`](docs/ROADMAP_500.md) | **39** | Kill switches and drawdown circuit breakers, correlation-aware exposure limits, Kupiec test VaR backtesting, tail risk hedging, risk escalation matrices |
| [`deployment-ops`](docs/ROADMAP_500.md) | **30** | systemd process supervision, paper-to-live promotion checklist, IaC for trading hosts, canary releases, chaos engineering, secrets vault |
| [`global-market-integration`](docs/ROADMAP_500.md) | **44** | Crypto exchange APIs (Binance/Coinbase/Kraken/Deribit/Bybit/OKX), FX (OANDA/MT5), CME Globex, Eurex, HKEX, SGX, ASX, JPX, CBOE, LSE, Xetra |
| [`regulatory-compliance-global`](docs/ROADMAP_500.md) | **38** | US SEC Rule 15c3-5, PDT, FINRA, EU MiFID II/RTS 6/MAR, UK FCA, ASIC, MAS, India SEBI, Canada IIROC, Hong Kong SFC, Japan FSA |
| [`multi-asset-derivatives`](docs/ROADMAP_500.md) | **28** | SPAN margin calculation, futures contract roll automation, real-time Greeks aggregation, perpetual futures funding rates, variance swaps, CDS, quanto options |
| [`execution-algorithms`](docs/ROADMAP_500.md) | **32** | TWAP/VWAP order slicing, POV execution, implementation shortfall minimization, iceberg detection, smart order routing (SOR), dark pool routing, auctions |
| [`data-management-global`](docs/ROADMAP_500.md) | **37** | Global exchange holiday calendars, DST transition handling, multi-timezone session scheduling, multi-currency P&L, ISIN/CUSIP/SEDOL cross-referencing |
| [`crypto-custody-security`](docs/ROADMAP_500.md) | **29** | Wallet key custody, hot-cold split, withdrawal whitelisting, multi-sig approval, HSM integration, Shamir secret sharing, MPC custody |
| [`portfolio-multi-strategy`](docs/ROADMAP_500.md) | **28** | Cross-strategy correlation monitoring, performance-based capital reallocation, strategy retirement criteria, risk parity allocation, meta-strategy signal arbitration |
| [`market-microstructure-latency`](docs/ROADMAP_500.md) | **24** | Colocation latency budgets, PTP clock sync, tick-to-trade measurement, order book signals, adverse selection measurement, FPGA/microwave evaluation |
| [`quant-research-alt-data`](docs/ROADMAP_500.md) | **20** | Satellite imagery signals, credit card transaction data, web-scraped sentiment, supply chain networks, Google Trends, social media bot filtering, transcript NLP |
| [`tax-accounting-reporting-global`](docs/ROADMAP_500.md) | **16** | US wash sale tracking, FIFO vs specific-lot accounting, Section 475 MTM election, crypto tax lot tracking, 1099-B reconciliation, Section 1256 futures tax |

Full searchable index: [`index.json`](index.json). Every skill listed by domain with its trigger description: [`docs/ROADMAP_500.md`](docs/ROADMAP_500.md).

---

## How AI agents use these skills

The full library is roughly **3.5 million tokens** of Markdown — far past any context window. Progressive disclosure is what makes it usable: an agent searches short descriptions to find the right skill, then loads only that one.

| Stage | What the agent reads | Cost |
|---|---|---|
| **Discover** | `index.json` — name, description, domain and tags for every skill, queryable without touching a single skill file | ~250 tokens per skill as shipped (~80 for a name+description projection) |
| **Load** | The one matching `SKILL.md` — workflow, scope boundaries, pitfalls, verification | ~2,000-3,700 tokens (median ~2,700) |
| **Go deeper** | `references/` and `scripts/` for that skill only, once it is actually implementing | on demand |

`index.json` is a single JSON object with a `skills` array and a `subdomains` count map, so an agent can filter by domain or grep descriptions and narrow the whole library to a handful of candidates before loading anything.

User prompt: *"My Fyers bot's live orders keep getting placed twice after a timeout"*

Agent's internal process:

```text
  1. Queries index.json descriptions for all 501 skills
     → identifies order-placement-idempotency and token-lifecycle-live-probing as top matches.

  2. Loads top match: skills/order-placement-idempotency/SKILL.md
     → checks When NOT to Use first — this is order placement, not a cancel-request race,
       so the skill applies.
     → follows the structured Workflow section: classify timeout as ambiguous (not failed),
       reconcile against broker order book before any retry.

  3. Loads references/workflows.md for full sequence diagrams and
     scripts/order_ledger.py for working helper logic.

  4. Validates results using the Verification section
     → runs `python -m unittest discover -s skills/order-placement-idempotency/scripts`
     → confirms a simulated network timeout no longer produces duplicate executions.
```

Without these skills, the agent guesses at retry logic and doubles order risk. With them, it follows the exact playbook a senior trading engineer would use.

---

## Skill anatomy

Every skill follows a consistent directory structure:

```text
skills/order-placement-idempotency/
├── SKILL.md                  ← Skill definition (YAML frontmatter + Markdown body)
├── references/
│   ├── standards.md          ← Broker/framework coverage + regulatory touchpoints
│   └── workflows.md          ← Deep technical procedure reference
├── scripts/
│   ├── order_ledger.py       ← Working reference implementation
│   └── test_order_ledger.py  ← Its unittest suite
└── assets/
    └── checklist.md          ← Printable sign-off checklist
```

Each helper is a **standalone module** — no imports from other skills, no shared package — so you can lift one file out of the repo and into your own codebase without dragging the library along.

### YAML frontmatter (real example)

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

The [agentskills.io specification](https://agentskills.io/specification) allows six
top-level fields, so everything this repository adds lives under `metadata:` as string
values. Two rules on `description` do the heavy lifting for discovery: it starts with
**"Use when …"** (the situation an agent is in, not a description of the subject), and
it fits in **280 characters**, because every installed skill's description is loaded
into context on every session. Both are enforced by
[`tools/validate_skills.py`](tools/validate_skills.py), alongside the official `agentskills validate`.

### Markdown body sections

```text
## When to Use          Trigger conditions — when should an AI agent activate this skill?
## When NOT to Use      Scope boundaries — each excluded case handed to the skill that owns it.
## Prerequisites        Required tools, access, and environment setup.
## Workflow             Step-by-step execution guide with specific decision points.
## Common Pitfalls      Named, specific failure modes this skill prevents.
## Verification         How to confirm the skill was executed successfully, with a runnable command.
## Related Skills       Cross-links to other skills in this repo.
```

All seven sections are required. [`tools/validate_skills.py`](tools/validate_skills.py) enforces them in CI, along with the `scripts/` layout and the runnability of every documented test command — see [`docs/skill-anatomy.md`](docs/skill-anatomy.md) for the contract in prose and [`.github/workflows/validate-skills.yml`](.github/workflows/validate-skills.yml) for the pipeline.

---

## Compatible platforms & Zero-Config Auto-Discovery

This repository includes native auto-discovery instructions for all major AI coding platforms:

| Platform / Agent | Zero-Config Rule File | Auto-Discovery Standard |
|---|---|---|
| **Claude Code** | [`CLAUDE.md`](CLAUDE.md) + [`.claude-plugin/`](.claude-plugin/) | Anthropic Claude Code project + plugin marketplace convention |
| **OpenAI Codex CLI / OpenHands** | [`AGENTS.md`](AGENTS.md) | Open cross-tool agent instruction format |
| **Gemini CLI** | [`GEMINI.md`](GEMINI.md) | Google Gemini CLI project instructions |
| **GitHub Copilot** | [`.github/copilot-instructions.md`](.github/copilot-instructions.md) | GitHub Copilot repo-level rules |
| **Cursor** | [`.cursor/rules/algo-trading-skills.mdc`](.cursor/rules/algo-trading-skills.mdc) | Cursor MDC rule format |
| **Cline / Roo Code** | [`.clinerules`](.clinerules) | Cline / Roo Code system rule |
| **Windsurf** | [`.windsurfrules`](.windsurfrules) | Windsurf rule configuration |
| **Continue** | [`.continue/rules/algo-trading-skills.md`](.continue/rules/algo-trading-skills.md) | Continue dev auto-discovery rule |
| **Aider** | [`CONVENTIONS.md`](CONVENTIONS.md) / [`.aider.conf.yml`](.aider.conf.yml) | Aider project conventions |

The table above lists the tools this repository ships a rule file for. Beyond those,
the skills are plain `SKILL.md` directories in the [agentskills.io](https://agentskills.io)
format, so any agent or framework that reads that format can load them — point it at
`skills/` and it has everything it needs.

---

## 🏃 Runnable Examples & Cookbook

To see how skills chain together in complete pipelines, explore the runnable walkthroughs in [`examples/`](examples/):

- [`examples/01_resilient_order_execution_and_circuit_breaker.py`](examples/01_resilient_order_execution_and_circuit_breaker.py) — Order idempotency + drawdown circuit breakers + live token probing.
- [`examples/02_lookahead_free_backtest_with_slippage.py`](examples/02_lookahead_free_backtest_with_slippage.py) — Point-in-time signal engine + market impact slippage simulation + performance tear-sheet.
- [`examples/03_cross_strategy_risk_parity_allocation.py`](examples/03_cross_strategy_risk_parity_allocation.py) — Rolling strategy correlations + risk parity rebalancing + automated strategy retirement.

Cross-cutting maps live in [`mappings/broker-api-coverage.md`](mappings/broker-api-coverage.md) and [`mappings/regulatory-coverage.md`](mappings/regulatory-coverage.md); the system architecture the skills were extracted from is in [`docs/architecture.md`](docs/architecture.md).

---

## 🤖 LLM-Crawler Discoverability (`llms.txt`)

This repository supports machine-discoverable documentation standards for LLMs and AI crawlers:
- [`llms.txt`](llms.txt) — Concise index of core documentation, skill categories, and developer entrypoints.
- [`llms-full.txt`](llms-full.txt) — Full architecture breakdown and domain mapping for large context windows.

---

## Releases & Build Verification

| Version | Highlights |
|---|---|
| **v3.0.0** | Frontmatter migrated to the agentskills.io specification (repo fields under `metadata:`), one Claude Code plugin per domain instead of one monolith, descriptions rewritten as "Use when …" triggers capped at 280 characters, duplicate skills merged, and CI extended with `skills-ref`, a Python version matrix, generated-file drift checks and the cookbook examples. See [CHANGELOG.md](CHANGELOG.md). |
| **v2.0.0** | Reference implementations upgraded across the core broker, risk and real-time skills. |
| **v1.0.0** | Initial library across 16 engineering domains, with `tools/validate_skills.py` and `tools/run_all_tests.py` enforced in CI. |

---

## Contributing

This project grows through community contributions. Here is how to get involved:

- **Add a new skill** — Follow the template and frontmatter structure enforced by `tools/validate_skills.py` and submit a PR.
- **Improve existing skills** — Update workflows, refine code engines, add unit tests, or extend regulatory mappings.
- **Report issues** — Found an edge case or missing failure mode? Open an issue.

Before opening a PR, run the gates locally. CI runs these plus the agentskills.io
reference validator and the cookbook examples — see
[`CONTRIBUTING.md`](CONTRIBUTING.md#how-the-library-is-verified) for the full pipeline:

```bash
python tools/validate_skills.py
python tools/run_all_tests.py
python tools/build_index.py --check      # regenerate and commit if this fails
python tools/build_marketplace.py --check
python -m unittest discover -s tests
```

The quality bar is in [`CONTRIBUTING.md`](CONTRIBUTING.md), and it is a high one: would following this skill have prevented a real production bug, and is its Verification section actually checkable? A regulatory or broker-behaviour claim must be verifiable against an authoritative source — **a missing claim is better than a wrong or fabricated one.**

Every PR is reviewed for technical accuracy and `agentskills.io` standard compliance.

---

## Citation

If you use this project in research or publications:

```bibtex
@software{algo_trading_skills,
  author       = {Jangir, Himanshu},
  title        = {Algo-Trading-Skills},
  year         = {2026},
  url          = {https://github.com/HimanshuJ16/Algo-Trading-Skills},
  license      = {Apache-2.0},
  note         = {501 structured algorithmic trading skills for AI agents,
                  mapped to SEC Rule 15c3-5, Reg NMS, MiFID II, FCA, SEBI, and ISDA standards}
}
```

---

## License

This project is licensed under the [Apache License 2.0](LICENSE). You are free to use, modify, and distribute these skills in both personal and commercial projects.

---

If this project helps your quantitative trading work, consider giving it a ⭐

⭐ **Star** · 🍴 **Fork** · 💬 **Discuss** · 📝 **Contribute**

*Community project by [@HimanshuJ16](https://github.com/HimanshuJ16). Not affiliated with Anthropic PBC or any broker referenced in this repository.*
