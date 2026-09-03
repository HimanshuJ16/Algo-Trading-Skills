---
name: custodial-vs-non-custodial-tradeoff-assessment
description: Use when choosing between custodial exchange execution, off-exchange
  settlement, and non-custodial on-chain execution, to disqualify architectures that
  breach a mandate's key-control, counterparty-exposure or latency limits before
  ranking the survivors
domain: Crypto Custody & Security
subdomain: Custody Architecture Design
tags:
- crypto-custody
- cex-vs-dex
- non-custodial
- off-exchange-settlement
- fireblocks
- counterparty-risk
- mpc-wallet
brokers_frameworks:
- Fireblocks Off-Exchange
- Copper ClearLoop
- Python Dataclasses
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when designing institutional crypto trading infrastructure, fund treasury management, or choosing between centralized exchanges (CEX), off-exchange settlement networks (Fireblocks Off-Exchange, Copper ClearLoop), and non-custodial on-chain execution. Custodial venues offer the lowest execution latency and deepest liquidity but place the traded balance inside the venue's balance sheet. Non-custodial execution gives sole key control but is bounded by block inclusion, priced in volatile gas, and exposed to MEV and contract risk. Off-exchange settlement sits between them, and the point of this assessment is to make that middle position explicit rather than assumed.

The module runs **hard constraints first**. A key-sovereignty mandate, a counterparty-exposure budget, a latency floor and a qualified-custodian requirement are pass/fail — they disqualify architectures outright. Only survivors are ranked.

## When NOT to Use

- **As a risk model.** The composite score is an ordinal heuristic for comparing survivors under weights you declare. It is not a calibrated probability of loss, and no part of it is empirically fitted. Two architectures separated by three points are not meaningfully different.
- **With the shipped default figures.** `DEFAULT_ARCHITECTURE_PROFILES` carries `is_illustrative_default=True` and the engine raises an advisory whenever it is used. The residual-exposure and gas-burden numbers are placeholders, not due diligence. Replace them with your own before acting — see `custody-solution-vendor-due-diligence-checklist`.
- **As a substitute for a jurisdictional analysis.** Whether you *may* self-custody, and whether a third-party qualified custodian is mandatory, is determined by your licence and jurisdiction — not by a latency budget. The engine accepts a `regulatory_constraint` you assert; it does not determine one. See `regulatory-custody-requirements-by-jurisdiction`.
- **To compare venues within an architecture class.** This ranks architecture *classes*. It says nothing about whether one exchange is safer than another, which is counterparty due diligence.
- **For chains other than the configured one.** The default non-custodial latency floor is Ethereum L1's ~12 s slot time. An L2 or a high-throughput chain has a materially lower floor — override `min_achievable_latency_ms` or the assessment will disqualify on-chain execution that is in fact viable.

## Prerequisites

- Strategy requirements: `required_latency_ms` (end-to-end budget), `monthly_volume_usd`, `max_counterparty_risk_pct`, `gas_sensitivity_score` (0.0–1.0), `requires_key_sovereignty`, optional `regulatory_constraint`.
- Architecture profiles carrying, per candidate: key-control model, latency floor, residual counterparty exposure, gas cost burden, and whether a qualified custodian is provided.
- Composite weights (`latency`, `custody`, `cost`), which must be non-negative and sum to 1.0.

## Workflow

1. **Requirements Ingestion**: Build `StrategyRequirements`. Every field is validated on construction. NaN is the case that matters — every `<=` comparison against NaN is False, so an unvalidated NaN latency budget skips every latency constraint and yields a confident recommendation from garbage. It now raises.

   Read `max_counterparty_risk_pct` as a **tolerance budget**: the maximum share of deployed capital the mandate permits to sit exposed to a custodial counterparty. `0.0` means zero tolerance and disqualifies every architecture carrying residual exposure. It does not mean "unconstrained".

2. **Hard Constraints (disqualify, do not discount)**:
   - `requires_key_sovereignty` is satisfied **only** by sole client signing control. MPC co-control with a venue and third-party qualified custody are not self-sovereignty. A firm that accepts those should set this `False` and express its limit through `max_counterparty_risk_pct` instead — that is the decision point, and it should be made deliberately.
   - Disqualify when `residual_counterparty_exposure_pct > max_counterparty_risk_pct`. Exposure sitting exactly on the mandate limit passes.
   - Disqualify when `min_achievable_latency_ms > required_latency_ms`. The floor for on-chain execution is block inclusion, not network round-trip: a 200 ms budget cannot be served by an architecture whose settlement floor is a 12-second slot, however fast the client's link is.
   - Disqualify architectures without a qualified custodian when `regulatory_constraint=QUALIFIED_CUSTODIAN_REQUIRED`.

