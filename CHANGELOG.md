# Changelog

All notable changes to the `Algo-Trading-Skills` repository will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [2.0.0] - 2026-08-07

### Major Enhancements (10 Architecture Core Skills)
- **Institutional Multi-Broker & Risk Core (v2.0.0)**: Major architecture upgrade to core infrastructure skills:
  - `headless-broker-auth-patterns`: Added pure Python TOTP window-safety generator fallback, date-keyed token cache permissions (0600 POSIX mode), and dual REST/Selenium archetype dispatchers.
  - `kill-switch-and-drawdown-circuit-breakers`: Upgraded with real-time portfolio & strategy level drawdown triggers, hard liquidation cascades, and mandatory strategy decommission logs.
  - `order-placement-idempotency`: Upgraded with ambiguous timeout reconciliation, client order ID tracking ledgers, and zero-duplicate retry verification.
  - `sec-rule-15c3-5-risk-controls-us`: Upgraded pre-trade risk engine with single-order notional caps, restricted security lists, credit limit checks, and microsecond latency logging.
  - `zero-downtime-database-schema-migrations`: Standardized 5-phase Expand-Contract state machine with concurrent lock-free DDL generation (`CREATE INDEX CONCURRENTLY` in Postgres, `ALGORITHM=INPLACE` in MySQL).
  - Upgraded core real-time tick architecture (`producer-consumer-tick-pipeline`, `tick-buffering-burst-handling`, `backpressure-drop-degrade-policy`, `websocket-reconnect-without-duplicate-subscriptions`).

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
