# Standards — environment-parity-dev-staging-production

## What is a standard here, and what is a house rule

No regulator or standards body publishes a "parity score", a mandatory set of parity
vectors, or a required Python pinning granularity. The thresholds below are this
library's engineering defaults, chosen so that the gate fails closed. The regulatory
material in the last section is real, is scoped to a named jurisdiction, and is about
*testing and deployment discipline* — not about this particular calculation.

## Enforced rules (the module raises rather than auditing)

| Rule | Rationale |
|---|---|
| `env_name` ∈ {`DEV`, `STAGING`, `PRODUCTION`} | An unrecognised name classified as "not production" lets a live environment wired to a testnet endpoint pass the endpoint check. |
| Every parity field non-blank | `"" == ""` would make a no-evidence audit report 100% parity. Absent evidence must never read as a match. |
| `python_version` matches `major.minor.patch` | `"3.11"` on both sides compares equal across 3.11.2 and 3.11.8 — the exact drift the vector exists to catch. |
| `lockfile_sha256` is 64 hex characters, compared case-insensitively | Hex digest case is not semantic; a 16-character string is not a SHA-256 and cannot be audited as one. |
| `db_schema_revision` yields at least one revision id after separators are stripped | A separator-only value (`","`) parses to an empty head set on both sides, reintroducing the blank fail-open through a side door. |
| `broker_endpoint_mode` ∈ {`TESTNET`, `MAINNET`} | An unrecognised mode cannot be checked against the expected mode for the environment. |
| `required_env_var_keys` is a sequence, not a bare string | A `str` satisfies `Sequence[str]`, so an unguarded string would audit one single-character key per letter. |
| An env var whose value is `None` counts as missing | `str(None)` is the truthy string `"None"`; an unset `os.environ.get()` or a valueless YAML entry must not read as present. |
| `production_baseline.env_name == "PRODUCTION"` | The two arguments share a type; an order swap would otherwise produce a plausible report about the wrong environment. |

## Engineering defaults (calibrate before use)

| Parameter | Default | What it does |
|---|---|---|
| Required env vars | `BROKER_API_KEY`, `MAX_POSITION_LIMIT`, `DATABASE_URL` | Presence-and-non-emptiness only. A starting point for a trading deployment, not an inventory — override to match your schema. |
| Expected endpoint mode | `DEV`/`STAGING` → `TESTNET`, `PRODUCTION` → `MAINNET` | Both directions are audited. A production release on testnet is a failure, not a safe default. |
| Gate threshold | All five vectors must pass | `parity_score_pct` is diagnostic only. |
| Hash display prefix | 12 characters | Display only; the full 64-character digest is always what is compared. |

## Verified technical facts

| Fact | Source |
|---|---|
| Dev/prod parity is **Factor X** of the twelve-factor methodology, framed as closing the time, personnel and tools gaps; "the twelve-factor developer resists the urge to use different backing services between development and production, even when adapters theoretically abstract away any differences" | [12factor.net — X. Dev/prod parity](https://12factor.net/dev-prod-parity) |
| Environment markers — `python_version`, `sys_platform`, `platform_machine`, `platform_python_implementation` and others — are part of the dependency specifier grammar, so one requirements file resolves differently per interpreter, OS and architecture. An identical lockfile hash therefore does not imply an identical installed environment. | [PyPA — Dependency specifiers](https://packaging.python.org/en/latest/specifications/dependency-specifiers/) (formerly PEP 508) |
| Alembic supports multiple simultaneous heads on a branched history; `alembic heads` prints each head as a distinct entry and `alembic current` can report several revisions at once. A single revision string cannot represent such a state. | [Alembic — Working with Branches](https://alembic.sqlalchemy.org/en/latest/branches.html) |

## Regulatory context — EU only, and about discipline rather than this metric

**Applicability:** investment firms engaged in algorithmic trading under MiFID II
(Directive 2014/65/EU). Mandatory for those firms; of no direct force for a proprietary
trader outside that perimeter, and not a substitute for checking your own jurisdiction
(SEC/FINRA, FCA, SEBI, MAS and others impose their own, differently-worded regimes).

Commission Delegated Regulation (EU) 2017/589 ("RTS 6") devotes Section I —
*Testing and deployment of trading algorithms systems and strategies* — to this ground:
Article 5 (testing methodologies), Article 6 (conformance testing), **Article 7 (testing
environments)**, **Article 8 (controlled deployment of algorithms)**, and Article 10
(stress testing). ESMA's own footnote to the briefing below cites "Articles 5, 6 and 7 on
testing and article 10 on stress testing."

> These article numbers and titles are cited as scope pointers. The operative text was
> not retrievable from EUR-Lex at the time of writing (the CELEX HTML/PDF endpoints
> returned the Official Journal index rather than the document), so no clause is quoted
> or paraphrased here. Read the regulation directly before relying on it.

**ESMA, *Supervisory Briefing on Algorithmic Trading in the EU*, 26 February 2026,
ESMA74-1505669079-10311** ([esma.europa.eu](https://www.esma.europa.eu/sites/default/files/2026-02/ESMA74-1505669079-10311_Supervisory_Briefing_on_Algorithmic_Trading_in_the_EU.pdf))
— quoted verbatim from the published PDF:

- ¶30: "Testing of an algorithm, algorithmic trading system or algorithmic trading
  strategy is required following each 'material change' or 'substantial update' thereof.
  In this regard, firms should manage the risk that a series of minor or small changes
  due to recalibrations could accumulate over time, when uncontrolled or unchecked, into
  a material change in the model output without it being tested."
- ¶31: "A material change or substantial update is any modification that may alter the
  behaviour, risk profile, or compliance posture of an algorithm, algorithmic trading
  system or algorithmic trading strategy. Investment firms are required to timestamp,
  approve, and record all material changes." Its table of change types warranting
  retesting includes **External Dependencies** — "Replacing third-party providers or
  data feeds, changes to the trading systems, or changes in access arrangements."
- ¶29: "Investment firms need to ensure that testing methodologies, procedures and
  internal authorisations to deploy algorithmic trading are well documented."

**Why this bears on the lockfile vector specifically.** A change to the resolved
dependency set *is* a change to external dependencies under ESMA's own table, so for an
in-scope firm a lockfile-hash mismatch is a signal to consider retesting — not merely a
build-hygiene nit. Correspondingly, the audit report is a documentation artifact worth
retaining: it timestamps which release specification an environment was gated against.
This module produces that record; it does not itself discharge any obligation.

## Category

`deployment-ops`
