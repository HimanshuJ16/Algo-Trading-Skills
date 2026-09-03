# Standards for SEC Regulation NMS Rule 611 (Order Protection Rule)

Source of record: **17 CFR 242.611** and the definitions at **17 CFR 242.600(b)**,
read with the SEC Division of Trading and Markets' *Responses to Frequently
Asked Questions Concerning Rule 611 and Rule 610 of Regulation NMS*. Verified
against the rule text on **2 September 2026**. Quotations below are the rule's
own words, not a secondary summary.

| Metric | Engineering standard |
|---|---|
| Instrument scope | **NMS stocks only.** Listed options, futures, fixed income and FX are outside Rule 611. |
| Session scope | **09:30–16:00 Eastern** (Rule 600(b)(88)). Rule 600(b)(105) confines "trade-through" to regular trading hours, so Rule 611 does not reach a print outside it (FAQ 7.01). The Rule 605(a)(3) procedures that could change the window have never been used. |
| Trade-through test | **Price-based, not side-based**: `price < protected bid` OR `price > protected offer`, applied to purchases and sales alike. |
| Protected quotation | Rule 600(b)(81)/(82): displayed by an **automated trading centre**, disseminated under an effective NMS plan, and the **best** bid or offer of a national securities exchange or national securities association. **Top of book only** — depth is never protected. |
| Manual quotations | Rule 600(b)(54): any quotation that is not automated. A manual quotation may be traded through freely. |
| Quote data of record | **Firm-specific** quotation data with the firm's own receipt timestamps (FAQ 6.01/6.02). Network (SIP) data is what regulators screen with (FAQ 6.04) and will disagree with the firm's book. |
| Time of execution | "*When final agreement is reached on the stock, price, and size of the trade*", documented simultaneously and not subject to retrospective alteration (FAQ 3.02). Not the report time. |
| Who is bound | Rule 611(a) binds **trading centres** (Rule 600(b)(106)): exchanges, SRO trading facilities, ATSs, exchange and OTC market makers, and any broker-dealer that executes orders internally. Rule 611(c) binds anyone **routing** an ISO. |
| Nature of the obligation | Rule 611(a)(1) is a **policies-and-procedures** standard, not per-trade strict liability, plus a Rule 611(a)(2) duty to "*regularly surveil*" and "*take prompt action to remedy deficiencies*". |

## 1. The obligation — Rule 611(a)

> **(a)(1)** A trading center shall establish, maintain, and enforce written
> policies and procedures that are reasonably designed to prevent trade-throughs
> on that trading center of protected quotations in NMS stocks that do not fall
> within an exception set forth in paragraph (b) of this section and, if relying
> on such an exception, that are reasonably designed to assure compliance with
> the terms of the exception.
>
> **(a)(2)** A trading center shall regularly surveil to ascertain the
> effectiveness of the policies and procedures required by paragraph (a)(1) of
> this section and shall take prompt action to remedy deficiencies in such
> policies and procedures.

## 2. The definition that governs — Rule 600(b)(105)

> **Trade-through** means the purchase or sale of an NMS stock during regular
> trading hours, either as principal or agent, at a price that is lower than a
> protected bid or higher than a protected offer.

Two consequences engineers routinely miss:

1. **The side of the order is not in the test.** A *purchase* below the
   protected bid is a trade-through of that bid. The clearest internal proof is
   Rule 611(b)(9): it exists to except a **stopped buy order** printed *lower
   than the national best bid*. An exception is only needed for conduct the rule
   otherwise prohibits.
2. **Outside 09:30–16:00 ET there is no trade-through**, and therefore nothing
   to except. FAQ 7.01: policies and procedures "*are not required to address
   trades that occur outside of regular trading hours, and the exceptions in
   Rule 611(b), including the ISO exception, are not needed outside of regular
   trading hours*."

## 3. The nine exceptions — Rule 611(b), verbatim

