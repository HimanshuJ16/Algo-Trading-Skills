# Standards for Smart Order Router Failover on Venue Outage

## What is actually mandated, and on whom

Read the scope column before applying any row. Most of this skill's subject
matter is **engineering practice, not regulation**, and the one rule that speaks
directly to bypassing a broken venue binds *trading centers*, not every router.

| Source | Jurisdiction | Binds | What it actually says |
|---|---|---|---|
| 17 CFR 242.611(b)(1) — Order Protection Rule, self-help exception | US | Trading centers | A trade-through is excepted where it "was effected when the trading center displaying the protected quotation that was traded through was experiencing a failure, material delay, or malfunction of its systems or equipment." |
| 17 CFR 242.611(a)(1) | US | Trading centers | Requires "written policies and procedures that are reasonably designed to prevent trade-throughs ... that do not fall within an exception." |
| 17 CFR 242.600(b)(106) | US | — | "Trading center" = an exchange, an SRO trading facility, an ATS, an exchange or OTC market maker, "or any other broker or dealer that executes orders internally by trading as principal or crossing orders as agent." **A broker that only routes orders away is generally not a trading center**, and Rule 611 does not attach to its routing decisions. |
| 17 CFR 242.600(b)(7) | US | Automated trading centers | An automated trading center must "[i]mmediately identif[y] its quotations as manual quotations whenever it has reason to believe that it is not capable of displaying automated quotations." The venue is supposed to withdraw its own protection when it breaks — do not rely on it doing so. |
| FINRA Rule 5310(a)(1) | US | FINRA member firms | Reasonable diligence for best execution weighs, among other factors, "(C) the number of markets checked" and "(D) accessibility of the quotation." A quotation at a venue you cannot reach is not an accessible one. |
| FINRA Rule 5310 Supplementary Material .09 | US | FINRA member firms | Firms not conducting order-by-order review must conduct "regular and rigorous reviews of the quality of the executions of its customers' orders," at minimum quarterly. Venue outage and rejection statistics belong in that review. |
| 17 CFR 240.15c3-5(b), (c) | US | Broker-dealers with market access | Requires "a system of risk management controls and supervisory procedures reasonably designed to manage the financial, regulatory, and other risks" of market access. Note the rule does **not** prescribe automation; it requires controls "reasonably designed to systematically limit" exposure. |
| 17 CFR 240.15c3-5(d)(1) | US | Broker-dealers with market access | Those controls "shall be under the direct and exclusive control of the broker or dealer." A failover policy outsourced to a vendor's black box does not satisfy this. |
| Commission Delegated Regulation (EU) 2017/589 (RTS 6), Article 14 — Business continuity arrangements | EU (and UK assimilated law) | Investment firms engaged in algorithmic trading | Requires documented business continuity arrangements proportionate to the business, that "effectively deal with disruptive incidents and, where appropriate, ensure a timely resumption of the algorithmic trading"; that the trading system can be shut down "without creating disorderly trading conditions"; and that the arrangements are reviewed and tested annually. |

### Live rule-change watch — Rule 611 rescission is *proposed*, not adopted

On **11 June 2026** the SEC proposed rescinding Rule 611 in its entirety along
with Rule 610(e) (locked/crossed markets) — Securities Exchange Act Release No.
**34-105680**; the comment period closed **17 August 2026**. **As of this
skill's revision date the proposal has not been adopted and Rule 611, including
the 611(b)(1) self-help exception, remains in effect.** Do not write code or
compliance text that assumes either outcome. If the rescission is adopted, the
engineering in this skill does not become obsolete — the SEC's own proposing
release argues broker-dealer best execution obligations would carry more weight
without the Rule 611 floor, which raises rather than lowers the bar on
documenting *why* a router bypassed a venue.

## The self-help standard, quoted

These are the operative passages from the Regulation NMS adopting release, SEC
Release No. **34-51808** (70 FR 37496, 29 June 2005), Section II.A on the
self-help remedy. They are the closest thing that exists to a published
threshold for "when may I stop routing to this venue," and they are worth
reading literally.

