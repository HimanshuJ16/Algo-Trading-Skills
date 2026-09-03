# Changelog

All notable changes to the `Algo-Trading-Skills` repository will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

---

## [3.0.0] - 2026-09-04

A launch-readiness release. The breaking change is the frontmatter and `index.json` schema;
anything parsing either needs updating.

### Changed — specification conformance (breaking)
- **Frontmatter now matches the [agentskills.io specification](https://agentskills.io/specification) exactly.**
  The spec allows six top-level fields, so every repo-specific field (`domain`, `subdomain`,
  `tags`, `brokers_frameworks`, `version`, `author`) moved under `metadata:` as a string value.
  `tags` is comma-separated; `brokers_frameworks` is semicolon-separated. Previously 191 skills
  failed the reference validator on flow-style YAML lists, and all of them failed on unknown
  top-level keys.
- **`index.json` is now a single object** with a `subdomains` count map, list-typed `tags` and
  `brokers_frameworks`, and no `generated_at` timestamp, so regenerating unchanged sources
  produces no diff.
- **Taxonomy normalised.** `domain` is `algorithmic-trading` everywhere and `subdomain` is one
  of the 16 domains in `docs/ROADMAP_500.md`, replacing 154 free-text `domain` values and 326
  `subdomain` values. Every skill is now Apache-2.0 (31 previously claimed MIT against an
  Apache-2.0 repository) and attributed to `algo-trading-skills-contributors`.
- **Descriptions rewritten as triggers.** Every description starts with "Use when …" and fits
  in 280 characters, enforced by the validator, because it is the only text an agent reads
  before choosing a skill and it costs context on every session.

### Changed — packaging
- **One Claude Code plugin per domain** instead of a single monolith. Installing everything
  loaded roughly 45,000 tokens of descriptions into every session, over Claude Code's listing
  budget, so most skills were silently truncated. `tools/build_marketplace.py` generates 16
  domain plugins plus an opt-in all-skills plugin.
- `plugin.json` gained `homepage`, `repository`, `license` and `keywords`; the repository
  version is now consistent across `plugin.json`, the marketplace, `index.json` and
  `CITATION.cff`, and the validator fails if they disagree.

### Fixed — reference implementations
- `order-placement-idempotency`: the "absent, safe to re-send" path was unreachable — a
  re-invocation looped back to ABSENT forever. Transport-level failures
  (`NetworkException`, gateway timeouts, 5xx) were also classified as terminal rejections;
  they are now UNKNOWN and require reconciliation, which is the exact failure the skill's own
  pitfalls section warns about. `record_intent` now requires `symbol`.
- `us-reg-sho-short-sale-locate-requirements`: the duplicate-order fingerprint included the
  NBB price, so a retry carrying a fresh quote was rejected as a different order, defeating
  retry safety.
- `kill-switch-and-drawdown-circuit-breakers`: removed a setter that let any caller clear a
  halt with no operator, reason, or audit record.
- `satellite-imagery-based-signal-research` and `supply-chain-data-for-earnings-prediction`:
  removed deprecated placeholder signal functions and the tests asserting their values.
- `point-in-time-database-for-ml-training-data` and
  `post-incident-forensics-for-suspected-key-compromise`: both documented a trailing `Z` as a
  valid timestamp but passed it straight to `datetime.fromisoformat`, which only accepts `Z`
  from Python 3.11. On 3.10 each rejected its own documented format. Both now normalise `Z`
  to `+00:00` before parsing.
- `backtest-vs-live-performance-divergence-tracking`: `DivergenceSeverity` now pins `__str__`
  to its value. Python 3.10 and 3.11 disagree on how a `(str, Enum)` member renders, so a
  severity written into a report differed by interpreter.
- `backtest-determinism-and-reproducibility` and `reproducible-ml-training-pipelines`: two
  tests built a one-ULP float drift with `sum([0.1] * 10)`. CPython 3.12 gave the built-in
  `sum()` Neumaier compensated summation for floats, so that expression is one ULP below 1.0
  up to 3.11 and exactly 1.0 from 3.12 on, and both tests silently lost the divergence they
  existed to detect. They now accumulate in an explicit loop, which stays naive on every
  version and matches how a backtest actually accumulates a P&L series.

### Changed — library shape
- Merged duplicates: `cross-strategy-correlation-monitoring` into
  `cross-strategy-correlation-monitoring` (which gains EWMA weighting and a shrunken
  covariance output), `implementation-shortfall-minimization` into
  `implementation-shortfall-minimization`, and
  `benchmark-relative-performance-attribution` into
  `benchmark-relative-performance-attribution`.
- Renamed for accuracy: `cme-group-fix-api-for-futures` to
  `cme-stp-fix-and-ilink2-tag-value-encoding` (it never covered live order entry), and
  `websocket-reconnect-without-duplicate-subscriptions` to
  `websocket-subscription-reconciliation-after-reconnect` (its description was
  indistinguishable from `websocket-reconnection-with-state-recovery`).
- `moscow-exchange-moex-api-integration` reframed around its fail-closed OFAC sanctions gate,
  with the "submit an order once your sanctions position permits" verification step removed.
- Removed the content generator's self-narration ("an earlier revision of this file
  asserted …") from published reference files, keeping the corrected statements.

### Changed — tooling and CI
- `tools/run_all_tests.py` runs each skill's suite in its own subprocess with a timeout, so a
  deadlocked test can no longer hang CI for six hours and two skills may share a module name.
  Failures print the full traceback; a green run is quiet. Added `--skill`, `--jobs`,
  `--timeout` and `--quiet`.
- `tools/validate_skills.py` now checks the spec's field set, description length and trigger
  form, the domain vocabulary, single license and author, exact level-2 headings, the three
  named reference and asset files, skill slugs quoted in the repo-level docs and examples, and
  that the plugin manifests cover every skill exactly once.
- `tools/build_index.py` shares the validator's parser, fails on any unparseable skill instead
  of silently dropping it, and gained `--check` and `--output`.
- CI runs on Python 3.10, 3.12 and 3.13 (the real floor is 3.10), adds generated-file drift
  checks, the repository test suite, and the three cookbook examples, and has a job timeout.
  The agentskills.io reference validator (`skills-ref`, invoked as `agentskills`) needs Python
  3.11+, so it is installed in its own step and skipped on the 3.10 job rather than being
  pinned in `requirements-dev.txt`, which would break the 3.10 install.
- The Claude review workflow no longer fails on pull requests from forks, which never receive
  secrets; the `@claude` workflow now only responds to maintainers and collaborators.
- Added `pytest.ini` so a bare `pytest` runs the repository suite instead of colliding on
  skill module names, and `.gitattributes` to stop CRLF/LF churn.

### Changed — documentation
- Fixed 15 references to skills that do not exist across `mappings/`, `examples/` and the root
  docs, and the README's token-count and `index.json` shape claims.
- `docs/ROADMAP_500.md` is now a generated skill index by domain; `docs/skill-anatomy.md`,
  `CONTRIBUTING.md`, `CLAUDE.md`, `AGENTS.md` and the other agent rule files document the
  current contract.
- The examples now import the real skill helpers instead of re-implementing them inline, and
  run deterministically under a fixed seed.
- Removed links to the git-ignored `.claude/config.json`; Code of Conduct and support
  channels now name real reporting routes.

---

## [2.0.0] - 2026-08-07

### Major Enhancements (10 Architecture Core Skills)
- **Institutional Multi-Broker & Risk Core (v2.0.0)**: Major architecture upgrade to core infrastructure skills:
  - `headless-broker-auth-patterns`: Added pure Python TOTP window-safety generator fallback, date-keyed token cache permissions (0600 POSIX mode), and dual REST/Selenium archetype dispatchers.
  - `kill-switch-and-drawdown-circuit-breakers`: Upgraded with real-time portfolio & strategy level drawdown triggers, hard liquidation cascades, and mandatory strategy decommission logs.
  - `order-placement-idempotency`: Upgraded with ambiguous timeout reconciliation, client order ID tracking ledgers, and zero-duplicate retry verification.
  - `sec-rule-15c3-5-risk-controls-us`: Upgraded pre-trade risk engine with single-order notional caps, restricted security lists, credit limit checks, and microsecond latency logging.
  - `zero-downtime-database-schema-migrations`: Standardized 5-phase Expand-Contract state machine with concurrent lock-free DDL generation (`CREATE INDEX CONCURRENTLY` in Postgres, `ALGORITHM=INPLACE` in MySQL).
  - Upgraded core real-time tick architecture (`producer-consumer-tick-pipeline`, `tick-buffering-burst-handling`, `backpressure-drop-degrade-policy`, `websocket-subscription-reconciliation-after-reconnect`).

---

## [1.1.0] - 2026-08-07

### Feature & Domain Extensions (53 Skills)
- **Global Market & Derivatives Expansion (v1.1.0)**: Extended 53 domain skills with full executable Python engines, unit tests, and reference workflows:
  - **Global Venue Integrations**: Added support for CME Globex, Eurex, HKEX, SGX, ASX, JPX, TASE, Borsa Istanbul, Bursa Malaysia, Saxo Bank, Tastytrade, Binance, Coinbase, and Kraken APIs.
  - **Regulatory Compliance**: Integrated US SEC Reg NMS/SHO, EU MiFID II/MAR, UK FCA SYSC 25, SEBI Algo Circulars, MAS Cyber Hygiene, ASIC, FINMA, IIROC, SFC, and JFSA compliance modules.
  - **Quantitative ML & Alt Data**: Added leakage-free feature engineering, triple-barrier labeling, sample weighting for overlapping labels, satellite imagery signals, credit card data pipelines, and web-scraped sentiment analysis.
- **Cross-Reference Integrity**: Reconciled and fixed 78 `Related Skills` cross-references across all skills, ensuring every link resolves to an existing directory in `skills/`.
- **Frontmatter Standardization**: Standardized all frontmatter `version` fields to quoted semver string format (`"1.0.0"`, `"1.1.0"`, `"2.0.0"`).

---

## [1.0.0] - 2026-08-06

### Added
- **504 Production-Grade Algorithmic Trading Skills**: Built across 16 core engineering domains following the `agentskills.io` open standard.
- **Global Financial Framework Mappings**: Integrated mappings for US SEC Rule 15c3-5, Reg NMS Rule 611, Reg SHO Rule 203/204, FINRA, EU MiFID II / RTS 6 / MAR, UK FCA SYSC 25 & SM&CR, ASIC, SEBI, MAS, IIROC, and ISDA OTC derivative standards.
- **Multi-Platform Agent Configurations**: Native support files for Claude Code (`.claude-plugin/`), Cursor (`.cursor/rules/`), GitHub Copilot (`.github/copilot-instructions.md`), Windsurf (`.windsurfrules`), and Cline/Roo Code (`.clinerules`).
- **Automated Validation & Unit Test Runner**: Added `tools/validate_skills.py` for structural verification and `tools/run_all_tests.py` for executing all `test_*.py` unit test suites across all 504 skills.
- **Dependency Manifests**: Added `requirements.txt` and `requirements-dev.txt` for local development and CI test execution.
- **CI/CD Automation**: Configured GitHub Actions workflow (`.github/workflows/validate-skills.yml`) to validate structure and run unit tests on every pull request and push.
