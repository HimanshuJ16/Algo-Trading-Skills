---
name: transfer-pricing-considerations-for-multi-entity-trading-operations
description: Intercompany pricing engine for trading groups spread across legal
  entities — computes Cost Plus, CUP and TNMM settlement amounts from
  benchmarks you register, computes a Berry ratio only from a supplied
  COGS/opex split, and runs a DEMPE-keyed profit split as either a contribution
  or a residual analysis.
domain: Tax Accounting & Reporting
subdomain: Transfer Pricing & Multi-Entity Allocation
tags:
- transfer-pricing
- oecd-beps
- dempe
- arms-length-principle
- cost-plus
- berry-ratio
- profit-split
- multi-entity
brokers_frameworks:
- OECD Transfer Pricing Guidelines 2022 (Ch. II, VI, VII)
- IRC s.482 and Treas. Reg. s.1.482-9
- IRC s.6662(e)/(h) and Treas. Reg. s.1.6662-6
- TIOPA 2010 Part 4 (UK)
- IRAS Transfer Pricing Guidelines (Singapore)
- Python Dataclasses
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when a trading operation is split across legal entities and value has to be priced as it crosses between them: a US holdco that owns the strategy code, a UK-regulated manager, a Singapore execution hub, a Cayman fund. Quant research charged to the IP owner, execution routing recharged to the manager, shared market-data and co-location cost pools, and the split of global trading PnL between the entity that built the alpha and the entity that ran it are all controlled transactions that need an arm's length price and contemporaneous documentation.

The engine computes:

- **Cost Plus** and **TNMM** settlement fees from a cost base and a markup **you have benchmarked** (`calculate_cost_plus_fee`).
- **CUP** fees from executed volume and a third-party rate card (`calculate_cup_fee`).
- The **Berry ratio** — gross profit over operating expenses (OECD TPG 2022 para. 2.106) — but only from a COGS/opex split you supply.
- A **DEMPE-keyed profit split**, run as a contribution analysis (para. 2.150) or, when routine returns are supplied, as a residual analysis (para. 2.152).

Every settlement carries a `warnings` tuple recording the assumptions and red flags behind the number.

## When NOT to Use

- **As a benchmarking database.** It ships with no markups and no arm's length ranges. A markup is arm's length because a benchmarking study of comparable independent parties says so (TPG 2022 Chapter III), not because it is the default in a dataclass. The `markup_pct=10.0` default is a placeholder, not a safe harbour.
- **To justify a profit allocation you have already decided on.** DEMPE weights are an input. Feeding in weights reverse-engineered from a desired split produces an arithmetically consistent answer with no substance behind it, which is the exact fact pattern IRC s.6662(e)/(h) penalises at 20%/40% of the underpayment.
- **As the source of a Berry ratio range.** The OECD defines the ratio (para. 2.106) and warns it is very sensitive to the COGS/opex classification (para. 2.107). It publishes no target range and no audit-trigger threshold. Any range you apply must come from your own comparables set.
- **For the low value-adding simplified approaches.** The OECD 5% markup (para. 7.61), the US Services Cost Method at cost (Treas. Reg. s.1.482-9(b)) and the IRAS 5% routine-support concession all **exclude** R&D, scientific, and financial-transaction services — which is what quant research and execution routing are. See `references/standards.md`.
- **As a filing position or a tax opinion.** Output is decision support for the adviser preparing the Local File. Entity characterisation, risk control, and whether a transaction should be recognised at all are legal determinations this engine does not make.

## Prerequisites

- Python 3.9+ (standard library only: `dataclasses`, `decimal`, `enum`, `logging`).
- A benchmarking study, or an accepted APA/safe harbour, supporting each markup and each CUP rate you register.
- Written intercompany service agreements covering each service line, in force for the periods being priced.
- The provider's own P&L split into COGS and operating expenses, if you want a Berry ratio.
- A functional analysis identifying who actually performs and **controls** each DEMPE function — not who is named in the licence.

## Workflow