| Cite | Name | Rule text (condensed to its operative clause) | Verifiable from quote data? |
|---|---|---|---|
| **(b)(1)** | Self-Help | The trade-through "*was effected when the trading center displaying the protected quotation that was traded through was experiencing a failure, material delay, or malfunction of its systems or equipment*" | **Yes**, given declaration intervals |
| **(b)(2)** | Not regular way | The transaction "*was not a 'regular way' contract*" | No — settlement terms are asserted |
| **(b)(3)** | Single-priced auction | The transaction "*was a single-priced opening, reopening, or closing transaction by the trading center*" | No — transaction type is asserted |
| **(b)(4)** | Crossed market | Executed "*at a time when a protected bid was priced higher than a protected offer in the NMS stock*" | **Yes** |
| **(b)(5)** | ISO executed | The transaction "*was the execution of an order identified as an intermarket sweep order*" | Marking only |
| **(b)(6)** | ISO routed | Effected by a trading centre "*that simultaneously routed an intermarket sweep order to execute against the full displayed size of any protected quotation in the NMS stock that was traded through*" | **Yes**, given the routes |
| **(b)(7)** | Benchmark | Execution "*at a price that was not based, directly or indirectly, on the quoted price of the NMS stock at the time of execution and for which the material terms were not reasonably determinable at the time the commitment to execute the order was made*" | No — facts and circumstances |
| **(b)(8)** | Flickering quote | "*The trading center displaying the protected quotation that was traded through had displayed, within one second prior to execution of the transaction that constituted the trade-through, a best bid or best offer, as applicable, for the NMS stock with a price that was equal or inferior to the price of the trade-through transaction*" | **Yes** |
| **(b)(9)** | Stopped order | Execution of an order the trading centre had guaranteed at no worse than a specified price, where **(i)** it was for a customer account, **(ii)** the customer agreed to the price order-by-order, and **(iii)** the price was, "*for a stopped buy order, lower than the national best bid ... or, for a stopped sell order, higher than the national best offer*" | **(iii) yes**; (i)–(ii) asserted |

Rule 611(d) additionally lets the Commission grant exemptions by order. Standing
staff-level exemptions discussed in the FAQ include **qualified contingent
trades** (FAQ 3.12), certain **sub-penny trade-throughs** (3.13), **error
correction transactions** (3.19), **print protection transactions** (3.20) and
**non-convertible preferred securities** (3.21). These are Commission orders,
not paragraphs of Rule 611(b) — do not model them as if they were.

## 4. Rule 611(b)(8) precisely

The exception is **per venue**, **strictly backward looking**, and **directional**:

- Only the venue that displayed the protected quotation *that was traded
  through* can support it. A different venue's flicker is irrelevant.
- The window is the one second **prior to** execution. The quote in force *at*
  the execution is the quotation that was traded through, so it can never
  support the exception.
- "Equal or inferior to the price of the trade-through transaction" means
  inferior from the taker's standpoint: where a protected **offer** was traded
  through at price `P`, a prior offer `O ≥ P` qualifies; where a protected
  **bid** was traded through, a prior bid `B ≤ P` qualifies.

Read as "any protected quote changed within the last second", the exception
excepts essentially every trade-through in a liquid NMS stock, and the
surveillance engine reports nothing.

## 5. Intermarket sweep orders — Rule 600(b)(47), Rule 611(c)

> **Intermarket sweep order** means a limit order for an NMS stock that meets
> the following requirements: **(i)** When routed to a trading center, the limit
> order is identified as an intermarket sweep order; and **(ii)** Simultaneously
> with the routing of the limit order identified as an intermarket sweep order,
> one or more additional limit orders, as necessary, are routed to execute
> against the full displayed size of any protected bid, in the case of a limit
> order to sell, or the full displayed size of any protected offer, in the case
> of a limit order to buy, for the NMS stock with a price that is superior to
> the limit price of the limit order identified as an intermarket sweep order.
> These additional routed orders also must be marked as intermarket sweep
> orders.

> **Rule 611(c)** The trading center, broker, or dealer responsible for the
> routing of an intermarket sweep order shall take reasonable steps to establish
> that such order meets the requirements set forth in § 242.600(b)(47).

Engineering consequences:

- An ISO is a **limit** order. A market order cannot be one.
- The sweep obligation is measured against the ISO's **limit price**, not its
  execution price, and it runs to the **full displayed size** of each superior
  protected quotation.
- The directions are opposite: a **sell** ISO sweeps superior (higher)
  protected **bids**; a **buy** ISO sweeps superior (lower) protected **offers**.
- Under FAQ 4.09, a router may decline to route to a venue against which it is
  currently exercising Self-Help, and a broker-dealer that is not itself a
  trading centre may use the combined ISO/Self-Help exceptions if it elects to
  comply with the Rule 611(a) requirements applicable to trading centres.
- Older SEC guidance (including the 2008 FAQ) cites the ISO definition as
  **Rule 600(b)(30)**. The current codification is **600(b)(47)**; the text is
  the same.

## 6. Self-Help — Rule 611(b)(1) and FAQ 4.07

The staff FAQ specifies **three mandatory elements** of the policies and
procedures behind the exception:

1. **Notice.** "*A trading center that elects to use the self-help exception
   must notify the trading center whose quotations are bypassed. The notice can
   be sent by electronic mail and must be sent immediately upon use of the
   exception.*" It must provide a mechanism for reaching someone who can respond
   to inquiries. Automated trading centres displaying protected quotations must
   in turn provide a mechanism for **receiving** such notices and staff it in
   real time (FAQ 2.03).
2. **Systems assessment and response.** "*An order router is not entitled to
   bypass protected quotations pursuant to the self-help exception if it has
   reason to believe that an order-response problem is not attributable to a
   problem with systems for which the destination trading center is
   responsible.*" A router with a history of its own connectivity failures to a
   venue may not elect Self-Help when they recur.