| Point | Release text |
|---|---|
| The threshold is *repeated* failure, at one second | "the Commission believes that trading centers should be entitled to bypass another trading center's quotations if it repeatedly fails to respond within one second to incoming orders attempting to access its protected quotations." |
| You need pre-committed objective parameters | Policies and procedures "will need to set forth specific objective parameters for dealing with problem trading centers and for monitoring compliance with the self-help remedy." |
| Notify — but not beforehand | "a trading center should be allowed simply to notify the non-responding trading center immediately after (or at the same time as) electing self-help pursuant to objective standards consistent with Rule 611 that are contained in its policies and procedures." |
| **Check yourself first** | "An electing trading center must also assess, however, whether the cause of a problem lies with its own systems and, if so, take immediate steps to resolve the problem appropriately." |
| A vendor may detect, but you stay responsible | "a third-party vendor could perform such a function, but ... the responsibility for compliance with the exception remains with the relevant trading center that uses the services of the third-party vendor." |

Note what the one-second figure is and is not. It is a *response-time* standard
for judging whether an away venue is broken — how long that venue takes to
answer an order. It is **not** a deadline for your own failover logic, and the
Commission frames it as an entitlement to bypass, not a command to bypass.

Separately, the SEC's 2016 interpretation of "immediate" for automated
quotations (Release No. 34-78102) treats intentional access delays of less than
one millisecond as de minimis. That governs whether a venue's quote counts as
automated; it is unrelated to outage failover, and it is not a latency budget
for a router.

## Engineering defaults — configurable, not mandated

Nothing below is imposed by any regulator or exchange. These are the reference
implementation's defaults, stated here so they are not mistaken for rules. Every
one is a constructor argument; calibrate them to your venues and measure them.

| Parameter | Default | Why this value, and what actually drives it |
|---|---|---|
| `max_error_threshold` | 3 consecutive errors | Enough to ride out a single lost datagram or one-off gateway hiccup without flapping. The adopting release's standard is *repeated* failure, and names no count. Calibrate against your own measured per-venue reject and timeout base rate. |
| `max_quote_age_seconds` | 1.0 s | Deliberately aligned to the release's one-second response standard so a quote that could not have been refreshed within it stops leading price selection. Tighten hard for a co-located feed; 1 s is far too loose for HFT and roughly right for a consolidated-feed retail router. |
| `cooldown_seconds` | 60 s | Long enough that a probe order is not thrown at a venue still restarting. No regulatory basis whatsoever. |
| `backoff_multiplier` / `max_cooldown_seconds` | 2.0 / 600 s | Prevents a venue that is down for the session from being probed with a live order every 60 seconds all day. |
| `local_fault_threshold_ratio` | 0.5 | Implements the release's self-diagnosis duty. Half your venues failing simultaneously is far more often your NIC, DNS, clock, or credentials than a simultaneous multi-venue outage. |

There is no regulatory failover-latency SLA. Any figure you adopt — 10 ms or
otherwise — is an internal engineering target you must measure and evidence, not
a standard you can cite.

## Sources

- 17 CFR 242.611 — <https://www.law.cornell.edu/cfr/text/17/242.611>
- 17 CFR 242.600 (definitions) — <https://www.law.cornell.edu/cfr/text/17/242.600>
- SEC Release No. 34-51808, Regulation NMS adopting release (70 FR 37496, 29 June 2005) — <https://www.sec.gov/files/rules/final/34-51808.pdf>
- SEC Release No. 34-105680, proposed rescission of Rules 611 and 610(e) (11 June 2026; comments closed 17 August 2026; **not adopted**) — <https://www.federalregister.gov/documents/2026/06/17/2026-12163/the-trade-through-rule-and-locked-and-crossed-markets-provisions-of-regulation-nms>
- SEC Release No. 34-78102, Commission Interpretation Regarding Automated Quotations (17 June 2016) — <https://www.sec.gov/files/rules/interp/2016/34-78102.pdf>
- FINRA Rule 5310, Best Execution and Interpositioning — <https://www.finra.org/rules-guidance/rulebooks/finra-rules/5310>
- 17 CFR 240.15c3-5, Risk Management Controls for Brokers or Dealers with Market Access — <https://www.law.cornell.edu/cfr/text/17/240.15c3-5>
- Commission Delegated Regulation (EU) 2017/589 (RTS 6) — <https://eur-lex.europa.eu/eli/reg_del/2017/589/oj/eng>
