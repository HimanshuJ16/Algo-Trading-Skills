# Standards — strategy-research-to-production-pipeline-governance

## Configuration defaults (calibrate before use)

These are the engine's defaults. They are **not** industry standards and **not** regulatory
minimums. No regulator, exchange or standards body publishes a minimum backtest Sharpe
ratio, a maximum shadow-trading divergence, or a minimum paper-trading duration. The right
values depend on the strategy's holding period, capacity, instrument liquidity and the
firm's risk appetite. Calibrate each, and record the values alongside the promotion
decision — the engine binds them into the audit hash precisely so that a later loosening
is visible.

| Parameter | Default | What it actually does |
|---|---|---|
| `min_backtest_sharpe` | $1.50$ | Out-of-sample Sharpe floor. On breach: `SHARPE_GATE`. Compared unrounded; exactly at the floor passes. May be negative if you deliberately want a permissive floor. |
| `max_backtest_drawdown_pct` | $15.0\%$ | Backtest drawdown cap, as a **positive magnitude**. On breach: `DRAWDOWN_GATE`. Constructor rejects values outside $(0, 100]$. |
| `max_shadow_tracking_error_pct` | $5.0\%$ | Cap on divergence between shadow fills and simulated fills. On breach: `SHADOW_TRACKING_GATE`. See the definition warning below. |
| `min_paper_trading_days` | $14$ | Consecutive calendar days of shadow paper trading required before entry to `STAGING_CANARY` or `LIVE_PRODUCTION`. On breach: `PAPER_DAYS_GATE`. |

Changing any of these is itself a governance event, not a config tweak — see the ESMA
retesting triggers in the table below.

## Regulatory position (verified — read this before citing anything)

**Nothing in MiFID II RTS 6, FINRA guidance, or SEC Rule 15c3-5 mandates a numeric
promotion threshold.** What the rules do mandate is *that a documented testing methodology
exists, that a designated person authorises deployment, and that the change is recorded*.
That is the obligation this skill supports; the numbers are the firm's own.

