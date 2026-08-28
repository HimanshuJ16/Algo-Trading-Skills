# Standards for Smart Order Routing Across Venues

Jurisdiction: **United States, NMS stocks only.** Every rule below is a US federal
securities regulation administered by the SEC. None of it applies to listed
options, to non-US venues, or to instruments outside the NMS stock definition.

## 1. Who the Order Protection Rule binds

| Rule / Concept | Requirement | Citation |
|---|---|---|
| Rule 611(a) — Order Protection Rule | A **trading center** must establish, maintain and enforce written policies and procedures reasonably designed to prevent trade-throughs of **protected quotations** — i.e. executions at prices inferior to a protected bid/offer displayed elsewhere. "Trading center" covers exchanges, SRO trading facilities, ATSs, OTC market makers, and broker-dealers that execute orders internally. | 17 CFR 242.611(a) |
| Protected bid / protected offer | A quotation in an NMS stock that (i) is displayed by an *automated trading center*, (ii) is disseminated under an effective NMS plan, and (iii) is an *automated quotation* that is the **best bid or best offer of a national securities exchange or national securities association**. Depth-of-book quotations behind a venue's own BBO are **not** protected. | 17 CFR 242.600(b)(81)–(82) |
| Best execution (routing brokers) | A broker-dealer that routes rather than executes internally owes a best-execution duty under FINRA Rule 5310; it is not itself the Rule 611 obligor. Its routing choices are nonetheless what expose the receiving trading center. | FINRA Rule 5310 |
| Scope — options excluded | Rule 611 reaches NMS stocks. Listed options trade-through protection is governed instead by the **Options Order Protection and Locked/Crossed Market Plan** (the options linkage plan), approved as an NMS plan in 2009. Its protected-quote definition and exemptions are not the same. | Options Order Protection and Locked/Crossed Market Plan (2009) |

## 2. Intermarket Sweep Orders — the exception this engine's remainder invokes

An ISO is defined as a limit order in an NMS stock that:

1. is **identified as an intermarket sweep order** when routed to a trading center; **and**
2. **simultaneously** with that routing, one or more additional limit orders, as
   necessary, are routed to execute against the **full displayed size** of any
   protected bid (for a sell) or protected offer (for a buy) priced **superior to
   the ISO's limit price**.

*Source: 17 CFR 242.600(b)(47).*

Both conditions are constitutive. Condition 2 is not advisory: an order tagged
ISO without the accompanying full-size orders to superior protected quotations is
not an ISO, and the resulting execution is an unexcused trade-through.

Rule 611(b) exceptions that reference ISOs:

| Exception | Condition | Citation |
|---|---|---|
| 611(b)(5) | The trade-through transaction was the execution of an order identified as an ISO. | 17 CFR 242.611(b)(5) |
| 611(b)(6) | The trade-through was effected by a trading center that simultaneously routed an ISO to execute against the full displayed size of the protected quotation traded through. | 17 CFR 242.611(b)(6) |
| 611(b)(8) | Flickering quote: the venue displaying the traded-through protected quotation had, within **one second** prior to execution, displayed a BBO equal or inferior to the trade-through price. | 17 CFR 242.611(b)(8) |

The full exception set (self-help, benchmark/VWAP, stopped orders, crossed
markets) and the surveillance side of Rule 611 are covered by
`us-reg-nms-order-protection-rule-compliance`.

## 3. Access fee cap — Rule 610(c), a value in transition

| Period | Cap for protected quotations / exchange BBO in NMS stocks priced ≥ \$1.00 | Sub-\$1.00 |
|---|---|---|
| Operative today | **\$0.0030 per share** (the long-standing Reg NMS cap) | 0.3% of quotation price |
| Under the 2024 amendments, once their compliance date arrives | **\$0.0010 per share** | 0.1% of quotation price |

Timeline, as verified at the time of writing:

- **September 2024** — the SEC adopted amendments to Rules 610(c) and 612 reducing
  the access fee cap to \$0.0010/share and introducing a \$0.005 minimum pricing
  increment tier. *(SEC press release 2024-137.)*
- **October 14, 2025** — the D.C. Circuit denied the petition for review, upholding
  the amendments.
- **Compliance date deferred.** The compliance date was first pushed to the first
  business day of November 2026, then extended a further year by SEC exemptive
  order in June 2026. Until it arrives, the \$0.0030 cap governs.

Because this number is in motion, the engine exposes it as
`SmartOrderRoutingAcrossVenuesConfig.access_fee_cap_per_share` (default
\$0.0030) and *warns* rather than rejects when a venue's taker fee exceeds it —
off-exchange venues and non-protected quotations are not bound by the cap.
**Re-verify the operative cap and its compliance date against the SEC before
relying on the default.**

## 4. Minimum pricing increment — Rule 612, and why the router needs it

| Instrument | Minimum quoting increment | Citation |
|---|---|---|
| NMS stock priced ≥ \$1.00 | \$0.01 (today). The 2024 amendments add a \$0.005 tier for stocks whose Time Weighted Average Quoted Spread over the evaluation period is ≤ \$0.015 — same deferred compliance date as the fee cap. | 17 CFR 242.612(a) |
| NMS stock priced < \$1.00 | \$0.0001 | 17 CFR 242.612(b) |

The router quantizes every price onto this grid before comparing, so passing the
wrong increment is a correctness bug, not a rounding preference: too coarse and
distinct price levels merge (and normal books read as locked); too fine and
identically-quoted venues split apart.

## 5. Pending: proposed rescission of Rule 611

On **June 11, 2026** the SEC proposed rescinding Rule 611 (trade-through) and
Rule 610(e) (locked/crossed market prohibition), citing complexity, fragmentation
and the sufficiency of best-execution duties. *(SEC Release No. 34-105655;
60-day comment period from Federal Register publication.)*

**This is a proposal, not law.** Rule 611 remains in effect and enforceable. Do
not relax trade-through controls on the strength of a proposed rule. If the
rescission is adopted, the routing mechanics in this skill remain sound as
best-execution practice, but the Section 1 and Section 2 obligations would need
restating — re-verify status before relying on this section.

## 6. Concurrency

Child orders in a multi-venue sweep must be dispatched **concurrently**, not
serially. Two reasons, and they are different: serial dispatch lets the market
react to the first child before the rest arrive (execution quality), and the ISO
definition in §2 requires the accompanying orders to be routed *simultaneously*
(regulatory validity). This engine emits a plan; the simultaneity guarantee is
the dispatcher's responsibility.

## References

- 17 CFR 242.600 — Reg NMS definitions (ISO at (b)(47); protected bid/offer at (b)(81)–(82)): https://www.law.cornell.edu/cfr/text/17/242.600
- 17 CFR 242.610 — access to quotations, fee caps: https://www.law.cornell.edu/cfr/text/17/242.610
- 17 CFR 242.611 — Order Protection Rule: https://www.law.cornell.edu/cfr/text/17/242.611
- 17 CFR 242.612 — minimum pricing increment: https://www.law.cornell.edu/cfr/text/17/242.612
- SEC press release 2024-137, "SEC Adopts Rules to Amend Minimum Pricing Increments and Access Fee Caps": https://www.sec.gov/newsroom/press-releases/2024-137
- Options Order Protection and Locked/Crossed Market Plan (2009): https://www.theocc.com/getmedia/7fc629d9-4e54-4b99-9f11-c0e4db1a2266/options_order_protection_plan.pdf
- FINRA Rule 5310 — Best Execution and Interpositioning: https://www.finra.org/rules-guidance/rulebooks/finra-rules/5310
