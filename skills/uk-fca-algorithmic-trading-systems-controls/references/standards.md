# UK Algorithmic Trading Systems and Controls — Regulatory Standards

Jurisdiction: **United Kingdom**. The instruments below are the FCA Handbook rule
and MiFID RTS 6 as it forms part of UK law (assimilated law). RTS 6 was the
standard the FCA assessed firms against in its August 2025 multi-firm review; the
Smarter Regulatory Framework programme is moving MiFID-era assimilated law into the
Handbook, so confirm current status before relying on a citation.

## 1. What actually binds

| Instrument | Provision | Obligation |
| :--- | :--- | :--- |
| FCA Handbook | **MAR 7A.3.2R** | A firm must have effective systems and controls so its trading systems are resilient and have sufficient capacity, are subject to appropriate trading thresholds and limits, prevent erroneous orders or systems "functioning in a way that may create or contribute to a disorderly market", and cannot be used contrary to the Market Abuse Regulation or venue rules. |
| MiFID RTS 6 | **Art. 12** | Kill functionality: cancel **immediately**, as an emergency measure, "any or all of its unexecuted orders submitted to any or all trading venues", covering orders from individual traders, desks and clients, with the firm able to identify which algorithm, trader, desk or client is responsible for each order. |
| MiFID RTS 6 | **Art. 15(1)** | Four pre-trade controls on order entry, for all financial instruments: (a) price collars that automatically block or cancel orders outside set price parameters, differentiating by instrument, "both on an order-by-order basis and over a specified period of time"; (b) maximum order values; (c) maximum order volumes; (d) maximum messages limits. |
| MiFID RTS 6 | **Art. 15(2)** | All orders sent to a venue must be included in the pre-trade limit calculation **immediately**. |
| MiFID RTS 6 | **Art. 15(3)** | Repeated automated execution throttles controlling how many times a strategy has been applied; after a pre-determined number of repeated executions the system "shall be automatically disabled until re-enabled by a designated staff member". |
| MiFID RTS 6 | **Art. 15(4)** | Market and credit risk limits based on the firm's capital base, clearing arrangements, trading strategy, risk tolerance and experience, adjusted for changing price and liquidity levels. |
| MiFID RTS 6 | **Art. 15(5)** | Automatically block or cancel orders from a trader without permission for an instrument, or orders that "risk compromising the investment firm's own risk thresholds", applied where appropriate per client, instrument, trader, desk or firm. |
| MiFID RTS 6 | **Art. 15(6)** | Blocked orders the firm nevertheless wishes to submit: procedures applied to a specific trade, on a temporary basis, in exceptional circumstances, subject to verification by the risk management function and authorisation by a designated individual. |
| MiFID RTS 9 | **Art. 3** (Comm. Del. Reg. (EU) 2017/566) | Ratio of unexecuted orders to transactions, calculated by the **trading venue** per member at least daily, in volume terms and in number terms, each as `total / total − 1`. Venues may set a maximum. Not an RTS 6 firm control. |
| FCA Handbook | **SYSC 9.1** | Records for MiFID business retained at least five years; the FCA has reserved the ability to require longer in specified cases. |
| MiFID RTS 6 | **Art. 28(3)** | Five-year retention of the Annex II order records — this applies **only** to a firm engaging in a high-frequency algorithmic trading technique. |

### Adjacent RTS 6 articles this skill does not implement

Art. 1 governance and desk/risk separation · Arts. 5–8 testing methodology,
conformance testing, testing environments, controlled deployment · Art. 9 annual
self-assessment and validation · Art. 10 stress testing at 2× the previous six
months' peak messaging and trading volumes · Art. 11 material change management ·
Art. 13 automated market-abuse surveillance · Art. 14 business continuity ·
Art. 16 real-time monitoring, including the only numeric latency bound in the
regime — "Real-time alerts shall be generated within five seconds after the
relevant event" (Art. 16(5)) · Art. 17 post-trade controls and reconciliation ·
Art. 18 security and access · Arts. 19–23 DEA provider obligations.

## 2. No numeric limits are prescribed

RTS 6 specifies **no** collar percentage, notional cap, volume cap, message rate,
execution count or capacity threshold. Art. 15(4) makes the calibration the firm's,
tied to its capital base and clearing arrangements. Any table that presents
"2.5% from mid" or "£500,000 per order" as a *regulatory limit* is wrong: those are
engineering placeholders. What is auditable is that a limit exists, that it blocks,
and that the firm can explain how it was derived.