3. **Objective parameters.** "*[T]he repeated failure of a destination trading
   center to respond within one second to an incoming IOC order (after adjusting
   for order transmission time) would justify use of the exception.*" The
   parameters must also state what **terminates** it.

Note what the one-second figure is and is not: it is a **repeated** failure to
turn around an **IOC** order, measured **after adjusting for transmission
time**. It is not a single slow round trip, and it is a floor for reasonableness
rather than the only permissible parameter.

FAQ 4.08 adds a narrower alternative: a trading centre that routed an order to
access the full displayed size of a protected quotation may continue trading
without regard to that quotation until a response is received — without
electing Self-Help at all. Bypassing the venue's quotations *generally* requires
the exception.

## 7. Trade-through severity

The rule prescribes no severity metric; the following is a surveillance
convention, useful for triage and for the Rule 611(a)(2) effectiveness review:

```
                  | P_exec − P_protected |
TradeThrough_bps = ------------------------- × 10,000
                        P_protected
```

where `P_protected` is **the protected quotation that was traded through** — the
protected offer for a through-the-offer print, the protected bid for a
through-the-bid print. Do **not** measure against the NBBO midpoint: the
midpoint is not a price anyone was obliged to respect, and it understates a
one-sided trade-through by roughly half a spread.

## 8. Records, clocks and CAT

| Requirement | Standard | Source |
|---|---|---|
| Industry Member clock synchronisation | Business Clocks within **50 ms** of NIST | CAT NMS Plan / FAQ R1 |
| Participant clock synchronisation | Within **100 µs** of NIST | CAT NMS Plan / FAQ R1 |
| Manual Order Event clocks | Within **1 second** of NIST | CAT NMS Plan / FAQ R1 |
| Timestamp granularity | **Milliseconds or finer**; where the firm's order-handling or execution systems capture a finer increment, report that increment, up to nanoseconds, **truncated** rather than rounded | CAT NMS Plan §6.8(b) / FAQ B2 |
| Retention of CAT-reportable records | **SEA Rule 17a-4(b)** — three years, the first two in an accessible place | CAT FAQ A23 |
| Retention of clock-synchronisation logs | **Five years** (or the whole compliance period if shorter) | CAT FAQ A23 |
| Rule 611 quotation data | No comprehensive quote database is mandated. FAQ 6.03 permits periodic reviews over selected periods, provided the firm retains enough firm-specific quotation data to demonstrate their reasonableness | Reg NMS FAQ 6.03 |

**Do not assert a six-year Rule 611 retention period.** Six years is the
Rule 17a-4(a) figure for blotters and ledgers, not for CAT-reportable order and
trade records or for trade-through surveillance output.

## 9. Rule status — proposed rescission

| Item | Detail |
|---|---|
| Release | No. 34-105655; File No. S7-2026-20; RIN 3235-AN50 |
| Title | *The Trade-Through Rule and Locked and Crossed Markets Provisions of Regulation NMS* |
| Action | **Proposed rule** |
| Published | 91 FR 36656, 17 June 2026 (Commission vote 11 June 2026) |
| Comments closed | 17 August 2026 |
| Scope | Rescind **Rule 611 in its entirety**; rescind **Rule 610(e)** (locking and crossing) in its entirety; rescind the definitions at **Rule 600(b)(6), (7), (47), (54), (81), (82) and (105)**; revise Rule 600(b)(26), (72), (89) and Rule 610(c), and Exchange Act Rules 15c3-5 and 15b9-1 |
| Status as of 2 Sep 2026 | **No final rule adopted.** Rule 611 remains in effect and enforceable |

Treat this as a watch item, not a change. Removing trade-through controls ahead
of a final rule would be a Rule 611(a)(1) deficiency on the day it happened.

## References

- 17 CFR 242.611 — Order protection rule.
  <https://www.law.cornell.edu/cfr/text/17/242.611>
- 17 CFR 242.600 — NMS security designation and definitions (paragraph (b)).
  <https://www.law.cornell.edu/cfr/text/17/242.600>
- SEC Division of Trading and Markets, *Responses to Frequently Asked Questions
  Concerning Rule 611 and Rule 610 of Regulation NMS*.
  <https://www.sec.gov/divisions/marketreg/nmsfaq610-11.htm>
- SEC Release No. 34-51808, *Regulation NMS* (adopting release, 2005).
  <https://www.sec.gov/files/rules/final/34-51808.pdf>
- SEC Release No. 34-105655, *The Trade-Through Rule and Locked and Crossed
  Markets Provisions of Regulation NMS* (proposed rescission, June 2026).
  <https://www.sec.gov/files/rules/proposed/2026/34-105655.pdf>
- CAT NMS Plan FAQ R1 (clock synchronisation), B2 (timestamp granularity),
  A23 (record retention).
  <https://www.catnmsplan.com/faq/r1>