1. **Register entities.** `register_entity(LegalEntity(...))` for each party, with jurisdiction, `EntityType`, and headline tax rate. The engine rejects a transaction whose provider or recipient is unregistered, and rejects a provider that equals its own recipient.
2. **Characterise the service before picking a method.** If it is a genuine routine support service (payroll, accounting, IT helpdesk) a simplified approach may be open; if it is quant research, engineering, or execution, it is excluded from every simplified approach and needs a benchmarked markup or a CUP. Do not select a method to fit a target margin.
3. **Register the benchmark.** Set `markup_pct` from your study, or `benchmark_cup_rate_usd` from a third-party rate card. `markup_pct=0.0` is a legitimate value for a US Services Cost Method charge or an IRAS strict pass-through cost pool — it is not a missing value.
4. **Price the transaction.** `process_intercompany_transaction(tx)` returns the fee, the markup amount, the `profit_level_indicator` actually tested (`GROSS_COST_PLUS_MARKUP` vs `NET_COST_PLUS_MARKUP` vs `COMPARABLE_UNCONTROLLED_PRICE`), and warnings. **Read the warnings** — a below-cost CUP fee or a TNMM run on a gross-margin cost base is flagged there, not raised.
5. **Supply the COGS/opex split if you need a Berry ratio.** Pass `cogs_usd` and `operating_expenses_usd`. Omit them and `berry_ratio` is `None` with a note, because a ratio inferred from the cost base is the markup factor wearing a different name.
6. **Split combined profits deliberately.** Call `calculate_profit_split(pnl, contributions)` for a contribution analysis. If some contributions *can* be benchmarked with a one-sided method, price those first and pass them as `routine_returns_usd` — that is a residual analysis, and the two produce different numbers. Use `dimension_weights` when the five DEMPE functions are not equally valuable here, and record why.
7. **Archive the working, not just the answer.** Store the registered benchmarks, the functional analysis behind each weight, the agreements, and the `warnings` output alongside the Master File and Local File.

## Common Pitfalls

- **Reporting revenue over cost as a Berry ratio.** The Berry ratio is gross profit over operating expenses (TPG 2022 para. 2.106). For a provider with real COGS, dividing the whole fee by the whole cost base overstates it — a provider billing $1.2m with $700k COGS and $400k opex has a Berry ratio of 1.25, not 1.09. Where COGS is genuinely zero the two coincide, but that has to be a stated fact, not an inference.
- **Calling a contribution split a residual split.** Dividing the entire PnL by DEMPE keys is a contribution analysis (para. 2.150). A residual analysis (para. 2.152) rewards benchmarkable functions first and divides only what is left. Describing one as the other in the Local File misstates the method to the examiner.
- **Treating equal-weighted DEMPE scores as an OECD formula.** The OECD publishes no numeric DEMPE score. Development and exploitation of a trading algorithm are rarely worth the same, and an equal weighting needs the same evidence as any other allocation key (paras. 2.166, 2.170, 2.171).
- **Applying the 5% simplified markup to quant research.** OECD para. 7.47 excludes R&D and the group's core business from low value-adding services; the US Services Cost Method excludes research, engineering or scientific services and financial transactions. For a quant fund, the core intercompany flows are exactly the excluded ones.
- **Assuming the IP owner keeps the residual because it owns the IP.** Legal ownership alone confers no right to retain intangible return (para. 6.42). An IP holdco with no people performing or controlling DEMPE functions is a cash box, and the return follows the functions.
- **Routing a rate card through an entity with no cost base.** A CUP transaction with `base_cost_usd=0.0` prices and settles, but a provider recharging a third-party rate with no cost and no risk is a conduit; the markup it retains needs a functional justification.
- **Letting documentation lapse.** US contemporaneous documentation must exist when the return is filed and be produced within 30 days of an IRS request (Treas. Reg. s.1.6662-6(d)(2)(iii)); the UK Master File/Local File must be produced within 30 days of an HMRC request. Documentation produced after the fact does not restore the penalty defence.

## Verification

```bash
python -m unittest discover -s skills/transfer-pricing-considerations-for-multi-entity-trading-operations/scripts
```

The suite covers half-up money rounding, cost-plus and CUP arithmetic against independently derived figures, rejection of out-of-range markups and DEMPE weights, self-dealing and unregistered-party rejection, the Berry ratio computed from a real COGS/opex split, and contribution versus residual splits reconciling to the total PnL.

## Related Skills

- `multi-jurisdiction-tax-residency-implications`
- `double-taxation-treaty-considerations-cross-border-trading`
- `vat-gst-treatment-of-trading-related-services`
- `record-keeping-requirements-for-tax-audit-defense`