There is also no UK NBBO to collar against. The US national best bid and offer is a
Reg NMS construct; the UK equity consolidated tape was still in procurement as of
2026. Art. 15(1)(a) requires only "set price parameters", so the reference price is
the firm's own documented choice.

## 3. Control formulas as implemented

1. **Price collar deviation** — evaluated cross-multiplied so behaviour exactly at
   the limit does not depend on a division rounding:

   $$|P_{\text{order}} - P_{\text{ref}}| \times 100 \;\le\; L_{\%} \times P_{\text{ref}}$$

   with $P_{\text{ref}} > 0$ required; a non-positive or non-finite reference is a
   rejection, not a skipped check.

2. **Unexecuted orders to transactions**, RTS 9 Art. 3 in number terms:

   $$R = \frac{N_{\text{orders}}}{N_{\text{transactions}}} - 1$$

   Undefined at $N_{\text{transactions}} = 0$; the engine then falls back to
   $N_{\text{orders}}$ as a conservative proxy, which is explicitly **not** the
   RTS 9 measure.

3. **Message-rate utilisation** against the firm's configured ceiling — the lower of
   its Art. 15(1)(d) maximum messages limit and the capacity its systems were tested
   to withstand under Art. 10:

   $$U_{\%} = \frac{\text{msgs/sec}_{\text{current}}}{\text{msgs/sec}_{\text{ceiling}}} \times 100$$

   A non-positive ceiling raises rather than reporting 0%, so a missing ceiling
   cannot silently disable the control.

## 4. Enforcement precedent

**Citigroup Global Markets Limited, FCA final notice 22 May 2024, £27,766,200**
(30% settlement discount from £39,666,000; the PRA imposed a concurrent
£33,880,000). On 2 May 2022 a trader intending to sell US$58m of equities created a
US$444bn basket through an input error. Controls blocked US$255bn; US$189bn reached
a trading algorithm and US$1.4bn was sold across European exchanges before
cancellation. The FCA found no hard block rejected the erroneous basket, that the
pop-up warning could be dismissed without the trader reading its full content, and
that real-time monitoring did not escalate quickly enough. Breaches cited:
Principles 2 and 3, and **MAR 7A.3.2R**.

The operative lesson for control design: Art. 15(1) says controls shall
"automatically **block or cancel**". An overridable alert is not that control.

## 5. Supervisory expectations

- **FCA, *Multi-firm review of algorithmic trading controls: high-level
  observations*, 21 August 2025.** Ten principal trading firms assessed against
  RTS 6. Self-assessment quality had improved since 2018; "all firms had adequate
  pre-trade controls in place", but ownership and compliance oversight of those
  controls needed clearer documentation at some firms, and simulation-testing
  sophistication varied widely. The publication "creates no new requirements".
- **FCA, *Algorithmic Trading Compliance in Wholesale Markets*, February 2018.** The
  earlier multi-firm review, covering algorithm identification and inventory,
  development and testing, risk controls, governance and oversight, and market
  conduct. It is a multi-firm review, not Finalised Guidance, and carries no FG
  number.
- **Governance sign-off.** RTS 6 Art. 9 requires the annual validation report to be
  drawn up by the risk management function, audited by internal audit where such a
  function exists, and approved by **senior management**. It names no SM&CR
  function. Under SM&CR, SMF16 (compliance oversight) applies to Core and Enhanced
  firms while SMF24 (chief operations function) exists only at Enhanced firms — so
  who signs is a firm-specific allocation of responsibilities, not an RTS 6
  requirement. See `uk-senior-managers-regime-algo-accountability`.

## 6. Sources

- Commission Delegated Regulation (EU) 2017/589 (RTS 6) — Commission text, and the
  FCA Handbook rendering at `handbook.fca.org.uk/technical-standards` (article
  headings for Arts. 5–18).
- Commission Delegated Regulation (EU) 2017/566 (RTS 9), Art. 3.
- FCA Handbook MAR 7A.3.2R; SYSC 9.1.
- FCA, *Multi-firm review of algorithmic trading controls: high-level observations*,
  21 August 2025.
- FCA, *Algorithmic Trading Compliance in Wholesale Markets*, February 2018.
- FCA press release and final notice, *FCA fines CGML £27,766,200*, 22 May 2024.
