# Standards for Canary Releases of Strategy Code

## 0. How to read this document

Sections 1-3 are **regulatory touchpoints**: obligations or published supervisory
expectations, with the jurisdiction stated. Sections 4-7 are **engineering standards** —
this repository's recommended practice, not legal requirements, labelled as such so an
agent does not present them to an operator as compliance mandates.

Nothing here substitutes for your own compliance function's determination of which regime
applies to you. Where a number appears (a scale factor, a sample size), it is an
engineering default to be calibrated, not a threshold any regulator has set.

## 1. EU / MiFID II — Commission Delegated Regulation (EU) 2017/589 ("RTS 6")

**Applicability:** investment firms engaged in algorithmic trading authorised under
MiFID II (Directive 2014/65/EU). It does **not** bind a US-only broker-dealer, an
unregulated proprietary trader outside the EU, or an individual trading their own
capital. The UK operates a materially equivalent onshored version supervised by the FCA.

| RTS 6 Article | Subject | Relevance to this skill |
|---|---|---|
| Art. 5(2) | Testing methodology / authorisation | A person designated by senior management must authorise the deployment or substantial update of an algorithmic trading system, algorithm or strategy. This is what the mandatory `authorised_by` argument on `set_phase()` and `reset_canary_budget()` captures; an unattributed promotion does not satisfy it. |
| Art. 5(7) | Records of material change | The firm must be able to say when a material change to algorithmic trading software was made, by whom, who approved it, and what it was. `phase_history` is structured to answer those questions, including for refused and forced transitions. |
| Art. 8 | Controlled deployment of algorithms | Deployment of a new or materially modified algorithm into production must be controlled — including cautious limits on the number of instruments traded, on price, order value and order count, on strategy positions and on the number of markets involved, together with more intensive monitoring of the algorithm's activity. This article is the regulatory shape of a canary release, and note what it bounds: **value and count, not just a percentage**. That is why `max_canary_order_notional` and `canary_notional_budget` exist alongside `canary_scale_factor`. Instrument-count and venue-count limits are *not* implemented here and remain the caller's obligation. |
| Art. 9 | Annual self-assessment and validation | The self-assessment is conducted article-by-article across RTS 6; a documented, attributable promotion trail is what makes the Art. 5 and Art. 8 answers evidenced rather than asserted. |
| Art. 12 | Kill functionality | Ability to cancel immediately, as an emergency measure, any or all unexecuted orders at any or all venues. Demoting a strategy to SHADOW is not this: it stops new submissions and touches nothing already resting at the venue. |
| Art. 15 | Pre-trade controls on order entry | Price collars, maximum order value, maximum order volume and maximum message limits, applied automatically before order entry. The canary router is *not* this control layer — it is strategy-side and configured by the strategy owner — but its limits should be set well inside the firm's Art. 15 limits, never as a substitute for them. |

Primary text: Commission Delegated Regulation (EU) 2017/589, EUR-Lex ELI
<https://eur-lex.europa.eu/eli/reg_del/2017/589/oj/eng>. UK onshored text:
<https://www.legislation.gov.uk/eur/2017/589>.

## 2. EU — ESMA Supervisory Briefing on Algorithmic Trading (26 February 2026)

**Status:** convergence tool for national competent authorities, issued under Art. 29(2)
of the ESMA Regulation. The briefing states it is **non-binding** and not subject to a
"comply or explain" mechanism. Treat it as supervisory expectation, not law.

Points that bear on canary releases:

- Firms are expected to test and validate **each algorithmic trading strategy before
  deployment and after any material change** — the trigger for this skill is the same
  "new or materially changed" test.
- The briefing defines an algorithmic trading strategy as decision logic, implemented
  through one or more algorithms, that autonomously pursues a defined trading objective,
  and requires each strategy to be **testable, distinguishable and attributable**, so
  supervisors can link observed trading behaviour to a specific strategy. Registering
  each strategy under a stable `strategy_id` and carrying that ID through to the order is
  what makes the canary's live activity attributable.
- ESMA flags both over-aggregation (many instruments and venues treated as one strategy)
  and over-fragmentation as supervisory problems; the granularity you register in the
  router should match the granularity you claim to your supervisor.
- Testing methodologies, procedures and **internal authorisations to deploy** must be
  well documented.

Reference: ESMA74-1505669079-10311, *Supervisory Briefing on Algorithmic Trading in the
EU*, 26 February 2026.

## 3. US — SEC Rule 15c3-5 and FINRA guidance

**Applicability:** SEC Rule 15c3-5 binds broker-dealers with market access. FINRA
Regulatory Notice 15-09 is **guidance**, addressed to FINRA member firms; it is not a
rule and sets no thresholds.

