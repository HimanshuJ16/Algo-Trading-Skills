# Support — Algo-Trading-Skills

Thank you for using **Algo-Trading-Skills**. Here is how to get help, report issues, or contribute.

## Where to go

| Need | Best place |
|---|---|
| **Usage questions & design advice** | [Open a question issue](https://github.com/HimanshuJ16/Algo-Trading-Skills/issues/new) |
| **Bug reports** (broken scripts, inaccurate workflows) | [Report a bug](https://github.com/HimanshuJ16/Algo-Trading-Skills/issues/new?template=bug_report.md) |
| **New skill or platform proposals** | [Propose a skill](https://github.com/HimanshuJ16/Algo-Trading-Skills/issues/new?template=skill_proposal.md) |
| **Security concerns** | [Security policy](SECURITY.md) — report privately, not in a public issue |
| **Community conduct** | [Code of Conduct](CODE_OF_CONDUCT.md) |

## Reporting a bug

1. Name the specific skill directory (e.g. `skills/order-placement-idempotency/`).
2. Give the exact steps or code snippet that triggered the issue.
3. State your environment: Python version (the library needs 3.10+), operating system, and
   the broker/exchange API version if the skill is venue-specific.
4. If a test fails, paste the output of
   `python -m unittest discover -s skills/<skill-name>/scripts -v`.

## Before reporting an inaccuracy

Regulatory rules, exchange specifications and broker APIs change. If a claim in
`references/standards.md` looks wrong, please include the authoritative source that
contradicts it — the rule text, exchange notice, or vendor documentation. A correction with a
source can be merged immediately; one without needs research first.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for the skill structure, the frontmatter contract, and
the gates your PR needs to pass.
