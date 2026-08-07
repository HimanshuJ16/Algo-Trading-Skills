# Changelog

All notable changes to the `Algo-Trading-Skills` repository will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.0.0] - 2026-08-06

### Added
- **504 Production-Grade Algorithmic Trading Skills**: Built across 16 core engineering domains following the `agentskills.io` open standard.
- **Global Financial Framework Mappings**: Integrated mappings for US SEC Rule 15c3-5, Reg NMS Rule 611, Reg SHO Rule 203/204, FINRA, EU MiFID II / RTS 6 / MAR, UK FCA SYSC 25 & SM&CR, ASIC, SEBI, MAS, IIROC, and ISDA OTC derivative standards.
- **Multi-Platform Agent Configurations**: Native support files for Claude Code (`.claude-plugin/`), Cursor (`.cursor/rules/`), GitHub Copilot (`.github/copilot-instructions.md`), Windsurf (`.windsurfrules`), and Cline/Roo Code (`.clinerules`).
- **Automated Validation & Unit Test Runner**: Added `tools/validate_skills.py` for structural verification and `tools/run_all_tests.py` for executing all `test_*.py` unit test suites across all 504 skills.
- **Dependency Manifests**: Added `requirements.txt` and `requirements-dev.txt` for local development and CI test execution.
- **CI/CD Automation**: Configured GitHub Actions workflow (`.github/workflows/validate-skills.yml`) to validate structure and run unit tests on every pull request and push.