3. **Rank the survivors**: composite = `w_latency × latency_headroom + w_custody × custody_control + w_cost × cost_efficiency`, with the weights returned on the report so the trade-off actually applied is auditable. Latency scores **headroom**, not raw speed — an architecture sitting exactly on the budget scores 0 because it has no margin for a bad tail. Ties break toward lower residual counterparty exposure, then by name; without an explicit rule, ties resolve by declaration order and quietly favour whichever profile is listed first.

4. **Fail closed**: if every candidate is disqualified, raise `NoEligibleArchitectureError`. Returning the least-bad disqualified option would present a mandate breach as a recommendation. A zero-tolerance mandate with a 1 ms budget is genuinely unsatisfiable and a human has to relax something.

5. **Advisories and mitigations**: the report carries per-architecture disqualification reasons, mitigations for the recommended model, an off-exchange-settlement review advisory when a custodial venue wins above the volume threshold (a firm-policy default, not a regulatory one), and a warning whenever illustrative defaults were used.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Scoring a Mandate Breach Instead of Disqualifying It**: If "zero counterparty tolerance" is a penalty rather than a veto, a strong latency and fee score carries the custodial venue back to the top of the ranking. That is how a fund ends up with its balance sheet inside the next FTX. Constraints that a mandate states as absolute must be pass/fail.
- **Inverting the Counterparty Tolerance**: Treating a low `max_counterparty_risk_pct` as "little risk here, score it well" rather than "little risk permitted, exclude exposed options" reverses the recommendation for exactly the most risk-averse mandates — the ones where being wrong costs the most.
- **Modelling On-Chain Latency as Network Latency**: On-chain execution cannot settle faster than block inclusion. Ethereum mainnet's slot time is 12 seconds, so sub-second budgets are unreachable on L1 regardless of colocation or RPC quality.
- **Treating Off-Exchange Settlement as Zero Counterparty Risk**: It removes venue *insolvency* risk from principal, but unsettled P&L on open positions stays exposed between settlement cycles, and custodian and trust-structure risk is substituted for venue risk. It is a reduction and a substitution, not an elimination.
- **Claiming MEV Protection Is Prevention**: A private mempool hides transactions from sandwich and frontrunning bots, but it does not guarantee no MEV — protection is partly remedial (refunds), inclusion is not guaranteed, and the relay is a trusted intermediary.
- **Assuming Self-Custody Removes Counterparty Risk Without Adding Any**: It replaces it with key-management, smart-contract and bridge risk, which are uncorrelated with venue risk but not smaller by default.
- **Silent Tie-Breaking**: Equal composite scores resolved by list order make the recommendation an artefact of declaration order.
- **Unjustified Magic Weights**: Weights buried in the implementation make the trade-off unauditable. Declare them, validate that they sum to 1.0, and return them on the report.
- **Ignoring Jurisdiction**: A recommendation that is optimal on latency and cost may be one the entity is not permitted to implement.

## Verification

- Instantiate `CustodialTradeoffAssessorEngine`. Evaluate an HFT strategy (1 ms budget, \$50M/month, 100% tolerance) and verify a custodial or hybrid architecture is recommended and `NON_CUSTODIAL_DEX` is **disqualified on its latency floor**, not merely ranked last.
- Evaluate a treasury strategy (12,000 ms budget, `max_counterparty_risk_pct=0.0`) and verify `CUSTODIAL_CEX` and `HYBRID_OFF_EXCHANGE_SETTLEMENT` appear in `disqualified` with an "exceeds the mandate tolerance" reason, and `NON_CUSTODIAL_DEX` is recommended.
- Verify raising `max_counterparty_risk_pct` only ever admits more architectures, never fewer.
- Verify `requires_key_sovereignty=True` leaves only the sole-key-control architecture eligible.
- Verify a 200 ms budget disqualifies on-chain execution on its 12,000 ms floor.
- Verify a 1 ms budget with zero counterparty tolerance raises `NoEligibleArchitectureError` rather than recommending anything.
- Verify `required_latency_ms=float("nan")` and `gas_sensitivity_score=-1.0` both raise `ValueError`.
- Verify `monthly_volume_usd` above the threshold with a custodial recommendation produces the off-exchange-settlement advisory, and that changing the weights changes the ranking.
- Run `python -m unittest discover -s skills/custodial-vs-non-custodial-tradeoff-assessment/scripts` and confirm a 100% pass rate.

## Related Skills

- `hot-cold-wallet-split-for-trading-bots`
- `multi-party-computation-mpc-custody-solutions`
- `custody-solution-vendor-due-diligence-checklist`
- `regulatory-custody-requirements-by-jurisdiction`
- `crypto-wallet-key-custody-security`
---
