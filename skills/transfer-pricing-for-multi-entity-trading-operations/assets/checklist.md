# Transfer Pricing Sign-Off Checklist — Multi-Entity Trading Operations

Complete per financial year, per service line. Paragraph references are to the OECD Transfer
Pricing Guidelines 2022.

## 1. Entities and agreements
- [ ] **Written intercompany agreements in force** covering each service line, dated before
      the period they price, and matching what the entities actually do.
- [ ] **Entities registered** via `register_entity()` with jurisdiction, `EntityType`, and
      headline tax rate.
- [ ] **Functional analysis completed** identifying who performs *and controls* each function
      — not who is named in the licence. Legal ownership alone confers no right to retain
      intangible return (para. 6.42).
- [ ] **Domestic pairs identified.** Settlements between two entities in the same jurisdiction
      are flagged in `settlement.warnings`; confirm domestic TP rules are addressed and that
      cross-border documentation is not being prepared for them by mistake.

## 2. Method and benchmark selection
- [ ] **Service characterised before the method was chosen** — not chosen to reach a target
      margin.
- [ ] **Simplified-approach eligibility tested and documented.** The OECD 5% (para. 7.61),
      US SCM at cost (Treas. Reg. s.1.482-9(b)) and IRAS Annex C 5% concession all exclude
      R&D, engineering/scientific services, financial transactions, and the group's core
      business (para. 7.47; s.1.482-9(b)(4)). Record which exclusion applies, or why none does.
- [ ] **Every `markup_pct` traced to a benchmarking study**, an APA, or a named safe harbour.
      The `markup_pct=10.0` dataclass default is a placeholder and is not evidence.
- [ ] **Every `benchmark_cup_rate_usd` traced to a third-party rate card** for a comparable
      service in comparable circumstances.
- [ ] **Cost base composition documented** — which costs are in, which are pass-through, and
      under which regime's rule.

## 3. Settlement review
- [ ] **`settlement.warnings` read and cleared** for every transaction, not just the totals.
- [ ] **`profit_level_indicator` matches the comparables set.** `NET_COST_PLUS_MARKUP` (TNMM)
      and `GROSS_COST_PLUS_MARKUP` (Cost Plus) are screened on different indicators and
      different cost bases.
- [ ] **Below-cost CUP fees justified in writing**, or repriced. A provider bearing cost and
      risk while pricing below cost is a standing adjustment risk.
- [ ] **Berry ratio, if used, computed from a real COGS/opex split** (`cogs_usd` and
      `operating_expenses_usd` supplied). A `None` Berry ratio is the correct output when the
      split is unknown — do not substitute the markup factor.
- [ ] **Berry ratio range sourced from your own comparables set.** The OECD publishes no
      target range and no audit-trigger threshold (paras. 2.106–2.108), and warns the ratio is
      very sensitive to the COGS/opex classification.
- [ ] **Berry ratio conditions of para. 2.107 satisfied** — function value proportional to
      operating expenses, not materially affected by the value of the product handled, and no
      other significant function requiring a different method.

## 4. Profit split
- [ ] **Profit split use justified** — unique and valuable contributions by both parties, or
      highly integrated operations. Not selected merely because a one-sided method was hard.
- [ ] **Split basis recorded.** `split_basis` is `CONTRIBUTION_ANALYSIS` (para. 2.150) or
      `RESIDUAL_ANALYSIS` (para. 2.152); the Local File must name the one actually run.
- [ ] **Routine returns benchmarked separately** where a residual analysis is used.
- [ ] **DEMPE weights evidenced.** Each score traces to the functional analysis. If the
      default equal weighting across the five functions was kept, record why that is
      appropriate here (paras. 2.166, 2.170, 2.171).
- [ ] **Allocations reconcile to combined profit** and to the statutory accounts of each entity.
- [ ] **Loss periods reviewed separately.** A negative PnL is flagged in `split.warnings`;
      confirm which entity contractually assumes and controls the downside risk before
      allocating a loss by an IP contribution key.

## 5. Documentation and penalty protection
- [ ] **Master File and Local File prepared** to each jurisdiction's own threshold and deadline.
- [ ] **CbC report filed only if in scope** — annual consolidated group revenue at or above
      **EUR 750 million** in the immediately preceding fiscal year (BEPS Action 13).
- [ ] **US: documentation in existence when the return was filed** and producible within
      **30 days** of an IRS request, covering the ten principal documents
      (Treas. Reg. s.1.6662-6(d)(2)(iii)). Exposure without it: **20%** of the underpayment
      where the net s.482 adjustment exceeds the lesser of $5m or 10% of gross receipts;
      **40%** at $20m or 20% (IRC s.6662(e), (h)).
- [ ] **UK: Master File and Local File** producible within **30 days** of an HMRC request for
      groups at the CbCR threshold (SI 2023/818); s.166 TIOPA 2010 SME exemption checked.
- [ ] **Working papers archived**, not just the answers: registered benchmarks, functional
      analysis, agreements, engine `warnings` output, and the version of the analysis used.
- [ ] **Reviewed by a qualified tax adviser.** Engine output is decision support, not a filing
      position.