- **SEC Rule 15c3-5 (Market Access Rule), 17 CFR 240.15c3-5.** Financial and regulatory
  risk-management controls must be applied on an automated, **pre-trade** basis and be
  under the **direct and exclusive control** of the broker-dealer with market access.
  Consequence for this skill: a strategy-side scaling router is never the control the
  rule contemplates. Canary limits sit *inside* the broker-dealer's limits. Adopting
  release: SEC Rel. No. 34-63241
  <https://www.sec.gov/files/rules/final/2010/34-63241.pdf>.
- **FINRA Regulatory Notice 15-09 (March 2015), "Guidance on Effective Supervision and
  Control Practices for Firms Engaging in Algorithmic Trading Strategies".** Under
  *Software/Code Development and Implementation*, firms should consider "where feasible,
  deploying new algorithmic strategies in a pilot phase of limited size, increasing only
  as results are confirmed", and "when deploying new code, maintaining heightened
  scrutiny of the impacted trading account, including real-time monitoring of the subject
  algorithmic strategy". That is a canary release described in supervisory language, and
  it is the closest US analogue to RTS 6 Art. 8. The Notice also stresses that
  supervisory obligations **continue after** the strategy is in production.
  <https://www.finra.org/rules-guidance/notices/15-09>

## 4. Engineering standard — sizing the canary

*Recommended practice, not a regulatory requirement.*

- Size the canary in **two dimensions at once**: a fraction of intended size (typically
  1-10% as a starting point, to be calibrated), and an absolute per-order and cumulative
  notional cap. Either alone is insufficient — the fraction fails to bound a runaway
  request, and the cap alone makes small orders indistinguishable from full-size ones.
- Floor scaled quantities to the venue's lot step, never round to nearest: rounding up
  can exceed the very budget the canary is enforcing.
- Do the arithmetic in `Decimal`. `int(100 * 0.29)` is 28 in binary floating point.
- Set the caps from what a *defect* could cost, not from what normal operation needs. The
  question is "what is the largest loss I am willing to discover tomorrow morning", not
  "what size does the strategy usually trade".

## 5. Engineering standard — venue minimums and canary validity

*Recommended practice, not a regulatory requirement.*

- Lot step and minimum quantity are distinct constraints. Binance's spot `LOT_SIZE`
  filter publishes `minQty`, `maxQty` and `stepSize` separately, and an order passes only
  when `quantity >= minQty`, `quantity <= maxQty` and `quantity % stepSize == 0`. The
  separate `NOTIONAL` / `MIN_NOTIONAL` filter additionally requires
  `price * quantity >= minNotional`, which is precisely the check a scaled-down canary
  order fails.
  <https://developers.binance.com/docs/binance-spot-api-docs/filters>
- Below-lot behaviour is venue-specific and changes what the canary measures:
  - **HKEX**: odd lots are not accepted for auto-matching in the main order book; they
    trade in a separate special-lot facility matched by exchange participants. Slippage
    observed there does not predict slippage at board-lot size.
  - **US equities**: odd lots are executable, so the failure mode is execution quality
    rather than rejection. Note that the round-lot definition itself is no longer a flat
    100 shares — under the SEC's Market Data Infrastructure rules, price-tiered round lot
    sizes (100/40/10/1 shares) took effect on 3 November 2025, with odd-lot quote
    dissemination to the consolidated tape following in 2026. Treat "round lot = 100" as
    an assumption to verify per symbol, not a constant.
- If the scaled order cannot be executed in a representative way, the honest options are
  to canary a liquid subset of the universe, raise the scale factor for that instrument,
  or accept the phase as a plumbing smoke test and say so.

## 6. Engineering standard — what a canary can and cannot measure

*Recommended practice, not a regulatory requirement.*

- **Measurable at 5%**: order acceptance and rejection codes, order state transitions,
  round-trip latency, fee and rebate treatment, borrow availability, market-data-to-order
  path correctness, reconciliation against the broker, and slippage on liquid names.
- **Not measurable at 5%**: PnL (dominated by noise), market impact, queue position
  dynamics at size, capacity, margin and netting behaviour, and anything about the
  portfolio-level interaction of the strategy with others.
- Promotion criteria should therefore be stated in samples and execution behaviour, never
  in elapsed calendar time and rarely in PnL.

## 7. Engineering standard — auditability and shadow-data hygiene

*Recommended practice, which for in-scope EU firms also carries the Art. 5(7) obligation
in Section 1.*

- Record refused promotions, not only successful ones — a blocked SHADOW → PRODUCTION
  jump is exactly what a post-incident review needs to find.
- Record overrides distinguishably. A forced promotion that reads like an ordinary one
  defeats the guard.
- History list order is authoritative; wall-clock timestamps are for human reading and
  can move backwards across a clock correction.
- Store shadow-mode hypothetical fills in a separate store from live executions.
  A boolean flag on a shared table is one forgotten `WHERE` clause away from polluting
  PnL, exposure and tax-lot reporting.
- Retention periods are set by your applicable regime; this skill asserts none.