| Claim | Source | Status |
|---|---|---|
| "Prior to the deployment or substantial update of an algorithmic trading system, trading algorithm or algorithmic trading strategy, an investment firm shall establish clearly delineated methodologies to develop and test such systems, algorithms or strategies." | Commission Delegated Regulation (EU) 2017/589 (RTS 6), **Art. 5(1)** — [FCA Handbook rendition](https://handbook.fca.org.uk/technical-standards/s119c1039); [EUR-Lex CELEX:32017R0589](https://eur-lex.europa.eu/eli/reg_del/2017/589/oj/eng) | Mandatory (EU/EEA investment firms engaged in algorithmic trading; assimilated in the UK). **Qualitative — prescribes no numeric threshold.** |
| "A person designated by the senior management of the investment firm shall authorise the deployment or substantial update of an algorithmic trading system, trading algorithm or algorithmic trading strategy." | RTS 6, **Art. 5(2)** | Mandatory. This is the actual anchor for the sign-off gate. Note it names a **senior-management-designated person**, *not* a risk committee — mapping that person onto a committee is a house governance choice. |
| Art. 5(2)–(5) "shall only apply to trading algorithms leading to order execution". | RTS 6, **Art. 5(6)** | Mandatory scoping. A signal-generation or research algorithm that does not itself lead to order execution is outside the Art. 5(2) authorisation obligation. |
| Firms must keep records of software changes documenting the timing, the person making the change, approvals, and the nature of the modification. | RTS 6, **Art. 5(7)** | Mandatory. Anchors `git_commit_hash`, `author_id`, `validator_id`, `decided_at_utc` and the ledger. |
| Testing of the criteria in Art. 5(4)(a),(b),(d) must be undertaken in "an environment that is separated from its production environment". | RTS 6, **Art. 7(1)**; responsibility is non-delegable per **Art. 7(3)** | Mandatory. Anchors the `PAPER_TRADING_SHADOW` / `STAGING_CANARY` separation. See `research-environment-vs-production-environment-parity`. |
| Before deployment, firms must set predefined limits on "(a) the number of financial instruments being traded; (b) the price, value and numbers of orders; (c) the strategy positions; and (d) the number of trading venues to which orders are sent." | RTS 6, **Art. 8** (Controlled deployment of algorithms) | Mandatory. **Not modelled by this engine** — it gates the decision, not the runtime limits. Enforce these in the execution layer. |
| "An investment firm shall ensure that any proposed material change to the production environment related to algorithmic trading is preceded by a review of that change by a person designated by senior management." | RTS 6, **Art. 11(1)** | Mandatory. A promotion into `LIVE_PRODUCTION` is such a change. |
| "Investment firms need to ensure that testing methodologies, procedures and internal authorisations to deploy algorithmic trading are well documented." | ESMA, *Supervisory Briefing on Algorithmic Trading in the EU*, ESMA74-1505669079-10311, Feb 2026, ¶29 | Supervisory guidance. Explicitly **"non-binding and not subject to a 'comply or explain' mechanism"** (¶3). |
| "Investment firms are required to timestamp, approve, and record all material changes." Retesting triggers include "Risk Controls: changing thresholds, kill switch logic, or alert triggers" and "Adaptive Capabilities: retraining or modifying machine learning components". | ibid. ¶31 | Guidance. Anchors `decided_at_utc`. **Directly relevant:** changing this engine's own gate thresholds is a material change warranting re-audit. |
| "The scope, frequency, and intensity of testing vary significantly across the industry… ESMA recognises the need for proportionality in applying the testing provisions." | ibid. ¶26 | Guidance. Argues **against** presenting any fixed numeric threshold as an industry standard. |
| Firms should consider "implementing a development and change management process that tracks the development of new trading code or material changes to existing code", "conducting any significant testing in a development environment that is segregated from production", "a set of approval protocols that are appropriate given the scope of the code or any change(s) to the code", and "archiving code versions in a retrievable manner". | [FINRA Regulatory Notice 15-09](https://www.finra.org/rules-guidance/notices/15-09) | **Guidance, not a rule.** The notice describes "suggested effective practices" and states the list "is not intended to be an exhaustive list". Do not cite it as a mandatory US requirement. |
| Exchanges must implement a Standard Operating Procedure for testing algos; algo providers must be empanelled with the exchange and their strategies approved before being offered. | [SEBI circular dated 4 Feb 2025, "Safer participation of retail investors in Algorithmic trading"](https://www.sebi.gov.in/legal/circulars/sep-2025/extension-of-timeline-for-implementation-of-sebi-circular-dated-february-04-2025-on-safer-participation-of-retail-investors-in-algorithmic-trading-_96979.html) (implementation timeline subsequently extended) | Mandatory, **India only, and scoped to retail-facing algo provision.** Adds an *external* approval gate this engine does not model. See `india-sebi-algo-trading-tagging-requirements`. |

**SEC Rule 15c3-5** requires a broker-dealer to maintain risk management controls
"reasonably designed" to manage the risks of market access, with an annual review and CEO
certification. It governs the *pre-trade risk controls* at the point of access, not the
research-to-production promotion of a strategy, and imposes no promotion threshold. Do not
cite it in support of the numbers above — see `sec-rule-15c3-5-risk-controls-us`.

The defensible framing is therefore: staged promotion with a designated approver is a
**house control that evidences** the RTS 6 Art. 5(1)/5(2)/5(7) and Art. 11(1) obligations.
The thresholds are not derived from any regulator.

## Quantitative definitions

| Fact | Source | Applied here |
|---|---|---|
| *Tracking error* (equivalently active risk / tracking risk) is the **standard deviation of active returns** — the difference between portfolio and benchmark returns — over a series of periods, conventionally annualized by $\sqrt{T}$. | Standard portfolio-analytics definition (textbook, not regulatory) — [Wikipedia, "Tracking error"](https://en.wikipedia.org/wiki/Tracking_error) | **The field name is a misnomer retained for API compatibility.** `shadow_tracking_error_pct` gates a *divergence between shadow fills and simulated fills*, which is not a standard deviation of active returns. The engine validates only that it is finite and non-negative. **You must fix the definition** — an annualized standard deviation of return differences and a mean absolute per-fill price divergence are different quantities, and `5.0` means something very different under each. Whichever you pick, apply it identically to every strategy you compare, and record it with the audit report. |
| A Git object name is hexadecimal: 40 characters for SHA-1, 64 for SHA-256; `git` abbreviates to a minimum of 7 characters (`core.abbrev`, which scales with repository size). | [Git documentation, `git rev-parse --short`](https://git-scm.com/docs/git-rev-parse) | The reproducibility gate accepts 7–64 hexadecimal characters and rejects an all-zero digest. A length-only check accepted `"notahash"` and `"0000000"`. |
| SHA-256 produces a 256-bit (64 hexadecimal character) digest. | [NIST FIPS 180-4](https://csrc.nist.gov/pubs/fips/180-4/upd1/final) | `audit_trail_hash` is the full 64-character digest. The previous implementation truncated to 16 characters (64 bits), which is well inside birthday-collision range for a long-lived ledger and gained nothing. |

## Known limitations

- **Artifacts are asserted, not measured.** The engine cannot distinguish a genuine
  out-of-sample Sharpe from an in-sample one, nor detect look-ahead bias. Passing means the
  paperwork is in order, never that the edge is real.
- **Stateless about strategy identity.** It validates that the requested transition is a
  single forward step, not that a given strategy genuinely completed the earlier stages.
- **RTS 6 Art. 8 deployment limits are out of scope.** Declare and enforce them separately.
- **Identity is an opaque string.** The engine enforces `author_id != validator_id`; it
  cannot authenticate either party or verify the validator holds designated authority.
- **The hash chain is tamper-evident, not tamper-proof.** It detects an edit or deletion
  made without recomputing the chain. Anyone able to rewrite the entire ledger can
  recompute every downstream digest, so anchor the digests in storage the strategy owner
  cannot rewrite.
